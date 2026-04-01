import logging
from pathlib import Path

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Project root (folder that contains `backend/` and `.env`), regardless of process cwd.
REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent

_log = logging.getLogger(__name__)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=REPO_ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    BOT_TOKEN: str
    WEBAPP_URL: str

    @field_validator("BOT_TOKEN", "WEBAPP_URL", mode="before")
    @classmethod
    def _strip_token_and_url(cls, v: object) -> object:
        if isinstance(v, str):
            s = v.strip().strip("\ufeff").replace("\r", "").strip()
            s = s.strip('"').strip("'").strip()
            return s
        return v

    @field_validator("ADMIN_TELEGRAM_IDS", "OWNER_TELEGRAM_USER_ID", mode="before")
    @classmethod
    def _strip_id_fields(cls, v: object) -> object:
        if isinstance(v, str):
            s = v.strip().strip("\ufeff").replace("\r", "").strip()
            s = s.strip('"').strip("'").strip()
            return s
        return v

    ADMIN_TELEGRAM_IDS: str = ""

    DATABASE_URL: str = "sqlite:///./habesha-bingo.db"

    DEFAULT_CALL_INTERVAL_SEC: int = 2
    # After the lobby pick countdown ends: seconds of pause before the first ball is drawn.
    POST_LOBBY_FIRST_CALL_DELAY_SEC: int = 3
    DEFAULT_WIN_MULTIPLIER: float = 2.0
    DEFAULT_MIN_STAKE_ETB: int = 10
    # Lobby: how long players can choose a card number before the round auto-starts.
    LOBBY_PICK_DURATION_SEC: int = 30
    # Lobby: how many bet-placed players are required before the host can start early.
    MIN_PLAYERS_TO_START: int = 2
    # Card numbers shown in the lobby (actual bingo calls stay 1–75).
    DEFAULT_LOBBY_CARD_MAX: int = 400
    # Minimum amount users can request for /withdraw (won balance only).
    MIN_WITHDRAWAL_ETB: float = 50.0
    # Receipts up to this amount (ETB) credit balance immediately; larger amounts queue for admin approve/reject.
    MAX_TELEBIRR_AUTO_CREDIT_ETB: float = 50.0
    # House commission on each round's total stakes (derash): winner receives (1 - fraction) × pool.
    OWNER_RAKE_FRACTION: float = 0.2
    # Telegram user ID that receives the rake (withdrawable). If empty, first ID in ADMIN_TELEGRAM_IDS is used.
    OWNER_TELEGRAM_USER_ID: str = ""
    # How long WebApp initData stays valid (Telegram auth_date check).
    TELEGRAM_INIT_MAX_AGE_SEC: int = 172800  # 48h — avoids false “wrong bot” after idle tab

    def admin_ids(self) -> set[int]:
        if not self.ADMIN_TELEGRAM_IDS.strip():
            return set()
        out: set[int] = set()
        for part in self.ADMIN_TELEGRAM_IDS.split(","):
            part = part.strip().strip('"').strip("'").strip()
            if not part:
                continue
            try:
                out.add(int(part))
            except ValueError:
                _log.warning("Invalid ADMIN_TELEGRAM_IDS segment (not an integer): %r", part)
        return out

    def bot_tokens(self) -> list[str]:
        """All bot API tokens (comma-separated in BOT_TOKEN). Used to verify WebApp initData from any of your bots."""
        parts = [p.strip() for p in str(self.BOT_TOKEN).split(",") if p.strip()]
        return parts

    def primary_bot_token(self) -> str:
        """First token: used for long-polling bot and /health getMe."""
        tokens = self.bot_tokens()
        if not tokens:
            raise ValueError("BOT_TOKEN is empty")
        return tokens[0]

    def owner_telegram_user_id(self) -> int | None:
        raw = (self.OWNER_TELEGRAM_USER_ID or "").strip()
        if raw:
            try:
                return int(raw)
            except ValueError:
                return None
        admins = self.admin_ids()
        return min(admins) if admins else None


settings = Settings()

