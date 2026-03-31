import json
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from backend.app.api.deps import get_current_user, get_db
from backend.app.core.config import settings
from backend.app.db.crud import (
    advance_game_if_needed,
    claim_bingo,
    create_game,
    get_active_game_for_user as fetch_active_game_for_user,
    get_bet_for_user,
    load_marks_json,
    lobby_pick_deadline,
    lobby_pick_state,
    mark_cell,
    maybe_auto_start_lobby,
    place_bet,
    release_lobby_bet,
    running_next_call_interval_sec,
    unmark_cell,
)
from backend.app.db.models import Game, GameBet, GameStatus, User


router = APIRouter(prefix="/games", tags=["games"])


class CreateGameRequest(BaseModel):
    board_min: int = Field(default=1, ge=1, le=399)
    board_max: int = Field(default=75, ge=2, le=400)
    call_interval_sec: int = Field(default_factory=lambda: settings.DEFAULT_CALL_INTERVAL_SEC, ge=1, le=60)
    win_multiplier: float = Field(default_factory=lambda: settings.DEFAULT_WIN_MULTIPLIER, ge=0.1, le=1000)
    min_stake_etb: int = Field(default_factory=lambda: settings.DEFAULT_MIN_STAKE_ETB, ge=1, le=10_000)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _lock_game_row(db: Session, game_id: str) -> Game:
    """Serialize game state progression across concurrent clients."""
    return db.execute(select(Game).where(Game.id == game_id).with_for_update()).scalar_one()


class BetRequest(BaseModel):
    stake_etb: float
    pick_number: int


class StartGameRequest(BaseModel):
    pass


class MarkCellRequest(BaseModel):
    row: int = Field(ge=0, le=4)
    col: int = Field(ge=0, le=4)


class ClaimBingoRequest(BaseModel):
    pass


def _your_role(game: Game, user: Any, bet: GameBet | None) -> str:
    """Host vs player vs spectator.

    In the lobby, the Telegram host keeps role "host" (Start Calling, etc.).
    Once the round is running or finished, anyone with a bet is "player" so
    they can mark cells and BINGO — including the host if they bought a card.
    No bet while the round is live ⇒ "spectator" (e.g. missed the pick window).
    In lobby, joiners without a bet are still "player" so they can pick a card.
    """
    is_host = int(user.telegram_user_id) == int(game.host_telegram_user_id)
    if is_host and game.status == GameStatus.lobby.value:
        return "host"
    if bet:
        # After a false BINGO claim, the user is disqualified for the rest of the round.
        if game.status == GameStatus.running.value and int(getattr(bet, "bingo_claim_blocked", 0) or 0):
            return "spectator"
        return "player"
    if game.status == GameStatus.lobby.value:
        return "player"
    return "spectator"


def _other_ticket_picks(db: Session, game_id: str, exclude_user_id: int) -> list[int]:
    """Ticket numbers already chosen by other players (lobby)."""
    bets = db.execute(select(GameBet).where(GameBet.game_id == game_id).where(GameBet.user_id != exclude_user_id)).scalars().all()
    out: list[int] = []
    for b in bets:
        nums = json.loads(b.picked_numbers_json) if b.picked_numbers_json else []
        if nums:
            out.append(int(nums[0]))
    return out


