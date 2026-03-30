import json
import urllib.error
import urllib.request

from fastapi import APIRouter

from backend.app.core.config import REPO_ROOT, settings

router = APIRouter(tags=["health"])


@router.get("/health")
def health():
    env_path = REPO_ROOT / ".env"
    return {
        "ok": True,
        "env_file_path": str(env_path),
        "env_file_on_disk": env_path.is_file(),
    }


@router.get("/health/bot")
def health_bot():
    """
    Calls Telegram getMe with the server's BOT_TOKEN.
    Compare `username` with the @bot that shows your WebApp button — they must match.
    Does not expose the token.
    """
    env_path = REPO_ROOT / ".env"
    try:
        primary = settings.primary_bot_token()
    except ValueError:
        primary = ""
    tokens = settings.bot_tokens()
    token_len = len(primary)
    token_hint = f"{primary[:6]}…{primary[-4:]}" if token_len > 12 else "(too short or empty)"
    out: dict = {
        "env_file_path": str(env_path),
        "env_file_on_disk": env_path.is_file(),
        "bot_token_count": len(tokens),
        "bot_token_chars": token_len,
        "bot_token_fingerprint": token_hint,
    }
    if not primary or token_len < 40:
        out["ok"] = False
        out["error"] = "BOT_TOKEN missing or implausibly short — check .env and Windows/macOS environment variables (they override .env)."
        return out
    url = f"https://api.telegram.org/bot{primary}/getMe"
    try:
        req = urllib.request.Request(url, method="GET")
        with urllib.request.urlopen(req, timeout=8) as resp:
            data = json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        body = e.read().decode(errors="replace") if e.fp else ""
        out["ok"] = False
        out["error"] = f"Telegram HTTP {e.code}"
        out["telegram_body"] = body[:500]
        return out
    except Exception as e:
        out["ok"] = False
        out["error"] = str(e)
        return out
    if not data.get("ok"):
        out["ok"] = False
        out["error"] = data.get("description", "getMe failed")
        return out
    r = data.get("result") or {}
    uname = r.get("username")
    out["ok"] = True
    out["bot_id"] = r.get("id")
    out["bot_username"] = uname
    out["bot_first_name"] = r.get("first_name")
    out["mini_app_must_open_from"] = f"https://t.me/{uname}" if uname else None
    out["hint"] = (
        "Long-polling uses the first BOT_TOKEN only. For multiple bots, set BOT_TOKEN=\"token1,token2\" "
        "(WebApp login is verified against every token). Restart uvicorn after changes."
    )
    return out

