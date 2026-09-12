# packages/ml-pipeline/db_utils.py
"""Shared DB connection helpers for the ml-pipeline scripts.

Kept separate from the backend package on purpose: this package has its own
virtualenv/requirements.txt (xgboost, geopandas, jupyter, ...) and is never
imported by the deployed FastAPI app, so it does not import anything from
`packages/backend/app`. It reads the same `DATABASE_URL` value out of the same
`.env` convention instead of sharing code.
"""
from __future__ import annotations

import os
from pathlib import Path


def load_settings() -> dict[str, str]:
    """Load DATABASE_URL from the environment or a .env file.

    Checks, in order: an already-exported env var, then `.env` in this
    package's directory, then `.env` at the repo root (so a single root-level
    .env used for local dev is picked up without duplicating it).
    """
    if os.getenv("DATABASE_URL"):
        return {"DATABASE_URL": os.environ["DATABASE_URL"]}

    try:
        from dotenv import dotenv_values
    except ImportError as exc:
        raise SystemExit(
            "python-dotenv is not installed and DATABASE_URL is not set in the "
            "environment. `pip install -r requirements.txt` or export "
            "DATABASE_URL yourself."
        ) from exc

    here = Path(__file__).resolve().parent
    for candidate in (here / ".env", here.parent.parent / ".env"):
        if candidate.exists():
            values = dotenv_values(candidate)
            if values.get("DATABASE_URL"):
                return {"DATABASE_URL": values["DATABASE_URL"]}

    raise SystemExit(
        "DATABASE_URL not found in the environment or in a .env file. Set it to "
        "the same Supabase/Postgres connection string the backend uses."
    )


def sync_engine_url(database_url: str) -> str:
    """Normalise a backend-style async URL into a sync one for pandas/SQLAlchemy.

    The backend requires `postgresql+asyncpg://` (see app/core/config.py) so
    its asyncio engine works; these are plain batch scripts, so they use the
    ordinary sync psycopg2 driver instead. Accepts the same postgres://,
    postgresql://, or postgresql+asyncpg:// forms Supabase/Vercel hand out.
    """
    url = database_url
    if url.startswith("postgres://"):
        url = "postgresql://" + url[len("postgres://") :]
    if url.startswith("postgresql+asyncpg://"):
        url = "postgresql+psycopg2://" + url[len("postgresql+asyncpg://") :]
    elif url.startswith("postgresql://"):
        url = "postgresql+psycopg2://" + url[len("postgresql://") :]
    # asyncpg-style query params that psycopg2 does not understand.
    if "?" in url and ("sslmode=" not in url) and "supabase" in url:
        url = url + ("&" if "?" in url else "?") + "sslmode=require"
    return url