def _build_winner_info(db: Session, g: Game) -> dict[str, Any] | None:
    """Snapshot of winner card/payout for a finished game (or None)."""
    if g.winner_telegram_user_id is None:
        return None
    called_g = json.loads(g.called_numbers_json) if g.called_numbers_json else []
    last_call = g.current_call
    if last_call is None and called_g:
        last_call = called_g[-1]
    wu = db.execute(select(User).where(User.telegram_user_id == g.winner_telegram_user_id)).scalar_one_or_none()
    win_bet = None
    if wu:
        win_bet = db.execute(
            select(GameBet).where(GameBet.game_id == g.id).where(GameBet.user_id == wu.id)
        ).scalar_one_or_none()
    win_card: list[Any] = []
    payout = 0.0
    win_line_cells: list[Any] = []
    board_number: int | None = None
    if win_bet:
        payout = float(win_bet.payout_etb)
        try:
            win_card = json.loads(win_bet.card_json) if win_bet.card_json else []
        except json.JSONDecodeError:
            win_card = []
        try:
            picks = json.loads(win_bet.picked_numbers_json) if win_bet.picked_numbers_json else []
            if picks:
                board_number = int(picks[0])
        except (json.JSONDecodeError, ValueError, TypeError, IndexError):
            board_number = None
        if getattr(g, "winner_line_cells_json", None):
            try:
                win_line_cells = json.loads(g.winner_line_cells_json)
            except json.JSONDecodeError:
                win_line_cells = []
    wg = getattr(g, "winner_gross_pool_etb", None)
    wh = getattr(g, "winner_house_rake_etb", None)
    return {
        "telegram_user_id": g.winner_telegram_user_id,
        "username": wu.telegram_username if wu else None,
        "payout_etb": payout,
        "gross_pool_etb": float(wg) if wg is not None else None,
        "house_rake_etb": float(wh) if wh is not None else None,
        "card": win_card,
        "last_call": last_call,
        "winning_pattern": getattr(g, "winner_pattern_label", None),
        "winning_line_cells": win_line_cells,
        "board_number": board_number,
    }


def game_to_state(db: Session, game: Game, user: Any, bet: GameBet | None) -> dict[str, Any]:
    # called numbers are stored as JSON list.
    called = json.loads(game.called_numbers_json) if game.called_numbers_json else []
    current_call = game.current_call

    taken_ticket_numbers = _other_ticket_picks(db, game.id, user.id)

    winner_info = _build_winner_info(db, game)

    previous_round: dict[str, Any] | None = None
    prid = getattr(game, "previous_round_game_id", None)
    if game.status == GameStatus.lobby.value and prid:
        prev = db.get(Game, prid)
        if prev and prev.winner_telegram_user_id is not None:
            pw = _build_winner_info(db, prev)
            if pw:
                prev_called = json.loads(prev.called_numbers_json) if prev.called_numbers_json else []
                previous_round = {
                    "source_game_id": prev.id,
                    "winner": pw,
                    "called_numbers": prev_called,
                }

    your_card = json.loads(bet.card_json) if bet and bet.card_json else []
    marked = load_marks_json(bet.marked_json) if bet else []

    now = _utc_now()
    row = db.execute(
        select(
            func.count(func.distinct(GameBet.user_id)),
            func.coalesce(func.sum(GameBet.stake_etb), 0.0),
        ).where(GameBet.game_id == game.id).where(GameBet.settled == 0)
    ).one()
    players_count = int(row[0])
    prize_pool_etb = float(row[1])
    rake_frac = max(0.0, min(1.0, float(settings.OWNER_RAKE_FRACTION)))
    net_prize_pool_etb = round(prize_pool_etb * (1.0 - rake_frac), 2)

    recent_calls = list(reversed(called[-5:])) if called else []

    seconds_until_next_ball: int | None = None
    if game.status == GameStatus.running.value and game.last_advance_at:
        elapsed = (now - game.last_advance_at).total_seconds()
        gap = float(running_next_call_interval_sec(game))
        rem = gap - elapsed
        seconds_until_next_ball = max(0, int(rem)) if rem > 0 else 0

    lobby = lobby_pick_state(game, now)
    if game.status == GameStatus.lobby.value:
        # Whole-second UTC — some Telegram WebViews fail Date.parse on microsecond ISO.
        du = lobby_pick_deadline(game).replace(tzinfo=timezone.utc).replace(microsecond=0)
        lobby = {**lobby, "pick_deadline_utc": du.strftime("%Y-%m-%dT%H:%M:%SZ")}

    return {
        "game_id": game.id,
        "status": game.status,
        "board": {"min": game.board_min, "max": game.board_max},
        "lobby": lobby,
        "lobby_pick_duration_sec": int(settings.LOBBY_PICK_DURATION_SEC),
        "call_interval_sec": game.call_interval_sec,
        "win_multiplier": float(game.win_multiplier),
        "min_stake_etb": int(game.min_stake_etb),
        "min_players_to_start": int(settings.MIN_PLAYERS_TO_START),
        "current_call": current_call,
        "called_numbers": called,
        "players_count": players_count,
        "prize_pool_etb": prize_pool_etb,
        "net_prize_pool_etb": net_prize_pool_etb,
        "call_count": len(called),
        "recent_calls": recent_calls,
        "seconds_until_next_ball": seconds_until_next_ball,
        "taken_ticket_numbers": taken_ticket_numbers,
        "host_telegram_user_id": game.host_telegram_user_id,
        "winner": winner_info,
        "previous_round": previous_round,
        "your_role": _your_role(game, user, bet),
        "your_bet": {
            "stake_etb": float(bet.stake_etb) if bet else 0.0,
            "picked_numbers": json.loads(bet.picked_numbers_json) if bet else [],
            "settled": bool(bet.settled) if bet else False,
            "win": bool(bet.win) if bet else False,
            "payout_etb": float(bet.payout_etb) if bet else 0.0,
            "your_card": your_card,
            "marked": marked,
            "bingo_claim_blocked": bool(getattr(bet, "bingo_claim_blocked", 0)) if bet else False,
        },
    }


