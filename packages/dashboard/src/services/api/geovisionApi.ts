// packages/dashboard/src/services/api/geovisionApi.ts
//
// Typed client for the GeoVision API.
//
// Every list endpoint reports `data_source`, so the UI can tell "we asked and
// there is nothing" apart from "we have not asked yet". That distinction is why
// no component here ever falls back to a placeholder number: an absent value
// renders as an explicit empty state, never as a zero.
import apiClient, { LONG_TIMEOUT_MS } from '@/utils/axios'

export type DataSource = 'timestampdb' | 'no_data' | 'none'

/** Named ranges the API accepts. Custom windows use start/end instead. */
export type RangeKey = '7d' | '30d' | '3m' | '6m' | '1y' | '5y' | '10y'

export const RANGE_LABELS: Record<RangeKey, string> = {
  '7d': '7D',
  '30d': '30D',
  '3m': '3M',
  '6m': '6M',
  '1y': '1Y',
  '5y': '5Y',
  '10y': '10Y',
}

export type MetricKey =
  | 'ndvi'
  | 'evi'
  | 'rainfall_mm'
  | 'lst_day_c'
  | 'lst_night_c'
  | 'backscatter_vv'
  | 'backscatter_vh'

export type DatasetName =
  | 'sentinel2'
  | 'sentinel1'
  | 'chirps'
  | 'mod13q1'
  | 'mod11a2'

export type DatasetState =
  | 'HEALTHY'
  | 'WARNING'
  | 'NO NEW DATA'
  | 'NO DATA'
  | 'FAILED'

export type WatchPriority = 'INFO' | 'WATCH' | 'HIGH' | 'CRITICAL'

/** Query shape shared by every filtered endpoint. */
export interface GeovisionQuery {
  range?: RangeKey
  start?: string
  end?: string
  province?: string | null
  district?: string | null
  tehsil?: string | null
  region_id?: string | null
}

/** Strips nulls/undefined so axios does not send `?province=null`. */
function params(query: GeovisionQuery, extra: Record<string, unknown> = {}) {
  const merged: Record<string, unknown> = { ...query, ...extra }
  return Object.fromEntries(
    Object.entries(merged).filter(([, v]) => v !== null && v !== undefined && v !== '')
  )
}

// ---------------------------------------------------------------------------
// Response types
// ---------------------------------------------------------------------------

export interface Period {
  start: string
  end: string
  label?: string
  bucket?: 'day' | 'week' | 'month'
}

export interface Kpi {
  metric: string
  label: string
  unit: string
  description: string
  value: number | string | null
  observation_date: string | null
  dataset: string
  observations: number
  regions: number
  aggregation: 'mean' | 'sum'
  change_pct: number | null
  direction: 'up' | 'down' | 'stable' | null
  interpretation: 'improving' | 'declining' | null
  trend_status?: 'ok' | 'insufficient_data'
  status: 'ok' | 'no_data'
  /** Present only on the derived crop-condition KPI. */
  numeric_basis?: number | null
  basis_metric?: string
  classes?: string[]
}

export interface Freshness {
  latest_observation: string | null
  last_ingested_at: string | null
  age_days: number | null
  as_of: string
  status: 'ok' | 'no_data'
}

export interface Overview {
  period: Period
  filters: Record<string, string | null>
  kpis: Record<string, Kpi>
  coverage: {
    regions_monitored: number
    provinces: number
    observations: number
  }
  freshness: Freshness
  datasets: {
    total: number
    healthy: number
    failed: number
    states: Record<string, DatasetState>
  }
  data_source: DataSource
}

export interface RegionMetric {
  value: number | null
  unit: string
  dataset: string
  observation_date: string
  cloud_percentage: number | null
  pixel_count: number | null
  processing_status: string
}

export interface RegionSummary {
  region_id: string
  region_type: string
  province: string | null
  district: string | null
  tehsil: string | null
  name: string
  metrics: Partial<Record<MetricKey, RegionMetric>>
  latest_observation: string | null
  vegetation_status: string | null
  crop_condition: string | null
  conditions?: Partial<Record<string, RegionCondition>>
  anomalies?: Partial<Record<string, RegionAnomaly>>
  overall_condition?: ConditionLevel
  rainfall_window_total?: {
    value: number | null
    unit: string
    observations: number
    period: Period
  }
}

