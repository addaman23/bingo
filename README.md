Habesha Bingo (Telegram Bot + FastAPI WebApp)

This project creates the “Telegram bot -> Play Bingo -> WebApp mini app” flow.

Features
- Telegram bot deep-link: `https://t.me/<BOT_USERNAME>?start=<payload>`
- “Play Bingo” button opens a Telegram WebApp (mini app) inside Telegram
- Manual admin deposit system (no Telegram payments)
- Wallet balance per Telegram user
- Bingo game API with:
  - Active lobby (shared)
  - Place a bet/pick before the game starts
  - Host starts calling
  - Clients poll game state for current call and results

Quick start (developer)
1. Create a Telegram bot with BotFather and get `BOT_TOKEN`
2. Deploy FastAPI somewhere public (Telegram requires a reachable URL)
   - For local testing you can use `ngrok` and set `WEBAPP_URL` to the https URL
3. Copy `.env.example` to `.env` and fill in:
   - `BOT_TOKEN`, `WEBAPP_URL`, `ADMIN_TELEGRAM_IDS`
4. Install dependencies:
   - `pip install -r requirements.txt`
5. Run backend:
   - `uvicorn backend.app.main:app --reload --port 8000`
   - **Cloudflare Tunnel (`cloudflared`) 502:** The tunnel is up but the origin is not. Start this backend *before* (or leave it running with) the tunnel, and point the tunnel at the same host/port, e.g. `cloudflared tunnel --url http://localhost:8000`. If you use another `--port` for uvicorn, use that port in the tunnel URL.
   - **Cloudflare Error 1016 on `*.trycloudflare.com`:** Quick tunnels use a **new random hostname each run**; old URLs in `.env` or Telegram stop working after you stop `cloudflared`. Copy the **current** URL from the running tunnel into `WEBAPP_URL`, restart the bot, and use a fresh **Play Bingo** link.
   - **Windows — `cloudflared` not recognized:** The WinGet installer puts the binary under `C:\Program Files (x86)\cloudflared\`. Add that folder to your user **PATH**, or run: `"C:\Program Files (x86)\cloudflared\cloudflared.exe" tunnel --url http://127.0.0.1:8000`
6. Run bot (separate terminal):
   - `python bot/bot.py`

Notes
- The WebApp uses Telegram `initData` to securely identify the user.
- Manual admin credits: `/addbalance <telegram_user_id> <amount> [note]` (admin Telegram IDs in `ADMIN_TELEGRAM_IDS`). Users use `/deposit` for Telebirr top-up instructions.

Folder structure (implemented)
`backend/app/main.py` (FastAPI app)
`backend/app/api/routes/*` (endpoints)
`backend/app/db/*` (SQLite models + wallet/game logic)
`backend/app/static/*` (Telegram WebApp UI)
`bot/bot.py` (Telegram bot: menu commands + `/addbalance` for admins)

API endpoints (paths)
- `GET /health`
- `GET /wallet/balance` (requires Telegram WebApp header `X-Telegram-InitData`)
- `GET /games/active`
- `POST /games` (create a new lobby game)
- `POST /games/{game_id}/bets` (place bet/pick in lobby)
- `POST /games/{game_id}/host/start` (host starts calling)
- `GET /games/{game_id}` (get current state; server advances calls by time)
- `POST /admin/deposit` (admin-only, requires Telegram WebApp `initData`)

