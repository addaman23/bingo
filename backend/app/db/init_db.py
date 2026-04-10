from sqlalchemy import inspect, text

from backend.app.db.crud import backfill_telebirr_phone_keys_from_deposit_notes, backfill_user_telebirr_phone_keys
from backend.app.db.models import Base
from backend.app.db.session import SessionLocal, engine


def _sqlite_migrate() -> None:
    """Add columns introduced after first deploy (SQLite)."""
    if engine.dialect.name != "sqlite":
        return
    insp = inspect(engine)
    gcols = {c["name"] for c in insp.get_columns("games")}
    if "winner_telegram_user_id" not in gcols:
        with engine.begin() as conn:
            conn.execute(text("ALTER TABLE games ADD COLUMN winner_telegram_user_id INTEGER"))
    if "winner_pattern_label" not in gcols:
        with engine.begin() as conn:
            conn.execute(text("ALTER TABLE games ADD COLUMN winner_pattern_label VARCHAR(128)"))
    if "winner_line_cells_json" not in gcols:
        with engine.begin() as conn:
            conn.execute(text("ALTER TABLE games ADD COLUMN winner_line_cells_json TEXT"))
    if "winner_gross_pool_etb" not in gcols:
        with engine.begin() as conn:
            conn.execute(text("ALTER TABLE games ADD COLUMN winner_gross_pool_etb FLOAT"))
    if "winner_house_rake_etb" not in gcols:
        with engine.begin() as conn:
            conn.execute(text("ALTER TABLE games ADD COLUMN winner_house_rake_etb FLOAT"))
    if "previous_round_game_id" not in gcols:
        with engine.begin() as conn:
            conn.execute(text("ALTER TABLE games ADD COLUMN previous_round_game_id VARCHAR(36)"))
    bcols = {c["name"] for c in insp.get_columns("game_bets")}
    if "card_json" not in bcols:
        with engine.begin() as conn:
            conn.execute(text("ALTER TABLE game_bets ADD COLUMN card_json TEXT NOT NULL DEFAULT '[]'"))
    if "marked_json" not in bcols:
        with engine.begin() as conn:
            conn.execute(text("ALTER TABLE game_bets ADD COLUMN marked_json TEXT NOT NULL DEFAULT '[]'"))
    if "bingo_claim_blocked" not in bcols:
        with engine.begin() as conn:
            conn.execute(text("ALTER TABLE game_bets ADD COLUMN bingo_claim_blocked INTEGER NOT NULL DEFAULT 0"))
    dcols = {c["name"] for c in insp.get_columns("deposits")}
    if "telebirr_txn_id" not in dcols:
        with engine.begin() as conn:
            conn.execute(text("ALTER TABLE deposits ADD COLUMN telebirr_txn_id VARCHAR(64)"))
            conn.execute(text("CREATE UNIQUE INDEX IF NOT EXISTS ix_deposits_telebirr_txn_id ON deposits(telebirr_txn_id)"))
    if "reversed_at" not in dcols:
        with engine.begin() as conn:
            conn.execute(text("ALTER TABLE deposits ADD COLUMN reversed_at DATETIME"))
    ucols = {c["name"] for c in insp.get_columns("users")}
    if "play_only_balance_etb" not in ucols:
        with engine.begin() as conn:
            conn.execute(text("ALTER TABLE users ADD COLUMN play_only_balance_etb FLOAT NOT NULL DEFAULT 0"))
            conn.execute(text("ALTER TABLE users ADD COLUMN withdrawable_balance_etb FLOAT NOT NULL DEFAULT 0"))
            # Legacy rows: we cannot tell deposits vs wins — treat existing total as withdrawable
            # so past winners are not locked. New deposits/wins use the split rules in crud.
            conn.execute(text("UPDATE users SET withdrawable_balance_etb = balance_etb, play_only_balance_etb = 0"))
    ucols2 = {c["name"] for c in insp.get_columns("users")}
    if "telebirr_phone_key" not in ucols2:
        with engine.begin() as conn:
            conn.execute(text("ALTER TABLE users ADD COLUMN telebirr_phone_key VARCHAR(32)"))
            conn.execute(text("CREATE INDEX IF NOT EXISTS ix_users_telebirr_phone_key ON users(telebirr_phone_key)"))


def _postgres_migrate() -> None:
    """Adjust column types/compatibility for PostgreSQL deployments."""
    if engine.dialect.name != "postgresql":
        return
    with engine.begin() as conn:
        # Telegram IDs can exceed 32-bit range, so int4 columns overflow.
        conn.execute(text("ALTER TABLE users ALTER COLUMN telegram_user_id TYPE BIGINT"))
        conn.execute(text("ALTER TABLE games ALTER COLUMN host_telegram_user_id TYPE BIGINT"))
        conn.execute(text("ALTER TABLE games ALTER COLUMN winner_telegram_user_id TYPE BIGINT"))


def init_db() -> None:
    Base.metadata.create_all(bind=engine)
    _sqlite_migrate()
    _postgres_migrate()
    db = SessionLocal()
    try:
        backfill_user_telebirr_phone_keys(db)
        backfill_telebirr_phone_keys_from_deposit_notes(db)
        db.commit()
    except Exception:
        db.rollback()
    finally:
        db.close()