export type ConditionLevel =
  | 'healthy'
  | 'watch'
  | 'stressed'
  | 'critical'
  | 'unknown'

export interface RegionCondition {
  level: ConditionLevel
  /** 'value' for vegetation indices, 'anomaly' for rainfall/temperature. */
  basis: 'value' | 'anomaly'
  detail: string
  z_score?: number | null
}

export interface RegionAnomaly {
  value: number | null
  baseline: number | null
  standard_deviation: number | null
  baseline_years: number
  z_score: number | null
  status: 'ok' | 'insufficient_baseline'
}

export interface ConditionScale {
  levels: Array<{ level: ConditionLevel; label: string; rank: number }>
  value_classified_metrics: string[]
  anomaly_classified_metrics: string[]
  value_bands: Array<{ min: number; level: ConditionLevel }>
  anomaly_thresholds: {
    watch_z: number
    stressed_z: number
    critical_z: number
    min_baseline_years: number
  }
}

export interface ClassBand {
  min: number
  label: string
}

export interface RegionsResponse {
  status: string
  data_source: DataSource
  count: number
  period: Period
  metrics: MetricKey[]
  regions: RegionSummary[]
  classification: {
    ndvi: ClassBand[]
    crop_condition: ClassBand[]
  }
  condition_scale?: ConditionScale
}

export interface HierarchyResponse {
  status: string
  count: number
  regions: Array<{
    region_id: string
    region_type: string
    province: string | null
    district: string | null
    tehsil: string | null
    name: string
    observations: number
    latest_observation: string | null
  }>
  provinces: Array<{
    province: string
    districts: Array<{ district: string; tehsils: string[] }>
  }>
}

export interface TrendResult {
  metric: string
  label: string
  unit: string
  aggregation: 'mean' | 'sum'
  current: { value: number; unit: string; observations: number; regions: number; last_observation: string | null; dataset: string } | null
  previous: { value: number } | null
  period: Period
  previous_period: Period
  status: 'ok' | 'insufficient_data'
  change_pct: number | null
  change_absolute: number | null
  direction: 'up' | 'down' | 'stable' | null
  interpretation?: 'improving' | 'declining' | null
  reason?: string
}

export interface SeriesPoint {
  bucket_start: string
  value: number | null
  min: number | null
  max: number | null
  observations: number
  regions: number
}

export interface SeriesResponse {
  status: string
  metric: string
  label: string
  unit: string
  dataset: string
  dataset_asset_id: string | null
  native_cadence: string | null
  bucket: 'day' | 'week' | 'month'
  aggregation: 'mean' | 'sum'
  period: Period
  data_source: DataSource
  count: number
  series: SeriesPoint[]
  trend: TrendResult
}

export interface AnomalyResponse {
  status: 'ok' | 'insufficient_data'
  metric: string
  label: string
  unit: string
  /** Always "derived_anomaly" — this is computed, not measured. */
  kind: string
  observed: { value: number } | null
  baseline_years: number
  baseline_period: string
  min_baseline_years: number
  baseline: { value: number; standard_deviation: number; years: number[] } | null
  anomaly: { absolute: number; percent: number | null; z_score: number | null } | null
  reason?: string
}

export interface RegionDetail {
  status: string
  data_source: DataSource
  region_id: string
  period: Period
  region: RegionSummary | null
  trends: Record<string, TrendResult>
  datasets: Array<{
    dataset: string
    latest_observation: string | null
    observations: number
    mean_cloud_percentage: number | null
  }>
  detail?: string
}

