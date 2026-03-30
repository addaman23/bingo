import hashlib
import hmac
import json
import time
from typing import Any
from urllib.parse import parse_qs


class TelegramWebAppAuthError(Exception):
    pass


def _parse_init_data(init_data: str) -> dict[str, str]:
    # Telegram passes initData as query-string like: key=value&key2=value2&...
    parsed = parse_qs(init_data, keep_blank_values=True)
    # parse_qs returns list values
    return {k: (v[0] if v else "") for k, v in parsed.items()}


def verify_telegram_webapp_init_data(init_data: str, bot_token: str, max_age_seconds: int = 86400) -> dict[str, Any]:
    """
    Validates Telegram WebApp initData integrity using the bot token.

    Docs follow: https://core.telegram.org/bots/webapps#webappinitdata
    """
    if not init_data or not bot_token:
        raise TelegramWebAppAuthError("Missing initData or bot token")

    params = _parse_init_data(init_data)
    if "hash" not in params:
        raise TelegramWebAppAuthError("Missing hash")

    received_hash = params.pop("hash")
    if "auth_date" not in params:
        raise TelegramWebAppAuthError("Missing auth_date")

    auth_date = int(params["auth_date"])
    now = int(time.time())
    if now - auth_date > max_age_seconds:
        raise TelegramWebAppAuthError("initData expired")

    # Data-check-string: all fields except hash, sorted by key, joined by \n (Mini App spec).
    data_check_string = "\n".join([f"{k}={v}" for k, v in sorted(params.items())])

    # Secret key is HMAC-SHA256(bot_token, key="WebAppData"), not SHA256(bot_token).
    # See https://core.telegram.org/bots/webapps#validating-data-received-via-the-mini-app
    secret_key = hmac.new(
        b"WebAppData",
        bot_token.encode("utf-8"),
        hashlib.sha256,
    ).digest()
    computed_hash = hmac.new(secret_key, data_check_string.encode("utf-8"), hashlib.sha256).hexdigest()

    if not hmac.compare_digest(computed_hash, received_hash):
        raise TelegramWebAppAuthError("Invalid initData hash")

    user_obj = None
    if "user" in params and params["user"]:
        try:
            user_obj = json.loads(params["user"])
        except json.JSONDecodeError:
            user_obj = None

    return {
        "auth_date": auth_date,
        "raw": params,
        "telegram_user": user_obj,
    }

