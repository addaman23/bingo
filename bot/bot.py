import asyncio
import logging
import sys
from pathlib import Path

# Repo root must be on path when running `python bot/bot.py`
_root = Path(__file__).resolve().parent.parent
if str(_root) not in sys.path:
    sys.path.insert(0, str(_root))

from dotenv import load_dotenv
from sqlalchemy import select
from telegram import BotCommand, InlineKeyboardButton, InlineKeyboardMarkup, MenuButtonDefault, Update, WebAppInfo
from telegram.ext import Application, CallbackQueryHandler, CommandHandler, ContextTypes, MessageHandler, filters

from backend.app.core.config import settings
from backend.app.db.crud import (
    DuplicateTelebirrTxnError,
    admin_complete_withdrawal,
    admin_reverse_deposit,
    admin_reject_withdrawal,
    admin_set_withdrawal_processing,
    approve_pending_telebirr_deposit,
    create_withdrawal_request,
    deposit_amount,
    deposit_from_telebirr_paste,
    find_users_by_telebirr_phone_key,
    find_withdrawal_by_short_id,
    get_or_create_user,
    get_user_by_telegram_username,
    list_pending_telebirr_deposits,
    looks_like_phone_query,
    telebirr_phone_key_normalize,
    list_recent_deposits,
    list_withdrawal_requests,
    reject_pending_telebirr_deposit,
    withdrawal_request_short_id,
)
from backend.app.db.models import User
from backend.app.telebirr_receipt import parse_telebirr_receipt_text
from backend.app.db.init_db import init_db
from backend.app.db.session import SessionLocal


logging.basicConfig(level=logging.INFO)
log = logging.getLogger("ethio-bingo-bot")

TELEBIRR_INSTRUCTIONS = """Telebirr አካውንት

Account Number:
0988013094

Account Name:
Addisu

መመሪያ
1️⃣ ከላይ ባለው የ Telebirr አካውንት ገንዘቡን ያስገቡ
2️⃣ ብሩን ስትልኩ የከፈላችሁበትን መረጃ የያዝ አጭር የጹሁፍ መልክት(sms) ከ Telebirr ይደርሳችኋል
3️⃣ የደረሳችሁን አጭር የጹሁፍ መለክት(sms) ሙሉዉን ኮፒ(copy) በማድረግ ከታች ባለው የቴሌግራም የጹሁፍ ማስገቢአው ላይ ፔስት(paste) በማድረግ ይላኩት

⚠️ Do not use ✏️ Edit on a pasted receipt — send the SMS again as a new message.

የሚያጋጥማቹ የክፍያ ችግር ካለ @Addisu Abebaw በዚ ሳፖርት ማዉራት ይችላሉ"""


def _get_db():
    return SessionLocal()


def _is_admin(telegram_user_id: int) -> bool:
    return telegram_user_id in settings.admin_ids()


def _not_admin_help_text() -> str:
    if not settings.admin_ids():
        return (
            "No admins are configured on the server (ADMIN_TELEGRAM_IDS is empty).\n\n"
            "Send /myid to see your numeric Telegram user id, add it to the project .env as:\n"
            "ADMIN_TELEGRAM_IDS=\"your_id\"\n"
            "then restart the bot process."
        )
    return (
        "Your Telegram account is not listed as an admin.\n\n"
        "Send /myid to see your numeric user id, add it to ADMIN_TELEGRAM_IDS in .env "
        "(comma-separated for multiple admins), then restart the bot."
    )


async def _reply_not_admin(update: Update) -> None:
    if update.message:
        await update.message.reply_text(_not_admin_help_text())
    elif update.callback_query:
        await update.callback_query.answer(
            "Not an admin — set ADMIN_TELEGRAM_IDS in .env and restart the bot.",
            show_alert=True,
        )


def _fmt_etb(x: float) -> str:
    v = float(x)
    return f"{int(v)}" if abs(v - int(v)) < 1e-6 else f"{v:.2f}"


def _fmt_user_ref(telegram_user_id: int | str, telegram_username: str | None) -> str:
    uname = f"@{telegram_username}" if telegram_username else "username:unknown"
    return f"tg:{telegram_user_id} · {uname}"


def _strip_addbalance_phone_prefix(raw: str) -> tuple[str, bool]:
    t = raw.strip()
    low = t.lower()
    for p in ("phone:", "tel:", "p:"):
        if low.startswith(p):
            return t[len(p) :].strip(), True
    return t, False


def _clear_withdraw_flow(context: ContextTypes.DEFAULT_TYPE) -> None:
    context.user_data.pop("withdraw_flow", None)
    context.user_data.pop("withdraw_step", None)
    for k in ("withdraw_amount", "withdraw_bank", "withdraw_account"):
        context.user_data.pop(k, None)


async def _ensure_user(update: Update) -> tuple[int, str | None]:
    u = update.effective_user
    assert u is not None
    return int(u.id), u.username


