// packages/dashboard/src/components/geovision/SatelliteWatch.tsx
'use client'

import { Info, ShieldAlert } from 'lucide-react'

import { useAnomaly, useWatch } from '@/hooks/useGeovision'
import type { AnomalyResponse, MetricKey } from '@/services/api/geovisionApi'
import {
  formatValue,
  NO_VALUE,
  Panel,
  QueryBoundary,
  PriorityBadge,
} from './primitives'

const ANOMALY_METRICS: Array<{ metric: MetricKey; label: string; decimals: number }> = [
  { metric: 'ndvi', label: 'Vegetation (NDVI)', decimals: 3 },
  { metric: 'rainfall_mm', label: 'Rainfall', decimals: 1 },
  { metric: 'lst_day_c', label: 'Temperature (LST day)', decimals: 1 },
]

/**
 * One anomaly readout.
 *
 * Shows the OBSERVED value and the DERIVED departure as separate things, with
 * the baseline that produced it. When the record is too short the component
 * says so and shows no number — a z-score against one prior year would look
 * authoritative and mean nothing.
 */
function AnomalyRow({
  metric,
  label,
  decimals,
}: {
  metric: MetricKey
  label: string
  decimals: number
}) {
  const query = useAnomaly(metric)
  const data = query.data as AnomalyResponse | undefined

  return (
    <div className="py-2 border-b border-slate-100 dark:border-slate-800 last:border-b-0">
      <div className="flex items-start justify-between gap-3">
        <span className="text-xs font-medium text-slate-700 dark:text-slate-200">
          {label}
        </span>

        {query.isLoading ? (
          <span className="text-[11px] text-slate-400">Loading…</span>
        ) : query.isError || !data ? (
          <span className="text-[11px] text-slate-400">Unavailable</span>
        ) : data.status === 'insufficient_data' ? (
          <span
            className="text-[11px] text-slate-400 dark:text-slate-500 text-right max-w-[16rem]"
            title={data.reason}
          >
            Insufficient baseline
          </span>
        ) : (
          <span className="text-right">
            <span className="block text-xs font-semibold tabular-nums text-slate-900 dark:text-slate-100">
              {data.anomaly!.absolute > 0 ? '+' : ''}
              {formatValue(data.anomaly!.absolute, data.unit, decimals)}
            </span>
            <span className="block text-[10px] text-slate-500 dark:text-slate-400">
              z = {data.anomaly!.z_score ?? NO_VALUE}
            </span>
          </span>
        )}
      </div>

      {data?.status === 'ok' && (
        <div className="mt-1 text-[10px] text-slate-500 dark:text-slate-400">
          Observed {formatValue(data.observed?.value ?? null, data.unit, decimals)} vs
          baseline {formatValue(data.baseline!.value, data.unit, decimals)} over{' '}
          {data.baseline_years} prior year(s)
        </div>
      )}
      {data?.status === 'insufficient_data' && data.reason && (
        <div className="mt-1 text-[10px] text-slate-400 dark:text-slate-500">
          {data.reason}
        </div>
      )}
    </div>
  )
}

export default function SatelliteWatch() {
  const query = useWatch()
  const indicators = query.data?.indicators ?? []

  return (
    <div className="grid grid-cols-1 lg:grid-cols-2 gap-3">
      <Panel
        title="Satellite Watch"
        subtitle="Analytical indicators derived from stored observations"
      >
        {/* The disclaimer is placed above the indicators, not buried below
            them: the brief is explicit that these must never read as official
            disaster warnings. */}
        <div className="flex items-start gap-2 mb-3 p-2 rounded bg-slate-50 dark:bg-slate-800/60 border border-slate-200 dark:border-slate-700">
          <ShieldAlert
            className="w-3.5 h-3.5 text-slate-400 mt-0.5 shrink-0"
            aria-hidden="true"
          />
          <p className="text-[10px] leading-relaxed text-slate-600 dark:text-slate-300">
            {query.data?.disclaimer ??
              'These are satellite-derived analytical indicators produced by this system. They are not official disaster warnings and carry no authority from NDMA, PDMA or any government body.'}
          </p>
        </div>

        <QueryBoundary
          isLoading={query.isLoading}
          isError={query.isError}
          error={query.error}
          isEmpty={indicators.length === 0}
          loadingLabel="Evaluating indicators"
          emptyTitle="No indicators raised"
          emptyDetail="Nothing in the current selection crosses an indicator threshold, and data freshness is within the expected publication cycle for every dataset."
          onRetry={() => query.refetch()}
        >
          <ul className="space-y-2">
            {indicators.map((indicator, i) => (
              <li
                key={`${indicator.kind}-${indicator.title}-${i}`}
                className="p-2.5 rounded border border-slate-200 dark:border-slate-700"
              >
                <div className="flex items-start justify-between gap-2">
                  <span className="text-xs font-medium text-slate-900 dark:text-slate-100">
                    {indicator.title}
                  </span>
                  <PriorityBadge priority={indicator.priority} />
                </div>
                <p className="mt-1 text-[11px] text-slate-600 dark:text-slate-300">
                  {indicator.detail}
                </p>
                {indicator.rule && (
                  // The rule travels with the indicator so a reader can check
                  // the reasoning rather than trust a bare severity badge.
                  <p className="mt-1 text-[10px] text-slate-400 dark:text-slate-500">
                    Rule: {indicator.rule}
                  </p>
                )}
              </li>
            ))}
          </ul>
        </QueryBoundary>
      </Panel>

      <Panel
        title="Anomaly analysis"
        subtitle="Departure from the same season in prior years"
      >
        <div className="flex items-start gap-2 mb-2 text-[10px] text-slate-500 dark:text-slate-400">
          <Info className="w-3.5 h-3.5 shrink-0 mt-0.5" aria-hidden="true" />
          <p>
            Anomalies are <strong>derived</strong> values, not measurements. The
            baseline is the mean of the same calendar period in every earlier
            year on record, so a July value is compared against other Julys.
          </p>
        </div>
        {ANOMALY_METRICS.map((m) => (
          <AnomalyRow key={m.metric} {...m} />
        ))}
      </Panel>
    </div>
  )
}
