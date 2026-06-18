"""
Unit tests for verify_init_data.

A bug here is a security hole, not just a broken feature.
Tests cover: valid data, wrong token, tampered payload, missing hash.
"""
import hashlib
import hmac
import json
import urllib.parse

import pytest

from tg import verify_init_data

BOT_TOKEN = "123456789:test_token_for_unit_tests_only"

USER = {"id": 42, "first_name": "Alice", "username": "alice", "language_code": "en"}


def make_init_data(fields: dict, token: str) -> str:
    """Build a correctly signed initData string from arbitrary fields."""
    data_check_string = "\n".join(f"{k}={v}" for k, v in sorted(fields.items()))
    secret = hmac.new(b"WebAppData", token.encode(), hashlib.sha256).digest()
    hash_val = hmac.new(secret, data_check_string.encode(), hashlib.sha256).hexdigest()
    return urllib.parse.urlencode({**fields, "hash": hash_val})


FIELDS = {
    "auth_date": "1717776000",
    "user": json.dumps(USER, separators=(",", ":")),
}


def test_valid_init_data_returns_user():
    init_data = make_init_data(FIELDS, BOT_TOKEN)
    user, chat_id = verify_init_data(init_data, BOT_TOKEN)
    assert user["id"] == USER["id"]
    assert user["first_name"] == USER["first_name"]
    assert user["username"] == USER["username"]
    assert chat_id is None   # no chat field in FIELDS


def test_chat_id_extracted_when_present():
    chat = {"id": -1001234567890, "type": "supergroup", "title": "Test Group"}
    fields = {**FIELDS, "chat": json.dumps(chat, separators=(",", ":"))}
    init_data = make_init_data(fields, BOT_TOKEN)
    user, chat_id = verify_init_data(init_data, BOT_TOKEN)
    assert chat_id == -1001234567890


def test_wrong_token_rejected():
    init_data = make_init_data(FIELDS, BOT_TOKEN)
    with pytest.raises(ValueError, match="Invalid hash"):
        verify_init_data(init_data, "999:wrong_token")


def test_tampered_auth_date_rejected():
    init_data = make_init_data(FIELDS, BOT_TOKEN)
    tampered = init_data.replace("1717776000", "9999999999")
    with pytest.raises(ValueError, match="Invalid hash"):
        verify_init_data(tampered, BOT_TOKEN)


def test_tampered_user_rejected():
    init_data = make_init_data(FIELDS, BOT_TOKEN)
    parsed = dict(urllib.parse.parse_qsl(init_data, keep_blank_values=True))
    parsed["user"] = json.dumps({"id": 99999, "first_name": "Eve"})
    tampered = urllib.parse.urlencode(parsed)
    with pytest.raises(ValueError, match="Invalid hash"):
        verify_init_data(tampered, BOT_TOKEN)


def test_missing_hash_rejected():
    init_data = urllib.parse.urlencode(FIELDS)
    with pytest.raises(ValueError, match="Missing hash"):
        verify_init_data(init_data, BOT_TOKEN)


def test_empty_string_rejected():
    with pytest.raises(ValueError, match="Missing hash"):
        verify_init_data("", BOT_TOKEN)


def test_no_user_field_returns_empty_dict():
    fields = {"auth_date": "1717776000"}
    init_data = make_init_data(fields, BOT_TOKEN)
    user, chat_id = verify_init_data(init_data, BOT_TOKEN)
    assert user == {}
    assert chat_id is None