async def post_init(application: Application) -> None:
    await application.bot.set_my_commands(
        [
            BotCommand("start", "Start the bot"),
            BotCommand("register", "Register account"),
            BotCommand("play", "Open game"),
            BotCommand("balance", "Check balance"),
            BotCommand("myid", "Your Telegram user id"),
            BotCommand("deposit", "Deposit info"),
            BotCommand("withdraw", "Withdraw funds"),
            BotCommand("transfer", "Transfer balance"),
            BotCommand("invite", "Referral link"),
            BotCommand("instructions", "Instructions"),
            BotCommand("cancel", "Cancel process"),
        ]
    )
    log.info("Bot commands menu registered.")
    try:
        # Keep Telegram's standard Menu button so users can access bot commands.
        await application.bot.set_chat_menu_button(menu_button=MenuButtonDefault())
        log.info("Default Telegram menu button restored.")
    except Exception as e:
        log.warning("set_chat_menu_button default failed: %s", e)


def _webapp_keyboard(payload: str = "") -> InlineKeyboardMarkup:
    webapp_url = (
        settings.WEBAPP_URL.rstrip("/") + f"/?start={payload}"
        if payload
        else settings.WEBAPP_URL.rstrip("/") + "/"
    )
    return InlineKeyboardMarkup([[InlineKeyboardButton(text="Play ETHIO BINGO", web_app=WebAppInfo(url=webapp_url))]])


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message:
        return
    init_args = context.args or []
    payload = init_args[0] if init_args else ""

    telegram_user = update.effective_user
    assert telegram_user is not None
    telegram_user_id = int(telegram_user.id)
    telegram_username = telegram_user.username

    db = _get_db()
    try:
        init_db()
        user = get_or_create_user(db, telegram_user_id=telegram_user_id, telegram_username=telegram_username)
        db.commit()
        balance = float(user.balance_etb)
        msg = f"Welcome, {telegram_user.first_name or 'player'}!\nYour balance: {balance:.0f} ETB\n\nTap Play ETHIO BINGO to start."
        await update.message.reply_text(msg, reply_markup=_webapp_keyboard(payload))
    finally:
        db.close()


async def register_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message:
        return
    telegram_user = update.effective_user
    assert telegram_user is not None
    db = _get_db()
    try:
        init_db()
        user = get_or_create_user(db, telegram_user_id=int(telegram_user.id), telegram_username=telegram_user.username)
        db.commit()
        await update.message.reply_text(
            f"You're registered, {telegram_user.first_name or 'player'}.\n"
            f"Balance: {float(user.balance_etb):.0f} ETB\n\n"
            "Use /play to open ETHIO BINGO or /deposit for top-up instructions."
        )
    finally:
        db.close()


async def play_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message:
        return
    init_args = context.args or []
    payload = init_args[0] if init_args else ""
    telegram_user = update.effective_user
    assert telegram_user is not None
    db = _get_db()
    try:
        init_db()
        get_or_create_user(db, telegram_user_id=int(telegram_user.id), telegram_username=telegram_user.username)
        db.commit()
    finally:
        db.close()
    await update.message.reply_text("Open ETHIO BINGO:", reply_markup=_webapp_keyboard(payload))


