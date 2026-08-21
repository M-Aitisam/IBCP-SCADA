# packages/backend/app/main.py
import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.v1.api import api_router
from app.core.config import settings

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-7s %(name)s | %(message)s",
)

app = FastAPI(
    title="IBCP-SCADA API",
    description="Indus Basin Cyber-Physical SCADA System",
    version="1.0.0",
    # Interactive docs are useful in development but expose the full API
    # surface; disabled in production unless explicitly re-enabled.
    docs_url="/docs" if settings.ENABLE_DOCS else None,
    redoc_url="/redoc" if settings.ENABLE_DOCS else None,
    openapi_url="/openapi.json" if settings.ENABLE_DOCS else None,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    # Wildcards in allow_origins are matched as literal strings by Starlette,
    # so "https://*.vercel.app" silently matched nothing. Preview deployments
    # need a regex.
    allow_origin_regex=settings.CORS_ORIGIN_REGEX,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type"],
)


@app.get("/")
async def root():
    return {
        "message": "IBCP-SCADA API",
        "version": "1.0.0",
        "status": "operational",
    }


@app.get("/health")
async def health_check():
    return {"status": "healthy"}


app.include_router(api_router, prefix="/api/v1")


if __name__ == "__main__":
    # Local development only. Vercel imports `app` directly via index.py.
    import uvicorn

    uvicorn.run("app.main:app", host="127.0.0.1", port=8000, reload=True)
