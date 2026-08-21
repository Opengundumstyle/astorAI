"""Application factory: CORS, routers, optional demo seed on startup."""
from __future__ import annotations

import os

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from astor.api.routers import catalog, chat, dashboard, health, pricing, protocols, shopify_proxy


def create_app() -> FastAPI:
    app = FastAPI(title="AstorScientific API", version="0.1.0")

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:3000"],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.get("/api/health")
    def _health() -> dict:
        return {"status": "ok"}

    @app.get("/healthz")
    def healthz_endpoint() -> dict:
        return {"ok": True}

    app.include_router(health.router)
    app.include_router(catalog.router)
    app.include_router(chat.router)
    app.include_router(dashboard.router)
    app.include_router(pricing.router)
    app.include_router(protocols.router)
    app.include_router(shopify_proxy.router)

    if os.getenv("SEED_DEMO") == "1":
        from astor.api.seed import seed_demo
        from astor.db.base import session_scope

        @app.on_event("startup")
        def _seed() -> None:
            with session_scope() as session:
                seed_demo(session)

    return app


app = create_app()