@router.get("/active")
def get_active_game(db: Session = Depends(get_db), user=Depends(get_current_user)):
    now = _utc_now()
    game = fetch_active_game_for_user(db, now, user)
    if not game:
        game = create_game(
            db=db,
            host_telegram_user_id=user.telegram_user_id,
            board_min=1,
            board_max=settings.DEFAULT_LOBBY_CARD_MAX,
            call_interval_sec=settings.DEFAULT_CALL_INTERVAL_SEC,
            win_multiplier=settings.DEFAULT_WIN_MULTIPLIER,
            min_stake_etb=settings.DEFAULT_MIN_STAKE_ETB,
        )
        db.flush()
    else:
        game = _lock_game_row(db, game.id)

    bet = get_bet_for_user(db, game, user)
    if game.status == GameStatus.lobby.value:
        game = maybe_auto_start_lobby(db, game, now)
        bet = get_bet_for_user(db, game, user)
    # If running, settle/advance as needed
    if game.status == GameStatus.running.value:
        game = advance_game_if_needed(db, game, now)
        bet = get_bet_for_user(db, game, user)
    return game_to_state(db, game, user, bet)


@router.post("", status_code=201)
def create_new_game(payload: CreateGameRequest, db: Session = Depends(get_db), user=Depends(get_current_user)):
    game = create_game(
        db=db,
        host_telegram_user_id=user.telegram_user_id,
        board_min=payload.board_min,
        board_max=payload.board_max,
        call_interval_sec=payload.call_interval_sec,
        win_multiplier=payload.win_multiplier,
        min_stake_etb=payload.min_stake_etb,
    )
    db.flush()
    return {"game_id": game.id, "status": game.status}


@router.post("/{game_id}/bets")
def place_bet_endpoint(game_id: str, payload: BetRequest, db: Session = Depends(get_db), user=Depends(get_current_user)):
    game = db.get(Game, game_id)
    if not game:
        raise HTTPException(status_code=404, detail="Game not found")
    try:
        bet = place_bet(db=db, user=user, game=game, stake_etb=payload.stake_etb, picked_numbers=[payload.pick_number])
        db.flush()
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"ok": True, "your_bet": {"stake_etb": float(bet.stake_etb), "pick": json.loads(bet.picked_numbers_json)[0]}}


@router.post("/{game_id}/lobby/release-pick")
def release_lobby_pick(game_id: str, db: Session = Depends(get_db), user=Depends(get_current_user)):
    game = db.get(Game, game_id)
    if not game:
        raise HTTPException(status_code=404, detail="Game not found")
    try:
        release_lobby_bet(db=db, user=user, game=game)
        db.flush()
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"ok": True}


