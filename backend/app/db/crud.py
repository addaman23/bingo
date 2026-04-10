import json
import re
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from backend.app.core.config import settings
from backend.app.db.models import Deposit, Game, GameBet, GameStatus, PendingTelebirrDeposit, User, WithdrawalRequest
from backend.app.game.bingo_card import (
    generate_card,
    generate_card_from_card_id,
    has_complete_line,
    marks_to_set,
    winning_line_cells,
)


def telebirr_phone_key_normalize(raw: str) -> str:
    """Canonical key for matching Telebirr / mobile numbers (Ethiopia-oriented). Empty if unusable."""
    d = "".join(c for c in (raw or "") if c.isdigit())
    if not d:
        return ""
    if d.startswith("251") and len(d) > 3:
        d = d[3:]
    if d.startswith("0"):
        d = d[1:]
    if len(d) >= 9 and d[0] == "9":
        return d[:9]
    return d if len(d) >= 9 else ""


def looks_like_phone_query(raw: str) -> bool:
    s = (raw or "").strip()
    low = s.lower()
    if low.startswith("phone:") or low.startswith("tel:") or low.startswith("p:"):
        return True
    if "+" in s:
        return True
    if any(c in s for c in " -"):
        return True
    d = "".join(c for c in s if c.isdigit())
    if len(d) < 9:
        return False
    if d.startswith("251") and len(d) >= 12:
        return True
    if len(d) == 10 and d.startswith("0"):
        return True
    if len(d) == 9 and d[0] == "9":
        return True
    return False


def find_users_by_telebirr_phone_key(db: Session, key: str) -> list[User]:
    if len(key) < 9:
        return []
    return list(db.execute(select(User).where(User.telebirr_phone_key == key)).scalars().all())


def find_users_by_telebirr_phone_lookup(db: Session, key: str) -> list[User]:
    """
    Match users by ``telebirr_phone_key``, or by any past withdrawal ``account_number``
    that normalizes to the same key (covers users whose key was never backfilled).
    """
    if len(key) < 9:
        return []
    direct = find_users_by_telebirr_phone_key(db, key)
    if direct:
        return direct
    wrs = db.execute(
        select(WithdrawalRequest).order_by(WithdrawalRequest.created_at.desc()).limit(8000)
    ).scalars().all()
    seen_ids: set[int] = set()
    out: list[User] = []
    for wr in wrs:
        if telebirr_phone_key_normalize(wr.account_number or "") != key:
            continue
        u = db.get(User, wr.user_id)
        if u and u.id not in seen_ids:
            seen_ids.add(u.id)
            out.append(u)
    return out


# Ethiopian mobile-like segments in Telebirr SMS / receipts (avoid greedy digit runs from amounts).
_TELEBIRR_TEXT_PHONE_RES = tuple(
    re.compile(p, re.IGNORECASE)
    for p in (
        r"(?:\+?251|0)(9\d{8})(?!\d)",
        r"(?:\+?251|0)(7\d{8})(?!\d)",
        r"(?<![\d])(9\d{8})(?!\d)",
        r"(?<![\d])(7\d{8})(?!\d)",
    )
)


def extract_telebirr_phone_keys_from_text(text: str | None) -> list[str]:
    """Normalized mobile keys found in pasted Telebirr / bank SMS text."""
    if not text:
        return []
    seen: set[str] = set()
    out: list[str] = []
    for rx in _TELEBIRR_TEXT_PHONE_RES:
        for m in rx.finditer(text):
            k = telebirr_phone_key_normalize(m.group(0))
            if len(k) >= 9 and k not in seen:
                seen.add(k)
                out.append(k)
    return out


def attach_telebirr_phone_key_from_receipt(user: User, text: str | None) -> None:
    """If the user has no phone key yet, try to learn it from receipt / SMS body."""
    if user.telebirr_phone_key or not text:
        return
    for k in extract_telebirr_phone_keys_from_text(text):
        if len(k) >= 9:
            user.telebirr_phone_key = k
            return


def backfill_user_telebirr_phone_keys(db: Session) -> None:
    """Set telebirr_phone_key from the latest withdrawal row per user when still empty."""
    wrs = db.execute(select(WithdrawalRequest).order_by(WithdrawalRequest.created_at.desc())).scalars().all()
    for wr in wrs:
        u = db.get(User, wr.user_id)
        if not u or u.telebirr_phone_key:
            continue
        k = telebirr_phone_key_normalize(wr.account_number)
        if len(k) >= 9:
            u.telebirr_phone_key = k


