import os

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse

from backend.app.api.routes.admin import router as admin_router
from backend.app.api.routes.games import router as games_router
from backend.app.api.routes.health import router as health_router
from backend.app.api.routes.wallet import router as wallet_router
from backend.app.db.init_db import init_db


def create_app() -> FastAPI:
    app = FastAPI(title="ETHIO BINGO API")

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    init_db()

    app.include_router(health_router)
    app.include_router(wallet_router)
    app.include_router(games_router)
    app.include_router(admin_router)

    # Serve the Telegram WebApp frontend (no build step).
    static_dir = os.path.join(os.path.dirname(__file__), "static")
    app.mount("/static", StaticFiles(directory=static_dir, html=True), name="static")

    # Telegram WebApp opens the root URL, so serve index.html at `/`.
    index_path = os.path.join(static_dir, "index.html")

    @app.get("/")
    def index():
        return FileResponse(index_path)

    return app


app = create_app()

