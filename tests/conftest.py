"""Test-wide defaults.

`settings` is a module-level singleton loaded from `.env`, so a developer who sets
ADMIN_TOKEN or ENABLE_DEMO_CHAT locally would otherwise turn the suite red: the tests
describe the app's DEFAULT posture, and the environment must not redefine it.
"""
import pytest

from astor.api.ratelimit import SlidingWindowLimiter
from astor.api.routers import shopify_proxy as proxy_router
from astor.config import settings


@pytest.fixture(autouse=True)
def _pin_deployment_settings(monkeypatch):
    monkeypatch.setattr(settings, "admin_token", None)
    monkeypatch.setattr(settings, "admin_token_required", False)
    monkeypatch.setattr(settings, "enable_demo_chat", True)


@pytest.fixture(autouse=True)
def _fresh_chat_limiter(monkeypatch):
    """`_chat_limiter` is a process-wide singleton; without this, every /proxy/chat test
    in every file would silently share one 20-call budget."""
    monkeypatch.setattr(
        proxy_router, "_chat_limiter",
        SlidingWindowLimiter(proxy_router.settings.proxy_chat_rate_per_min))
