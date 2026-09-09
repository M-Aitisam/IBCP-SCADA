# Vercel and ingestion deployment

Use the repository root as the Vercel Root Directory for the combined deployment
in `vercel.json`. It builds the dashboard and Python API together; `/api/*` routes
to `packages/backend/index.py`. Python dependencies live beside that entrypoint
in `packages/backend/requirements.txt`, including PyJWT and earthengine-api.
Use Python 3.12 for these pinned dependencies.

## Vercel environment variables

Set these in the project's Production environment (and Preview if needed).
Local `packages/backend/.env` settings do not configure the deployed project.

| Variable | Value / purpose |
| --- | --- |
| `NEXT_PUBLIC_API_URL` | `/api/v1` for the combined deployment; this is also the production fallback. For a separate backend, use its HTTPS URL ending in `/api/v1`. Never use localhost in a deployed build. |
| `DATABASE_URL` | Hosted Postgres URL; the same database used by ingestion. Apply `python -m alembic upgrade head` against this database. |
| `ENVIRONMENT` | `production` |
| `SECRET_KEY` | A stable random secret of at least 32 characters. |
| `GEE_PROJECT_ID` | Earth Engine enabled Cloud project ID, not numeric client ID. |
| `GEE_SERVICE_ACCOUNT` | Service account email with Earth Engine access. |
| `GEE_PRIVATE_KEY` | Whole service account JSON, preferably base64 encoded. A Windows file path cannot work on Vercel. Required for boundary export on a cache miss. |
| `GEE_ROI_PROVINCES` | Empty for all Pakistan, or a comma-separated list. Match GitHub Actions. The Python default when absent is Balochistan,Sindh. |
| `FRONTEND_URL` | Deployed dashboard HTTPS origin. |
| `CORS_ORIGINS` | Dashboard HTTPS origin, especially for separate frontend/backend deployments. |
| `ENABLE_DOCS` | `false` to disable interactive docs explicitly. |

For dashboard ingestion buttons, also set `GITHUB_REPOSITORY=owner/repo` and
`GITHUB_DISPATCH_TOKEN` with Actions write access to that repository. Optional
`GITHUB_WORKFLOW_REF` defaults to `main`. For Google login, configure
`GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`, and `GOOGLE_REDIRECT_URI` as
`https://YOUR-DOMAIN/api/v1/auth/google/callback`, and register that callback
in Google Cloud. These are separate from Earth Engine credentials.

Redeploy after changing environment variables. Next.js embeds `NEXT_PUBLIC_*`
at build time. Setting them only in the backend `.env` does not configure Next.js.
Keep all credentials server-side; never give them a `NEXT_PUBLIC_` prefix.

## GitHub Actions ingestion

The API reads satellite observations from Postgres. It does not ingest them
automatically during deployment. Configure these repository Actions secrets:

- `DATABASE_URL` (same database as Vercel)
- `GEE_PROJECT_ID`
- `GEE_SERVICE_ACCOUNT`
- `GEE_PRIVATE_KEY_BASE64` (mapped by the workflow to `GEE_PRIVATE_KEY`)

The workflow is `.github/workflows/gee-daily-ingestion.yml`. Ensure scheduled
Actions are enabled. Set repository variable `GEE_ROI_PROVINCES` to the same
scope as Vercel; unset/empty means all Pakistan in the workflow. Optional
`GEE_DAILY_ENABLED=false` skips daily ingestion.

Leave repository variable `GEE_TARGET_END` unset for daily runs to advance to
today's UTC date. An explicit variable or dispatch `end_date` freezes the cutoff.
The Python configuration still defaults to the historical August 19, 2026 cutoff
for local CLI/backfill; set a current `GEE_TARGET_END` when running daily locally.
Run a manual backfill with explicit start/end dates if historical data is needed.
The workflow runs the analytics cascade after ingestion to refresh derived results.

## Diagnosing blank or stale panels

- Requests to localhost: remove the deployed localhost API override and rebuild.
- API 401: sign in again; protected endpoints require a valid token.
- API 500: inspect function logs for database connectivity, missing migrations,
  or dependency errors. A successful `/health` alone does not check the database.
- Boundary API 503: check Vercel GEE credentials, project permissions and ROI.
  Geometry uses a database cache; a cold export still needs Earth Engine access.
- API 200 with empty/stale observations: inspect Actions runs, database identity,
  requested cutoff, and dashboard date/province filters.
- Ingestion buttons report unconfigured: add the GitHub dispatch variables on Vercel.

Monitoring currently uses HTTP polling of stored observations and analytics.
There is no MQTT subscriber or WebSocket client wired into the dashboard source;
setting `NEXT_PUBLIC_MQTT_BROKER` alone cannot enable live sensor ingestion.
Satellite freshness also depends on each source's publication cadence.

References: [Vercel Python runtime](https://vercel.com/docs/functions/runtimes/python),
[Vercel environment variables](https://vercel.com/docs/environment-variables),
[Next.js environment variables](https://nextjs.org/docs/app/guides/environment-variables).
