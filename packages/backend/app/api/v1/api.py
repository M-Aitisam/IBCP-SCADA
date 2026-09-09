# packages/backend/app/api/v1/api.py
from fastapi import APIRouter
from .endpoints import (
    auth,
    events,
    field_reports,
    flood,
    geovision,
    ingestion,
    intelligence,
    soil,
)

api_router = APIRouter()

api_router.include_router(auth.router, prefix="/auth", tags=["Authentication"])
api_router.include_router(geovision.router, prefix="/geovision", tags=["GeoVision"])
api_router.include_router(
    ingestion.router, prefix="/ingestion", tags=["Ingestion"]
)
api_router.include_router(
    intelligence.router, prefix="/intelligence", tags=["Intelligence"]
)
api_router.include_router(events.router, prefix="/events", tags=["Hazard Events"])
api_router.include_router(
    field_reports.router, prefix="/field", tags=["Field Verification"]
)
api_router.include_router(flood.router, prefix="/flood", tags=["Flood SCADA"])
api_router.include_router(soil.router, prefix="/soil", tags=["Soil Monitoring"])