def backfill_telebirr_phone_keys_from_deposit_notes(db: Session, *, limit: int = 4000) -> None:
    """Set telebirr_phone_key from Telebirr deposit note text when still empty (SMS excerpt)."""
    q = (
        select(Deposit)
        .join(User, Deposit.user_id == User.id)
        .where(User.telebirr_phone_key.is_(None))
        .where(Deposit.note.is_not(None))
        .order_by(Deposit.created_at.desc())
        .limit(limit)
    )
    for dep in db.execute(q).scalars().all():
        u = db.get(User, dep.user_id)
        if not u or u.telebirr_phone_key:
            continue
        attach_telebirr_phone_key_from_receipt(u, dep.note)


def _sync_wallet_total(user: User) -> None:
    user.balance_etb = float(user.play_only_balance_etb) + float(user.withdrawable_balance_etb)


def deduct_stake_from_wallet(user: User, stake_etb: float) -> None:
    """Use play-only (deposited) balance first, then withdrawable (winnings)."""
    stake = float(stake_etb)
    if stake <= 0:
        return
    play = float(user.play_only_balance_etb)
    from_play = min(play, stake)
    user.play_only_balance_etb = play - from_play
    rem = stake - from_play
    user.withdrawable_balance_etb = float(user.withdrawable_balance_etb) - rem
    _sync_wallet_total(user)


def credit_play_only(user: User, amount_etb: float) -> None:
    user.play_only_balance_etb = float(user.play_only_balance_etb) + float(amount_etb)
    _sync_wallet_total(user)


def deduct_from_wallet(user: User, amount_etb: float) -> None:
    """Remove ETB from play-only first, then withdrawable (mirror of stake order)."""
    amt = float(amount_etb)
    if amt <= 0:
        return
    play = float(user.play_only_balance_etb)
    won = float(user.withdrawable_balance_etb)
    total = play + won
    if total + 1e-6 < amt:
        raise ValueError(
            f"Cannot remove {amt:.2f} ETB: user only has {total:.2f} ETB (play {play:.2f} + won {won:.2f})"
        )
    from_play = min(play, amt)
    user.play_only_balance_etb = play - from_play
    rem = amt - from_play
    user.withdrawable_balance_etb = won - rem
    _sync_wallet_total(user)


def credit_withdrawable(user: User, amount_etb: float) -> None:
    user.withdrawable_balance_etb = float(user.withdrawable_balance_etb) + float(amount_etb)
    _sync_wallet_total(user)


def get_user_by_telegram_username(db: Session, username: str) -> User | None:
    """Match Telegram @username (case-insensitive). User must exist in DB (e.g. after /start)."""
    u = username.strip().removeprefix("@").lower()
    if not u:
        return None
    return db.execute(select(User).where(func.lower(User.telegram_username) == u)).scalar_one_or_none()


def get_or_create_user(db: Session, telegram_user_id: int, telegram_username: str | None) -> User:
    user = db.execute(select(User).where(User.telegram_user_id == telegram_user_id)).scalar_one_or_none()
    if user:
        if telegram_username and user.telegram_username != telegram_username:
            user.telegram_username = telegram_username
        return user
    user = User(
        telegram_user_id=telegram_user_id,
        telegram_username=telegram_username,
        balance_etb=0.0,
        play_only_balance_etb=0.0,
        withdrawable_balance_etb=0.0,
    )
    db.add(user)
    db.flush()
    return user


def deposit_amount(db: Session, telegram_user_id: int, telegram_username: str | None, amount_etb: float, note: str | None) -> User:
    user = get_or_create_user(db, telegram_user_id, telegram_username)
    credit_play_only(user, amount_etb)
    dep = Deposit(user_id=user.id, amount_etb=float(amount_etb), note=note)
    db.add(dep)
    db.flush()
    return user


class DuplicateTelebirrTxnError(Exception):
    """This Telebirr transaction ID was already used to credit a balance."""


def telebirr_txn_id_taken_in_deposits(db: Session, telebirr_txn_id: str) -> bool:
    tid = telebirr_txn_id.strip().upper()
    return db.execute(select(Deposit).where(Deposit.telebirr_txn_id == tid)).scalar_one_or_none() is not None


def create_pending_telebirr_deposit(
    db: Session,
    telegram_user_id: int,
    telegram_username: str | None,
    amount_etb: float,
    telebirr_txn_id: str,
    raw_excerpt: str,
) -> tuple[PendingTelebirrDeposit, User]:
    tid = telebirr_txn_id.strip().upper()
    if not tid:
        raise ValueError("Missing transaction reference")
    if telebirr_txn_id_taken_in_deposits(db, tid):
        raise DuplicateTelebirrTxnError()
    if (
        db.execute(select(PendingTelebirrDeposit).where(PendingTelebirrDeposit.telebirr_txn_id == tid)).scalar_one_or_none()
        is not None
    ):
        raise ValueError("This transaction number is already waiting for admin review.")
    user = get_or_create_user(db, telegram_user_id=telegram_user_id, telegram_username=telegram_username)
    excerpt = (raw_excerpt or "").strip().replace("\n", " ")[:2000]
    attach_telebirr_phone_key_from_receipt(user, excerpt)
    p = PendingTelebirrDeposit(
        user_id=user.id,
        amount_etb=float(amount_etb),
        telebirr_txn_id=tid,
        raw_excerpt=excerpt or f"telebirr:{tid}",
    )
    db.add(p)
    db.flush()
    return p, user


