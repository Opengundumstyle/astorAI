"""Application factory: CORS, routers, optional demo seed on startup."""
from __future__ import annotations

import os

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from astor.api.routers import catalog, dashboard, pricing


def create_app() -> FastAPI:
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

    app.include_router(catalog.router)
    app.include_router(dashboard.router)
    app.include_router(pricing.router)

    return app


app = create_app()