async def myid_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show numeric Telegram user id (for ADMIN_TELEGRAM_IDS setup)."""
    if not update.message:
        return
    u = update.effective_user
    assert u is not None
    uname = f"@{u.username}" if u.username else "(no username)"
    await update.message.reply_text(
        f"Your Telegram user id: {u.id}\n{uname}\n\n"
        "Use this number in ADMIN_TELEGRAM_IDS in the project .env if you run the bot server."
    )


async def balance_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message:
        return
    tid, tun = await _ensure_user(update)
    db = _get_db()
    try:
        init_db()
        user = get_or_create_user(db, telegram_user_id=tid, telegram_username=tun)
        db.commit()
        total = float(user.balance_etb)
        won = float(user.withdrawable_balance_etb)
        play = float(user.play_only_balance_etb)
        await update.message.reply_text(
            f"Your total balance: {_fmt_etb(total)} ETB\n"
            f"🏆 Withdrawable (winnings): {_fmt_etb(won)} ETB\n"
            f"💵 Play-only (deposits): {_fmt_etb(play)} ETB"
        )
    finally:
        db.close()


async def deposit_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """User-facing deposit: choose bank → Telebirr details."""
    if not update.message:
        return
    keyboard = InlineKeyboardMarkup([[InlineKeyboardButton("Telebirr", callback_data="deposit_telebirr")]])
    await update.message.reply_text(
        "Please select the bank option you wish to use for the top-up.",
        reply_markup=keyboard,
    )


async def deposit_telebirr_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    if not q or not q.message:
        return
    await q.answer()
    if q.data != "deposit_telebirr":
        return
    await q.message.reply_text(TELEBIRR_INSTRUCTIONS)


async def telebirr_paste_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Credit balance when user pastes Ethio Telecom / Telebirr confirmation text."""
    if update.edited_message is not None:
        em = update.edited_message
        if not em.text or context.user_data.get("withdraw_flow"):
            return
        text = em.text.strip()
        parsed = parse_telebirr_receipt_text(text)
        looks_like_receipt = parsed is not None or (
            len(text) > 80
            and "etb" in text.lower()
            and ("telebirr" in text.lower() or "ethio telecom" in text.lower() or "transaction number" in text.lower())
        )
        if looks_like_receipt:
            await em.reply_text(
                "We do not accept edited Telebirr messages.\n\n"
                "Copy the SMS from Telebirr again and send it as a **new message** (tap in the message box, paste, send — do not use ✏️ Edit)."
            )
        return

    msg = update.message
    if not msg or not msg.text:
        return
    text = msg.text.strip()
    parsed = parse_telebirr_receipt_text(text)
    if not parsed:
        if len(text) > 100 and "etb" in text.lower() and (
            "telebirr" in text.lower() or "ethio telecom" in text.lower() or "transaction" in text.lower()
        ):
            await msg.reply_text(
                "We could not read the amount or transaction number from that message.\n\n"
                "Please paste the full Telebirr SMS or receipt, including:\n"
                "• the line with \"transferred ETB …\"\n"
                "• \"Your transaction number is …\"\n\n"
                "Then send it again in this chat (not as a command)."
            )
        return

    amount_etb, txn_id = parsed
    tid, tun = await _ensure_user(update)
    db = _get_db()
    try:
        init_db()
        user = deposit_from_telebirr_paste(
            db,
            telegram_user_id=tid,
            telegram_username=tun,
            amount_etb=amount_etb,
            telebirr_txn_id=txn_id,
            raw_excerpt=text,
        )
        db.commit()
        bal = float(user.balance_etb)
        amt_txt = f"{int(amount_etb)}" if float(amount_etb).is_integer() else f"{float(amount_etb):.2f}"
        bal_txt = f"{int(bal)}" if bal == int(bal) else f"{bal:.2f}"
        await msg.reply_text(
            "✅ Deposit Verified!\n\n"
            f"💰 Amount: {amt_txt} ETB\n"
            f"💳 Your new balance: {bal_txt} ETB\n\n"
            "Thank you for your deposit! You can start playing now."
        )
    except DuplicateTelebirrTxnError:
        db.rollback()
        await msg.reply_text(
            "This Telebirr transaction was already used to credit a balance.\n\n"
            "If you sent a new payment, paste the new receipt. If you think this is wrong, contact support: @Addisu Abebaw"
        )
    except ValueError as e:
        db.rollback()
        await msg.reply_text(str(e))
    except Exception as e:
        db.rollback()
        log.exception("telebirr paste failed")
        await msg.reply_text(f"Could not apply deposit: {e}")
    finally:
        db.close()


async def withdraw_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message:
        return
    tid, tun = await _ensure_user(update)
    db = _get_db()
    try:
        init_db()
        user = get_or_create_user(db, telegram_user_id=tid, telegram_username=tun)
        db.commit()
        won = float(user.withdrawable_balance_etb)
        play = float(user.play_only_balance_etb)
        min_w = float(settings.MIN_WITHDRAWAL_ETB)
    finally:
        db.close()

    _clear_withdraw_flow(context)
    context.user_data["withdraw_flow"] = True
    context.user_data["withdraw_step"] = "amount"
    await update.message.reply_text(
        "💸 Withdraw Funds — ETHIO BINGO\n\n"
        f"🏆 Won balance: {_fmt_etb(won)} ETB\n"
        f"💵 Deposited balance: {_fmt_etb(play)} ETB (play only)\n\n"
        f"✅ Available for withdrawal: {_fmt_etb(won)} ETB\n\n"
        f"Minimum withdrawal: {_fmt_etb(min_w)} ETB\n\n"
        "💡 How much would you like to withdraw?\n\n"
        "Please enter the amount in ETB:\n\n"
        "Example: 100"
    )


async def withdraw_bank_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    if not q or not q.message:
        return
    await q.answer()
    if q.data != "withdraw_bank_telebirr":
        return
    if not context.user_data.get("withdraw_flow") or context.user_data.get("withdraw_step") != "wait_bank":
        await q.message.reply_text("Start again with /withdraw.")
        return
    context.user_data["withdraw_bank"] = "Telebirr"
    context.user_data["withdraw_step"] = "account"
    await q.message.reply_text(
        "✅ Bank: Telebirr\n\n"
        "📱 Please enter your account number:\n\n"
        "Example: 0912345678"
    )


