import hashlib
import hmac
import json
from urllib.parse import parse_qsl, unquote


def verify_init_data(init_data: str, bot_token: str) -> tuple[dict, int | None]:
    """
    Verify Telegram Mini App initData signature.

    Returns (user_data, chat_id).
    chat_id is the integer id from the 'chat' field when present (group context),
    or None when the app was opened outside a group (DM / direct link).

    Raises ValueError if the hash is missing or doesn't match.
    """
    parsed = dict(parse_qsl(init_data, keep_blank_values=True))
    received_hash = parsed.pop("hash", None)
    if not received_hash:
        raise ValueError("Missing hash")
    data_check_string = "\n".join(f"{k}={v}" for k, v in sorted(parsed.items()))
    secret_key = hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()
    expected   = hmac.new(secret_key, data_check_string.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, received_hash):
        raise ValueError("Invalid hash — reject request")
    user_data = json.loads(unquote(parsed.get("user", "{}")))
    chat_raw  = parsed.get("chat")
    chat_id   = json.loads(unquote(chat_raw)).get("id") if chat_raw else None
    return user_data, chat_id
