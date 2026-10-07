"""FundFlow FastAPI application (PRODUCT.md §39)."""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.core.config import get_settings
from app.core.database import init_db


def create_app() -> FastAPI:
    settings = get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        if settings.auto_create_tables:
            init_db()  # dev/demo convenience — Alembic owns real migrations
        yield

    app = FastAPI(
        title="FundFlow API",
        description=(
            "Kora-powered programmable fundraising platform. "
            "Raise money. Show where it is going. Release it according to rules."
        ),
        version="0.1.0",
        lifespan=lifespan,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    prefix = settings.api_prefix
    from app.api import (
        auth,
        beneficiaries,
        campaigns,
        contributions,
        dashboard,
        milestones,
        payouts,
        webhooks,
    )

    app.include_router(auth.router, prefix=prefix)
    app.include_router(campaigns.router, prefix=prefix)
    app.include_router(contributions.router, prefix=prefix)
    app.include_router(beneficiaries.router, prefix=prefix)
    app.include_router(milestones.router, prefix=prefix)
    app.include_router(payouts.router, prefix=prefix)
    app.include_router(dashboard.router, prefix=prefix)
    app.include_router(webhooks.router, prefix=prefix)

    @app.get("/health")
    def health():
        kora_mode = "mock" if settings.is_mock else "live"
        return {"status": "ok", "service": settings.app_name, "kora_mode": kora_mode}

    return app


app = create_app()