def approve_pending_telebirr_deposit(db: Session, pending_id: int) -> tuple[User, float, str]:
    p = db.get(PendingTelebirrDeposit, pending_id)
    if not p:
        raise ValueError("Pending deposit not found")
    user = db.get(User, p.user_id)
    if not user:
        raise ValueError("User not found")
    amount = float(p.amount_etb)
    tid = p.telebirr_txn_id
    excerpt = p.raw_excerpt
    user = deposit_from_telebirr_paste(
        db,
        telegram_user_id=user.telegram_user_id,
        telegram_username=user.telegram_username,
        amount_etb=amount,
        telebirr_txn_id=tid,
        raw_excerpt=excerpt,
    )
    db.delete(p)
    db.flush()
    return user, amount, tid


def reject_pending_telebirr_deposit(db: Session, pending_id: int) -> tuple[int, float, str]:
    p = db.get(PendingTelebirrDeposit, pending_id)
    if not p:
        raise ValueError("Pending deposit not found")
    u = db.get(User, p.user_id)
    tg = int(u.telegram_user_id) if u else 0
    amount = float(p.amount_etb)
    txn = p.telebirr_txn_id
    db.delete(p)
    db.flush()
    return tg, amount, txn


def list_pending_telebirr_deposits(db: Session, limit: int = 30) -> list[PendingTelebirrDeposit]:
    q = select(PendingTelebirrDeposit).order_by(PendingTelebirrDeposit.created_at.desc()).limit(limit)
    return list(db.execute(q).scalars().all())


def deposit_from_telebirr_paste(
    db: Session,
    telegram_user_id: int,
    telegram_username: str | None,
    amount_etb: float,
    telebirr_txn_id: str,
    raw_excerpt: str | None,
) -> User:
    """Credit user from a pasted receipt; ``telebirr_txn_id`` must be unused."""
    tid = telebirr_txn_id.strip().upper()
    if not tid:
        raise ValueError("Missing transaction reference")
    dup = db.execute(select(Deposit).where(Deposit.telebirr_txn_id == tid)).scalar_one_or_none()
    if dup:
        raise DuplicateTelebirrTxnError()
    user = get_or_create_user(db, telegram_user_id=telegram_user_id, telegram_username=telegram_username)
    credit_play_only(user, amount_etb)
    note = (raw_excerpt or "").strip().replace("\n", " ")[:240] or f"telebirr:{tid}"
    dep = Deposit(
        user_id=user.id,
        amount_etb=float(amount_etb),
        note=note,
        telebirr_txn_id=tid,
    )
    db.add(dep)
    attach_telebirr_phone_key_from_receipt(user, note)
    db.flush()
    return user


def list_recent_deposits(db: Session, *, limit: int = 20, telebirr_only: bool = True) -> list[Deposit]:
    q = select(Deposit).order_by(Deposit.created_at.desc()).limit(limit)
    if telebirr_only:
        q = q.where(Deposit.telebirr_txn_id.is_not(None))
    return list(db.execute(q).scalars().all())


def admin_reverse_deposit(db: Session, deposit_id: int) -> tuple[Deposit, User]:
    """Claw back a deposit row and mark it reversed (Telebirr txn id stays — cannot auto-credit twice)."""
    dep = db.get(Deposit, deposit_id)
    if not dep:
        raise ValueError("Deposit not found")
    if dep.reversed_at is not None:
        raise ValueError("This deposit was already reversed")
    user = db.get(User, dep.user_id)
    if not user:
        raise ValueError("User not found")
    deduct_from_wallet(user, float(dep.amount_etb))
    dep.reversed_at = datetime.now(timezone.utc)
    db.flush()
    return dep, user


def withdrawal_request_short_id(wr: WithdrawalRequest) -> str:
    return wr.id.replace("-", "")[:8]


def find_withdrawal_by_short_id(db: Session, token: str) -> WithdrawalRequest | None:
    raw = (token or "").strip()
    # Users often paste "<a62b650f>" or "/withdraw_complete<a62b650f>" fragments from chat.
    while raw.startswith("<"):
        raw = raw[1:].strip()
    while raw.endswith(">"):
        raw = raw[:-1].strip()
    normalized = raw.lower().replace("-", "")
    if len(normalized) < 6:
        return None
    rows = db.execute(select(WithdrawalRequest)).scalars().all()
    for wr in rows:
        if wr.id.replace("-", "").lower().startswith(normalized):
            return wr
    return None