@router.post("/{game_id}/host/start")
def start_game(game_id: str, payload: StartGameRequest, db: Session = Depends(get_db), user=Depends(get_current_user)):
    """Deprecated: rounds start only when the lobby pick timer reaches zero."""
    raise HTTPException(
        status_code=400,
        detail="The round starts automatically when the pick timer reaches zero.",
    )


@router.get("/{game_id}")
def get_game_state(game_id: str, db: Session = Depends(get_db), user=Depends(get_current_user)):
    game = db.get(Game, game_id)
    if not game:
        raise HTTPException(status_code=404, detail="Game not found")
    game = _lock_game_row(db, game.id)

    bet = get_bet_for_user(db, game, user)
    now = _utc_now()
    if game.status == GameStatus.lobby.value:
        game = maybe_auto_start_lobby(db, game, now)
        bet = get_bet_for_user(db, game, user)
    if game.status == GameStatus.running.value:
        game = advance_game_if_needed(db, game, now)
        bet = get_bet_for_user(db, game, user)
    return game_to_state(db, game, user, bet)


@router.post("/{game_id}/mark")
def mark_cell_endpoint(game_id: str, payload: MarkCellRequest, db: Session = Depends(get_db), user=Depends(get_current_user)):
    game = db.get(Game, game_id)
    if not game:
        raise HTTPException(status_code=404, detail="Game not found")
    try:
        mark_cell(db=db, game=game, user=user, row=payload.row, col=payload.col)
        db.flush()
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    bet = get_bet_for_user(db, game, user)
    return {"ok": True, "marked": load_marks_json(bet.marked_json) if bet else []}


@router.post("/{game_id}/unmark")
def unmark_cell_endpoint(game_id: str, payload: MarkCellRequest, db: Session = Depends(get_db), user=Depends(get_current_user)):
    game = db.get(Game, game_id)
    if not game:
        raise HTTPException(status_code=404, detail="Game not found")
    try:
        unmark_cell(db=db, game=game, user=user, row=payload.row, col=payload.col)
        db.flush()
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    bet = get_bet_for_user(db, game, user)
    return {"ok": True, "marked": load_marks_json(bet.marked_json) if bet else []}


@router.post("/{game_id}/claim-bingo")
def claim_bingo_endpoint(game_id: str, payload: ClaimBingoRequest, db: Session = Depends(get_db), user=Depends(get_current_user)):
    game = db.get(Game, game_id)
    if not game:
        raise HTTPException(status_code=404, detail="Game not found")
    now = _utc_now()
    try:
        bet, rake_meta = claim_bingo(db=db, game=game, user=user, now=now)
        db.flush()
    except ValueError as e:
        # False BINGO sets bingo_claim_blocked + flush inside claim_bingo. Raising HTTPException
        # would make get_db rollback and drop that flag — user could keep playing and win.
        db.commit()
        raise HTTPException(status_code=400, detail=str(e))
    picks = json.loads(bet.picked_numbers_json) if bet.picked_numbers_json else []
    board_number = int(picks[0]) if picks else None
    called = json.loads(game.called_numbers_json) if game.called_numbers_json else []
    card = json.loads(bet.card_json) if bet.card_json else []
    win_cells: list[Any] = []
    if getattr(game, "winner_line_cells_json", None):
        try:
            win_cells = json.loads(game.winner_line_cells_json)
        except json.JSONDecodeError:
            win_cells = []
    return {
        "ok": True,
        "finished_game_id": game.id,
        "payout_etb": float(bet.payout_etb),
        "your_bet": {"win": True, "payout_etb": float(bet.payout_etb)},
        "winner_modal": {
            "payout_etb": float(bet.payout_etb),
            "gross_pool_etb": float(rake_meta.get("gross_pool_etb", 0)),
            "house_rake_etb": float(rake_meta.get("house_rake_etb", 0)),
            "card": card,
            "last_call": game.current_call,
            "winning_pattern": getattr(game, "winner_pattern_label", None),
            "winning_line_cells": win_cells,
            "board_number": board_number,
            "called_numbers": called,
        },
    }

