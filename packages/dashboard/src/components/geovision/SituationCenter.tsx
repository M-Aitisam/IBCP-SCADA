// packages/dashboard/src/components/geovision/SituationCenter.tsx
'use client'

// Phases 20/21/22 — the operational view.
//
// Answers, in the order an operator asks them:
//   what is the situation right now?      (brief headline + counters)
//   what changed since last cycle?        (deterioration/improvement)
//   which regions are worst?              (ranked hazard table)
//   what has been raised?                 (alerts)
//   is the data behind this trustworthy?  (freshness + pipeline)
//
// Every figure comes from a row the analytics cascade wrote. Where the cascade
// could not compute something the panel says so explicitly — the empty states
// here are load-bearing, because a fresh database legitimately has no hazards
// and pretending otherwise would be the worst thing this page could do.
import {
  Activity,
  AlertTriangle,
  ArrowDownRight,
  ArrowUpRight,
  ClipboardList,
  Database,
  ShieldAlert,
  TrendingUp,
} from 'lucide-react'
import { useMemo, useState } from 'react'

import {
  useAlerts,
  useBrief,
  useChanges,
  useDataFreshness,
  useHazards,
  usePipeline,
} from '@/hooks/useGeovision'
import { useGeovisionFilters } from '@/store/geovisionFilters'
import type { AlertSeverity, HazardKey, HazardLevel } from '@/services/api/geovisionApi'
import EventsPanel from './EventsPanel'
import {
  formatDate,
  formatDateTime,
  NO_VALUE,
  Panel,
  QueryBoundary,
  ScrollX,
} from './primitives'

const HAZARDS: Array<{ key: HazardKey; label: string }> = [
  { key: 'multi_hazard', label: 'Multi-hazard risk' },
  { key: 'drought', label: 'Drought' },
  { key: 'crop_stress', label: 'Crop stress' },
  { key: 'heat_stress', label: 'Heat stress' },
]

/** Severity colouring, paired with a word everywhere it is used. */
const LEVEL_STYLE: Record<string, string> = {
  CRITICAL: 'bg-rose-50 text-rose-700 border-rose-300 dark:bg-rose-950 dark:text-rose-300 dark:border-rose-900',
  EXTREME: 'bg-rose-50 text-rose-700 border-rose-300 dark:bg-rose-950 dark:text-rose-300 dark:border-rose-900',
  HIGH: 'bg-orange-50 text-orange-800 border-orange-300 dark:bg-orange-950 dark:text-orange-300 dark:border-orange-900',
  SEVERE: 'bg-orange-50 text-orange-800 border-orange-300 dark:bg-orange-950 dark:text-orange-300 dark:border-orange-900',
  WARNING: 'bg-orange-50 text-orange-800 border-orange-300 dark:bg-orange-950 dark:text-orange-300 dark:border-orange-900',
  MEDIUM: 'bg-amber-50 text-amber-800 border-amber-200 dark:bg-amber-950 dark:text-amber-300 dark:border-amber-900',
  MODERATE: 'bg-amber-50 text-amber-800 border-amber-200 dark:bg-amber-950 dark:text-amber-300 dark:border-amber-900',
  ADVISORY: 'bg-amber-50 text-amber-800 border-amber-200 dark:bg-amber-950 dark:text-amber-300 dark:border-amber-900',
  WATCH: 'bg-amber-50 text-amber-800 border-amber-200 dark:bg-amber-950 dark:text-amber-300 dark:border-amber-900',
  LOW: 'bg-emerald-50 text-emerald-700 border-emerald-200 dark:bg-emerald-950 dark:text-emerald-300 dark:border-emerald-900',
  NORMAL: 'bg-emerald-50 text-emerald-700 border-emerald-200 dark:bg-emerald-950 dark:text-emerald-300 dark:border-emerald-900',
  INSUFFICIENT_DATA:
    'bg-slate-100 text-slate-600 border-slate-300 dark:bg-slate-800 dark:text-slate-400 dark:border-slate-700',
}

function LevelBadge({ level }: { level: HazardLevel | AlertSeverity | string }) {
  const style = LEVEL_STYLE[level] ?? LEVEL_STYLE.INSUFFICIENT_DATA
  return (
    <span
      className={`inline-flex items-center px-1.5 py-0.5 rounded border text-[10px] font-semibold tracking-wide whitespace-nowrap ${style}`}
    >
      {level === 'INSUFFICIENT_DATA' ? 'NO DATA' : level}
    </span>
  )
}