def create_withdrawal_request(
    db: Session,
    user: User,
    amount_etb: float,
    bank: str,
    account_number: str,
    account_name: str,
) -> WithdrawalRequest:
    min_w = float(settings.MIN_WITHDRAWAL_ETB)
    amount = float(amount_etb)
    if amount < min_w - 1e-9:
        raise ValueError(f"Minimum withdrawal is {min_w:.0f} ETB")
    if amount > float(user.withdrawable_balance_etb) + 1e-6:
        raise ValueError("Insufficient withdrawable (won) balance")
    user.withdrawable_balance_etb = float(user.withdrawable_balance_etb) - amount
    _sync_wallet_total(user)
    wid = str(uuid.uuid4())
    wr = WithdrawalRequest(
        id=wid,
        user_id=user.id,
        amount_etb=amount,
        bank=bank.strip()[:32] or "Telebirr",
        account_number=account_number.strip()[:32],
        account_name=account_name.strip()[:128],
        status="pending",
    )
    db.add(wr)
    pk = telebirr_phone_key_normalize(account_number)
    if len(pk) >= 9:
        user.telebirr_phone_key = pk
    db.flush()
    return wr


def admin_set_withdrawal_processing(db: Session, request_id: str) -> WithdrawalRequest:
    wr = db.get(WithdrawalRequest, request_id)
    if not wr:
        raise ValueError("Request not found")
    if wr.status != "pending":
        raise ValueError("Only pending requests can move to processing")
    wr.status = "processing"
    db.flush()
    return wr


def admin_complete_withdrawal(db: Session, request_id: str) -> WithdrawalRequest:
    wr = db.get(WithdrawalRequest, request_id)
    if not wr:
        raise ValueError("Request not found")
    if wr.status not in ("pending", "processing"):
        raise ValueError("Request is not active")
    wr.status = "completed"
    db.flush()
    return wr


def list_withdrawal_requests(db: Session, status: str | None = None, limit: int = 40) -> list[WithdrawalRequest]:
    q = select(WithdrawalRequest).join(User, WithdrawalRequest.user_id == User.id)
    if status:
        q = q.where(WithdrawalRequest.status == status)
    q = q.order_by(User.id.asc(), WithdrawalRequest.created_at.desc()).limit(limit)
    return list(db.execute(q).scalars().all())


def admin_reject_withdrawal(db: Session, request_id: str) -> tuple[WithdrawalRequest, User]:
    wr = db.get(WithdrawalRequest, request_id)
    if not wr:
        raise ValueError("Request not found")
    if wr.status not in ("pending", "processing"):
        raise ValueError("Cannot reject this request")
    user = db.execute(select(User).where(User.id == wr.user_id)).scalar_one()
    credit_withdrawable(user, float(wr.amount_etb))
    wr.status = "rejected"
    db.flush()
    return wr, user


def create_game(db: Session, host_telegram_user_id: int, board_min: int, board_max: int, call_interval_sec: int, win_multiplier: float, min_stake_etb: int) -> Game:
    import random

    # Calling sequence is always standard 75-ball when the lobby sells many "card" IDs.
    if board_max > 75:
        numbers = list(range(1, 76))
    else:
        numbers = list(range(board_min, board_max + 1))
    random.shuffle(numbers)
    game_id = str(uuid.uuid4())

    game = Game(
        id=game_id,
        host_telegram_user_id=host_telegram_user_id,
        board_min=board_min,
        board_max=board_max,
        call_interval_sec=call_interval_sec,
        win_multiplier=float(win_multiplier),
        min_stake_etb=int(min_stake_etb),
        status=GameStatus.lobby.value,
        created_at=datetime.utcnow(),
        started_at=None,
        finished_at=None,
        last_advance_at=None,
        current_call=None,
        next_index=0,
        sequence_json=json.dumps(numbers),
        called_numbers_json=json.dumps([]),
    )
    db.add(game)
    db.flush()
    return game


def spawn_next_lobby_after_round(db: Session, finished: Game) -> Game:
    """Start a fresh lobby immediately after a round ends so players can pick new cards."""
    ng = create_game(
        db=db,
        host_telegram_user_id=int(finished.host_telegram_user_id),
        board_min=int(finished.board_min),
        board_max=int(finished.board_max),
        call_interval_sec=int(finished.call_interval_sec),
        win_multiplier=float(finished.win_multiplier),
        min_stake_etb=int(finished.min_stake_etb),
    )
    ng.previous_round_game_id = finished.id
    db.flush()
    return ng


