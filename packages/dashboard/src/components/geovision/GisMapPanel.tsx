// packages/dashboard/src/components/geovision/GisMapPanel.tsx
'use client'

import type { FeatureCollection } from 'geojson'
import dynamic from 'next/dynamic'
import { Layers, Map as MapIcon } from 'lucide-react'
import { useMemo } from 'react'

import { useRegionGeometry, useRegions } from '@/hooks/useGeovision'
import { useGeovisionFilters } from '@/store/geovisionFilters'
import {
  buildContinuousScale,
  CONDITION_COLOURS,
  CONDITION_LABELS,
  CONDITION_ORDER,
  getLayer,
  MAP_LAYERS,
  NO_DATA_COLOUR,
  paletteFor,
  type ConditionLevel,
} from './mapScales'
import {
  EmptyState,
  ErrorState,
  LoadingState,
  Panel,
  errorMessage,
} from './primitives'
import type { RegionSummary } from '@/services/api/geovisionApi'
import type { MapColourMode, MapLayer } from '@/store/geovisionFilters'

// Leaflet reads `window` during module evaluation, and this app is a static
// export, so the map must never be part of the server/prerender bundle.
const GisMap = dynamic(() => import('./GisMap'), {
  ssr: false,
  loading: () => <LoadingState label="Loading map" />,
})

function valueFor(region: RegionSummary, layer: MapLayer): number | null {
  const def = getLayer(layer)
  if (def.metric === 'rainfall_mm' && region.rainfall_window_total) {
    return region.rainfall_window_total.value
  }
  return region.metrics[def.metric]?.value ?? null
}

/**
 * Legend.
 *
 * Categorical layers list the exact thresholds the backend applied. Continuous
 * layers show the numeric range actually present, because there is no
 * climate-independent "high rainfall" or "hot" threshold to name — see
 * mapScales.ts for the full reasoning.
 */
function Legend({
  layer,
  regions,
  classification,
}: {
  layer: MapLayer
  regions: RegionSummary[]
  classification?: { ndvi: Array<{ min: number; label: string }>; crop_condition: Array<{ min: number; label: string }> }
}) {
  const def = getLayer(layer)

  const continuous = useMemo(() => {
    if (def.kind !== 'continuous') return null
    const values = regions
      .map((r) => valueFor(r, layer))
      .filter((v): v is number => v !== null)
    return buildContinuousScale(values, layer)
  }, [regions, layer, def.kind])

  const swatch = 'w-4 h-3 rounded-sm border border-black/10 shrink-0'

  if (def.kind === 'categorical') {
    const bands =
      layer === 'crop_condition' ? classification?.crop_condition : classification?.ndvi
    const palette = paletteFor(layer)
    if (!bands || bands.length === 0) return null

    return (
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1.5">
        {bands
          .slice()
          .reverse()
          .map((band, i) => (
            <span key={band.label} className="inline-flex items-center gap-1.5">
              <span
                className={swatch}
                style={{ backgroundColor: palette[i] ?? NO_DATA_COLOUR }}
                aria-hidden="true"
              />
              <span className="text-[11px] text-slate-600 dark:text-slate-300">
                {band.label}
                <span className="text-slate-400 dark:text-slate-500">
                  {' '}
                  {band.min <= -1 ? '' : `≥ ${band.min}`}
                </span>
              </span>
            </span>
          ))}
        <NoDataKey />
      </div>
    )
  }

  if (!continuous) {
    return (
      <div className="flex items-center gap-2 text-[11px] text-slate-500 dark:text-slate-400">
        Not enough variation across the selection to build a scale.
        <NoDataKey />
      </div>
    )
  }

  return (
    <div className="flex flex-wrap items-center gap-3">
      <div className="inline-flex items-center gap-1.5">
        <span className="text-[11px] tabular-nums text-slate-600 dark:text-slate-300">
          {continuous.min.toFixed(def.decimals)}
        </span>
        <span className="inline-flex" aria-hidden="true">
          {continuous.ramp.map((c) => (
            <span key={c} className={swatch} style={{ backgroundColor: c }} />
          ))}
        </span>
        <span className="text-[11px] tabular-nums text-slate-600 dark:text-slate-300">
          {continuous.max.toFixed(def.decimals)} {def.unit}
        </span>
      </div>
      <span className="text-[11px] text-slate-400 dark:text-slate-500">
        scaled across displayed regions
      </span>
      <NoDataKey />
    </div>
  )
}