async def withdraw_on_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    if not msg or not msg.text:
        return
    step = context.user_data.get("withdraw_step")
    text = msg.text.strip()

    if step == "amount":
        raw = text.replace(",", "").strip()
        try:
            amount = float(raw)
        except ValueError:
            await msg.reply_text("Please send a number only, e.g. 100")
            return
        if amount <= 0:
            await msg.reply_text("Please enter a positive amount.")
            return
        tid, tun = await _ensure_user(update)
        db = _get_db()
        try:
            init_db()
            user = get_or_create_user(db, telegram_user_id=tid, telegram_username=tun)
            db.commit()
            won = float(user.withdrawable_balance_etb)
            min_w = float(settings.MIN_WITHDRAWAL_ETB)
        finally:
            db.close()
        if amount + 1e-6 < min_w:
            await msg.reply_text(f"Minimum withdrawal is {_fmt_etb(min_w)} ETB. Try again or /cancel.")
            return
        if amount > won + 1e-6:
            await msg.reply_text(
                f"You can withdraw at most {_fmt_etb(won)} ETB (winnings only). Enter a lower amount or /cancel."
            )
            return
        context.user_data["withdraw_amount"] = amount
        context.user_data["withdraw_step"] = "wait_bank"
        keyboard = InlineKeyboardMarkup([[InlineKeyboardButton("Telebirr", callback_data="withdraw_bank_telebirr")]])
        await msg.reply_text(
            f"✅ Amount: {_fmt_etb(amount)} ETB\n\n🏦 Please select your bank:",
            reply_markup=keyboard,
        )
        return

    if step == "account":
        acct = "".join(c for c in text if c.isdigit() or c == "+")
        if len(acct.replace("+", "")) < 9:
            await msg.reply_text("That does not look like a valid phone/account number. Try again or /cancel.")
            return
        context.user_data["withdraw_account"] = text.strip()[:32]
        context.user_data["withdraw_step"] = "name"
        await msg.reply_text(
            "✅ Account saved.\n\n"
            "👤 Please enter the account holder name:\n\n"
            "Example: Abebe Kebede"
        )
        return

    if step == "name":
        name = text.strip()
        if len(name) < 3:
            await msg.reply_text("Please enter the full name (at least 3 characters) or /cancel.")
            return
        tid, tun = await _ensure_user(update)
        amount = float(context.user_data.get("withdraw_amount") or 0)
        bank = str(context.user_data.get("withdraw_bank") or "Telebirr")
        account = str(context.user_data.get("withdraw_account") or "")
        db = _get_db()
        short = ""
        won_after = 0.0
        total_after = 0.0
        try:
            init_db()
            user = get_or_create_user(db, telegram_user_id=tid, telegram_username=tun)
            wr = create_withdrawal_request(
                db,
                user=user,
                amount_etb=amount,
                bank=bank,
                account_number=account,
                account_name=name[:128],
            )
            db.commit()
            short = withdrawal_request_short_id(wr)
            won_after = float(user.withdrawable_balance_etb)
            total_after = float(user.balance_etb)
        except ValueError as e:
            db.rollback()
            await msg.reply_text(str(e))
            return
        except Exception as e:
            db.rollback()
            log.exception("withdraw create failed")
            await msg.reply_text(f"Could not submit: {e}")
            return
        finally:
            db.close()

        _clear_withdraw_flow(context)
        await msg.reply_text(
            "✅ Withdrawal Request Submitted!\n\n"
            f"💰 Amount: {_fmt_etb(amount)} ETB\n"
            f"🏦 Bank: {bank}\n"
            f"📱 Account: {account}\n"
            f"👤 Name: {name}\n\n"
            "⏳ Your request is now pending review. Admin will process it manually and transfer the money to your account.\n\n"
            f"📝 Request ID: {short}\n\n"
            "You'll receive a notification once the withdrawal is processed.\n\n"
            "Processing time: Usually within 24 hours."
        )
        await msg.reply_text(
            "Your current balances after reserving this withdrawal:\n"
            f"🏆 Won balance: {_fmt_etb(won_after)} ETB\n"
            f"💳 Total: {_fmt_etb(total_after)} ETB"
        )
        return

    if step == "wait_bank":
        await msg.reply_text("Please tap the Telebirr button on the message above (bank selection).")
        return

    await msg.reply_text("Use /withdraw to start again.")


