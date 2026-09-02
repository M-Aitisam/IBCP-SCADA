// packages/dashboard/src/components/geovision/EventsPanel.tsx
'use client'

// Hazard events, hotspots and replay (§10-12, §21).
//
// The distinction this panel exists to make visible: an event is a persistent
// thing with a lifecycle, not a row per observation. A nine-day flood appears
// here once, with its trajectory — not ninety times.
import { Activity, Layers, Radio, Rewind } from 'lucide-react'
import { useState } from 'react'

import {
  useEventTimeline,
  useHazardEvents,
  useHotspots,
  useReplay,
} from '@/hooks/useGeovision'
import { useGeovisionFilters } from '@/store/geovisionFilters'
import type { EventStatus } from '@/services/api/geovisionApi'
import { formatDate, NO_VALUE, Panel, QueryBoundary, ScrollX } from './primitives'

/** Lifecycle colouring. Escalating is the operationally urgent state. */
const STATUS_STYLE: Record<string, string> = {
  DETECTED: 'bg-amber-50 text-amber-800 border-amber-200 dark:bg-amber-950 dark:text-amber-300 dark:border-amber-900',
  CONFIRMED: 'bg-orange-50 text-orange-800 border-orange-300 dark:bg-orange-950 dark:text-orange-300 dark:border-orange-900',
  ESCALATING: 'bg-rose-50 text-rose-700 border-rose-300 dark:bg-rose-950 dark:text-rose-300 dark:border-rose-900',
  PEAK: 'bg-rose-100 text-rose-800 border-rose-400 dark:bg-rose-900 dark:text-rose-200 dark:border-rose-700',
  DECLINING: 'bg-sky-50 text-sky-700 border-sky-200 dark:bg-sky-950 dark:text-sky-300 dark:border-sky-900',
  RESOLVED: 'bg-emerald-50 text-emerald-700 border-emerald-200 dark:bg-emerald-950 dark:text-emerald-300 dark:border-emerald-900',
}

function StatusBadge({ status }: { status: EventStatus | string }) {
  const style = STATUS_STYLE[status] ?? STATUS_STYLE.DETECTED
  return (
    <span
      className={`inline-flex items-center px-1.5 py-0.5 rounded border text-[10px] font-semibold tracking-wide whitespace-nowrap ${style}`}
    >
      {status}
    </span>
  )
}

const TH =
  'text-left px-3 py-2 font-medium text-[10px] uppercase tracking-wider text-content-subtle whitespace-nowrap'
const TD = 'px-3 py-2 text-xs text-content-muted whitespace-nowrap'

