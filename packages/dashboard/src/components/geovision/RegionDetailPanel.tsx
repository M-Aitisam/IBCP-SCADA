// packages/dashboard/src/components/geovision/RegionDetailPanel.tsx
'use client'

import { X } from 'lucide-react'

import { useRegionDetail } from '@/hooks/useGeovision'
import { useGeovisionFilters } from '@/store/geovisionFilters'
import type { RegionDetail, TrendResult } from '@/services/api/geovisionApi'
import {
  EmptyState,
  formatDate,
  formatValue,
  NO_VALUE,
  QueryBoundary,
  TrendIndicator,
} from './primitives'

const METRIC_ROWS: Array<{ key: string; label: string; unit: string; decimals: number }> = [
  { key: 'ndvi', label: 'NDVI', unit: '', decimals: 3 },
  { key: 'evi', label: 'EVI', unit: '', decimals: 3 },
  { key: 'rainfall_mm', label: 'Rainfall', unit: 'mm', decimals: 1 },
  { key: 'lst_day_c', label: 'LST (day)', unit: '°C', decimals: 1 },
  { key: 'lst_night_c', label: 'LST (night)', unit: '°C', decimals: 1 },
]

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div className="px-4 py-3 border-b border-slate-200 dark:border-slate-800">
      <h3 className="text-[10px] uppercase tracking-wider text-slate-500 dark:text-slate-400 mb-2">
        {title}
      </h3>
      {children}
    </div>
  )
}

function Row({
  label,
  value,
  trend,
  meta,
}: {
  label: string
  value: string
  trend?: TrendResult
  meta?: string
}) {
  return (
    <div className="flex items-start justify-between gap-3 py-1">
      <span className="text-xs text-slate-600 dark:text-slate-300">{label}</span>
      <span className="text-right min-w-0">
        <span className="block text-xs font-medium tabular-nums text-slate-900 dark:text-slate-100">
          {value}
        </span>
        {trend && (
          <TrendIndicator
            changePct={trend.change_pct}
            direction={trend.direction}
            interpretation={trend.interpretation}
            status={trend.status}
          />
        )}
        {meta && (
          <span className="block text-[10px] text-slate-400 dark:text-slate-500">
            {meta}
          </span>
        )}
      </span>
    </div>
  )
}

function Body({ detail }: { detail: RegionDetail }) {
  const region = detail.region
  if (!region) {
    return (
      <EmptyState
        title="No observations for this region"
        detail={detail.detail ?? 'The region exists in the boundary source but nothing has been ingested for it yet.'}
      />
    )
  }

  return (
    <>
      <Section title="Region">
        <div className="space-y-0.5">
          <div className="text-sm font-semibold text-slate-900 dark:text-slate-100">
            {region.name}
          </div>
          <dl className="text-[11px] text-slate-500 dark:text-slate-400 space-y-0.5">
            <div className="flex gap-2">
              <dt className="w-16 shrink-0">Province</dt>
              <dd>{region.province ?? NO_VALUE}</dd>
            </div>
            <div className="flex gap-2">
              <dt className="w-16 shrink-0">District</dt>
              <dd>{region.district ?? NO_VALUE}</dd>
            </div>
            <div className="flex gap-2">
              <dt className="w-16 shrink-0">Tehsil</dt>
              <dd>{region.tehsil ?? NO_VALUE}</dd>
            </div>
          </dl>
        </div>
      </Section>

      <Section title="Current health">
        {METRIC_ROWS.map((row) => {
          const metric = region.metrics[row.key as keyof typeof region.metrics]
          // Rainfall is shown as the window total, matching the map, because a
          // single day's depth says little about the region's state.
          const isRainfall = row.key === 'rainfall_mm'
          const value = isRainfall
            ? region.rainfall_window_total?.value ?? null
            : metric?.value ?? null
          const observed = isRainfall
            ? region.rainfall_window_total
              ? 'total over period'
              : undefined
            : metric
              ? `observed ${formatDate(metric.observation_date)}`
              : undefined

          return (
            <Row
              key={row.key}
              label={row.label}
              value={
                value === null ? NO_VALUE : formatValue(value, row.unit, row.decimals)
              }
              trend={detail.trends?.[row.key]}
              meta={observed}
            />
          )
        })}
        <Row
          label="Crop condition"
          value={region.crop_condition ?? NO_VALUE}
          meta="derived from NDVI — indicator, not a diagnosis"
        />
        <Row label="Vegetation status" value={region.vegetation_status ?? NO_VALUE} />
      </Section>

      <Section title="Data status">
        {detail.datasets.length === 0 ? (
          <p className="text-xs text-slate-500 dark:text-slate-400">
            No datasets have stored observations for this region.
          </p>
        ) : (
          detail.datasets.map((d) => (
            <Row
              key={d.dataset}
              label={d.dataset}
              value={formatDate(d.latest_observation)}
              meta={`${d.observations.toLocaleString()} obs`}
            />
          ))
        )}
      </Section>

      <Section title="Quality">
        {detail.datasets.map((d) => (
          <Row
            key={d.dataset}
            label={`${d.dataset} cloud`}
            value={
              d.mean_cloud_percentage === null
                ? 'n/a'
                : `${d.mean_cloud_percentage}%`
            }
            meta={
              d.mean_cloud_percentage === null
                ? 'not reported by this product'
                : 'mean scene cloud cover'
            }
          />
        ))}
        <p className="text-[10px] text-slate-400 dark:text-slate-500 mt-2">
          Cloud cover is a scene-level property. SAR and MODIS composites do not
          publish one, so “n/a” means not applicable rather than missing.
        </p>
      </Section>
    </>
  )
}

/**
 * Right-hand detail panel for the selected region.
 *
 * Rendered as a sidebar on wide screens and a full-width panel below the map on
 * narrow ones, so it stays usable on a tablet without a separate mobile view.
 */
export default function RegionDetailPanel() {
  const selectedRegionId = useGeovisionFilters((s) => s.selectedRegionId)
  const selectRegion = useGeovisionFilters((s) => s.selectRegion)
  const query = useRegionDetail(selectedRegionId)

  if (!selectedRegionId) {
    return (
      <aside className="bg-white dark:bg-slate-900 border border-slate-200 dark:border-slate-800 rounded-lg">
        <EmptyState
          title="No region selected"
          detail="Select a region on the map, or search for one, to see its full detail."
        />
      </aside>
    )
  }

  return (
    <aside className="bg-white dark:bg-slate-900 border border-slate-200 dark:border-slate-800 rounded-lg overflow-hidden flex flex-col max-h-[820px]">
      <header className="flex items-center justify-between gap-2 px-4 py-3 border-b border-slate-200 dark:border-slate-800">
        <h2 className="text-sm font-semibold text-slate-900 dark:text-slate-100">
          Region detail
        </h2>
        <button
          type="button"
          onClick={() => selectRegion(null)}
          aria-label="Close region detail"
          className="p-1 rounded text-slate-400 hover:text-slate-700 hover:bg-slate-100 dark:hover:text-slate-200 dark:hover:bg-slate-800 focus:outline-none focus-visible:ring-2 focus-visible:ring-blue-500"
        >
          <X className="w-4 h-4" aria-hidden="true" />
        </button>
      </header>

      <div className="overflow-y-auto">
        <QueryBoundary
          isLoading={query.isLoading}
          isError={query.isError}
          error={query.error}
          loadingLabel="Loading region"
          onRetry={() => query.refetch()}
        >
          {query.data && <Body detail={query.data} />}
        </QueryBoundary>
      </div>
    </aside>
  )
}