def _naive_utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt
    return dt.astimezone(timezone.utc).replace(tzinfo=None)


def lobby_pick_deadline(game: Game) -> datetime:
    return _naive_utc(game.created_at) + timedelta(seconds=int(settings.LOBBY_PICK_DURATION_SEC))


def lobby_pick_state(game: Game, now: datetime) -> dict[str, Any]:
    if game.status != GameStatus.lobby.value:
        return {"pick_seconds_remaining": 0, "pick_open": False}
    deadline = lobby_pick_deadline(game)
    n = _naive_utc(now)
    rem = max(0, int((deadline - n).total_seconds()))
    return {"pick_seconds_remaining": rem, "pick_open": rem > 0}


def start_running_from_lobby(db: Session, game: Game, now: datetime) -> Game:
    if game.status != GameStatus.lobby.value:
        return game
    sequence = json.loads(game.sequence_json) if game.sequence_json else []
    if not sequence:
        return game
    game.status = GameStatus.running.value
    game.started_at = _naive_utc(now)
    game.last_advance_at = _naive_utc(now)
    # First ball is emitted after POST_LOBBY_FIRST_CALL_DELAY_SEC (see advance_game_if_needed).
    game.current_call = None
    game.called_numbers_json = json.dumps([])
    game.next_index = 0
    db.flush()
    return game


def running_next_call_interval_sec(game: Game) -> int:
    """Wait time after last_advance_at before the next call (longer gap before the very first ball)."""
    called = load_json_list(game.called_numbers_json)
    if len(called) == 0:
        return max(0, int(settings.POST_LOBBY_FIRST_CALL_DELAY_SEC))
    # Use the current default from settings for all running rounds so tweaks
    # (e.g. DEFAULT_CALL_INTERVAL_SEC=2) take effect immediately, even for
    # games created before the config change.
    return max(1, int(settings.DEFAULT_CALL_INTERVAL_SEC))


def maybe_auto_start_lobby(db: Session, game: Game, now: datetime) -> Game:
    if game.status != GameStatus.lobby.value:
        return game
    # Auto-start early when enough bet-placed players have joined the lobby.
    min_players = int(settings.MIN_PLAYERS_TO_START)
    players_count = int(
        db.execute(
            select(func.count(func.distinct(GameBet.user_id))).where(GameBet.game_id == game.id).where(
                GameBet.settled == 0
            )
        ).one()[0]
    )
    pick_state = lobby_pick_state(game, now)
    if pick_state["pick_open"]:
        # Always wait for the full pick window — no early start when enough players join.
        return game

    # Pick window closed (timer reached zero). Only start if enough players selected cards.
    if players_count >= min_players:
        return start_running_from_lobby(db, game, now)

    # Not enough players: reset the lobby pick timer and keep everyone in lobby.
    # We keep existing bets so previously selected cards remain secured.
    # IMPORTANT: Use the previous deadline as the anchor so multiple devices polling
    # `/games/active` don't each "extend from now" at slightly different moments.
    # This keeps the countdown consistent across devices.
    old_deadline = lobby_pick_deadline(game)
    game.created_at = _naive_utc(old_deadline)
    game.current_call = None
    game.called_numbers_json = json.dumps([])
    game.next_index = 0
    db.flush()
    return game