function Counter({
  label,
  value,
  detail,
  icon: Icon,
  tone = 'neutral',
}: {
  label: string
  value: string | number
  detail?: string
  icon: typeof Activity
  tone?: 'neutral' | 'warn' | 'bad' | 'good'
}) {
  const toneClass = {
    neutral: 'text-slate-900 dark:text-slate-100',
    good: 'text-emerald-600 dark:text-emerald-400',
    warn: 'text-amber-600 dark:text-amber-400',
    bad: 'text-rose-600 dark:text-rose-400',
  }[tone]

  return (
    <div className="bg-white dark:bg-slate-900 border border-slate-200 dark:border-slate-800 rounded-lg p-3">
      <div className="flex items-center gap-1.5">
        <Icon className="w-3.5 h-3.5 text-slate-400 shrink-0" aria-hidden="true" />
        <span className="text-[10px] uppercase tracking-wider text-slate-500 dark:text-slate-400 truncate">
          {label}
        </span>
      </div>
      <div className={`mt-1.5 text-xl font-semibold tabular-nums ${toneClass}`}>{value}</div>
      {detail && (
        <div className="text-[11px] text-slate-500 dark:text-slate-400 mt-0.5">{detail}</div>
      )}
    </div>
  )
}

const TH =
  'text-left px-3 py-2 font-medium text-[10px] uppercase tracking-wider text-slate-500 dark:text-slate-400 whitespace-nowrap'
const TD = 'px-3 py-2 text-xs text-slate-700 dark:text-slate-300 whitespace-nowrap'

