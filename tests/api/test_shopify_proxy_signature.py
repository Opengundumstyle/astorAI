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


def test_sorts_rendered_strings_not_keys():
    # Shopify sorts the RENDERED "key=value" strings, not the bare keys. For
    # {"a": "1", "a1": "2"} that means "a1=2" (< '=' at 0x3D, '1' is 0x31)
    # sorts before "a=1" — the opposite order a key-sort would produce.
    # Expected message hardcoded here (not derived from the implementation)
    # so this test actually discriminates key-sort from string-sort.
    secret = "s3cr3t"
    message = "a1=2a=1"
    sig = hmac.new(secret.encode(), message.encode(), hashlib.sha256).hexdigest()
    items = [("a", "1"), ("a1", "2"), ("signature", sig)]
    assert valid_app_proxy_signature(items, secret) is True


def test_blank_signature_fails():
    assert valid_app_proxy_signature([("shop", "demo.myshopify.com"), ("signature", "")], "s3cr3t") is False


def test_non_ascii_signature_fails_without_raising():
    # hmac.compare_digest on two `str` raises TypeError for non-ASCII input;
    # comparing bytes instead must make this cleanly return False.
    items = [("shop", "demo.myshopify.com"), ("signature", "café")]
    assert valid_app_proxy_signature(items, "s3cr3t") is False