def get_active_game_for_user(db: Session, now: datetime, user: User) -> Game | None:
    """
    Return the most relevant game for a specific user.

    Key behavior: if the user has a bet in some running/lobby game, we return
    that game. This prevents users from being dropped into "spectator mode"
    when there are multiple active games.
    """
    cutoff = now - timedelta(minutes=60)

    bets_subq = select(GameBet.game_id).where(GameBet.user_id == user.id)
    user_tg_id = int(user.telegram_user_id)

    # 1) Prefer a running game where the user has an active bet (player).
    running_bet = (
        select(Game)
        .where(Game.status == GameStatus.running.value)
        .where(Game.created_at >= cutoff)
        .where(Game.id.in_(bets_subq))
        .order_by(Game.started_at.desc().nullslast(), Game.created_at.desc())
        .limit(1)
    )
    running_game = db.execute(running_bet).scalar_one_or_none()
    if running_game:
        return running_game

    # 2) Otherwise prefer their lobby game (where they already picked).
    lobby_bet = (
        select(Game)
        .where(Game.status == GameStatus.lobby.value)
        .where(Game.created_at >= cutoff)
        .where(Game.id.in_(bets_subq))
        .order_by(Game.created_at.desc())
        .limit(1)
    )
    lobby_game = db.execute(lobby_bet).scalar_one_or_none()
    if lobby_game:
        return lobby_game

    # 3) If the user is the host of a running game, allow them to see it (they can call).
    running_host = (
        select(Game)
        .where(Game.status == GameStatus.running.value)
        .where(Game.created_at >= cutoff)
        .where(Game.host_telegram_user_id == user_tg_id)
        .order_by(Game.started_at.desc().nullslast(), Game.created_at.desc())
        .limit(1)
    )
    running_host_game = db.execute(running_host).scalar_one_or_none()
    if running_host_game:
        return running_host_game

    # 4) If the user is the host, show their latest lobby (no calls until enough picks).
    lobby_host = (
        select(Game)
        .where(Game.status == GameStatus.lobby.value)
        .where(Game.created_at >= cutoff)
        .where(Game.host_telegram_user_id == user_tg_id)
        .order_by(Game.created_at.desc())
        .limit(1)
    )
    lobby_host_game = db.execute(lobby_host).scalar_one_or_none()
    if lobby_host_game:
        return lobby_host_game

    # 5) Final fallback:
    # If any round is currently running, users without a bet should watch it
    # as spectators (Habesha behavior) until the next lobby opens.
    running_global = (
        select(Game)
        .where(Game.status == GameStatus.running.value)
        .where(Game.created_at >= cutoff)
        .order_by(Game.started_at.desc().nullslast(), Game.created_at.desc())
        .limit(1)
    )
    running_game_global = db.execute(running_global).scalar_one_or_none()
    if running_game_global:
        return running_game_global

    # No running game: show latest lobby so user can select a card.
    lobby_global = (
        select(Game)
        .where(Game.status == GameStatus.lobby.value)
        .where(Game.created_at >= cutoff)
        .order_by(Game.created_at.desc())
        .limit(1)
    )
    lobby_game_global = db.execute(lobby_global).scalar_one_or_none()

    return lobby_game_global


def get_active_game(db: Session, now: datetime) -> Game | None:
    """
    Return the most relevant in-progress/shared game:
    - prefer a running game
    - else return a recent lobby game
    """
    cutoff = now - timedelta(minutes=60)

    running = (
        select(Game)
        .where(Game.status == GameStatus.running.value)
        .where(Game.created_at >= cutoff)
        .order_by(Game.started_at.desc().nullslast(), Game.created_at.desc())
        .limit(1)
    )
    running_game = db.execute(running).scalar_one_or_none()
    if running_game:
        return running_game

    lobby = (
        select(Game)
        .where(Game.status == GameStatus.lobby.value)
        .where(Game.created_at >= cutoff)
        .order_by(Game.created_at.desc())
        .limit(1)
    )
    lobby_game = db.execute(lobby).scalar_one_or_none()
    if lobby_game:
        return lobby_game

    # Finished rounds always spawn a follow-up lobby when the round ends (see advance / claim_bingo).
    return None


def place_bet(db: Session, user: User, game: Game, stake_etb: float, picked_numbers: list[int]) -> GameBet:
    if game.status != GameStatus.lobby.value:
        raise ValueError("Game is not in lobby")
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    if not lobby_pick_state(game, now)["pick_open"]:
        raise ValueError("Card selection time has ended")
    if float(stake_etb) < float(game.min_stake_etb):
        raise ValueError("Stake below min")

    # Deduct stake immediately (hold).
    if float(user.balance_etb) < float(stake_etb):
        raise ValueError("Insufficient balance")

    for n in picked_numbers:
        if not (game.board_min <= int(n) <= game.board_max):
            raise ValueError(f"Pick out of range: {n}")

    # Enforce limit: only one pick supported in this MVP.
    picked_numbers = [int(n) for n in picked_numbers][:1]
    if not picked_numbers:
        raise ValueError("No picked number")

    # Unique constraint prevents duplicate bets; update instead of failing for MVP.
    bet = db.execute(select(GameBet).where(GameBet.game_id == game.id).where(GameBet.user_id == user.id)).scalar_one_or_none()

    ticket = int(picked_numbers[0])
    if game.board_max > 75:
        card = generate_card_from_card_id(ticket)
    else:
        card = generate_card(ticket)
    initial_marks = [[2, 2]]  # FREE space
    card_payload = json.dumps(card)
    marks_payload = json.dumps(initial_marks)

    if bet:
        # Replace existing pick/stake: return old stake first (as play-only for simplicity).
        if bet.settled:
            raise ValueError("Bet already settled")
        credit_play_only(user, float(bet.stake_etb))
        if float(user.balance_etb) < float(stake_etb):
            raise ValueError("Insufficient balance")
        bet.stake_etb = float(stake_etb)
        bet.picked_numbers_json = json.dumps(picked_numbers)
        bet.card_json = card_payload
        bet.marked_json = marks_payload
        deduct_stake_from_wallet(user, float(stake_etb))
        db.flush()
        return bet

    deduct_stake_from_wallet(user, float(stake_etb))
    bet = GameBet(
        game_id=game.id,
        user_id=user.id,
        stake_etb=float(stake_etb),
        picked_numbers_json=json.dumps(picked_numbers),
        card_json=card_payload,
        marked_json=marks_payload,
        settled=0,
        win=0,
        payout_etb=0.0,
    )
    db.add(bet)
    db.flush()
    return bet


