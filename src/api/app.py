from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
import os

from .routes import router
from .routes_v9 import router as router_v9


def create_app() -> FastAPI:
    app = FastAPI(
        title="InspectSummary API",
        description="長照居家監測一頁式 AI 摘要系統",
        version="0.1.0",
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(router, prefix="/api/v1")
    app.include_router(router_v9, prefix="/api/v1")

    # Serve static frontend
    static_dir = os.path.join(os.path.dirname(__file__), "..", "..", "static")
    if os.path.isdir(static_dir):
        app.mount("/static", StaticFiles(directory=static_dir), name="static")

    @app.get("/", include_in_schema=False)
    async def root():
        index = os.path.join(static_dir, "index.html")
        return FileResponse(index)

    # 真實資料串接頁（2026-08-12）：輸入 carenote 個案編號即可產生摘要，
    # 走 POST /api/v1/v9/generate-from-carenote，不用手貼 JSON
    @app.get("/carenote", include_in_schema=False)
    async def carenote_page():
        page = os.path.join(static_dir, "carenote.html")
        return FileResponse(page)

    @app.get("/health")
    async def health():
        return {"status": "ok"}

    return app