export default function SituationCenter() {
  const [hazard, setHazard] = useState<HazardKey>('multi_hazard')
  const selectRegion = useGeovisionFilters((s) => s.selectRegion)

  const brief = useBrief()
  const changes = useChanges()
  const alerts = useAlerts('active')
  const hazards = useHazards(hazard)
  const freshness = useDataFreshness()
  const pipeline = usePipeline(1)

  const b = brief.data?.brief
  const scored = useMemo(
    () => (hazards.data?.regions ?? []).filter((r) => r.score !== null),
    [hazards.data]
  )
  const unscored = (hazards.data?.regions.length ?? 0) - scored.length
  const lastRun = pipeline.data?.runs?.[0]

  return (
    <div className="space-y-4">
      {/* --- headline --------------------------------------------------- */}
      <Panel
        title="Situation summary"
        subtitle={
          b
            ? `Generated ${formatDateTime(b.generated_at)} · cycle ${b.brief_date}`
            : 'No brief generated yet'
        }
      >
        <QueryBoundary
          isLoading={brief.isLoading}
          isError={brief.isError}
          error={brief.error}
          isEmpty={!b}
          loadingLabel="Loading brief"
          emptyTitle="No daily brief yet"
          emptyDetail="Run the analytics cascade to generate one: python -m app.ingestion.cli analytics"
          onRetry={() => brief.refetch()}
        >
          <>
            <p className="text-sm text-slate-800 dark:text-slate-200">{b?.headline}</p>
            <div className="grid grid-cols-2 md:grid-cols-3 xl:grid-cols-6 gap-3 mt-3">
              <Counter
                label="Active alerts"
                value={b?.active_alerts ?? 0}
                detail={`${b?.new_alerts ?? 0} new this cycle`}
                icon={ShieldAlert}
                tone={(b?.active_alerts ?? 0) > 0 ? 'warn' : 'good'}
              />
              <Counter
                label="Critical regions"
                value={b?.critical_regions ?? 0}
                icon={AlertTriangle}
                tone={(b?.critical_regions ?? 0) > 0 ? 'bad' : 'good'}
              />
              <Counter
                label="Deteriorating"
                value={b?.deteriorating_regions ?? 0}
                detail="since last cycle"
                icon={ArrowUpRight}
                tone={(b?.deteriorating_regions ?? 0) > 0 ? 'warn' : 'neutral'}
              />
              <Counter
                label="Improving"
                value={b?.improving_regions ?? 0}
                detail="since last cycle"
                icon={ArrowDownRight}
                tone="good"
              />
              <Counter
                label="Datasets healthy"
                value={`${b?.datasets_healthy ?? 0}/${(b?.datasets_healthy ?? 0) + (b?.datasets_degraded ?? 0)}`}
                icon={Database}
                tone={(b?.datasets_degraded ?? 0) > 0 ? 'warn' : 'good'}
              />
              <Counter
                label="Last pipeline run"
                value={lastRun?.status ?? NO_VALUE}
                detail={lastRun ? `${lastRun.stages.length} stages` : undefined}
                icon={Activity}
                tone={
                  lastRun?.status === 'success'
                    ? 'good'
                    : lastRun?.status === 'failed'
                      ? 'bad'
                      : 'warn'
                }
              />
            </div>
          </>
        </QueryBoundary>
      </Panel>

      {/* --- what changed (Phase 21) ------------------------------------ */}
      <Panel
        title="What changed"
        subtitle={
          changes.data?.previous_date
            ? `Compared against the cycle of ${changes.data.previous_date}`
            : 'Needs a previous cycle to compare against'
        }
      >
        <QueryBoundary
          isLoading={changes.isLoading}
          isError={changes.isError}
          error={changes.error}
          isEmpty={
            !changes.data ||
            ((changes.data.deteriorating?.length ?? 0) === 0 &&
              (changes.data.improving?.length ?? 0) === 0)
          }
          loadingLabel="Comparing cycles"
          emptyTitle="No significant movement"
          emptyDetail={
            changes.data?.note ??
            'No region moved more than 5 risk points since the previous cycle. Smaller moves are within scoring noise and are not reported.'
          }
          onRetry={() => changes.refetch()}
        >
          <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
            {(
              [
                ['Deteriorated', changes.data?.deteriorating ?? [], 'text-rose-600 dark:text-rose-400'],
                ['Improved', changes.data?.improving ?? [], 'text-emerald-600 dark:text-emerald-400'],
              ] as const
            ).map(([title, items, tone]) => (
              <div key={title}>
                <h3 className="text-[10px] uppercase tracking-wider text-slate-500 dark:text-slate-400 mb-1.5">
                  {title} ({items.length})
                </h3>
                {items.length === 0 ? (
                  <p className="text-xs text-slate-400">None</p>
                ) : (
                  <ul className="space-y-1">
                    {items.map((item) => {
                      const record = item as Record<string, unknown>
                      const id = String(record.region_id)
                      return (
                        <li key={id}>
                          <button
                            type="button"
                            onClick={() => selectRegion(id)}
                            className="w-full text-left flex items-center justify-between gap-2 px-2 py-1 rounded hover:bg-slate-50 dark:hover:bg-slate-800 focus:outline-none focus-visible:ring-2 focus-visible:ring-blue-500"
                          >
                            <span className="text-xs text-slate-800 dark:text-slate-200 truncate">
                              {String(record.district ?? record.name ?? id)}
                            </span>
                            <span className={`text-xs font-medium tabular-nums ${tone}`}>
                              {Number(record.delta) > 0 ? '+' : ''}
                              {String(record.delta)} pts
                            </span>
                          </button>
                        </li>
                      )
                    })}
                  </ul>
                )}
              </div>
            ))}
          </div>
        </QueryBoundary>
      </Panel>

      {/* --- hazard events, hotspots, replay ----------------------------- */}
      <EventsPanel />

      {/* --- regional ranking ------------------------------------------- */}
      <Panel
        title="Regional hazard ranking"
        subtitle={
          hazards.data?.reference_date
            ? `Cycle ${hazards.data.reference_date} · ${scored.length} scored, ${unscored} without sufficient data`
            : undefined
        }
        bodyClassName=""
        actions={
          <>
            <label className="sr-only" htmlFor="sc-hazard">
              Hazard
            </label>
            <select
              id="sc-hazard"
              value={hazard}
              onChange={(e) => setHazard(e.target.value as HazardKey)}
              className="px-2 py-1 text-xs rounded border border-slate-300 dark:border-slate-700 bg-white dark:bg-slate-900 text-slate-900 dark:text-slate-100 focus:outline-none focus-visible:ring-2 focus-visible:ring-blue-500"
            >
              {HAZARDS.map((h) => (
                <option key={h.key} value={h.key}>
                  {h.label}
                </option>
              ))}
            </select>
          </>
        }
      >
        <QueryBoundary
          isLoading={hazards.isLoading}
          isError={hazards.isError}
          error={hazards.error}
          isEmpty={(hazards.data?.regions.length ?? 0) === 0}
          loadingLabel="Loading hazard scores"
          emptyTitle="No hazard scores computed"
          emptyDetail="The analytics cascade has not produced scores yet."
          onRetry={() => hazards.refetch()}
        >
          <>
            {scored.length === 0 && (
              // The honest explanation, not a blank table: scoring needs
              // multi-year baselines, and a database without history cannot
              // produce them.
              <p className="px-4 py-3 text-xs text-amber-700 dark:text-amber-400 bg-amber-50 dark:bg-amber-950/40 border-b border-amber-200 dark:border-amber-900">
                No region could be scored this cycle. Hazard scoring compares each
                region against its own seasonal baseline, which needs several
                years of history. Run the historical backfill to enable it.
              </p>
            )}
            <ScrollX>
              <table className="w-full border-collapse">
                <caption className="sr-only">Regions ranked by hazard score</caption>
                <thead className="bg-slate-50 dark:bg-slate-950/50">
                  <tr>
                    <th scope="col" className={TH}>Region</th>
                    <th scope="col" className={TH}>Province</th>
                    <th scope="col" className={`${TH} text-right`}>Score</th>
                    <th scope="col" className={TH}>Level</th>
                    <th scope="col" className={`${TH} text-right`}>Confidence</th>
                    <th scope="col" className={TH}>Primary driver</th>
                    <th scope="col" className={TH}>Held</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-slate-100 dark:divide-slate-800">
                  {(hazards.data?.regions ?? []).map((r) => (
                    <tr
                      key={r.region_id}
                      onClick={() => selectRegion(r.region_id)}
                      className="cursor-pointer hover:bg-slate-50 dark:hover:bg-slate-800/50"
                    >
                      <td className={`${TD} font-medium text-slate-900 dark:text-slate-100`}>
                        {r.district ?? r.name ?? r.region_id}
                      </td>
                      <td className={TD}>{r.province ?? NO_VALUE}</td>
                      <td className={`${TD} text-right tabular-nums`}>
                        {r.score === null ? NO_VALUE : r.score.toFixed(0)}
                      </td>
                      <td className={TD}>
                        <LevelBadge level={r.level} />
                      </td>
                      <td className={`${TD} text-right tabular-nums`}>
                        {r.confidence === null ? NO_VALUE : `${(r.confidence * 100).toFixed(0)}%`}
                      </td>
                      <td className={`${TD} max-w-[14rem] truncate`}>
                        {r.primary_driver ?? NO_VALUE}
                      </td>
                      <td className={`${TD} tabular-nums text-slate-500`}>
                        {r.consecutive_periods > 1 ? `${r.consecutive_periods} cycles` : '—'}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </ScrollX>
          </>
        </QueryBoundary>
      </Panel>

      {/* --- alerts ------------------------------------------------------ */}
      <Panel
        title="Active indicators"
        subtitle="Satellite-derived early-warning indicators — not official warnings"
      >
        <div className="flex items-start gap-2 mb-3 p-2 rounded bg-slate-50 dark:bg-slate-800/60 border border-slate-200 dark:border-slate-700">
          <ShieldAlert className="w-3.5 h-3.5 text-slate-400 mt-0.5 shrink-0" aria-hidden="true" />
          <p className="text-[10px] leading-relaxed text-slate-600 dark:text-slate-300">
            {alerts.data?.disclaimer ??
              'Satellite-derived analytical indicators produced by this system. Not official disaster warnings and carrying no authority from NDMA, PDMA or any government body.'}
          </p>
        </div>
        <QueryBoundary
          isLoading={alerts.isLoading}
          isError={alerts.isError}
          error={alerts.error}
          isEmpty={(alerts.data?.alerts.length ?? 0) === 0}
          loadingLabel="Loading indicators"
          emptyTitle="No active indicators"
          emptyDetail="No region currently crosses an alerting threshold with sufficient confidence and persistence."
          onRetry={() => alerts.refetch()}
        >
          <ul className="space-y-2">
            {(alerts.data?.alerts ?? []).map((a) => (
              <li
                key={a.alert_id}
                className="p-2.5 rounded border border-slate-200 dark:border-slate-700"
              >
                <div className="flex items-start justify-between gap-2">
                  <span className="text-xs font-medium text-slate-900 dark:text-slate-100">
                    {a.hazard.replace('_', ' ')} · {a.region_name ?? a.region_id}
                  </span>
                  <LevelBadge level={a.severity} />
                </div>
                <p className="mt-1 text-[11px] text-slate-600 dark:text-slate-300">{a.reason}</p>
                <p className="mt-1 text-[10px] text-slate-400">
                  {a.alert_id} · first detected {formatDate(a.first_detected)} ·
                  {' '}seen {a.occurrence_count}× · rule {a.rule_id ?? NO_VALUE}
                </p>
              </li>
            ))}
          </ul>
        </QueryBoundary>
      </Panel>

      {/* --- data health -------------------------------------------------- */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
        <Panel title="Data freshness" subtitle="Lag against what the source has published">
          <QueryBoundary
            isLoading={freshness.isLoading}
            isError={freshness.isError}
            error={freshness.error}
            isEmpty={(freshness.data?.datasets.length ?? 0) === 0}
            loadingLabel="Checking freshness"
            emptyTitle="No datasets registered"
            onRetry={() => freshness.refetch()}
          >
            <ul className="space-y-1.5">
              {(freshness.data?.datasets ?? []).map((d) => (
                <li key={d.dataset_id} className="flex items-start justify-between gap-3">
                  <div className="min-w-0">
                    <span className="text-xs font-medium text-slate-800 dark:text-slate-200">
                      {d.dataset_id}
                    </span>
                    <span className="block text-[10px] text-slate-500 dark:text-slate-400 truncate">
                      {d.detail}
                    </span>
                  </div>
                  <div className="text-right shrink-0">
                    <LevelBadge level={d.state === 'FRESH' ? 'NORMAL' : d.state} />
                    <span className="block text-[10px] text-slate-400 mt-0.5 tabular-nums">
                      latest {formatDate(d.latest_observation)}
                    </span>
                  </div>
                </li>
              ))}
            </ul>
          </QueryBoundary>
        </Panel>

        <Panel
          title="Pipeline"
          subtitle={lastRun ? `Last run ${lastRun.run_id}` : 'No cycle recorded yet'}
        >
          <QueryBoundary
            isLoading={pipeline.isLoading}
            isError={pipeline.isError}
            error={pipeline.error}
            isEmpty={!lastRun}
            loadingLabel="Loading pipeline"
            emptyTitle="No analytics cycle recorded"
            emptyDetail="Run: python -m app.ingestion.cli analytics"
            onRetry={() => pipeline.refetch()}
          >
            <ol className="space-y-1">
              {(lastRun?.stages ?? []).map((stage) => (
                <li key={stage.stage} className="flex items-center justify-between gap-3">
                  <span className="flex items-center gap-2 min-w-0">
                    <span
                      className={`w-1.5 h-1.5 rounded-full shrink-0 ${
                        stage.status === 'success'
                          ? 'bg-emerald-500'
                          : stage.status === 'partial'
                            ? 'bg-amber-500'
                            : stage.status === 'failed'
                              ? 'bg-rose-500'
                              : 'bg-slate-300'
                      }`}
                      aria-hidden="true"
                    />
                    <span className="text-xs text-slate-800 dark:text-slate-200 truncate">
                      {stage.stage}
                    </span>
                    {/* Status as a word too, never colour alone. */}
                    <span className="text-[10px] text-slate-400">{stage.status}</span>
                  </span>
                  <span className="text-[10px] text-slate-500 dark:text-slate-400 tabular-nums shrink-0">
                    {stage.duration_ms ?? 0}ms · {stage.records_out} out
                  </span>
                </li>
              ))}
            </ol>
            {lastRun?.stages.some((s) => s.error) && (
              <p className="mt-2 text-[10px] text-rose-600 dark:text-rose-400">
                {lastRun.stages.find((s) => s.error)?.error}
              </p>
            )}
          </QueryBoundary>
        </Panel>
      </div>
    </div>
  )
}