/**
 * The traffic light.
 *
 * Renders the levels present in the current selection plus the rule that
 * produced them, because the rule differs by metric: vegetation indices are
 * classified from the reading, while rainfall and temperature are classified
 * from their departure from that region's own seasonal normal.
 */
function ConditionLegend({
  layer,
  regions,
  scale,
}: {
  layer: MapLayer
  regions: RegionSummary[]
  scale?: {
    anomaly_classified_metrics: string[]
    anomaly_thresholds: {
      watch_z: number
      stressed_z: number
      critical_z: number
      min_baseline_years: number
    }
  }
}) {
  const counts = useMemo(() => {
    const tally = new Map<ConditionLevel, number>()
    for (const region of regions) {
      const level = (region.conditions?.[layer]?.level ?? 'unknown') as ConditionLevel
      tally.set(level, (tally.get(level) ?? 0) + 1)
    }
    return tally
  }, [regions, layer])

  const isAnomalyBased = scale?.anomaly_classified_metrics?.includes(layer) ?? false
  const unknown = counts.get('unknown') ?? 0
  const swatch = 'w-4 h-3 rounded-sm border border-black/10 shrink-0'

  return (
    <div className="flex flex-col gap-1.5">
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1.5">
        {CONDITION_ORDER.map((level) => {
          const count = counts.get(level) ?? 0
          return (
            <span
              key={level}
              className={`inline-flex items-center gap-1.5 ${count === 0 ? 'opacity-40' : ''}`}
            >
              <span
                className={swatch}
                style={{ backgroundColor: CONDITION_COLOURS[level] }}
                aria-hidden="true"
              />
              <span className="text-[11px] text-slate-600 dark:text-slate-300">
                {CONDITION_LABELS[level]}
                {count > 0 && (
                  <span className="text-slate-400 dark:text-slate-500"> ({count})</span>
                )}
              </span>
            </span>
          )
        })}
      </div>

      <p className="text-[10px] text-slate-400 dark:text-slate-500 max-w-2xl">
        {isAnomalyBased ? (
          <>
            Coloured by departure from each region&rsquo;s own seasonal normal
            (|z| &ge; {scale?.anomaly_thresholds.watch_z} watch, &ge;{' '}
            {scale?.anomaly_thresholds.stressed_z} stressed, &ge;{' '}
            {scale?.anomaly_thresholds.critical_z} critical). There is no
            climate-independent &ldquo;too hot&rdquo; or &ldquo;too wet&rdquo;, so an
            absolute threshold would not be meaningful here.
          </>
        ) : (
          <>
            Coloured from the measured index using conventional vegetation
            vigour bands.
          </>
        )}
        {unknown > 0 && (
          <>
            {' '}
            <strong>{unknown} region(s) show grey</strong>
            {isAnomalyBased
              ? ` — fewer than ${scale?.anomaly_thresholds.min_baseline_years} prior years of this season are stored, so there is no basis to judge them yet. Run the historical backfill to populate it.`
              : ' — no observation in this period.'}
          </>
        )}
      </p>
    </div>
  )
}

function NoDataKey() {
  return (
    <span className="inline-flex items-center gap-1.5">
      <span
        className="w-4 h-3 rounded-sm border border-black/10 shrink-0"
        style={{ backgroundColor: NO_DATA_COLOUR }}
        aria-hidden="true"
      />
      <span className="text-[11px] text-slate-600 dark:text-slate-300">
        No observation
      </span>
    </span>
  )
}

