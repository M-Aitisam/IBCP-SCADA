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
    <header className="bg-surface border-b border-line">
      <div className="max-w-[1600px] mx-auto px-4 py-3">
        <div className="flex flex-wrap items-start justify-between gap-4">
          <div className="flex items-start gap-3 min-w-0">
            <div className="mt-0.5 p-2 rounded bg-content">
              <Satellite
                className="w-5 h-5 text-content-inverse"
                aria-hidden="true"
              />
            </div>
            <div className="min-w-0">
              <h1 className="text-lg font-semibold tracking-tight text-content">
                GeoVision AI
              </h1>
              <p className="text-xs text-content-subtle">
                Satellite &amp; Multi-Hazard Intelligence
              </p>
            </div>
          </div>

          <div className="flex flex-wrap items-center gap-4">
            <dl className="flex flex-wrap items-center gap-x-5 gap-y-1 text-xs">
              <div>
                <dt className="text-content-subtle">
                  Latest observation
                </dt>
                <dd className="font-medium text-content tabular-nums">
                  {formatDate(freshness?.latest_observation)}
                  {freshness?.age_days !== null && freshness?.age_days !== undefined && (
                    <span className="ml-1.5 font-normal text-content-subtle">
                      ({relativeDays(freshness.age_days)})
                    </span>
                  )}
                </dd>
              </div>
              <div>
                <dt className="text-content-subtle">Last ingestion</dt>
                <dd className="font-medium text-content tabular-nums">
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
              className="inline-flex items-center gap-2 px-3 py-1.5 text-xs font-medium rounded border border-line-strong text-content hover:bg-surface-sunken disabled:opacity-50 focus:outline-none focus-visible:ring-2 focus-visible:ring-brand"
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
            className="inline-flex rounded border border-line-strong overflow-hidden"
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
                  className={`px-2.5 py-1 text-xs font-medium border-r last:border-r-0 border-line-strong focus:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-brand ${
                    active
                      ? 'bg-content text-content-inverse'
                      : 'bg-surface text-content-muted hover:bg-surface-sunken'
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
              className={`px-2.5 py-1 text-xs font-medium focus:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-brand ${
                isCustom
                  ? 'bg-content text-content-inverse'
                  : 'bg-surface text-content-muted hover:bg-surface-sunken'
              }`}
            >
              CUSTOM
            </button>
          </div>

          <span className="text-xs text-content-subtle">{label}</span>

          {showCustom && (
            <div className="flex items-center gap-2">
              <label className="text-xs text-content-subtle">
                <span className="sr-only">Custom range start</span>
                <input
                  type="date"
                  value={customStart ?? ''}
                  onChange={(e) =>
                    setCustomWindow(e.target.value || null, customEnd)
                  }
                  className="px-2 py-1 text-xs rounded border border-line-strong bg-surface-raised text-content focus:outline-none focus-visible:ring-2 focus-visible:ring-brand"
                />
              </label>
              <span className="text-xs text-content-subtle" aria-hidden="true">
                →
              </span>
              <label className="text-xs text-content-subtle">
                <span className="sr-only">Custom range end</span>
                <input
                  type="date"
                  value={customEnd ?? ''}
                  onChange={(e) =>
                    setCustomWindow(customStart, e.target.value || null)
                  }
                  className="px-2 py-1 text-xs rounded border border-line-strong bg-surface-raised text-content focus:outline-none focus-visible:ring-2 focus-visible:ring-brand"
                />
              </label>
            </div>
          )}
        </div>
      </div>
    </header>
  )
}
