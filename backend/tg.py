import hashlib
import hmac
import json
from urllib.parse import parse_qsl, unquote


def verify_init_data(init_data: str, bot_token: str) -> dict:
    """
    Verify Telegram Mini App initData signature and return the user dict.

    Raises ValueError if the hash is missing or doesn't match — caller
    must treat that as a 403, not a 500.
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
    return json.loads(unquote(parsed.get("user", "{}")))
