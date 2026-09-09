// packages/dashboard/src/hooks/useGeovision.ts
'use client'

// Data hooks for the GeoVision command centre.
//
// Each hook derives its cache key from the shared filter store, so two panels
// showing the same underlying query issue one request rather than two, and a
// filter change invalidates exactly the queries that depend on it.
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { useCallback } from 'react'

import geovisionApi, {
  eventsApi,
  intelligenceApi,
  type DatasetName,
  type HazardKey,
  type MetricKey,
} from '@/services/api/geovisionApi'
import {
  queryKey,
  toQuery,
  useGeovisionFilters,
} from '@/store/geovisionFilters'

/** Root of every GeoVision cache key, so one call can invalidate the lot. */
const ROOT = 'geovision'

/**
 * Filters that affect *observation* queries.
 *
 * Selecting a region opens the detail panel but must not re-fetch the map or
 * the KPI row, so `selectedRegionId` is deliberately excluded from this key.
 */
function useFilterKey() {
  return useGeovisionFilters(queryKey)
}

function useQueryParams() {
  return useGeovisionFilters(toQuery)
}

function usePollInterval() {
  return useGeovisionFilters((s) => s.pollIntervalMs)
}

// ---------------------------------------------------------------------------
// Observation-derived data
// ---------------------------------------------------------------------------

export function useOverview() {
  const key = useFilterKey()
  const params = useQueryParams()
  const poll = usePollInterval()

  return useQuery({
    queryKey: [ROOT, 'overview', key],
    queryFn: () => geovisionApi.overview(params),
    refetchInterval: poll > 0 ? poll : false,
  })
}

export function useRegions() {
  const key = useFilterKey()
  const params = useQueryParams()
  const poll = usePollInterval()

  return useQuery({
    queryKey: [ROOT, 'regions', key],
    queryFn: () => geovisionApi.regions(params),
    refetchInterval: poll > 0 ? poll : false,
  })
}

export function useHierarchy() {
  return useQuery({
    queryKey: [ROOT, 'hierarchy'],
    queryFn: () => geovisionApi.hierarchy(),
    // The region tree only changes when a new region is first ingested.
    staleTime: 600_000,
  })
}

export function useRegionGeometry() {
  return useQuery({
    queryKey: [ROOT, 'geometry'],
    queryFn: () => geovisionApi.geometry(),
    // Boundaries are effectively static and the export is expensive; never
    // refetch them on a filter change.
    staleTime: Infinity,
    gcTime: Infinity,
    retry: false,
  })
}

export function useRegionDetail(regionId: string | null) {
  const key = useFilterKey()
  const params = useQueryParams()

  return useQuery({
    queryKey: [ROOT, 'region', regionId, key],
    queryFn: () => geovisionApi.regionDetail(regionId!, params),
    // Nothing to fetch until a region is actually selected.
    enabled: Boolean(regionId),
  })
}

export function useTrends(metric: MetricKey, dataset?: DatasetName) {
  const key = useFilterKey()
  const params = useQueryParams()

  return useQuery({
    queryKey: [ROOT, 'trends', metric, dataset ?? 'default', key],
    queryFn: () => geovisionApi.trends(metric, params, dataset),
  })
}

/** Trend series scoped to one region, for the detail panel's charts. */
export function useRegionTrends(regionId: string | null, metric: MetricKey) {
  const key = useFilterKey()
  const params = useQueryParams()

  return useQuery({
    queryKey: [ROOT, 'trends', 'region', regionId, metric, key],
    queryFn: () =>
      geovisionApi.trends(metric, { ...params, region_id: regionId! }),
    enabled: Boolean(regionId),
  })
}

export function useAnomaly(metric: MetricKey) {
  const key = useFilterKey()
  const params = useQueryParams()

  return useQuery({
    queryKey: [ROOT, 'anomaly', metric, key],
    queryFn: () => geovisionApi.anomaly(metric, params),
  })
}

export function useWatch() {
  const key = useFilterKey()
  const params = useQueryParams()
  const poll = usePollInterval()

  return useQuery({
    queryKey: [ROOT, 'watch', key],
    queryFn: () => geovisionApi.watch(params),
    refetchInterval: poll > 0 ? poll : false,
  })
}

// ---------------------------------------------------------------------------
// System / operational data (unaffected by geography filters)
// ---------------------------------------------------------------------------

export function useDatasetCatalog() {
  return useQuery({
    queryKey: [ROOT, 'catalog'],
    queryFn: () => geovisionApi.datasets(),
    staleTime: 300_000,
  })
}

export function useIngestionStatus() {
  const poll = usePollInterval()
  return useQuery({
    queryKey: [ROOT, 'ingestion-status'],
    queryFn: () => geovisionApi.ingestionStatus(),
    refetchInterval: poll > 0 ? poll : false,
  })
}

