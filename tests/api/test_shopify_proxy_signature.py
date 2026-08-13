import hashlib
import hmac

from astor.api.shopify_proxy import valid_app_proxy_signature


def _sign(params: dict[str, str], secret: str) -> str:
    message = "".join(f"{k}={v}" for k, v in sorted(params.items()))
    return hmac.new(secret.encode(), message.encode(), hashlib.sha256).hexdigest()


def test_valid_signature_passes():
    secret = "s3cr3t"
    params = {"shop": "demo.myshopify.com", "timestamp": "1700000000"}
    items = list(params.items()) + [("signature", _sign(params, secret))]
    assert valid_app_proxy_signature(items, secret) is True


def test_wrong_secret_fails():
    params = {"shop": "demo.myshopify.com"}
    items = list(params.items()) + [("signature", _sign(params, "right"))]
    assert valid_app_proxy_signature(items, "wrong") is False


def test_tampered_param_fails():
    secret = "s3cr3t"
    params = {"shop": "demo.myshopify.com", "path_prefix": "/apps/astor"}
    sig = _sign(params, secret)
    tampered = [("shop", "evil.myshopify.com"), ("path_prefix", "/apps/astor"), ("signature", sig)]
    assert valid_app_proxy_signature(tampered, secret) is False


def test_missing_signature_fails():
    assert valid_app_proxy_signature([("shop", "demo.myshopify.com")], "s3cr3t") is False


def test_multi_value_params_joined_by_comma():
    # Shopify joins repeated params with a comma before signing.
    secret = "s3cr3t"
    message = "ids=1,2,3shop=demo.myshopify.com"
    sig = hmac.new(secret.encode(), message.encode(), hashlib.sha256).hexdigest()
    items = [("ids", "1"), ("ids", "2"), ("ids", "3"),
             ("shop", "demo.myshopify.com"), ("signature", sig)]
    assert valid_app_proxy_signature(items, secret) is True
