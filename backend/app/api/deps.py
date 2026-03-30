import logging

from fastapi import Depends, Header, HTTPException, status
from sqlalchemy.orm import Session

from backend.app.core.config import settings
from backend.app.core.security import TelegramWebAppAuthError, verify_telegram_webapp_init_data
from backend.app.db.session import SessionLocal
from backend.app.db.crud import get_or_create_user

log = logging.getLogger("backend.auth")


def get_db():
    db = SessionLocal()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def get_current_user(
    # Frontend sends `X-Telegram-InitData` (Telegram WebApp initData).
    # FastAPI normally derives header name from the parameter name, which would
    # become `X-Telegram-Init-Data` (with an extra hyphen) and fail auth.
    x_telegram_init_data: str = Header(default="", alias="X-Telegram-InitData"),
    db: Session = Depends(get_db),
):
    auth = None
    last_err: TelegramWebAppAuthError | None = None
    for tok in settings.bot_tokens():
        try:
            auth = verify_telegram_webapp_init_data(
                x_telegram_init_data,
                tok,
                max_age_seconds=int(settings.TELEGRAM_INIT_MAX_AGE_SEC),
            )
            break
        except TelegramWebAppAuthError as e:
            last_err = e
    if auth is None:
        detail = str(last_err) if last_err else "Telegram auth failed"
        log.warning(
            "WebApp auth failed: %s (initData chars: %s, tokens tried: %s)",
            detail,
            len(x_telegram_init_data or ""),
            len(settings.bot_tokens()),
        )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=detail,
        )

    tg_user = auth.get("telegram_user")
    if not tg_user or "id" not in tg_user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing telegram user in initData")

    telegram_user_id = int(tg_user["id"])
    telegram_username = tg_user.get("username")
    user = get_or_create_user(db, telegram_user_id=telegram_user_id, telegram_username=telegram_username)
    db.flush()
    return user

