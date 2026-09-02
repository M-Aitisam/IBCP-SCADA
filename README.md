# IBCP-SCADA — Indus Basin Cyber-Physical SCADA System

Unified system for flood management, water distribution, and agricultural
intelligence in Pakistan's Indus Basin.

## Modules

| Module | Status |
|---|---|
| **GeoVision AI** — remote sensing for drought/flood monitoring | Satellite acquisition working; drought index and prediction **not yet built** |
| **Flood SCADA** — barrage gate control | API contract only; **not connected to hardware** |
| **Soil Monitoring** — salinity and land degradation | API contract only; **placeholder data** |

Endpoints that return placeholder data say so in a `data_source` field, and
unbuilt features return `status: "not_implemented"` rather than fabricated
numbers.

## Stack

- **Frontend** — Next.js 14 (static export) + TypeScript (strict) + Tailwind
- **Backend** — FastAPI + SQLAlchemy 2 (async) + Alembic, Python 3.11+
- **Database** — PostgreSQL, with TimescaleDB used for the observation table
  where the extension is available
- **Acquisition** — Google Earth Engine, scheduled via GitHub Actions

## Requirements

- **Python 3.11, 3.12 or 3.13** (Vercel runs 3.12)
- **Node.js 18+**
- **PostgreSQL 14+** — locally the easiest route is Docker

## Setup

### 1. Database

```bash
docker run -d --name ibcp-db -p 5432:5432 \
  -e POSTGRES_PASSWORD=devpass -e POSTGRES_DB=ibcp_scada \
  timescale/timescaledb:latest-pg16
```

Plain `postgres:16` works too — the observation table is then created as a
regular indexed table instead of a hypertable, with identical behaviour.

### 2. Configuration

```bash
cp .env.example packages/backend/.env
```

Then set `DATABASE_URL` and a real `SECRET_KEY`:

```bash
python -c "import secrets; print(secrets.token_urlsafe(48))"
```

### 3. Backend

```bash
cd packages/backend
python -m venv venv
venv\Scripts\activate          # Windows
# source venv/bin/activate     # macOS/Linux
pip install -r requirements.txt

alembic upgrade head           # create tables
python check_db.py             # verify connectivity
uvicorn app.main:app --reload --port 8000
```

API docs: <http://localhost:8000/docs>

### 4. Dashboard

```bash
cd packages/dashboard
npm install
npm run dev                    # http://localhost:3000
```

### 5. Satellite data acquisition (optional)

Requires Google Earth Engine credentials. See
[packages/backend/docs/GEE_INGESTION.md](packages/backend/docs/GEE_INGESTION.md).

```bash
cd packages/backend
python -m app.ingestion.cli check-config    # reports what is missing
python -m app.ingestion.cli daily --dry-run # writes nothing
python -m app.ingestion.cli backfill        # explicit, never automatic
```

## Roles

Registration always creates an unprivileged `user`. Roles are assigned by an
administrator via `PUT /api/v1/auth/users/{id}/role`.

| Role | Can |
|---|---|
| `viewer` / `user` | Read dashboards and observations |
| `operator` | Also send gate and pump commands |
| `admin` | Also assign roles |

To create the first administrator, promote a registered user directly:

```sql
UPDATE users SET role = 'admin' WHERE username = 'your-username';
```

## Tests

```bash
cd packages/backend && pytest -q      # 164 tests, no database or GEE needed
cd packages/dashboard && npx tsc --noEmit && npm run lint
```

## Deployment

Vercel, configured in `vercel.json`: the dashboard builds as a static export,
the backend runs as a Python serverless function at `/api/*`.

Keep `DB_POOL_ENABLED` unset or `false` on serverless — pooled asyncpg
connections are bound to the event loop that created them, and a warm function
invocation runs on a different loop.

Daily satellite ingestion runs from GitHub Actions rather than Vercel, which
has no persistent scheduler and too short an execution budget for a backfill.


#vercel update