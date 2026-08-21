"""Centralised settings (twelve-factor: all config from env)."""
from __future__ import annotations

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql+psycopg://astor:astor@localhost:5432/astor"

    @field_validator("database_url")
    @classmethod
    def _force_psycopg_driver(cls, v: str) -> str:
        """Managed hosts (Render) inject a bare `postgres://` URL; SQLAlchemy needs an
        explicit driver or it reaches for psycopg2, which this project does not install."""
        for prefix in ("postgres://", "postgresql://"):
            if v.startswith(prefix):
                return "postgresql+psycopg://" + v[len(prefix):]
        return v

    embeddings_provider: str = "dev"  # dev | voyage | openai
    embedding_dim: int = 1024
    voyage_api_key: str | None = None
    openai_api_key: str | None = None

    anthropic_api_key: str | None = None
    chat_model: str = "claude-sonnet-5"  # storefront assistant (tool-use loop)

    # The unsigned demo chat routes (/api/chat, /api/chat/stream) call Anthropic with
    # no caller authentication. Fine on a laptop behind a tunnel; off on a public host.
    enable_demo_chat: bool = True

    # -- Shopify house-catalog inbound feed (secrets stay in .env) ----------- #
    shopify_shop_domain: str | None = None      # "astor" | "astor.myshopify.com"
    shopify_admin_token: str | None = None       # legacy static token (existing admin apps)
    shopify_client_id: str | None = None         # Dev Dashboard app Client ID
    shopify_client_secret: str | None = None     # Dev Dashboard app Client Secret (secret)
    shopify_api_version: str = "2026-01"          # set to a currently-supported version
    shopify_shop_currency: str = "USD"
    shopify_mpn_metafield: str | None = None      # "namespace.key" holding MPN/cat-no
    shopify_specs_metafield_namespace: str | None = None  # namespace -> specs
    shopify_supplier_name: str = "Astor Shopify (US)"
    shopify_supplier_region: str = "US"
    shopify_supplier_tier: str = "authorized"     # public | authorized | deep

    # App Proxy request verification. Same value as the app's API secret key
    # (shopify_client_secret); a dedicated field lets it be scoped/rotated apart.
    shopify_app_proxy_secret: str | None = None

    # Per-shop cap on storefront chat turns (sliding 60s window).
    proxy_chat_rate_per_min: int = 20

    # -- protocols.io ingest (v1 protocol source; secrets stay in .env) ------ #
    protocols_io_token: str | None = None  # required for live fetch (gated, see sources.py)
    protocols_io_licensed: bool = False  # second lock: bulk search/fetch stays off until a licence is confirmed

    equiv_exact_threshold: float = 0.92
    equiv_substitute_threshold: float = 0.80
    equiv_candidates: int = 20

    # Material→product matching runs a different comparison than product↔product:
    # a bare material name vs a product's rich canonical text has a lower similarity
    # ceiling (good matches observed ~0.71–0.77), so the equiv_* thresholds never
    # fire. These material-specific thresholds are tuned against extracted (clean)
    # materials. Override in .env to retune without a code change.
    material_exact_threshold: float = 0.82
    material_substitute_threshold: float = 0.75

    # Gates the internal /api/* surface when set (prod). Unset = open (local dev).
    admin_token: str | None = None
    # Public hosts set this true so a missing admin_token is a startup failure rather
    # than a silently open API. See create_app.
    admin_token_required: bool = False

    log_level: str = "INFO"


settings = Settings()
