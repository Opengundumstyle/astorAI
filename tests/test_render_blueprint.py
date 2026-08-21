from pathlib import Path

import yaml

BLUEPRINT = Path(__file__).resolve().parents[1] / "render.yaml"


def _spec() -> dict:
    return yaml.safe_load(BLUEPRINT.read_text())


def test_blueprint_exists_and_parses():
    assert BLUEPRINT.is_file()
    assert _spec()["services"]


def test_web_service_is_docker_and_health_checked():
    svc = _spec()["services"][0]
    assert svc["type"] == "web"
    assert svc["runtime"] == "docker"          # `env:` is the deprecated spelling
    assert svc["dockerfilePath"] == "./Dockerfile"
    assert svc["healthCheckPath"] == "/healthz"
    assert svc["plan"] == "starter"            # always-on; `free` sleeps
    assert svc["region"] == "oregon"


def test_database_url_is_injected_from_the_managed_database():
    env = {e["key"]: e for e in _spec()["services"][0]["envVars"]}
    assert env["DATABASE_URL"]["fromDatabase"] == {
        "name": "astor-db", "property": "connectionString"}


def test_demo_chat_is_off_in_production():
    env = {e["key"]: e for e in _spec()["services"][0]["envVars"]}
    assert env["ENABLE_DEMO_CHAT"]["value"] == "false"


def test_production_fails_closed_without_an_admin_token():
    env = {e["key"]: e for e in _spec()["services"][0]["envVars"]}
    assert env["ADMIN_TOKEN_REQUIRED"]["value"] == "true"


def test_no_secret_values_are_committed():
    env = {e["key"]: e for e in _spec()["services"][0]["envVars"]}
    for key in ("ANTHROPIC_API_KEY", "SHOPIFY_APP_PROXY_SECRET",
                "SHOPIFY_CLIENT_SECRET", "ADMIN_TOKEN"):
        assert env[key].get("sync") is False, f"{key} must be dashboard-set"
        assert "value" not in env[key], f"{key} must not carry a committed value"


def test_database_sized_for_the_existing_data():
    db = _spec()["databases"][0]
    assert db["name"] == "astor-db"
    assert db["region"] == "oregon"
    # Live data is 342 MB; diskSizeGB must be 1 or a multiple of 5.
    assert db["diskSizeGB"] >= 10 and db["diskSizeGB"] % 5 == 0


def test_database_is_not_open_to_the_internet():
    db = _spec()["databases"][0]
    assert "ipAllowList" in db, "omitting ipAllowList makes Render allow ALL IPs"
    assert db["ipAllowList"] == [], "external access must be granted temporarily, not committed"
