// packages/dashboard/src/components/geovision/TrendPanel.tsx
'use client'

import { useMemo } from 'react'
import {
  Area,
  AreaChart,
  CartesianGrid,
  Line,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts'

import { useTrends } from '@/hooks/useGeovision'
import { useTheme } from '@/context/ThemeContext'
import type { MetricKey, SeriesResponse } from '@/services/api/geovisionApi'
import {
  formatDate,
  formatValue,
  NO_VALUE,
  Panel,
  QueryBoundary,
  ScrollX,
  TrendIndicator,
} from './primitives'

const CHART_METRICS: Array<{ metric: MetricKey; label: string; colour: string; decimals: number }> = [
  { metric: 'ndvi', label: 'NDVI', colour: '#16a34a', decimals: 3 },
  { metric: 'evi', label: 'EVI', colour: '#0d9488', decimals: 3 },
  { metric: 'rainfall_mm', label: 'Rainfall', colour: '#2563eb', decimals: 1 },
  { metric: 'lst_day_c', label: 'LST (day)', colour: '#ea580c', decimals: 1 },
]

function bucketLabel(bucket: string): string {
  return bucket === 'day' ? 'daily' : bucket === 'week' ? 'weekly' : 'monthly'
}

function TrendChart({
  metric,
  label,
  colour,
  decimals,
}: {
  metric: MetricKey
  label: string
  colour: string
  decimals: number
}) {
  const query = useTrends(metric)
  const { resolvedTheme } = useTheme()
  const isDark = resolvedTheme === 'dark'

  const data = query.data as SeriesResponse | undefined
  const points = useMemo(
    () =>
      (data?.series ?? []).map((p) => ({
        ...p,
        // Recharts needs a primitive for the axis; the ISO date sorts
        // correctly as a string so no parsing is needed.
        label: p.bucket_start,
      })),
    [data]
  )

  const axisColour = isDark ? '#64748b' : '#94a3b8'
  const gridColour = isDark ? '#1e293b' : '#e2e8f0'

  return (
    <Panel
      title={label}
      subtitle={
        data
          ? `${data.dataset} · ${bucketLabel(data.bucket)} · native cadence ${data.native_cadence ?? 'n/a'}`
          : undefined
      }
      actions={
        data?.trend && (
          <TrendIndicator
            changePct={data.trend.change_pct}
            direction={data.trend.direction}
            interpretation={data.trend.interpretation}
            status={data.trend.status}
          />
        )
      }
    >
      <QueryBoundary
        isLoading={query.isLoading}
        isError={query.isError}
        error={query.error}
        isEmpty={points.length === 0}
        loadingLabel={`Loading ${label}`}
        emptyTitle={`No ${label} observations`}
        emptyDetail="Nothing has been ingested for this metric in the selected period and area."
        onRetry={() => query.refetch()}
      >
        <>
          <div className="h-40">
            <ResponsiveContainer width="100%" height="100%">
              <AreaChart data={points} margin={{ top: 4, right: 4, left: -18, bottom: 0 }}>
                <defs>
                  <linearGradient id={`fill-${metric}`} x1="0" y1="0" x2="0" y2="1">
                    <stop offset="0%" stopColor={colour} stopOpacity={0.28} />
                    <stop offset="100%" stopColor={colour} stopOpacity={0.02} />
                  </linearGradient>
                </defs>
                <CartesianGrid stroke={gridColour} strokeDasharray="3 3" vertical={false} />
                <XAxis
                  dataKey="label"
                  tick={{ fontSize: 10, fill: axisColour }}
                  tickLine={false}
                  axisLine={{ stroke: gridColour }}
                  minTickGap={24}
                />
                <YAxis
                  tick={{ fontSize: 10, fill: axisColour }}
                  tickLine={false}
                  axisLine={false}
                  width={44}
                />
                <Tooltip
                  contentStyle={{
                    fontSize: 11,
                    borderRadius: 6,
                    border: `1px solid ${gridColour}`,
                    background: isDark ? '#0f172a' : '#ffffff',
                    color: isDark ? '#e2e8f0' : '#0f172a',
                  }}
                  labelFormatter={(v) => formatDate(String(v))}
                  formatter={(value: number | string, _name, item) => {
                    const p = item?.payload as { observations?: number; regions?: number }
                    return [
                      `${formatValue(Number(value), data?.unit ?? '', decimals)} (${p?.observations ?? 0} obs, ${p?.regions ?? 0} regions)`,
                      label,
                    ]
                  }}
                />
                <Area
                  type="monotone"
                  dataKey="value"
                  stroke={colour}
                  strokeWidth={1.8}
                  fill={`url(#fill-${metric})`}
                  // Gaps stay gaps. A satellite record with no observation in a
                  // bucket must not be bridged by a straight line, which would
                  // imply a measurement that was never made.
                  connectNulls={false}
                  dot={points.length <= 40 ? { r: 1.8, fill: colour } : false}
                  isAnimationActive={false}
                />
              </AreaChart>
            </ResponsiveContainer>
          </div>

          {/* Explicit current-vs-previous comparison (§42). */}
          {data?.trend && (
            <dl className="mt-3 grid grid-cols-3 gap-2 text-[11px] border-t border-line-subtle pt-2">
              <div>
                <dt className="text-content-subtle">Current</dt>
                <dd className="font-medium tabular-nums text-content">
                  {data.trend.current
                    ? formatValue(data.trend.current.value, data.unit, decimals)
                    : NO_VALUE}
                </dd>
              </div>
              <div>
                <dt className="text-content-subtle">Previous</dt>
                <dd className="font-medium tabular-nums text-content">
                  {data.trend.previous
                    ? formatValue(data.trend.previous.value, data.unit, decimals)
                    : NO_VALUE}
                </dd>
              </div>
              <div>
                <dt className="text-content-subtle">Change</dt>
                <dd>
                  {data.trend.status === 'insufficient_data' ? (
                    <span
                      className="text-content-subtle"
                      title={data.trend.reason}
                    >
                      Insufficient data
                    </span>
                  ) : (
                    <span className="font-medium tabular-nums text-content">
                      {data.trend.change_pct === null
                        ? NO_VALUE
                        : `${data.trend.change_pct > 0 ? '+' : ''}${data.trend.change_pct}%`}
                    </span>
                  )}
                </dd>
              </div>
            </dl>
          )}
        </>
      </QueryBoundary>
    </Panel>
  )
}

export default function TrendPanel() {
  return (
    <div className="grid grid-cols-1 lg:grid-cols-2 gap-3">
      {CHART_METRICS.map((c) => (
        <TrendChart key={c.metric} {...c} />
      ))}
    </div>
  )
}
