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

    equiv_exact_threshold: float = 0.92
    equiv_substitute_threshold: float = 0.80
    equiv_candidates: int = 20

    log_level: str = "INFO"


settings = Settings()
