# packages/backend/app/core/config.py
from pydantic import field_validator
from pydantic_settings import BaseSettings
from typing import Optional

class Settings(BaseSettings):
    # Database (async driver — SQLAlchemy's asyncio engine requires postgresql+asyncpg://).
    # Providers (Vercel Postgres, Supabase, Neon, ...) hand out plain postgresql:// /
    # postgres:// URLs, so that scheme is normalized to +asyncpg below.
    DATABASE_URL: str = "postgresql+asyncpg://user:password@localhost:5432/ibcp_scada"

    @field_validator("DATABASE_URL")
    @classmethod
    def _use_asyncpg_driver(cls, v: str) -> str:
        if v.startswith("postgres://"):
            return "postgresql+asyncpg://" + v[len("postgres://"):]
        if v.startswith("postgresql://"):
            return "postgresql+asyncpg://" + v[len("postgresql://"):]
        return v
    
    # Redis
    REDIS_URL: str = "redis://localhost:6379"
    
    # MQTT
    MQTT_BROKER: str = "localhost"
    MQTT_PORT: int = 1883
    
    # JWT
    SECRET_KEY: str = "your-secret-key-change-in-production-12345"
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 30
    
    # GEE
    GEE_PROJECT: Optional[str] = None

    # Google OAuth
    GOOGLE_CLIENT_ID: Optional[str] = None
    GOOGLE_CLIENT_SECRET: Optional[str] = None
    GOOGLE_REDIRECT_URI: str = "http://localhost:8000/api/v1/auth/google/callback"
    FRONTEND_URL: str = "http://localhost:3000"

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"

settings = Settings()