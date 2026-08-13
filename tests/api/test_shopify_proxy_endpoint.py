import hashlib
import hmac

from fastapi.testclient import TestClient

from astor.config import settings
from astor.api.main import create_app

SECRET = "s3cr3t"


def _sign(params: dict[str, str], secret: str) -> str:
    message = "".join(f"{k}={v}" for k, v in sorted(params.items()))
    return hmac.new(secret.encode(), message.encode(), hashlib.sha256).hexdigest()


def _client(monkeypatch, secret=SECRET):
    monkeypatch.setattr(settings, "shopify_app_proxy_secret", secret)
    monkeypatch.setattr(settings, "shopify_client_secret", secret)
    return TestClient(create_app())


def test_ping_ok_with_valid_signature(monkeypatch):
    params = {"shop": "demo.myshopify.com", "timestamp": "1700000000"}
    q = {**params, "signature": _sign(params, SECRET)}
    resp = _client(monkeypatch).get("/proxy/ping", params=q)
    assert resp.status_code == 200
    assert resp.json() == {"ok": True, "shop": "demo.myshopify.com"}


def test_ping_401_on_tamper(monkeypatch):
    params = {"shop": "demo.myshopify.com"}
    sig = _sign(params, SECRET)
    resp = _client(monkeypatch).get("/proxy/ping", params={"shop": "evil.myshopify.com", "signature": sig})
    assert resp.status_code == 401


def test_ping_401_without_signature(monkeypatch):
    resp = _client(monkeypatch).get("/proxy/ping", params={"shop": "demo.myshopify.com"})
    assert resp.status_code == 401


def test_ping_503_without_secret(monkeypatch):
    monkeypatch.setattr(settings, "shopify_app_proxy_secret", None)
    monkeypatch.setattr(settings, "shopify_client_secret", None)
    resp = TestClient(create_app()).get("/proxy/ping", params={"shop": "demo.myshopify.com", "signature": "x"})
    assert resp.status_code == 503