def release_lobby_bet(db: Session, user: User, game: Game) -> None:
    """Refund stake and remove the user's pick while the lobby is still open."""
    if game.status != GameStatus.lobby.value:
        raise ValueError("Can only release a pick during the lobby")
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    if not lobby_pick_state(game, now)["pick_open"]:
        raise ValueError("Card selection time has ended")
    bet = get_bet_for_user(db, game, user)
    if not bet:
        raise ValueError("No card to release")
    if bet.settled:
        raise ValueError("Bet already settled")
    credit_play_only(user, float(bet.stake_etb))
    db.delete(bet)
    db.flush()


def get_bet_for_user(db: Session, game: Game, user: User) -> GameBet | None:
    return db.execute(select(GameBet).where(GameBet.game_id == game.id).where(GameBet.user_id == user.id)).scalar_one_or_none()


def mark_cell(db: Session, game: Game, user: User, row: int, col: int) -> GameBet:
    if game.status != GameStatus.running.value:
        raise ValueError("Game is not running")
    if not (0 <= row <= 4 and 0 <= col <= 4):
        raise ValueError("Invalid cell")
    bet = get_bet_for_user(db, game, user)
    if not bet:
        raise ValueError("Join the lobby and place a bet first")
    if bet.settled:
        raise ValueError("Bet already settled")
    # After a false BINGO claim, the user is disqualified for the rest of the round.
    if int(getattr(bet, "bingo_claim_blocked", 0) or 0):
        raise ValueError("you made a mistake, Blocked for this game")
    card = json.loads(bet.card_json) if bet.card_json else []
    if not card or len(card) != 5:
        raise ValueError("No bingo card — place a bet in the lobby")
    val = int(card[row][col])
    marks = load_marks_json(bet.marked_json)
    if any(m[0] == row and m[1] == col for m in marks):
        db.flush()
        return bet
    marks.append([row, col])
    bet.marked_json = json.dumps(marks)
    db.flush()
    return bet


def unmark_cell(db: Session, game: Game, user: User, row: int, col: int) -> GameBet:
    if game.status != GameStatus.running.value:
        raise ValueError("Game is not running")
    if not (0 <= row <= 4 and 0 <= col <= 4):
        raise ValueError("Invalid cell")
    bet = get_bet_for_user(db, game, user)
    if not bet:
        raise ValueError("Join the lobby and place a bet first")
    if bet.settled:
        raise ValueError("Bet already settled")
    # After a false BINGO claim, the user is disqualified for the rest of the round.
    if int(getattr(bet, "bingo_claim_blocked", 0) or 0):
        raise ValueError("you made a mistake, Blocked for this game")
    card = json.loads(bet.card_json) if bet.card_json else []
    if not card or len(card) != 5:
        raise ValueError("No bingo card — place a bet in the lobby")
    if int(card[row][col]) == 0:
        raise ValueError("The free space cannot be unmarked")
    marks = load_marks_json(bet.marked_json)
    if not any(m[0] == row and m[1] == col for m in marks):
        db.flush()
        return bet
    marks = [m for m in marks if not (m[0] == row and m[1] == col)]
    bet.marked_json = json.dumps(marks)
    db.flush()
    return bet


