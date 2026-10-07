"""API routers package."""

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

__all__ = [
    "auth",
    "beneficiaries",
    "campaigns",
    "contributions",
    "dashboard",
    "milestones",
    "payouts",
    "webhooks",
]