export interface CatalogEntry {
  dataset: string
  gee_collection: string
  version: string | null
  provider: string | null
  platform: string | null
  purpose: string | null
  revisit: string | null
  native_cadence: string
  nominal_cadence_days: number
  spatial_resolution_m: number
  metric_group: string
  metrics: Array<{ metric: string; label: string; unit: string | null; derived: boolean }>
  cloud_masking: string | null
  cloud_threshold_pct: number | null
  enabled: boolean
  coverage: {
    observations: number
    regions: number
    earliest_observation: string | null
    latest_observation: string | null
    last_ingested_at: string | null
  }
  requested_until: string | null
  latest_available_at_source: string | null
  availability_status: string | null
  backfill_complete: boolean
  backfill_cursor: string | null
  last_run_at: string | null
  last_status: string | null
  last_error: string | null
  health: {
    state: DatasetState
    detail: string
    staleness_threshold_days: number
    age_days: number | null
  }
}

export interface CatalogResponse {
  status: string
  count: number
  datasets: CatalogEntry[]
  boundary_source: {
    asset: string
    region_type: string
    vintage_note: string
  }
}

export interface IngestionDataset {
  dataset: string
  state: DatasetState
  detail: string
  latest_observation: string | null
  earliest_observation: string | null
  last_ingested_at: string | null
  last_run_at: string | null
  last_status: string | null
  last_error: string | null
  observations: number
  regions: number
  native_cadence: string
  requested_until: string | null
  latest_available_at_source: string | null
  availability_status: string | null
  backfill_complete: boolean
  backfill_cursor: string | null
}

export interface IngestionRun {
  run_id: string
  mode: string
  dry_run: boolean
  status: string
  started_at: string | null
  finished_at: string | null
  duration_ms: number | null
  datasets_attempted: number
  datasets_succeeded: number
  datasets_failed: number
  records_inserted: number
  records_updated: number
  records_skipped: number
  records_rejected: number
  errors: Record<string, string> | null
}

export interface IngestionStatusResponse {
  status: string
  datasets: IngestionDataset[]
  recent_runs: IngestionRun[]
  states: DatasetState[]
}

export interface WatchIndicator {
  kind: string
  priority: WatchPriority
  title: string
  detail: string
  metric: string | null
  rule: string | null
  evidence: Record<string, unknown>
  classification: string
  is_official_warning: boolean
}

export interface WatchResponse {
  status: string
  indicators: WatchIndicator[]
  count: number
  period: Period
  priorities: WatchPriority[]
  thresholds: Record<string, number>
  disclaimer: string
}

export interface RegionGeometry {
  type: 'FeatureCollection'
  features: Array<{
    type: string
    geometry: { type: string; coordinates: unknown }
    properties: {
      region_id: string
      name?: string
      province?: string | null
      district?: string | null
      tehsil?: string | null
      region_type?: string
    }
  }>
  count: number
  source: string
  region_type: string
  attribution: string
}

// ---------------------------------------------------------------------------
// Legacy per-metric feeds (still used by the original views)
// ---------------------------------------------------------------------------

export interface RegionObservation {
  region_id: string
  region_type: string
  province: string | null
  district: string | null
  tehsil: string | null
  value: number
  unit: string
  observation_date: string
  dataset: string
  source_image_id: string
  pixel_count: number | null
}

export interface VegetationObservation extends RegionObservation {
  health_status: string
}

export interface RainfallTotal {
  region_id: string
  province: string | null
  district: string | null
  tehsil: string | null
  total_rainfall_mm: number
  observations: number
  latest_observation: string | null
  unit: string
}

export interface MetricEnvelope<T> {
  status: string
  metric: string
  data_source: DataSource
  count: number
  window_days: number
  data: T[]
}

export interface DatasetCoverage {
  dataset: string
  observations: number
  regions: number
  earliest: string | null
  latest: string | null
}

// ---------------------------------------------------------------------------
// Client
// ---------------------------------------------------------------------------