export function useFreshness() {
  const poll = usePollInterval()
  return useQuery({
    queryKey: [ROOT, 'freshness'],
    queryFn: () => geovisionApi.freshness(),
    refetchInterval: poll > 0 ? poll : false,
  })
}

// ---------------------------------------------------------------------------
// Manual refresh
// ---------------------------------------------------------------------------

/**
 * Refresh everything the dashboard shows.
 *
 * Boundary geometry is left alone on purpose: it is marked permanently fresh
 * because re-exporting it costs an Earth Engine round-trip, and a refresh
 * button is about new observations, not new borders.
 */
export function useRefreshAll() {
  const client = useQueryClient()
  return useCallback(async () => {
    await client.invalidateQueries({
      predicate: (query) =>
        query.queryKey[0] === ROOT && query.queryKey[1] !== 'geometry',
    })
  }, [client])
}

// ---------------------------------------------------------------------------
// Intelligence layer
// ---------------------------------------------------------------------------

/**
 * Hazard scores are NOT keyed off the geography filters.
 *
 * The cascade scores every region nightly and the Situation Center ranks them
 * nationally; filtering is applied client-side to the same payload. That keeps
 * one request serving the map, the ranking and the counts, instead of three
 * that could disagree about which cycle they describe.
 */
export function useHazards(hazard: HazardKey = 'multi_hazard') {
  const poll = usePollInterval()
  return useQuery({
    queryKey: [ROOT, 'intel', 'hazards', hazard],
    queryFn: () => intelligenceApi.hazards(hazard),
    refetchInterval: poll > 0 ? poll : false,
  })
}

export function useRegionHazards(regionId: string | null) {
  return useQuery({
    queryKey: [ROOT, 'intel', 'region-hazards', regionId],
    queryFn: () => intelligenceApi.regionHazards(regionId!),
    enabled: Boolean(regionId),
  })
}

export function useAlerts(status = 'active') {
  const poll = usePollInterval()
  return useQuery({
    queryKey: [ROOT, 'intel', 'alerts', status],
    queryFn: () => intelligenceApi.alerts(status),
    refetchInterval: poll > 0 ? poll : false,
  })
}

export function useBrief() {
  return useQuery({
    queryKey: [ROOT, 'intel', 'brief'],
    queryFn: () => intelligenceApi.brief(),
    staleTime: 300_000,
  })
}

export function useChanges() {
  return useQuery({
    queryKey: [ROOT, 'intel', 'changes'],
    queryFn: () => intelligenceApi.changes(),
    staleTime: 300_000,
  })
}

export function usePipeline(limit = 5) {
  const poll = usePollInterval()
  return useQuery({
    queryKey: [ROOT, 'intel', 'pipeline', limit],
    queryFn: () => intelligenceApi.pipeline(limit),
    refetchInterval: poll > 0 ? poll : false,
  })
}

export function useDataFreshness() {
  const poll = usePollInterval()
  return useQuery({
    queryKey: [ROOT, 'intel', 'freshness'],
    queryFn: () => intelligenceApi.freshness(),
    refetchInterval: poll > 0 ? poll : false,
  })
}

export function useLineage(regionId: string | null, metric: string | null) {
  return useQuery({
    queryKey: [ROOT, 'intel', 'lineage', regionId, metric],
    queryFn: () => intelligenceApi.lineage(regionId!, metric!),
    enabled: Boolean(regionId && metric),
  })
}

// ---------------------------------------------------------------------------
// Hazard events, hotspots, replay
// ---------------------------------------------------------------------------

export function useHazardEvents(status = 'open', hazard?: string) {
  const poll = usePollInterval()
  return useQuery({
    queryKey: [ROOT, 'intel', 'events', status, hazard ?? 'all'],
    queryFn: () => eventsApi.list(status, hazard),
    refetchInterval: poll > 0 ? poll : false,
  })
}

export function useEventTimeline(eventId: string | null) {
  return useQuery({
    queryKey: [ROOT, 'intel', 'event-timeline', eventId],
    queryFn: () => eventsApi.timeline(eventId!),
    enabled: Boolean(eventId),
  })
}

export function useEventImpact(eventId: string | null) {
  return useQuery({
    queryKey: [ROOT, 'intel', 'event-impact', eventId],
    queryFn: () => eventsApi.impact(eventId!),
    enabled: Boolean(eventId),
  })
}

export function useHotspots(hazard?: string) {
  const poll = usePollInterval()
  return useQuery({
    queryKey: [ROOT, 'intel', 'hotspots', hazard ?? 'all'],
    queryFn: () => eventsApi.hotspots(hazard),
    refetchInterval: poll > 0 ? poll : false,
  })
}

export function useReplay(opts: {
  hazard?: string
  region_id?: string
  start?: string
  end?: string
} = {}) {
  return useQuery({
    queryKey: [ROOT, 'intel', 'replay', JSON.stringify(opts)],
    queryFn: () => eventsApi.replay(opts),
    // Replay is a historical record; it does not change under the user.
    staleTime: 600_000,
  })
}
