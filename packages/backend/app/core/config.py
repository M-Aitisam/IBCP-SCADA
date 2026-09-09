# packages/backend/app/core/config.py
import warnings
from typing import Optional

from pydantic import field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Placeholder shipped in .env.example. Refused outright when ENVIRONMENT is
# "production" so a deployment cannot inherit a publicly known signing key.
INSECURE_DEFAULT_SECRET = "your-secret-key-change-in-production-12345"


class Settings(BaseSettings):
    ENVIRONMENT: str = "development"

    # Database (async driver — SQLAlchemy's asyncio engine requires
    # postgresql+asyncpg://). Providers (Vercel Postgres, Supabase, Neon, ...)
    # hand out plain postgresql:// / postgres:// URLs, normalised below.
    DATABASE_URL: str = "postgresql+asyncpg://user:password@localhost:5432/ibcp_scada"

    @field_validator("DATABASE_URL")
    @classmethod
    def _use_asyncpg_driver(cls, v: str) -> str:
        for prefix in ("postgresql+asyncpg://", "postgresql+psycopg://"):
            if v.startswith(prefix):
                return v
        if v.startswith("postgres://"):
            v = "postgresql://" + v[len("postgres://") :]
        if v.startswith("postgresql://"):
            v = "postgresql+asyncpg://" + v[len("postgresql://") :]
        # asyncpg rejects libpq-style query params that providers often append.
        if "?" in v and "sslmode=" in v:
            base, _, query = v.partition("?")
            kept = [
                p for p in query.split("&") if not p.startswith(("sslmode=", "channel_binding="))
            ]
            v = f"{base}?{'&'.join(kept)}" if kept else base
        return v

    REDIS_URL: str = "redis://localhost:6379"

    MQTT_BROKER: str = "localhost"
    MQTT_PORT: int = 1883

    # JWT
    SECRET_KEY: str = INSECURE_DEFAULT_SECRET
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 30

    # CORS. Comma-separated exact origins, plus an optional regex for preview
    # deployments (Starlette matches allow_origins literally, never as globs).
    CORS_ORIGINS: str = "http://localhost:3000,http://127.0.0.1:3000"
    CORS_ORIGIN_REGEX: Optional[str] = r"^https://[a-z0-9-]+\.vercel\.app$"

    ENABLE_DOCS: bool = True

    # GEE
    GEE_PROJECT: Optional[str] = None

    # Ingestion control.
    #
    # The API never runs ingestion in-process: the backend deploys as a Vercel
    # serverless function with a seconds-long execution budget, and a backfill
    # runs for hours. Triggering instead dispatches the GitHub Actions workflow
    # that already owns the nightly schedule, so there is one execution path
    # rather than two that can disagree.
    #
    # Unset is a valid state: the trigger endpoints then report that remote
    # dispatch is not configured and hand back the CLI command, which is more
    # useful than a generic 500.
    GITHUB_REPOSITORY: Optional[str] = None  # "owner/repo"
    GITHUB_DISPATCH_TOKEN: Optional[str] = None  # PAT with `actions: write`
    GITHUB_WORKFLOW_FILE: str = "gee-daily-ingestion.yml"
    GITHUB_WORKFLOW_REF: str = "main"

    # Google OAuth
    GOOGLE_CLIENT_ID: Optional[str] = None
    GOOGLE_CLIENT_SECRET: Optional[str] = None
    GOOGLE_REDIRECT_URI: str = "http://localhost:8000/api/v1/auth/google/callback"
    FRONTEND_URL: str = "http://localhost:3000"

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    @field_validator("ALGORITHM")
    @classmethod
    def _supported_algorithm(cls, v: str) -> str:
        # "none" would make every token trivially forgeable.
        if v not in {"HS256", "HS384", "HS512"}:
            raise ValueError(f"unsupported JWT algorithm: {v}")
        return v

    @field_validator("ACCESS_TOKEN_EXPIRE_MINUTES")
    @classmethod
    def _positive_expiry(cls, v: int) -> int:
        if v <= 0:
            raise ValueError("ACCESS_TOKEN_EXPIRE_MINUTES must be positive")
        return v

    @model_validator(mode="after")
    def _secret_key_is_safe(self) -> "Settings":
        is_production = self.ENVIRONMENT.lower() in {"production", "prod"}
        if self.SECRET_KEY == INSECURE_DEFAULT_SECRET or len(self.SECRET_KEY) < 32:
            if is_production:
                raise ValueError(
                    "SECRET_KEY must be set to a strong random value (32+ chars) "
                    "in production. Generate one with: "
                    "python -c \"import secrets; print(secrets.token_urlsafe(48))\""
                )
            warnings.warn(
                "SECRET_KEY is the insecure default or too short. Fine for local "
                "development; must be replaced before deploying.",
                stacklevel=2,
            )
        return self

    @property
    def cors_origins(self) -> list[str]:
        origins = [o.strip().rstrip("/") for o in self.CORS_ORIGINS.split(",")]
        return [o for o in origins if o]

    @property
    def is_production(self) -> bool:
        return self.ENVIRONMENT.lower() in {"production", "prod"}


settings = Settings()
