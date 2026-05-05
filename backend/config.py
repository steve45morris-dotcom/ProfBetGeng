from pydantic_settings import BaseSettings
from pydantic import field_validator
from functools import lru_cache
from pathlib import Path
from typing import Any

# Resolve .env relative to this file (backend/.env) — works regardless of cwd
_ENV_FILE = Path(__file__).parent / ".env"


class Settings(BaseSettings):
    app_name: str = "ProfBetGeng"
    app_version: str = "0.9.2"
    debug: bool = False
    auth_enabled: bool = True
    batch_enabled: bool = False
    environment: str = "development"
    # Dev/test default — the production startup guard in main.py prevents this
    # value from being used in production (ENVIRONMENT=production requires a
    # real ADMIN_TOKEN env var to be set).
    admin_token: str = "pbg_admin_secret"

    # CORS — accepts a JSON list or a plain comma-separated string from the env var
    allowed_origins: list[str] = [
        "http://localhost:5173",
        "http://localhost:5174",
        "http://127.0.0.1:5173",
        "http://127.0.0.1:5174",
    ]

    # Supabase
    supabase_url: str = ""
    supabase_anon_key: str = ""
    supabase_service_role_key: str = ""

    # Anthropic
    anthropic_api_key: str = ""

    # Sentry error monitoring (optional — disabled if DSN is absent)
    sentry_dsn: str = ""
    sentry_traces_sample_rate: float = 0.1

    # Langfuse observability (optional — tracing disabled if keys are absent)
    langfuse_public_key: str = ""
    langfuse_secret_key: str = ""
    langfuse_host: str = "http://localhost:3000"

    # Live Data APIs
    the_odds_api_key: str = ""

    @field_validator("allowed_origins", mode="before")
    @classmethod
    def parse_allowed_origins(cls, v: Any) -> list[str]:
        """Accept a comma-separated string (from env) or a list (from code/JSON)."""
        if isinstance(v, str):
            return [origin.strip() for origin in v.split(",") if origin.strip()]
        return v

    model_config = {
        "env_file": str(_ENV_FILE),
        "env_file_encoding": "utf-8",
        "env_ignore_empty": True,   # empty shell vars don't override .env values
    }


@lru_cache()
def get_settings() -> Settings:
    return Settings()
