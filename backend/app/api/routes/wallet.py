from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from backend.app.api.deps import get_current_user


router = APIRouter(prefix="/wallet", tags=["wallet"])


class DepositRequest(BaseModel):
    # Admin-only; use /api/admin/deposit instead.
    amount_etb: float
    note: str | None = None


@router.get("/balance")
def get_balance(user=Depends(get_current_user)):
    return {
        "telegram_user_id": user.telegram_user_id,
        "balance_etb": float(user.balance_etb),
        "username": user.telegram_username,
    }