export const geovisionApi = {
  overview: async (query: GeovisionQuery = {}) => {
    const { data } = await apiClient.get<Overview>('/geovision/overview', {
      params: params(query),
    })
    return data
  },

  regions: async (query: GeovisionQuery = {}) => {
    const { data } = await apiClient.get<RegionsResponse>('/geovision/regions', {
      params: params(query),
    })
    return data
  },

  hierarchy: async () => {
    const { data } = await apiClient.get<HierarchyResponse>(
      '/geovision/regions/hierarchy'
    )
    return data
  },

  geometry: async (simplifyMetres?: number) => {
    const { data } = await apiClient.get<RegionGeometry>(
      '/geovision/regions/geometry',
      {
        params: simplifyMetres ? { simplify_metres: simplifyMetres } : undefined,
        // The only call that may legitimately exceed the default timeout: on a
        // deployment where boundaries have not been exported yet, the server
        // builds them from Earth Engine before persisting.
        timeout: LONG_TIMEOUT_MS,
      }
    )
    return data
  },

  regionDetail: async (regionId: string, query: GeovisionQuery = {}) => {
    const { data } = await apiClient.get<RegionDetail>(
      `/geovision/region/${encodeURIComponent(regionId)}`,
      { params: params(query) }
    )
    return data
  },

  trends: async (
    metric: MetricKey,
    query: GeovisionQuery = {},
    dataset?: DatasetName
  ) => {
    const { data } = await apiClient.get<SeriesResponse>('/geovision/trends', {
      params: params(query, { metric, dataset }),
    })
    return data
  },

  anomaly: async (metric: MetricKey, query: GeovisionQuery = {}) => {
    const { data } = await apiClient.get<AnomalyResponse>('/geovision/anomaly', {
      params: params(query, { metric }),
    })
    return data
  },

  datasets: async () => {
    const { data } = await apiClient.get<CatalogResponse>('/geovision/datasets')
    return data
  },

  ingestionStatus: async () => {
    const { data } = await apiClient.get<IngestionStatusResponse>(
      '/geovision/ingestion-status'
    )
    return data
  },

  watch: async (query: GeovisionQuery = {}) => {
    const { data } = await apiClient.get<WatchResponse>('/geovision/watch', {
      params: params(query),
    })
    return data
  },

  freshness: async () => {
    const { data } = await apiClient.get<Freshness & { status: string }>(
      '/geovision/freshness'
    )
    return data
  },

  // --- legacy feeds, preserved ---

  vegetation: async (days = 90) => {
    const { data } = await apiClient.get<MetricEnvelope<VegetationObservation>>(
      '/geovision/vegetation',
      { params: { days } }
    )
    return data
  },

  rainfall: async (days = 90) => {
    const { data } = await apiClient.get<MetricEnvelope<RainfallTotal>>(
      '/geovision/rainfall',
      { params: { days } }
    )
    return data
  },

  temperature: async (days = 90) => {
    const { data } = await apiClient.get<MetricEnvelope<RegionObservation>>(
      '/geovision/temperature',
      { params: { days } }
    )
    return data
  },

  coverage: async () => {
    const { data } = await apiClient.get<{
      status: string
      datasets: DatasetCoverage[]
    }>('/geovision/coverage')
    return data
  },
}

export default geovisionApi

// ---------------------------------------------------------------------------
// Intelligence layer (hazards, alerts, briefs, lineage, pipeline)
// ---------------------------------------------------------------------------

export type HazardKey =
  | 'multi_hazard'
  | 'drought'
  | 'crop_stress'
  | 'heat_stress'
  | 'flood'

export type HazardLevel =
  | 'NORMAL'
  | 'WATCH'
  | 'MODERATE'
  | 'SEVERE'
  | 'EXTREME'
  | 'LOW'
  | 'MEDIUM'
  | 'HIGH'
  | 'CRITICAL'
  | 'INSUFFICIENT_DATA'

export type AlertSeverity = 'WATCH' | 'ADVISORY' | 'WARNING' | 'CRITICAL'

export interface HazardContributor {
  component: string
  label: string
  value: number | null
  anomaly: number | null
  weight: number
  contribution: number
  direction: string
  detail: string
}

export interface HazardRegion {
  region_id: string
  province?: string | null
  district?: string | null
  tehsil?: string | null
  name?: string
  score: number | null
  level: HazardLevel
  confidence: number | null
  primary_driver: string | null
  reason: string | null
  previous_level: string | null
  consecutive_periods: number
  data_quality: number | null
  status: string
}

export interface HazardListResponse {
  status: string
  data_source: DataSource
  hazard: HazardKey
  reference_date: string | null
  count: number
  regions: HazardRegion[]
  calculation_version?: string
  disclaimer?: string
  detail?: string
}

