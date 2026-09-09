# packages/backend/app/db/database.py
import os

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import declarative_base
from sqlalchemy.pool import NullPool

from app.core.config import settings

# Connection pooling and serverless do not mix.
#
# An asyncpg connection is bound to the event loop that opened it. The engine
# is module-level, so it survives across invocations of a warm serverless
# function — but the event loop does not. A pooled connection handed to a new
# loop fails with "attached to a different loop" or "'NoneType' has no
# attribute 'send'".
#
# NullPool opens a connection per session and closes it afterwards, which is
# the correct trade-off for serverless and for short-lived CLI runs.
#
# It is the WRONG trade-off for a long-lived server, and expensively so. Against
# a managed Postgres in another region, opening a connection (TCP + TLS + auth)
# measured ~3.2s from here, while a query on an already-open connection costs
# ~0.2s. With NullPool every request paid that 3.2s again; pooling cut endpoint
# latency from ~3.5s to ~1.2s.
#
# So the default is now chosen from the runtime rather than assumed:
#   - serverless (Vercel sets VERCEL=1)  -> NullPool, because a pooled
#     asyncpg connection is bound to the event loop that opened it and a warm
#     function invocation gets a new loop, which fails with
#     "attached to a different loop".
#   - anything else (uvicorn, container, VM) -> pooled.
# DB_POOL_ENABLED still overrides explicitly in both directions.
#
# Set it to false for any harness that runs each request in a FRESH event loop
# — notably fastapi.testclient.TestClient, which drives the app through a
# per-request portal. A pooled connection handed to the next request's loop
# fails with "Event loop is closed". Under uvicorn this cannot happen: the
# whole process shares one loop.
_IS_SERVERLESS = bool(os.getenv("VERCEL") or os.getenv("AWS_LAMBDA_FUNCTION_NAME"))
_POOL_OVERRIDE = os.getenv("DB_POOL_ENABLED", "").strip().lower()

if _POOL_OVERRIDE in {"1", "true", "yes"}:
    _POOLING_ENABLED = True
elif _POOL_OVERRIDE in {"0", "false", "no"}:
    _POOLING_ENABLED = False
else:
    _POOLING_ENABLED = not _IS_SERVERLESS

_engine_kwargs: dict = {"echo": False}
if _POOLING_ENABLED:
    # pool_pre_ping avoids handing out a connection the server has already
    # dropped after an idle period.
    _engine_kwargs.update(pool_pre_ping=True, pool_size=5, max_overflow=10)
else:
    _engine_kwargs.update(poolclass=NullPool)

engine = create_async_engine(settings.DATABASE_URL, **_engine_kwargs)

AsyncSessionLocal = async_sessionmaker(
    bind=engine, expire_on_commit=False, class_=AsyncSession
)

Base = declarative_base()


async def get_db():
    """FastAPI dependency yielding a session, rolled back on error.

    Without the explicit rollback a failed request could return its connection
    to the caller mid-transaction; with NullPool the connection is closed on
    exit either way, but the rollback keeps behaviour identical under pooling.
    """
    async with AsyncSessionLocal() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise
