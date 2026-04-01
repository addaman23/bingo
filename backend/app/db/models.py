import enum
from datetime import datetime
from typing import Any, Optional

from sqlalchemy import BigInteger, DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class GameStatus(str, enum.Enum):
    lobby = "lobby"
    running = "running"
    finished = "finished"


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    telegram_user_id: Mapped[int] = mapped_column(BigInteger, unique=True, index=True)
    # Use Optional[...] instead of `T | None` for compatibility with SQLAlchemy
    # type introspection on Python versions where `X | None` can confuse parsing.
    telegram_username: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    # Normalized digits (e.g. Ethiopian mobile 9xxxxxxxx) from last Telebirr withdrawal account; used for admin /addbalance by phone.
    telebirr_phone_key: Mapped[Optional[str]] = mapped_column(String(32), nullable=True, index=True)

    balance_etb: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    # Deposits / admin credits — play only (not withdrawable). Stakes consume this bucket first.
    play_only_balance_etb: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    # Bingo winnings — available for /withdraw.
    withdrawable_balance_etb: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    deposits: Mapped[list["Deposit"]] = relationship("Deposit", back_populates="user")
    bets: Mapped[list["GameBet"]] = relationship("GameBet", back_populates="user")
    withdrawal_requests: Mapped[list["WithdrawalRequest"]] = relationship("WithdrawalRequest", back_populates="user")


class Deposit(Base):
    __tablename__ = "deposits"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    amount_etb: Mapped[float] = mapped_column(Float, nullable=False)
    note: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    # Telebirr SMS / receipt reference — unique when set (idempotent credits).
    telebirr_txn_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True, unique=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow, nullable=False)
    # Set when an admin reverses a fraudulent or mistaken Telebirr auto-deposit (balance clawed back).
    reversed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

    user: Mapped["User"] = relationship("User", back_populates="deposits")


class PendingTelebirrDeposit(Base):
    """Queued Telebirr paste when amount exceeds MAX_TELEBIRR_AUTO_CREDIT_ETB (admin approve/reject)."""

    __tablename__ = "pending_telebirr_deposits"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    amount_etb: Mapped[float] = mapped_column(Float, nullable=False)
    telebirr_txn_id: Mapped[str] = mapped_column(String(64), nullable=False, unique=True, index=True)
    raw_excerpt: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow, nullable=False)


class WithdrawalRequest(Base):
    __tablename__ = "withdrawal_requests"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    amount_etb: Mapped[float] = mapped_column(Float, nullable=False)
    bank: Mapped[str] = mapped_column(String(32), nullable=False)
    account_number: Mapped[str] = mapped_column(String(32), nullable=False)
    account_name: Mapped[str] = mapped_column(String(128), nullable=False)
    # pending → processing → completed | rejected
    status: Mapped[str] = mapped_column(String(16), default="pending", nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    user: Mapped["User"] = relationship("User", back_populates="withdrawal_requests")


class Game(Base):
    __tablename__ = "games"

    # Use string UUID from frontend/bot to keep it simple.
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    host_telegram_user_id: Mapped[int] = mapped_column(BigInteger, index=True)

    board_min: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    board_max: Mapped[int] = mapped_column(Integer, default=75, nullable=False)

    call_interval_sec: Mapped[int] = mapped_column(Integer, default=5, nullable=False)
    win_multiplier: Mapped[float] = mapped_column(Float, default=2.0, nullable=False)
    min_stake_etb: Mapped[int] = mapped_column(Integer, default=10, nullable=False)

    status: Mapped[str] = mapped_column(String(16), default=GameStatus.lobby.value, nullable=False, index=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow, nullable=False)
    started_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    last_advance_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

    current_call: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    next_index: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    winner_telegram_user_id: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True, index=True)
    winner_pattern_label: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    winner_line_cells_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    winner_gross_pool_etb: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    winner_house_rake_etb: Mapped[Optional[float]] = mapped_column(Float, nullable=True)

    # On a new lobby row: id of the game that just ended (so clients can show last round’s winner card).
    previous_round_game_id: Mapped[Optional[str]] = mapped_column(String(36), nullable=True, index=True)

    # Store list/arrays as JSON text to keep the model small.
    sequence_json: Mapped[str] = mapped_column(Text, nullable=False)  # shuffled numbers
    called_numbers_json: Mapped[str] = mapped_column(Text, nullable=False)  # numbers already called

    bets: Mapped[list["GameBet"]] = relationship("GameBet", back_populates="game", cascade="all,delete-orphan")


class GameBet(Base):
    __tablename__ = "game_bets"
    __table_args__ = (
        UniqueConstraint("game_id", "user_id", name="uq_game_user_bet"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    game_id: Mapped[str] = mapped_column(ForeignKey("games.id"), index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)

    stake_etb: Mapped[float] = mapped_column(Float, nullable=False)
    picked_numbers_json: Mapped[str] = mapped_column(Text, nullable=False)  # list[int] — ticket number(s); [ticket] for 75-ball

    # 5x5 card JSON (row-major lists); 0 = FREE space
    card_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    # [[row,col], ...] marked cells (user tapped when number was called)
    marked_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")

    # After one false BINGO this round, player cannot claim again (this game only).
    bingo_claim_blocked: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    # Settlement
    settled: Mapped[bool] = mapped_column(Integer, default=0, nullable=False)  # 0/1 for sqlite
    win: Mapped[bool] = mapped_column(Integer, default=0, nullable=False)  # 0/1 for sqlite
    payout_etb: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow, nullable=False)
    settled_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

    game: Mapped[Game] = relationship("Game", back_populates="bets")
    user: Mapped[User] = relationship("User", back_populates="bets")


def as_dict(obj: Any) -> dict[str, Any]:
    return {k: getattr(obj, k) for k in dir(obj) if not k.startswith("_")}

