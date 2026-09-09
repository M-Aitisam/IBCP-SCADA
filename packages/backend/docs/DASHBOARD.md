# Dashboard architecture

`packages/dashboard` — Next.js 14 App Router, static export, Leaflet, Recharts,
React Query, Zustand.

## Structure

```
src/store/geovisionFilters.ts     one source of truth for every filter
src/hooks/useGeovision.ts         React Query hooks keyed off that store
src/services/api/geovisionApi.ts  typed client
src/components/geovision/         command-centre components
src/app/geovision/page.tsx        Monitoring | Situation Center tabs
```

## Two views, one filter state

The GeoVision page carries two tabs rather than two routes:

- **Monitoring** — the original dashboard, unchanged: KPIs, trends, satellite
  watch, ingestion monitor, vegetation table, data-source catalog.
- **Situation Center** — the operational view: situation summary, what changed,
  regional hazard ranking, active indicators, data freshness, pipeline.

The **map stays visible in both**. An operator reading the situation summary
still needs to see where the regions are.

Adding a tab rather than replacing the page means nothing that already worked
was disturbed.

## Centralised filters

Every filtered component reads `useGeovisionFilters`, so the map, KPIs, charts,
tables and the region panel cannot disagree about what is selected. The cascade
(province → district → tehsil) clears narrower levels when a broader one
changes — a district from the previous province is not a valid selection.

The React Query key deliberately **excludes** `selectedRegionId`. Opening the
region detail panel must not re-fetch the map, the KPI row and every chart.
Likewise `mapColourMode`: switching between severity and value re-colours
existing data rather than re-querying it.

## Map colouring

Two modes:

**Condition** (default) — traffic light. Green healthy, yellow watch, orange
stressed, red critical, **grey not assessed**.

Grey is not green on purpose. Grey says "we have no basis to judge this"; green
says "we checked and it is fine". For temperature and rainfall that distinction
is the whole point — a district with no seasonal baseline has not been cleared,
it has not been assessed.

**Value** — the raw measurement ramp, because severity is a judgement and an
analyst sometimes needs the number behind it.

If a layer has no assessed regions, the map falls back to Value automatically
and says why in amber rather than showing a flat grey rectangle.

### Legend thresholds

NDVI/EVI/crop use conventional vigour bands, **sent with the data** so the
legend cannot drift from the classification actually applied.

Rainfall and LST deliberately have **no named bands**. There is no
climate-independent "high rainfall": 30 mm in a week is a drought in monsoon
Punjab and a deluge in Chagai. Those use a continuous ramp over the values
present, with real numeric endpoints in the legend.

## Empty states are load-bearing

Every panel renders one of four states — loading, error, empty, or content —
through `QueryBoundary`. A blank area is never acceptable: the user cannot tell
a slow request from an empty database from a broken one.

Where the cascade could not compute something, the panel says so *and why*:

> No region could be scored this cycle. Hazard scoring compares each region
> against its own seasonal baseline, which needs several years of history. Run
> the historical backfill to enable it.

## Charts

Gaps stay gaps. `connectNulls={false}` — a satellite record with no observation
in a bucket must not be bridged by a line implying a measurement that was never
made.

Buckets are chosen by span (daily ≤92d, weekly ≤400d, monthly beyond), so a
10-year request returns ~120 points rather than millions of rows.

## Accessibility

Status is never carried by colour alone. Every badge pairs its colour with an
icon and a word, so it survives greyscale and colour-blind vision. Tables carry
`aria-sort` on column headers; interactive rows are real buttons.

## Geometry safety

Earth Engine does not emit strict GeoJSON — it can produce `LinearRing`, which
the spec does not define and Leaflet throws on. One such sliver in one district
once took down the whole dashboard.

The backend now normalises every geometry to Polygon/MultiPolygon, and the map
additionally screens features before Leaflet sees them. A future quirk degrades
to "one district missing", not "site down".