export default function GisMapPanel() {
  const mapLayer = useGeovisionFilters((s) => s.mapLayer)
  const setMapLayer = useGeovisionFilters((s) => s.setMapLayer)
  const colourMode = useGeovisionFilters((s) => s.mapColourMode)
  const setColourMode = useGeovisionFilters((s) => s.setMapColourMode)

  const geometry = useRegionGeometry()
  const regionsQuery = useRegions()

  // Memoised: the `?? []` fallback allocates a new array each render, which
  // would invalidate the useMemo below every time.
  const regions = useMemo(
    () => regionsQuery.data?.regions ?? [],
    [regionsQuery.data]
  )
  const definition = getLayer(mapLayer)
  const conditionScale = regionsQuery.data?.condition_scale

  // Does the active layer have any condition to show?
  const assessed = useMemo(
    () =>
      regions.filter(
        (r) => (r.conditions?.[mapLayer]?.level ?? 'unknown') !== 'unknown'
      ).length,
    [regions, mapLayer]
  )

  // A condition map where nothing has been assessed is a flat grey rectangle —
  // technically honest, practically useless. Fall back to the measurement ramp
  // so the layer still says something, and let the legend explain why.
  const effectiveMode: MapColourMode =
    colourMode === 'condition' && regions.length > 0 && assessed === 0
      ? 'value'
      : colourMode

  const fellBack = effectiveMode !== colourMode

  return (
    <Panel
      title="GIS command map"
      subtitle={definition.description}
      bodyClassName=""
      actions={
        <div className="flex flex-wrap items-center gap-2">
          {/* Severity vs measurement. Severity is the operational default;
              the raw ramp stays one click away for an analyst. */}
          <div
            className="inline-flex rounded border border-slate-300 dark:border-slate-700 overflow-hidden"
            role="group"
            aria-label="Map colouring"
          >
            {(
              [
                ['condition', 'Condition'],
                ['value', 'Value'],
              ] as Array<[MapColourMode, string]>
            ).map(([mode, label]) => (
              <button
                key={mode}
                type="button"
                onClick={() => setColourMode(mode)}
                aria-pressed={colourMode === mode}
                className={`px-2 py-1 text-xs font-medium border-r last:border-r-0 border-slate-300 dark:border-slate-700 focus:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-blue-500 ${
                  colourMode === mode
                    ? 'bg-slate-900 text-white dark:bg-slate-100 dark:text-slate-900'
                    : 'bg-white text-slate-600 hover:bg-slate-50 dark:bg-slate-900 dark:text-slate-300 dark:hover:bg-slate-800'
                }`}
              >
                {label}
              </button>
            ))}
          </div>

          <Layers className="w-3.5 h-3.5 text-slate-400" aria-hidden="true" />
          <label className="sr-only" htmlFor="gv-map-layer">
            Map layer
          </label>
          <select
            id="gv-map-layer"
            value={mapLayer}
            onChange={(e) => setMapLayer(e.target.value as MapLayer)}
            className="px-2 py-1 text-xs rounded border border-slate-300 dark:border-slate-700 bg-white dark:bg-slate-900 text-slate-900 dark:text-slate-100 focus:outline-none focus-visible:ring-2 focus-visible:ring-blue-500"
          >
            {/* Only one thematic layer renders at a time. Painting several
                choropleths simultaneously would be unreadable and would make
                the map re-render every region on each toggle. */}
            {MAP_LAYERS.map((l) => (
              <option key={l.key} value={l.key}>
                {l.label}
              </option>
            ))}
          </select>
        </div>
      }
    >
      <div className="h-[420px] lg:h-[520px] relative border-b border-slate-200 dark:border-slate-800">
        {geometry.isLoading || regionsQuery.isLoading ? (
          <LoadingState label="Loading map" />
        ) : geometry.isError ? (
          <ErrorState
            title="Map boundaries unavailable"
            detail={errorMessage(geometry.error)}
            onRetry={() => geometry.refetch()}
          />
        ) : !geometry.data || geometry.data.features.length === 0 ? (
          <EmptyState
            icon={MapIcon}
            title="No region boundaries"
            detail="The configured region source returned no features."
          />
        ) : (
          <GisMap
            // The API type describes the same payload with narrower property
            // typing than the geojson package's FeatureCollection; the cast is
            // the structural bridge, not a suppression.
            geojson={geometry.data as unknown as FeatureCollection}
            regions={regions}
            classification={regionsQuery.data?.classification}
            colourMode={effectiveMode}
          />
        )}
      </div>

      <div className="px-4 py-2.5 flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          {effectiveMode === 'condition' ? (
            <ConditionLegend
              layer={mapLayer}
              regions={regions}
              scale={conditionScale}
            />
          ) : (
            <>
              <Legend
                layer={mapLayer}
                regions={regions}
                classification={regionsQuery.data?.classification}
              />
              {fellBack && (
                // Never silently show a different thing than the toggle says.
                <p className="text-[10px] text-amber-600 dark:text-amber-400 mt-1.5 max-w-2xl">
                  Showing measured values: no region has a seasonal baseline for{' '}
                  {definition.label} yet, so condition cannot be assessed. Run
                  the historical backfill to enable severity colouring for this
                  layer.
                </p>
              )}
            </>
          )}
        </div>
        {geometry.data && (
          <span className="text-[10px] text-slate-400 dark:text-slate-500 shrink-0">
            {geometry.data.attribution}
          </span>
        )}
      </div>
    </Panel>
  )
}
