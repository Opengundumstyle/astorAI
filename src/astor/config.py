"""Centralised settings (twelve-factor: all config from env)."""
from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql+psycopg://astor:astor@localhost:5432/astor"

    embeddings_provider: str = "dev"  # dev | voyage | openai
    embedding_dim: int = 1024
    voyage_api_key: str | None = None
    openai_api_key: str | None = None

    anthropic_api_key: str | None = None

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

    equiv_exact_threshold: float = 0.92
    equiv_substitute_threshold: float = 0.80
    equiv_candidates: int = 20

    log_level: str = "INFO"


settings = Settings()
