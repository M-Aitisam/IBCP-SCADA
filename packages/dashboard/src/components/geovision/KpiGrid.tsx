// packages/dashboard/src/components/geovision/KpiGrid.tsx
'use client'

import {
  CloudRain,
  Leaf,
  MapPin,
  Moon,
  Clock,
  Sprout,
  Thermometer,
} from 'lucide-react'

import type { Kpi, Overview } from '@/services/api/geovisionApi'
import {
  formatDate,
  formatValue,
  LoadingState,
  NO_VALUE,
  relativeDays,
  TrendIndicator,
} from './primitives'

/**
 * One KPI card.
 *
 * Every card carries value, unit, trend, the date the value was OBSERVED and
 * the dataset it came from. That provenance is not decoration: a number
 * without its observation date invites the reader to assume it is current,
 * which for an 8- or 16-day composite is usually wrong.
 */
function KpiCard({
  kpi,
  icon: Icon,
  decimals,
}: {
  kpi?: Kpi
  icon: typeof Leaf
  decimals?: number
}) {
  const unavailable = !kpi || kpi.status === 'no_data' || kpi.value === null

  return (
    <div className="bg-white dark:bg-slate-900 border border-slate-200 dark:border-slate-800 rounded-lg p-3">
      <div className="flex items-start justify-between gap-2">
        <div className="flex items-center gap-1.5 min-w-0">
          <Icon
            className="w-3.5 h-3.5 text-slate-400 dark:text-slate-500 shrink-0"
            aria-hidden="true"
          />
          <span className="text-[10px] uppercase tracking-wider text-slate-500 dark:text-slate-400 truncate">
            {kpi?.label ?? '—'}
          </span>
        </div>
        {kpi && !unavailable && (
          <TrendIndicator
            changePct={kpi.change_pct}
            direction={kpi.direction}
            interpretation={kpi.interpretation}
            status={kpi.trend_status}
          />
        )}
      </div>

      <div className="mt-2">
        {unavailable ? (
          <>
            <div className="text-xl font-semibold text-slate-300 dark:text-slate-600 tabular-nums">
              {NO_VALUE}
            </div>
            <div className="text-[11px] text-slate-500 dark:text-slate-400 mt-0.5">
              No observation available
            </div>
          </>
        ) : (
          <>
            <div className="text-xl font-semibold text-slate-900 dark:text-slate-100 tabular-nums">
              {formatValue(kpi.value, kpi.unit, decimals)}
            </div>
            <div className="text-[11px] text-slate-500 dark:text-slate-400 mt-0.5">
              Observed {formatDate(kpi.observation_date)}
            </div>
            <div className="text-[10px] text-slate-400 dark:text-slate-500 mt-0.5 truncate">
              {kpi.dataset}
              {kpi.aggregation === 'sum' && ' · total over period'}
              {kpi.regions > 0 && ` · ${kpi.regions} region(s)`}
            </div>
          </>
        )}
      </div>
    </div>
  )
}

/** A plain count/status card that is not a satellite measurement. */
function InfoCard({
  label,
  value,
  detail,
  icon: Icon,
}: {
  label: string
  value: string
  detail?: string
  icon: typeof MapPin
}) {
  return (
    <div className="bg-white dark:bg-slate-900 border border-slate-200 dark:border-slate-800 rounded-lg p-3">
      <div className="flex items-center gap-1.5">
        <Icon
          className="w-3.5 h-3.5 text-slate-400 dark:text-slate-500 shrink-0"
          aria-hidden="true"
        />
        <span className="text-[10px] uppercase tracking-wider text-slate-500 dark:text-slate-400 truncate">
          {label}
        </span>
      </div>
      <div className="mt-2">
        <div className="text-xl font-semibold text-slate-900 dark:text-slate-100 tabular-nums">
          {value}
        </div>
        {detail && (
          <div className="text-[11px] text-slate-500 dark:text-slate-400 mt-0.5">
            {detail}
          </div>
        )}
      </div>
    </div>
  )
}

export default function KpiGrid({
  overview,
  isLoading,
}: {
  overview?: Overview
  isLoading: boolean
}) {
  if (isLoading) return <LoadingState label="Loading indicators" />

  const kpis = overview?.kpis ?? {}
  const age = overview?.freshness.age_days ?? null

  return (
    <div className="grid grid-cols-2 md:grid-cols-4 xl:grid-cols-7 gap-3">
      <KpiCard kpi={kpis.ndvi} icon={Leaf} decimals={3} />
      <KpiCard kpi={kpis.evi} icon={Sprout} decimals={3} />
      <KpiCard kpi={kpis.rainfall_mm} icon={CloudRain} decimals={1} />
      <KpiCard kpi={kpis.lst_day_c} icon={Thermometer} decimals={1} />
      <KpiCard kpi={kpis.lst_night_c} icon={Moon} decimals={1} />
      {/* Crop condition is a classification derived from NDVI, so its card
          shows the class plus the NDVI it was derived from — never presented
          as an independent measurement. */}
      <KpiCard kpi={kpis.crop_condition} icon={Sprout} />
      <InfoCard
        label="Regions monitored"
        value={
          overview?.coverage.regions_monitored
            ? String(overview.coverage.regions_monitored)
            : NO_VALUE
        }
        detail={
          overview
            ? `${overview.coverage.provinces} province(s) · ${overview.coverage.observations.toLocaleString()} obs`
            : undefined
        }
        icon={MapPin}
      />
      <InfoCard
        label="Data freshness"
        value={age === null ? NO_VALUE : relativeDays(age)}
        detail={
          overview?.freshness.latest_observation
            ? `latest ${formatDate(overview.freshness.latest_observation)}`
            : 'no observations stored'
        }
        icon={Clock}
      />
    </div>
  )
}
