# packages/backend/test_db.py
# Quick connectivity check: confirms DATABASE_URL (from .env) is reachable
# and reports whether Alembic's migrations have been applied.
import asyncio

from sqlalchemy import text

from app.core.config import settings
from app.db.database import engine


async def test_connection():
    host = settings.DATABASE_URL.split("@")[-1] if "@" in settings.DATABASE_URL else settings.DATABASE_URL
    print(f"Connecting to: {host}")

    try:
        async with engine.connect() as conn:
            result = await conn.execute(text("SELECT 1"))
            print(f"Database connected. Result: {result.scalar()}")

            result = await conn.execute(
                text("SELECT tablename FROM pg_tables WHERE schemaname = 'public'")
            )
            tables = sorted(row[0] for row in result.fetchall())
            print(f"Tables: {tables if tables else 'none found — run: alembic upgrade head'}")
    except Exception as e:
        print(f"Connection failed: {e}")
        raise
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(test_connection())