export default function EventsPanel() {
  const [scope, setScope] = useState<'open' | 'all'>('open')
  const [selected, setSelected] = useState<string | null>(null)
  const selectRegion = useGeovisionFilters((s) => s.selectRegion)

  const events = useHazardEvents(scope)
  const hotspots = useHotspots()
  const timeline = useEventTimeline(selected)
  const replay = useReplay()

  return (
    <div className="space-y-4">
      <Panel
        title="Hazard events"
        subtitle="One event per hazard per region — updated as observations arrive, not duplicated"
        bodyClassName=""
        actions={
          <div
            className="inline-flex rounded border border-line-strong overflow-hidden"
            role="group"
            aria-label="Event scope"
          >
            {(['open', 'all'] as const).map((key) => (
              <button
                key={key}
                type="button"
                onClick={() => setScope(key)}
                aria-pressed={scope === key}
                className={`px-2.5 py-1 text-xs font-medium border-r last:border-r-0 border-line-strong focus:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-brand ${
                  scope === key
                    ? 'bg-content text-content-inverse'
                    : 'bg-surface text-content-muted hover:bg-surface-sunken'
                }`}
              >
                {key === 'open' ? 'Open' : 'All'}
              </button>
            ))}
          </div>
        }
      >
        <QueryBoundary
          isLoading={events.isLoading}
          isError={events.isError}
          error={events.error}
          isEmpty={(events.data?.events.length ?? 0) === 0}
          loadingLabel="Loading events"
          emptyTitle="No hazard events"
          emptyDetail={
            events.data?.detail ??
            'No region has crossed a hazard onset threshold. Events open automatically once the analytics cascade scores one.'
          }
          onRetry={() => events.refetch()}
        >
          <ScrollX>
            <table className="w-full border-collapse">
              <caption className="sr-only">Hazard events</caption>
              <thead className="bg-canvas/50">
                <tr>
                  <th scope="col" className={TH}>Event</th>
                  <th scope="col" className={TH}>Hazard</th>
                  <th scope="col" className={TH}>Region</th>
                  <th scope="col" className={TH}>Status</th>
                  <th scope="col" className={`${TH} text-right`}>Score</th>
                  <th scope="col" className={`${TH} text-right`}>Peak</th>
                  <th scope="col" className={`${TH} text-right`}>Days</th>
                  <th scope="col" className={TH}>First detected</th>
                  <th scope="col" className={TH}>Verification</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-line-subtle">
                {(events.data?.events ?? []).map((e) => (
                  <tr
                    key={e.event_id}
                    onClick={() => {
                      setSelected(e.event_id)
                      selectRegion(e.region_id)
                    }}
                    className={`cursor-pointer ${
                      selected === e.event_id
                        ? 'bg-blue-50 dark:bg-blue-950/40'
                        : 'hover:bg-surface-sunken/50'
                    }`}
                  >
                    <td className={`${TD} font-mono text-[11px] text-content`}>
                      {e.event_id}
                    </td>
                    <td className={TD}>{e.hazard_type.replace('_', ' ')}</td>
                    <td className={TD}>{e.district ?? e.region_name ?? e.region_id}</td>
                    <td className={TD}><StatusBadge status={e.status} /></td>
                    <td className={`${TD} text-right tabular-nums`}>
                      {e.current_score === null ? NO_VALUE : e.current_score.toFixed(0)}
                    </td>
                    <td className={`${TD} text-right tabular-nums`}>
                      {e.peak_score === null ? NO_VALUE : e.peak_score.toFixed(0)}
                    </td>
                    <td className={`${TD} text-right tabular-nums`}>{e.duration_days}</td>
                    <td className={`${TD} tabular-nums text-content-subtle`}>
                      {formatDate(e.first_detected_at)}
                    </td>
                    <td className={`${TD} text-content-subtle`}>{e.verification_status}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </ScrollX>
        </QueryBoundary>
      </Panel>

      {/* Timeline for the selected event — the replay of one occurrence. */}
      {selected && (
        <Panel
          title={`Event timeline — ${selected}`}
          subtitle="What the system believed on each cycle, as recorded at the time"
        >
          <QueryBoundary
            isLoading={timeline.isLoading}
            isError={timeline.isError}
            error={timeline.error}
            isEmpty={(timeline.data?.count ?? 0) === 0}
            loadingLabel="Loading timeline"
            emptyTitle="No recorded cycles"
            onRetry={() => timeline.refetch()}
          >
            <ol className="space-y-1.5">
              {(timeline.data?.timeline ?? []).map((frame, i) => {
                const f = frame as Record<string, unknown>
                return (
                  <li key={i} className="flex items-center gap-3">
                    <span className="text-[11px] tabular-nums text-content-subtle w-24 shrink-0">
                      {formatDate(String(f.reference_date))}
                    </span>
                    <StatusBadge status={String(f.status)} />
                    <span className="text-xs tabular-nums text-content-muted w-14 text-right">
                      {f.score === null ? NO_VALUE : Number(f.score).toFixed(0)}
                    </span>
                    {f.transition ? (
                      <span className="text-[11px] text-content-subtle">
                        {String(f.transition)}
                      </span>
                    ) : null}
                  </li>
                )
              })}
            </ol>
          </QueryBoundary>
        </Panel>
      )}

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
        <Panel
          title="Hazard hotspots"
          subtitle="Contiguous groups of affected districts"
        >
          <QueryBoundary
            isLoading={hotspots.isLoading}
            isError={hotspots.isError}
            error={hotspots.error}
            isEmpty={(hotspots.data?.hotspots.length ?? 0) === 0}
            loadingLabel="Detecting hotspots"
            emptyTitle="No active hotspots"
            emptyDetail={
              hotspots.data?.detail ??
              'A hotspot needs at least 3 neighbouring districts above the severity threshold. Scattered stressed districts are separate local problems, not a hotspot.'
            }
            onRetry={() => hotspots.refetch()}
          >
            <ul className="space-y-2">
              {(hotspots.data?.hotspots ?? []).map((h) => (
                <li
                  key={h.cluster_id}
                  className="p-2.5 rounded border border-line"
                >
                  <div className="flex items-start justify-between gap-2">
                    <span className="text-xs font-medium text-content">
                      <Layers className="inline w-3 h-3 mr-1" aria-hidden="true" />
                      {h.hazard_type.replace('_', ' ')} · {h.cluster_size} districts
                    </span>
                    {h.growth_regions > 0 && (
                      <span className="text-[10px] text-rose-600 dark:text-rose-400">
                        +{h.growth_regions} since last cycle
                      </span>
                    )}
                  </div>
                  <p className="mt-1 text-[11px] text-content-muted">
                    {h.regions.map((r) => r.district ?? r.region_id).join(', ')}
                  </p>
                  <p className="mt-0.5 text-[10px] text-content-subtle">
                    avg severity {h.average_severity?.toFixed(0) ?? NO_VALUE} · max{' '}
                    {h.maximum_severity?.toFixed(0) ?? NO_VALUE} ·{' '}
                    {h.provinces.join(', ')}
                  </p>
                </li>
              ))}
            </ul>
          </QueryBoundary>
        </Panel>

        <Panel
          title="Historical replay"
          subtitle="Stored event state per past cycle — never a recomputation"
        >
          <QueryBoundary
            isLoading={replay.isLoading}
            isError={replay.isError}
            error={replay.error}
            isEmpty={(replay.data?.frame_count ?? 0) === 0}
            loadingLabel="Loading replay"
            emptyTitle="No event history"
            emptyDetail={
              replay.data?.detail ??
              'Replay reads what the system recorded on each past cycle. Once events exist, this steps through their evolution.'
            }
            onRetry={() => replay.refetch()}
          >
            <>
              <p className="text-[11px] text-content-subtle mb-2">
                <Rewind className="inline w-3 h-3 mr-1" aria-hidden="true" />
                {replay.data?.frame_count} frame(s) between{' '}
                {formatDate(replay.data?.period.start)} and{' '}
                {formatDate(replay.data?.period.end)}
              </p>
              <ol className="space-y-1">
                {(replay.data?.frames ?? []).slice(-12).map((frame) => (
                  <li key={frame.reference_date} className="flex items-start gap-2">
                    <span className="text-[11px] tabular-nums text-content-subtle w-24 shrink-0">
                      {formatDate(frame.reference_date)}
                    </span>
                    <span className="text-[11px] text-content-muted">
                      {frame.events.length} active event(s)
                      {frame.events.some((e) => e.transition) && (
                        <span className="text-content-subtle">
                          {' '}
                          · {frame.events.filter((e) => e.transition).length} transition(s)
                        </span>
                      )}
                    </span>
                  </li>
                ))}
              </ol>
            </>
          </QueryBoundary>
        </Panel>
      </div>
    </div>
  )
}