export interface RegionHazardDetail {
  status: string
  data_source: DataSource
  region_id: string
  region: Record<string, unknown>
  reference_date: string
  hazards: Array<{
    hazard: HazardKey
    score: number | null
    level: HazardLevel
    confidence: number | null
    primary_driver: string | null
    reason: string | null
    status: string
    contributors: HazardContributor[]
    consecutive_periods: number
    calculation_version: string
  }>
  features: Array<Record<string, unknown>>
  is_official_warning: boolean
  disclaimer: string
}

export interface AlertItem {
  alert_id: string
  hazard: HazardKey
  region_id: string
  region_name: string | null
  province: string | null
  district: string | null
  severity: AlertSeverity
  previous_severity: string | null
  status: string
  score: number | null
  confidence: number | null
  reason: string | null
  rule_id: string | null
  evidence: Record<string, unknown> | null
  occurrence_count: number
  escalation_count: number
  first_detected: string
  reference_date: string
  acknowledged_at: string | null
  acknowledged_by: string | null
  is_official_warning: boolean
}

export interface BriefResponse {
  status: string
  data_source: DataSource
  brief: {
    brief_date: string
    generated_at: string
    headline: string | null
    active_alerts: number
    new_alerts: number
    critical_regions: number
    deteriorating_regions: number
    improving_regions: number
    datasets_healthy: number
    datasets_degraded: number
    top_risk_regions: Array<{
      region_id: string
      score: number | null
      level: string
      confidence: number | null
      primary_driver: string | null
    }>
    changes: Record<string, unknown> | null
    data_health: Record<string, unknown> | null
    calculation_version: string
  } | null
  detail?: string
}

export interface ChangesResponse {
  status: string
  data_source: DataSource
  reference_date?: string
  previous_date?: string | null
  deteriorating: Array<Record<string, unknown>>
  improving: Array<Record<string, unknown>>
  new_alerts: number
  datasets_degraded: number
  note?: string
}

export interface PipelineResponse {
  status: string
  stage_order: string[]
  dependencies: Record<string, string[]>
  runs: Array<{
    run_id: string
    status: string
    stages: Array<{
      stage: string
      sequence: number
      status: string
      started_at: string | null
      finished_at: string | null
      duration_ms: number | null
      records_in: number
      records_out: number
      error: string | null
      details: Record<string, unknown> | null
    }>
  }>
}

export interface FreshnessResponse {
  status: string
  as_of: string
  overall: string
  datasets: Array<{
    dataset_id: string
    state: string
    lag_days: number | null
    latest_observation: string | null
    expected_latest: string | null
    next_expected_update: string | null
    detail: string | null
  }>
}

export const intelligenceApi = {
  hazards: async (hazard: HazardKey = 'multi_hazard', query: GeovisionQuery = {}) => {
    const { data } = await apiClient.get<HazardListResponse>('/intelligence/hazards', {
      params: params(query, { hazard }),
    })
    return data
  },

  regionHazards: async (regionId: string) => {
    const { data } = await apiClient.get<RegionHazardDetail>(
      `/intelligence/hazards/${encodeURIComponent(regionId)}`
    )
    return data
  },

  alerts: async (status = 'active') => {
    const { data } = await apiClient.get<{
      status: string
      count: number
      active_by_severity: Record<string, number>
      alerts: AlertItem[]
      disclaimer: string
    }>('/intelligence/alerts', { params: { status } })
    return data
  },

  brief: async () => {
    const { data } = await apiClient.get<BriefResponse>('/intelligence/brief')
    return data
  },

  changes: async () => {
    const { data } = await apiClient.get<ChangesResponse>('/intelligence/changes')
    return data
  },

  pipeline: async (limit = 5) => {
    const { data } = await apiClient.get<PipelineResponse>('/intelligence/pipeline', {
      params: { limit },
    })
    return data
  },

  freshness: async () => {
    const { data } = await apiClient.get<FreshnessResponse>('/intelligence/freshness')
    return data
  },

  lineage: async (regionId: string, metric: string) => {
    const { data } = await apiClient.get<{
      status: string
      data_source: DataSource
      metric: string
      region_id: string
      value: number | null
      observation_date: string | null
      chain: Array<Record<string, unknown>>
      quality: Record<string, unknown>
      detail?: string
    }>('/intelligence/lineage', { params: { region_id: regionId, metric } })
    return data
  },
}

