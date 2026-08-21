# packages/backend/app/ingestion/__init__.py
"""Google Earth Engine satellite data acquisition.

Boundary of this module: GEE -> filter -> reduce -> validate -> timestampdb.
No indices, no classification, no model training happen here.

Entry point is the CLI: python -m app.ingestion.cli --help
"""
