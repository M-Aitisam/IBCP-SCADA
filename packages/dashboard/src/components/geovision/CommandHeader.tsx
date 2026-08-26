// packages/dashboard/src/components/geovision/CommandHeader.tsx
'use client'

import { RefreshCw, Satellite } from 'lucide-react'
import { useState } from 'react'

import { RANGE_LABELS, type RangeKey } from '@/services/api/geovisionApi'
import { useGeovisionFilters, windowLabel } from '@/store/geovisionFilters'
import { formatDate, formatDateTime, NO_VALUE, relativeDays } from './primitives'
import type { Freshness } from '@/services/api/geovisionApi'

const RANGES: RangeKey[] = ['7d', '30d', '3m', '6m', '1y', '5y', '10y']

/**
 * Command header: identity, live system state, time control, refresh.
 *
 * The freshness figures shown here are always the real observation timestamp
 * from the database, never the wall clock. The gap between the two is the
 * single most important thing an operator needs to see, so it is stated
 * explicitly rather than implied by a "live" badge.
 */
export default function CommandHeader({
  freshness,
  isRefreshing,
  onRefresh,
}: {
  freshness?: Freshness
  isRefreshing: boolean
  onRefresh: () => void
}) {
  const range = useGeovisionFilters((s) => s.range)
  const customStart = useGeovisionFilters((s) => s.customStart)
  const customEnd = useGeovisionFilters((s) => s.customEnd)
  const setRange = useGeovisionFilters((s) => s.setRange)
  const setCustomWindow = useGeovisionFilters((s) => s.setCustomWindow)
  const label = useGeovisionFilters(windowLabel)

  const [showCustom, setShowCustom] = useState(false)
  const isCustom = Boolean(customStart && customEnd)

  return (
    <header className="bg-white dark:bg-slate-900 border-b border-slate-200 dark:border-slate-800">
      <div className="max-w-[1600px] mx-auto px-4 py-3">
        <div className="flex flex-wrap items-start justify-between gap-4">
          <div className="flex items-start gap-3 min-w-0">
            <div className="mt-0.5 p-2 rounded bg-slate-900 dark:bg-slate-100">
              <Satellite
                className="w-5 h-5 text-white dark:text-slate-900"
                aria-hidden="true"
              />
            </div>
            <div className="min-w-0">
              <h1 className="text-lg font-semibold tracking-tight text-slate-900 dark:text-slate-100">
                GeoVision AI
              </h1>
              <p className="text-xs text-slate-500 dark:text-slate-400">
                Satellite &amp; Multi-Hazard Intelligence
              </p>
            </div>
          </div>

          <div className="flex flex-wrap items-center gap-4">
            <dl className="flex flex-wrap items-center gap-x-5 gap-y-1 text-xs">
              <div>
                <dt className="text-slate-500 dark:text-slate-400">
                  Latest observation
                </dt>
                <dd className="font-medium text-slate-900 dark:text-slate-100 tabular-nums">
                  {formatDate(freshness?.latest_observation)}
                  {freshness?.age_days !== null && freshness?.age_days !== undefined && (
                    <span className="ml-1.5 font-normal text-slate-500 dark:text-slate-400">
                      ({relativeDays(freshness.age_days)})
                    </span>
                  )}
                </dd>
              </div>
              <div>
                <dt className="text-slate-500 dark:text-slate-400">Last ingestion</dt>
                <dd className="font-medium text-slate-900 dark:text-slate-100 tabular-nums">
                  {freshness?.last_ingested_at
                    ? formatDateTime(freshness.last_ingested_at)
                    : NO_VALUE}
                </dd>
              </div>
            </dl>

            <button
              type="button"
              onClick={onRefresh}
              disabled={isRefreshing}
              className="inline-flex items-center gap-2 px-3 py-1.5 text-xs font-medium rounded border border-slate-300 dark:border-slate-600 text-slate-700 dark:text-slate-200 hover:bg-slate-50 dark:hover:bg-slate-800 disabled:opacity-50 focus:outline-none focus-visible:ring-2 focus-visible:ring-blue-500"
            >
              <RefreshCw
                className={`w-3.5 h-3.5 ${isRefreshing ? 'animate-spin' : ''}`}
                aria-hidden="true"
              />
              Refresh
            </button>
          </div>
        </div>

        {/* Time filter. Datasets have different native cadences, so the caption
            reminds the reader that a 7-day window may legitimately contain no
            observation from a 16-day composite. */}
        <div className="mt-3 flex flex-wrap items-center gap-2">
          <div
            className="inline-flex rounded border border-slate-300 dark:border-slate-700 overflow-hidden"
            role="group"
            aria-label="Time range"
          >
            {RANGES.map((key) => {
              const active = !isCustom && range === key
              return (
                <button
                  key={key}
                  type="button"
                  onClick={() => {
                    setRange(key)
                    setShowCustom(false)
                  }}
                  aria-pressed={active}
                  className={`px-2.5 py-1 text-xs font-medium border-r last:border-r-0 border-slate-300 dark:border-slate-700 focus:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-blue-500 ${
                    active
                      ? 'bg-slate-900 text-white dark:bg-slate-100 dark:text-slate-900'
                      : 'bg-white text-slate-600 hover:bg-slate-50 dark:bg-slate-900 dark:text-slate-300 dark:hover:bg-slate-800'
                  }`}
                >
                  {RANGE_LABELS[key]}
                </button>
              )
            })}
            <button
              type="button"
              onClick={() => setShowCustom((v) => !v)}
              aria-pressed={isCustom}
              aria-expanded={showCustom}
              className={`px-2.5 py-1 text-xs font-medium focus:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-blue-500 ${
                isCustom
                  ? 'bg-slate-900 text-white dark:bg-slate-100 dark:text-slate-900'
                  : 'bg-white text-slate-600 hover:bg-slate-50 dark:bg-slate-900 dark:text-slate-300 dark:hover:bg-slate-800'
              }`}
            >
              CUSTOM
            </button>
          </div>

          <span className="text-xs text-slate-500 dark:text-slate-400">{label}</span>

          {showCustom && (
            <div className="flex items-center gap-2">
              <label className="text-xs text-slate-500 dark:text-slate-400">
                <span className="sr-only">Custom range start</span>
                <input
                  type="date"
                  value={customStart ?? ''}
                  onChange={(e) =>
                    setCustomWindow(e.target.value || null, customEnd)
                  }
                  className="px-2 py-1 text-xs rounded border border-slate-300 dark:border-slate-600 bg-white dark:bg-slate-800 text-slate-900 dark:text-slate-100 focus:outline-none focus-visible:ring-2 focus-visible:ring-blue-500"
                />
              </label>
              <span className="text-xs text-slate-400" aria-hidden="true">
                →
              </span>
              <label className="text-xs text-slate-500 dark:text-slate-400">
                <span className="sr-only">Custom range end</span>
                <input
                  type="date"
                  value={customEnd ?? ''}
                  onChange={(e) =>
                    setCustomWindow(customStart, e.target.value || null)
                  }
                  className="px-2 py-1 text-xs rounded border border-slate-300 dark:border-slate-600 bg-white dark:bg-slate-800 text-slate-900 dark:text-slate-100 focus:outline-none focus-visible:ring-2 focus-visible:ring-blue-500"
                />
              </label>
            </div>
          )}
        </div>
      </div>
    </header>
  )
}