// ---------------------------------------------------------------------------
// Hazard events, hotspots, replay (§10-12, §21)
// ---------------------------------------------------------------------------

export type EventStatus =
  | 'DETECTED'
  | 'CONFIRMED'
  | 'ESCALATING'
  | 'PEAK'
  | 'DECLINING'
  | 'RESOLVED'

export interface HazardEventItem {
  event_id: string
  hazard_type: string
  region_id: string
  region_name: string | null
  province: string | null
  district: string | null
  status: EventStatus
  severity: string
  peak_severity: string | null
  current_score: number | null
  peak_score: number | null
  change_rate: number | null
  first_detected_at: string
  last_updated_at: string
  peak_at: string | null
  resolved_at: string | null
  duration_days: number
  observation_count: number
  affected_area_km2: number | null
  reason: string | null
  data_quality: number | null
  verification_status: string
  source_datasets: string[]
  is_official_warning: boolean
}

export interface EventListResponse {
  status: string
  data_source: DataSource
  count: number
  open_by_status: Record<string, number>
  events: HazardEventItem[]
  lifecycle: string[]
  detail?: string | null
}

export interface HotspotItem {
  cluster_id: string
  hazard_type: string
  cluster_size: number
  region_ids: string[]
  regions: Array<{
    region_id: string
    district: string | null
    province: string | null
    centroid: [number, number] | null
  }>
  average_severity: number | null
  maximum_severity: number | null
  dominant_level: string | null
  provinces: string[]
  first_detected: string
  growth_regions: number
  growth_rate: number | null
}

export interface HotspotResponse {
  status: string
  data_source: DataSource
  reference_date?: string
  count: number
  hotspots: HotspotItem[]
  rules?: Record<string, unknown>
  detail?: string
}

export interface ReplayFrame {
  reference_date: string
  events: Array<{
    event_id: string
    hazard_type: string
    region_id: string
    district: string | null
    province: string | null
    status: EventStatus
    severity: string
    score: number | null
    affected_area_km2: number | null
    change_from_previous: number | null
    transition: string | null
  }>
}

export interface ReplayResponse {
  status: string
  data_source: DataSource
  period: Period
  frame_count: number
  frames: ReplayFrame[]
  detail?: string | null
}

export const eventsApi = {
  list: async (status = 'open', hazard?: string) => {
    const { data } = await apiClient.get<EventListResponse>('/events', {
      params: params({}, { status, hazard }),
    })
    return data
  },

  detail: async (eventId: string) => {
    const { data } = await apiClient.get<{ status: string; event: HazardEventItem }>(
      `/events/${encodeURIComponent(eventId)}`
    )
    return data
  },

  timeline: async (eventId: string) => {
    const { data } = await apiClient.get<{
      status: string
      event_id: string
      count: number
      timeline: Array<Record<string, unknown>>
      phases: Record<string, string | null>
    }>(`/events/${encodeURIComponent(eventId)}/timeline`)
    return data
  },

  impact: async (eventId: string) => {
    const { data } = await apiClient.get<{
      status: string
      event_id: string
      windows: Record<string, [string, string]>
      comparison: Array<{
        metric: string
        before: number | null
        during: number | null
        after: number | null
        recovery_pct: number | null
        interpretation: string
      }>
      note: string
    }>(`/events/${encodeURIComponent(eventId)}/impact`)
    return data
  },

  hotspots: async (hazard?: string) => {
    const { data } = await apiClient.get<HotspotResponse>('/events/hotspots', {
      params: params({}, { hazard }),
    })
    return data
  },

  replay: async (opts: { hazard?: string; region_id?: string; start?: string; end?: string } = {}) => {
    const { data } = await apiClient.get<ReplayResponse>('/events/replay', {
      params: params({}, opts),
    })
    return data
  },
}
