"""Parse pasted Ethio Telecom / Telebirr SMS or receipt text for auto-deposit."""

from __future__ import annotations

import re


def parse_telebirr_receipt_text(text: str) -> tuple[float, str] | None:
    """
    Extract (amount_etb, transaction_id) from common Telebirr confirmation wording.

    Example:
        You have transferred ETB 100.00 to ...
        Your transaction number is DCO57AMM7R.
        ... https://transactioninfo.ethiotelecom.et/receipt/DCO57AMM7R
    """
    if not text or len(text.strip()) < 25:
        return None
    t = text.replace("\r\n", "\n").strip()

    low = t.lower()
    markers = (
        "telebirr",
        "ethio telecom",
        "ethiotelecom",
        "transaction number",
        "e-money account",
    )
    if not any(m in low for m in markers):
        return None

    # Amount: "transferred ETB 100.00" / "send ETB 50" / "ETB 100.00 to name"
    amount = None
    m_amt = re.search(
        r"(?:transferred|send|sent)\s+ETB\s*([\d,]+\.?\d*)",
        t,
        re.IGNORECASE,
    )
    if m_amt:
        amount = _parse_amount(m_amt.group(1))
    if amount is None:
        m_amt = re.search(r"ETB\s*([\d,]+\.?\d*)\s+to\s+", t, re.IGNORECASE)
        if m_amt:
            amount = _parse_amount(m_amt.group(1))
    if amount is None or amount <= 0 or amount > 1_000_000:
        return None

    txn_id = None
    m_txn = re.search(r"transaction\s+number\s+is\s+([A-Za-z0-9]+)", t, re.IGNORECASE)
    if m_txn:
        txn_id = m_txn.group(1).strip().upper()
    if not txn_id:
        m_txn = re.search(r"/receipt/([A-Za-z0-9]+)", t, re.IGNORECASE)
        if m_txn:
            txn_id = m_txn.group(1).strip().upper()
    if not txn_id or len(txn_id) < 6:
        return None

    return (amount, txn_id)


def _parse_amount(s: str) -> float | None:
    try:
        return float(s.replace(",", "").strip())
    except ValueError:
        return None
