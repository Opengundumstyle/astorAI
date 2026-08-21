"""Application factory: CORS, routers, optional demo seed on startup."""
from __future__ import annotations

import os

from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware

from astor.api.auth import require_admin_token
from astor.api.routers import health as health_router
from astor.api.routers import catalog, chat, dashboard, pricing, protocols, shopify_proxy
from astor.config import settings


def create_app() -> FastAPI:
    if settings.admin_token_required and not settings.admin_token:
        raise RuntimeError(
            "ADMIN_TOKEN_REQUIRED is set but ADMIN_TOKEN is empty — refusing to start "
            "with an unauthenticated /api/* surface.")

    app = FastAPI(title="AstorScientific API", version="0.1.0")

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:3000"],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.get("/api/health")
    def health() -> dict:
        return {"status": "ok"}

    app.include_router(health_router.router)

    # Internal, operator-facing surface: gated when ADMIN_TOKEN is set.
    admin = [Depends(require_admin_token)]
    app.include_router(catalog.router, dependencies=admin)
    app.include_router(chat.router, dependencies=admin)
    app.include_router(dashboard.router, dependencies=admin)
    app.include_router(pricing.router, dependencies=admin)
    app.include_router(protocols.router, dependencies=admin)

    # Storefront surface: authenticated by the Shopify App Proxy signature instead.
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