async def plain_text_router(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if context.user_data.get("withdraw_flow"):
        await withdraw_on_text(update, context)
        return
    await telebirr_paste_handler(update, context)


async def transfer_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message:
        return
    await update.message.reply_text(
        "In-chat transfers are not available yet.\n"
        "For help, contact support: @Addisu Abebaw"
    )


async def invite_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message:
        return
    me = await context.bot.get_me()
    if not me.username:
        await update.message.reply_text("Invite link is not configured (bot has no username).")
        return
    u = update.effective_user
    assert u is not None
    link = f"https://t.me/{me.username}?start=ref_{u.id}"
    await update.message.reply_text(f"Share ETHIO BINGO with friends:\n{link}")


async def instructions_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message:
        return
    await update.message.reply_text(
        "ETHIO BINGO — quick guide\n\n"
        "• /play — Open the game in Telegram\n"
        "• Pick a card number in the lobby — stake is set for the round; the game starts when the pick timer hits zero\n"
        "• When the round starts, mark numbers on your card as they are called\n"
        "• Tap BINGO! when you complete a valid line\n\n"
        "• /deposit — Telebirr top-up instructions\n"
        "• /withdraw — Cash out winnings (manual review)\n"
        "• /balance — Check your ETB balance"
    )


async def cancel_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message:
        return
    if context.user_data.get("withdraw_flow"):
        _clear_withdraw_flow(context)
        await update.message.reply_text("Withdrawal cancelled. You can start again with /withdraw.")
        return
    await update.message.reply_text("Nothing to cancel. You can start again with /play or /deposit.")


async def withdraw_pending_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message:
        return
    if not _is_admin(int(update.effective_user.id)):
        await _reply_not_admin(update)
        return
    db = _get_db()
    try:
        init_db()
        rows = list_withdrawal_requests(db, status="pending")
        if not rows:
            await update.message.reply_text("No pending withdrawals.")
            return
        blocks = []
        for wr in rows[:25]:
            u = db.get(User, wr.user_id)
            tid = u.telegram_user_id if u else "?"
            short = withdrawal_request_short_id(wr)
            acct = (wr.account_number or "").strip()
            name = (wr.account_name or "").strip()
            phone_line = f"📱 {wr.bank}: {acct}" if acct else f"📱 {wr.bank}: (no number on file — check DB or user chat)"
            blocks.append(
                f"📝 {short} · {_fmt_etb(wr.amount_etb)} ETB\n"
                f"{phone_line}\n"
                f"👤 {name}\n"
                f"🆔 Telegram user id: {tid}"
            )
        await update.message.reply_text("Pending withdrawals:\n\n" + "\n\n".join(blocks))
    finally:
        db.close()


async def withdraw_processing_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message:
        return
    if not _is_admin(int(update.effective_user.id)):
        await _reply_not_admin(update)
        return
    if not context.args:
        await update.message.reply_text("Usage: /withdraw_processing REQUEST_ID\nExample: /withdraw_processing a62b650f")
        return
    token = context.args[0]
    db = _get_db()
    try:
        init_db()
        wr = find_withdrawal_by_short_id(db, token)
        if not wr:
            await update.message.reply_text("Request not found.")
            return
        admin_set_withdrawal_processing(db, wr.id)
        db.commit()
        u = db.get(User, wr.user_id)
        if u:
            try:
                await context.bot.send_message(
                    chat_id=u.telegram_user_id,
                    text=(
                        f"Processing your withdrawal request of {_fmt_etb(wr.amount_etb)} ETB.\n\n"
                        f"The money will be transferred to your {wr.bank} account shortly.\n\n"
                        f"Request ID: {withdrawal_request_short_id(wr)}"
                    ),
                )
            except Exception as ex:
                log.warning("Could not notify user %s: %s", u.telegram_user_id, ex)
        await update.message.reply_text(f"Marked processing: {withdrawal_request_short_id(wr)}")
    except ValueError as e:
        db.rollback()
        await update.message.reply_text(str(e))
    finally:
        db.close()


async def withdraw_complete_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message:
        return
    if not _is_admin(int(update.effective_user.id)):
        await _reply_not_admin(update)
        return
    if not context.args:
        await update.message.reply_text("Usage: /withdraw_complete REQUEST_ID\nExample: /withdraw_complete a62b650f\n(Use a space — not angle brackets around the id.)")
        return
    token = context.args[0]
    db = _get_db()
    try:
        init_db()
        wr = find_withdrawal_by_short_id(db, token)
        if not wr:
            await update.message.reply_text("Request not found.")
            return
        admin_complete_withdrawal(db, wr.id)
        db.commit()
        u = db.execute(select(User).where(User.id == wr.user_id)).scalar_one()
        won = float(u.withdrawable_balance_etb)
        total = float(u.balance_etb)
        short = withdrawal_request_short_id(wr)
        try:
            await context.bot.send_message(
                chat_id=u.telegram_user_id,
                text=(
                    "Withdrawal Completed!\n\n"
                    f"Amount: {_fmt_etb(wr.amount_etb)} ETB\n"
                    f"Bank: {wr.bank}\n"
                    f"Account: {wr.account_number}\n\n"
                    "New Balances:\n"
                    f"Won balance: {_fmt_etb(won)} ETB\n"
                    f"Total balance: {_fmt_etb(total)} ETB\n\n"
                    "The money has been transferred to your account. Thank you!\n\n"
                    f"Request ID: {short}"
                ),
            )
        except Exception as ex:
            log.warning("Could not notify user: %s", ex)
        await update.message.reply_text(f"Completed {short}. User notified.")
    except ValueError as e:
        db.rollback()
        await update.message.reply_text(str(e))
    finally:
        db.close()


async def withdraw_reject_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message:
        return
    if not _is_admin(int(update.effective_user.id)):
        await _reply_not_admin(update)
        return
    if not context.args:
        await update.message.reply_text("Usage: /withdraw_reject REQUEST_ID\nExample: /withdraw_reject a62b650f")
        return
    token = context.args[0]
    db = _get_db()
    try:
        init_db()
        wr = find_withdrawal_by_short_id(db, token)
        if not wr:
            await update.message.reply_text("Request not found.")
            return
        wr, user = admin_reject_withdrawal(db, wr.id)
        db.commit()
        short = withdrawal_request_short_id(wr)
        try:
            await context.bot.send_message(
                chat_id=user.telegram_user_id,
                text=(
                    f"Your withdrawal request {short} was rejected. "
                    f"{_fmt_etb(wr.amount_etb)} ETB has been returned to your withdrawable (won) balance.\n\n"
                    "Contact support if you need help: @Addisu Abebaw"
                ),
            )
        except Exception as ex:
            log.warning("Could not notify user: %s", ex)
        await update.message.reply_text(f"Rejected {short}. User refunded.")
    except ValueError as e:
        db.rollback()
        await update.message.reply_text(str(e))
    finally:
        db.close()


async def deposit_pending_review_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    if not q or not q.data:
        return
    if not (q.data.startswith("depapp_") or q.data.startswith("deprej_")):
        return
    try:
        pending_id = int(q.data.split("_", 1)[1])
    except (IndexError, ValueError):
        return
    if not _is_admin(int(q.from_user.id)):
        await _reply_not_admin(update)
        return
    await q.answer()
    db = _get_db()
    try:
        init_db()
        if q.data.startswith("depapp_"):
            user, amount, tid = approve_pending_telebirr_deposit(db, pending_id)
            db.commit()
            bal = float(user.balance_etb)
            bal_txt = f"{int(bal)}" if bal == int(bal) else f"{bal:.2f}"
            await q.edit_message_text(
                f"✅ Approved pending #{pending_id}\n"
                f"{_fmt_etb(amount)} ETB · {_fmt_user_ref(int(user.telegram_user_id), user.telegram_username)} · {tid}\n"
                f"User balance now {bal_txt} ETB."
            )
            try:
                await context.bot.send_message(
                    chat_id=user.telegram_user_id,
                    text=(
                        f"✅ Your deposit of {_fmt_etb(amount)} ETB was approved.\n"
                        f"💳 New balance: {bal_txt} ETB"
                    ),
                )
            except Exception as ex:
                log.warning("approve deposit user notify: %s", ex)
        else:
            tg, amount, tid = reject_pending_telebirr_deposit(db, pending_id)
            db.commit()
            await q.edit_message_text(
                f"❌ Rejected pending #{pending_id}\n{_fmt_etb(amount)} ETB · {tid}"
            )
            if tg:
                try:
                    await context.bot.send_message(
                        chat_id=tg,
                        text=(
                            "Your Telebirr deposit request was not approved.\n\n"
                            "If you already paid, contact support with proof. "
                            "Do not edit SMS text — send the original message as a new message."
                        ),
                    )
                except Exception as ex:
                    log.warning("reject deposit user notify: %s", ex)
    except DuplicateTelebirrTxnError:
        db.rollback()
        await q.edit_message_text(
            "Could not approve: this transaction id is already credited. Reject this request or use /reverse_deposit."
        )
    except ValueError as e:
        db.rollback()
        await q.edit_message_text(str(e))
    except Exception as e:
        db.rollback()
        log.exception("deposit pending review callback")
        await q.edit_message_text(f"Error: {e}")
    finally:
        db.close()


async def deposit_pending_list_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message:
        return
    if not _is_admin(int(update.effective_user.id)):
        await _reply_not_admin(update)
        return
    db = _get_db()
    try:
        init_db()
        rows = list_pending_telebirr_deposits(db, limit=25)
        if not rows:
            await update.message.reply_text("No pending Telebirr deposit reviews.")
            return
        lines = []
        for p in rows:
            u = db.get(User, p.user_id)
            tid = u.telegram_user_id if u else "?"
            tun = u.telegram_username if u else None
            lines.append(f"#{p.id} · {_fmt_user_ref(tid, tun)} · {_fmt_etb(p.amount_etb)} ETB · {p.telebirr_txn_id}")
        await update.message.reply_text("Pending Telebirr deposits:\n\n" + "\n".join(lines))
    finally:
        db.close()


async def deposits_recent_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Admin: list recent Telebirr auto-deposits (audit)."""
    if not update.message:
        return
    if not _is_admin(int(update.effective_user.id)):
        await _reply_not_admin(update)
        return
    db = _get_db()
    try:
        init_db()
        rows = list_recent_deposits(db, limit=18, telebirr_only=True)
        if not rows:
            await update.message.reply_text("No Telebirr auto-deposits in the database yet.")
            return
        lines = []
        for d in rows:
            u = db.get(User, d.user_id)
            tid = u.telegram_user_id if u else "?"
            txn = (d.telebirr_txn_id or "").strip()
            rev = " · REVERSED" if d.reversed_at else ""
            lines.append(f"id {d.id} · tg {tid} · {_fmt_etb(d.amount_etb)} ETB · {txn}{rev}")
        await update.message.reply_text("Recent Telebirr deposits (newest first):\n\n" + "\n".join(lines))
    finally:
        db.close()


async def reverse_deposit_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Admin: claw back a deposit by internal id (from /deposits_recent)."""
    if not update.message:
        return
    if not _is_admin(int(update.effective_user.id)):
        await _reply_not_admin(update)
        return
    if not context.args:
        await update.message.reply_text("Usage: /reverse_deposit DEPOSIT_ID\nUse /deposits_recent to see ids.")
        return
    try:
        dep_id = int(context.args[0])
    except ValueError:
        await update.message.reply_text("Deposit id must be a number (e.g. /reverse_deposit 42).")
        return
    db = _get_db()
    try:
        init_db()
        dep, user = admin_reverse_deposit(db, dep_id)
        db.commit()
        short_txn = (dep.telebirr_txn_id or "")[:16]
        await update.message.reply_text(
            f"Reversed deposit #{dep.id} ({_fmt_etb(dep.amount_etb)} ETB, txn {short_txn}). "
            f"User tg:{user.telegram_user_id} balance now {_fmt_etb(user.balance_etb)} ETB."
        )
        try:
            await context.bot.send_message(
                chat_id=user.telegram_user_id,
                text=(
                    f"A deposit of {_fmt_etb(dep.amount_etb)} ETB was reversed by an admin "
                    "(invalid or duplicate Telebirr credit). If you believe this is a mistake, contact support."
                ),
            )
        except Exception as ex:
            log.warning("Could not notify user after reverse: %s", ex)
    except ValueError as e:
        db.rollback()
        await update.message.reply_text(str(e))
    finally:
        db.close()


async def addbalance_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Admin: credit play-only balance by Telegram user id or @username (no SMS / pending flow)."""
    if not update.message:
        return
    telegram_user = update.effective_user
    assert telegram_user is not None
    if not _is_admin(int(telegram_user.id)):
        await _reply_not_admin(update)
        return

    args = context.args or []
    if len(args) < 2:
        await update.message.reply_text(
            "Usage:\n"
            "• /addbalance <telegram_user_id> <amount_etb> [note]\n"
            "• /addbalance @username <amount_etb> [note]\n"
            "• /addbalance <phone> <amount_etb> [note] — e.g. 0988013094, +251988013094, phone:0988013094\n"
            "Same as /admin_deposit.\n\n"
            "Phone matching uses the Telebirr number from their last /withdraw request (stored on file).\n"
            "Pure digits that look like a phone are matched by phone first; if nobody is linked, the same digits are tried as a Telegram user id.\n"
            "@username only works if they already used /start or /register once."
        )
        return

    raw_target = args[0].strip()
    amount: float | None = None
    amount_arg_idx = 1
    try:
        amount = float(args[1].replace(",", "."))
    except ValueError:
        # Support spaced phone format, e.g.:
        # /addbalance +251 91 183 7353 100 note...
        found = False
        max_scan = min(len(args) - 1, 6)
        for idx in range(2, max_scan + 1):
            try:
                amt_try = float(args[idx].replace(",", "."))
            except ValueError:
                continue
            target_try = "".join(args[:idx]).strip()
            if len(telebirr_phone_key_normalize(target_try)) >= 9 or looks_like_phone_query(target_try):
                raw_target = target_try
                amount = amt_try
                amount_arg_idx = idx
                found = True
                break
        if not found:
            await update.message.reply_text(
                "Amount must be a number (e.g. 100 or 50.5).\n"
                "If phone contains spaces, use e.g. /addbalance +251911837353 100 or /addbalance +251 91 183 7353 100"
            )
            return
    assert amount is not None
    if amount <= 0:
        await update.message.reply_text("Amount must be greater than zero.")
        return

    note = " ".join(args[amount_arg_idx + 1 :]).strip() if len(args) > amount_arg_idx + 1 else None
    if not note:
        note = "admin manual credit"

    db = _get_db()
    try:
        init_db()
        target_id: int | None = None
        tun: str | None = None

        phone_body, explicit_phone = _strip_addbalance_phone_prefix(raw_target)
        if explicit_phone:
            pkey = telebirr_phone_key_normalize(phone_body)
            if len(pkey) < 9:
                await update.message.reply_text(
                    "Could not read that phone. Try e.g. phone:0988013094 or +251988013094"
                )
                return
            matches = find_users_by_telebirr_phone_key(db, pkey)
            if len(matches) == 1:
                target_id = int(matches[0].telegram_user_id)
                tun = matches[0].telegram_username
            elif len(matches) > 1:
                lines = [_fmt_user_ref(int(u.telegram_user_id), u.telegram_username) for u in matches]
                await update.message.reply_text(
                    "Several accounts share this Telebirr number. Credit by Telegram id instead:\n" + "\n".join(lines)
                )
                return
            else:
                await update.message.reply_text(
                    "No player is linked to that phone yet.\n"
                    "They need to request /withdraw once using that Telebirr account (so we store the number), "
                    "or you credit by Telegram user id."
                )
                return
        elif looks_like_phone_query(raw_target):
            pkey = telebirr_phone_key_normalize(raw_target)
            if len(pkey) >= 9:
                matches = find_users_by_telebirr_phone_key(db, pkey)
                if len(matches) == 1:
                    target_id = int(matches[0].telegram_user_id)
                    tun = matches[0].telegram_username
                elif len(matches) > 1:
                    lines = [_fmt_user_ref(int(u.telegram_user_id), u.telegram_username) for u in matches]
                    await update.message.reply_text(
                        "Several accounts share this Telebirr number. Use Telegram id:\n" + "\n".join(lines)
                    )
                    return
                elif raw_target.isdigit():
                    target_id = int(raw_target)
                    tun = None
                else:
                    await update.message.reply_text(
                        "No player linked to this phone. Use Telegram user id, or ask them to /withdraw once with this number."
                    )
                    return
        if target_id is None and raw_target.isdigit():
            target_id = int(raw_target)
            tun = None
        if target_id is None:
            uname = raw_target.removeprefix("@").strip()
            if not uname:
                await update.message.reply_text(
                    "Invalid target. Use Telegram user id, @username, Telebirr phone, or phone:…"
                )
                return
            row = get_user_by_telegram_username(db, uname)
            if not row:
                await update.message.reply_text(
                    f"No account found for @{uname}.\n"
                    "Use their numeric Telegram user id (Profile → id, or forward a message), "
                    "their Telebirr number (after a /withdraw), or ask them to tap /start once for @username."
                )
                return
            target_id = int(row.telegram_user_id)
            tun = row.telegram_username

        user = deposit_amount(
            db,
            telegram_user_id=target_id,
            telegram_username=tun,
            amount_etb=amount,
            note=note,
        )
        db.commit()
        total = float(user.balance_etb)
        play = float(user.play_only_balance_etb)
        await update.message.reply_text(
            f"✅ Credited {_fmt_etb(amount)} ETB (play-only) to {_fmt_user_ref(target_id, user.telegram_username)}\n"
            f"💳 Total balance: {_fmt_etb(total)} ETB · play-only: {_fmt_etb(play)} ETB"
        )
        try:
            await context.bot.send_message(
                chat_id=target_id,
                text=(
                    f"✅ {_fmt_etb(amount)} ETB was added to your ETHIO BINGO balance by an admin.\n"
                    f"💳 New balance: {_fmt_etb(total)} ETB"
                ),
            )
        except Exception as ex:
            log.warning("admin credit user notify: %s", ex)
    except Exception as e:
        db.rollback()
        await update.message.reply_text(f"Could not credit: {e}")
    finally:
        db.close()


def main():
    try:
        load_dotenv()
    except Exception:
        pass

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    application = (
        Application.builder()
        .token(settings.primary_bot_token())
        .post_init(post_init)
        .build()
    )

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("register", register_cmd))
    application.add_handler(CommandHandler("play", play_cmd))
    application.add_handler(CommandHandler("balance", balance_cmd))
    application.add_handler(CommandHandler("myid", myid_cmd))
    application.add_handler(CommandHandler("deposit", deposit_cmd))
    application.add_handler(CommandHandler("withdraw", withdraw_cmd))
    application.add_handler(CommandHandler("transfer", transfer_cmd))
    application.add_handler(CommandHandler("invite", invite_cmd))
    application.add_handler(CommandHandler("instructions", instructions_cmd))
    application.add_handler(CommandHandler("cancel", cancel_cmd))
    application.add_handler(CommandHandler("addbalance", addbalance_cmd))
    application.add_handler(CommandHandler("admin_deposit", addbalance_cmd))
    application.add_handler(CommandHandler("deposits_recent", deposits_recent_cmd))
    application.add_handler(CommandHandler("deposit_pending", deposit_pending_list_cmd))
    application.add_handler(CommandHandler("reverse_deposit", reverse_deposit_cmd))
    application.add_handler(CommandHandler("withdraw_pending", withdraw_pending_cmd))
    application.add_handler(CommandHandler("withdraw_processing", withdraw_processing_cmd))
    application.add_handler(CommandHandler("withdraw_complete", withdraw_complete_cmd))
    application.add_handler(CommandHandler("withdraw_reject", withdraw_reject_cmd))

    application.add_handler(CallbackQueryHandler(deposit_pending_review_callback, pattern=r"^dep(app|rej)_[0-9]+$"))
    application.add_handler(CallbackQueryHandler(deposit_telebirr_callback, pattern=r"^deposit_telebirr$"))
    application.add_handler(CallbackQueryHandler(withdraw_bank_callback, pattern=r"^withdraw_bank_telebirr$"))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, plain_text_router))

    log.info("ETHIO BINGO bot started. Telebirr receipt pastes credit immediately (txn id de-duplicated).")
    application.run_polling(close_loop=False)


if __name__ == "__main__":
    main()
