// packages/dashboard/src/store/geovisionFilters.ts
//
// One store for every GeoVision filter.
//
// The brief's requirement is that the map, KPIs, charts, tables and the region
// panel all respond consistently. The reliable way to get that is to give them
// a single source of truth rather than independent local state that has to be
// kept in step by hand — so every filtered component reads from here, and the
// query object handed to the API is derived, never assembled per component.
import { create } from 'zustand'

import type { GeovisionQuery, MetricKey, RangeKey } from '@/services/api/geovisionApi'

/** Layers the map can render. Only one is active at a time — see setLayer. */
export type MapLayer =
  | 'ndvi'
  | 'evi'
  | 'rainfall_mm'
  | 'lst_day_c'
  | 'lst_night_c'
  | 'crop_condition'

/**
 * How the choropleth is coloured.
 *
 * 'condition' is the operational view: green/yellow/orange/red severity.
 * 'value' is the raw measurement ramp, kept because severity is a judgement
 * and an analyst sometimes needs the number behind it.
 */
export type MapColourMode = 'condition' | 'value'

export interface GeovisionFilterState {
  range: RangeKey
  /** Custom window. When both are set they override `range`. */
  customStart: string | null
  customEnd: string | null

  province: string | null
  district: string | null
  tehsil: string | null
  /** The region whose detail panel is open, if any. */
  selectedRegionId: string | null

  mapLayer: MapLayer
  mapColourMode: MapColourMode
  trendMetric: MetricKey

  /** Milliseconds between background refreshes; 0 disables polling. */
  pollIntervalMs: number

  setRange: (range: RangeKey) => void
  setCustomWindow: (start: string | null, end: string | null) => void
  setProvince: (province: string | null) => void
  setDistrict: (district: string | null) => void
  setTehsil: (tehsil: string | null) => void
  selectRegion: (regionId: string | null) => void
  setMapLayer: (layer: MapLayer) => void
  setMapColourMode: (mode: MapColourMode) => void
  setTrendMetric: (metric: MetricKey) => void
  setPollInterval: (ms: number) => void
  reset: () => void
}

// 5 minutes. The pipeline writes at most once a night, so anything faster is
// load without information — the brief's "do not poll every second" rule.
export const DEFAULT_POLL_INTERVAL_MS = 300_000

const INITIAL = {
  range: '1y' as RangeKey,
  customStart: null,
  customEnd: null,
  province: null,
  district: null,
  tehsil: null,
  selectedRegionId: null,
  mapLayer: 'ndvi' as MapLayer,
  // Severity is what an operations map is for, so it is the default.
  mapColourMode: 'condition' as MapColourMode,
  trendMetric: 'ndvi' as MetricKey,
  pollIntervalMs: DEFAULT_POLL_INTERVAL_MS,
}

export const useGeovisionFilters = create<GeovisionFilterState>((set) => ({
  ...INITIAL,

  setRange: (range) =>
    // Choosing a named range clears any custom window, otherwise the custom
    // dates would keep winning and the button would look broken.
    set({ range, customStart: null, customEnd: null }),

  setCustomWindow: (customStart, customEnd) => set({ customStart, customEnd }),

  // The cascade: choosing a broader level clears the narrower ones, because a
  // district from the previous province is not a valid selection.
  setProvince: (province) =>
    set({ province, district: null, tehsil: null, selectedRegionId: null }),

  setDistrict: (district) => set({ district, tehsil: null, selectedRegionId: null }),

  setTehsil: (tehsil) => set({ tehsil, selectedRegionId: null }),

  selectRegion: (selectedRegionId) => set({ selectedRegionId }),

  setMapLayer: (mapLayer) => set({ mapLayer }),

  setMapColourMode: (mapColourMode) => set({ mapColourMode }),

  setTrendMetric: (trendMetric) => set({ trendMetric }),

  setPollInterval: (pollIntervalMs) => set({ pollIntervalMs }),

  reset: () => set({ ...INITIAL }),
}))

/**
 * The query object every data hook passes to the API.
 *
 * A custom window only counts when BOTH ends are present — a half-filled date
 * picker should keep showing the last valid range rather than silently
 * querying from the beginning of time.
 */
export function toQuery(state: GeovisionFilterState): GeovisionQuery {
  const hasCustom = Boolean(state.customStart && state.customEnd)
  return {
    ...(hasCustom
      ? { start: state.customStart!, end: state.customEnd! }
      : { range: state.range }),
    province: state.province,
    district: state.district,
    tehsil: state.tehsil,
  }
}

/** A stable string for cache keys, so identical filters share one request. */
export function queryKey(state: GeovisionFilterState): string {
  const q = toQuery(state)
  return JSON.stringify([
    q.range ?? null,
    q.start ?? null,
    q.end ?? null,
    q.province ?? null,
    q.district ?? null,
    q.tehsil ?? null,
  ])
}

/** Human-readable description of the active window, for headers and captions. */
export function windowLabel(state: GeovisionFilterState): string {
  if (state.customStart && state.customEnd) {
    return `${state.customStart} → ${state.customEnd}`
  }
  const labels: Record<RangeKey, string> = {
    '7d': 'Last 7 days',
    '30d': 'Last 30 days',
    '3m': 'Last 3 months',
    '6m': 'Last 6 months',
    '1y': 'Last 12 months',
    '5y': 'Last 5 years',
    '10y': 'Last 10 years',
  }
  return labels[state.range]
}