def claim_bingo(db: Session, game: Game, user: User, now: datetime) -> tuple[GameBet, dict[str, float]]:
    if game.status != GameStatus.running.value:
        raise ValueError("Game is not running")
    if game.winner_telegram_user_id is not None:
        raise ValueError("This round already has a winner")
    bet = get_bet_for_user(db, game, user)
    if not bet:
        raise ValueError("No bet")
    if bet.settled:
        raise ValueError("Already settled")
    if int(bet.bingo_claim_blocked or 0):
        raise ValueError("you made a mistake, Blocked for this game")
    card = json.loads(bet.card_json) if bet.card_json else []
    if not card or len(card) != 5:
        raise ValueError("No bingo card")
    called = set(load_json_list(game.called_numbers_json))
    marks = load_marks_json(bet.marked_json)
    mset = marks_to_set(marks)
    for r in range(5):
        for c in range(5):
            if (r, c) not in mset:
                continue
            v = int(card[r][c])
            if v != 0 and v not in called:
                bet.bingo_claim_blocked = 1
                db.flush()
                raise ValueError("you made a mistake, Blocked for this game")
    if not has_complete_line(mset):
        bet.bingo_claim_blocked = 1
        db.flush()
        raise ValueError("you made a mistake, Blocked for this game")

    wl = winning_line_cells(mset)
    pattern_label = wl[0] if wl else "Bingo"
    line_cells = wl[1] if wl else set()

    gross_pool = float(
        db.execute(
            select(func.coalesce(func.sum(GameBet.stake_etb), 0.0)).where(GameBet.game_id == game.id)
        ).scalar_one()
    )
    rake_frac = max(0.0, min(1.0, float(settings.OWNER_RAKE_FRACTION)))
    house_rake_etb = round(gross_pool * rake_frac, 2)
    winner_payout = round(gross_pool - house_rake_etb, 2)
    if winner_payout < 0:
        winner_payout = 0.0

    bet.settled = 1
    bet.win = 1
    bet.settled_at = now
    bet.payout_etb = winner_payout
    winner_user = db.execute(select(User).where(User.id == bet.user_id)).scalar_one()
    credit_withdrawable(winner_user, winner_payout)

    if house_rake_etb > 1e-6:
        owner_tid = settings.owner_telegram_user_id()
        if owner_tid is None:
            raise ValueError(
                "House commission is enabled but no owner Telegram ID is configured. "
                "Set OWNER_TELEGRAM_USER_ID or ADMIN_TELEGRAM_IDS in .env."
            )
        owner_user = get_or_create_user(db, telegram_user_id=int(owner_tid), telegram_username=None)
        credit_withdrawable(owner_user, house_rake_etb)

    game.status = GameStatus.finished.value
    game.finished_at = now
    game.winner_telegram_user_id = int(user.telegram_user_id)
    game.winner_pattern_label = pattern_label
    game.winner_line_cells_json = json.dumps([[r, c] for r, c in sorted(line_cells)]) if line_cells else json.dumps([])
    game.winner_gross_pool_etb = gross_pool
    game.winner_house_rake_etb = house_rake_etb

    others = db.execute(
        select(GameBet).where(GameBet.game_id == game.id).where(GameBet.user_id != user.id).where(GameBet.settled == 0)
    ).scalars().all()
    for ob in others:
        ob.settled = 1
        ob.win = 0
        ob.settled_at = now

    spawn_next_lobby_after_round(db, game)
    db.flush()
    rake_meta = {"gross_pool_etb": gross_pool, "house_rake_etb": house_rake_etb}
    return bet, rake_meta


def load_json_list(text: str) -> list[int]:
    data = json.loads(text)
    if not isinstance(data, list):
        return []
    return [int(x) for x in data]


def load_marks_json(text: str) -> list[list[int]]:
    data = json.loads(text) if text else []
    if not isinstance(data, list):
        return []
    out: list[list[int]] = []
    for x in data:
        if isinstance(x, (list, tuple)) and len(x) == 2:
            out.append([int(x[0]), int(x[1])])
    return out


def finalize_game_losers(db: Session, game: Game, now: datetime) -> None:
    """Mark unsettled bets as lost when the round ends with no bingo winner."""
    if game.winner_telegram_user_id is not None:
        return
    bets = (
        db.execute(select(GameBet).where(GameBet.game_id == game.id).where(GameBet.settled == 0)).scalars().all()
    )
    for bet in bets:
        bet.settled = 1
        bet.win = 0
        bet.settled_at = now
    db.flush()


def advance_game_if_needed(db: Session, game: Game, now: datetime) -> Game:
    if game.status != GameStatus.running.value:
        return game

    if not game.last_advance_at:
        game.last_advance_at = _naive_utc(now)
        db.flush()
        return game

    gap = running_next_call_interval_sec(game)
    elapsed = (_naive_utc(now) - _naive_utc(game.last_advance_at)).total_seconds()
    if elapsed < gap:
        return game

    sequence = load_json_list(game.sequence_json)
    called = load_json_list(game.called_numbers_json)

    if game.next_index >= len(sequence):
        game.status = GameStatus.finished.value
        game.finished_at = now
        finalize_game_losers(db, game, now)
        spawn_next_lobby_after_round(db, game)
        db.flush()
        return game

    # Next call
    next_call = int(sequence[game.next_index])
    game.current_call = next_call
    called.append(next_call)
    game.called_numbers_json = json.dumps(called)
    game.next_index += 1
    game.last_advance_at = _naive_utc(now)

    # If we've used all numbers, end round — no automatic win; players must claim bingo before this.
    if game.next_index >= len(sequence):
        game.status = GameStatus.finished.value
        game.finished_at = now
        finalize_game_losers(db, game, now)
        spawn_next_lobby_after_round(db, game)

    db.flush()
    return game

