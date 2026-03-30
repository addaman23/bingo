from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from backend.app.api.deps import get_db, get_current_user
from backend.app.core.config import settings
from backend.app.db.crud import deposit_amount


router = APIRouter(prefix="/admin", tags=["admin"])


class DepositAdminRequest(BaseModel):
    telegram_user_id: int
    amount_etb: float
    note: str | None = None
    telegram_username: str | None = None


@router.post("/deposit")
def admin_deposit(
    payload: DepositAdminRequest,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    if current_user.telegram_user_id not in settings.admin_ids():
        raise HTTPException(status_code=403, detail="Forbidden")
    user = deposit_amount(
        db=db,
        telegram_user_id=payload.telegram_user_id,
        telegram_username=payload.telegram_username,
        amount_etb=payload.amount_etb,
        note=payload.note,
    )
    db.flush()
    return {"ok": True, "telegram_user_id": user.telegram_user_id, "balance_etb": float(user.balance_etb)}

