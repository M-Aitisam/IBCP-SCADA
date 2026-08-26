// packages/dashboard/src/components/geovision/SystemStatusBar.tsx
'use client'

import {
  CheckCircle2,
  CircleSlash,
  Clock,
  Database,
  HardDrive,
  Loader2,
  Satellite,
  XCircle,
} from 'lucide-react'

import type { Overview, IngestionStatusResponse } from '@/services/api/geovisionApi'
import { relativeDays } from './primitives'

type Tone = 'ok' | 'warn' | 'bad' | 'idle'

const TONE_CLASS: Record<Tone, string> = {
  ok: 'text-emerald-600 dark:text-emerald-400',
  warn: 'text-amber-600 dark:text-amber-400',
  bad: 'text-rose-600 dark:text-rose-400',
  idle: 'text-slate-500 dark:text-slate-400',
}

const TONE_ICON: Record<Tone, typeof CheckCircle2> = {
  ok: CheckCircle2,
  warn: Clock,
  bad: XCircle,
  idle: CircleSlash,
}

function Item({
  icon: Icon,
  label,
  value,
  tone,
  detail,
}: {
  icon: typeof Database
  label: string
  value: string
  tone: Tone
  detail?: string
}) {
  const StatusIcon = TONE_ICON[tone]
  return (
    <div className="flex items-center gap-2 px-3 py-2">
      <Icon className="w-4 h-4 text-slate-400 dark:text-slate-500 shrink-0" aria-hidden="true" />
      <div className="min-w-0">
        <div className="text-[10px] uppercase tracking-wider text-slate-500 dark:text-slate-400">
          {label}
        </div>
        <div className={`flex items-center gap-1 text-xs font-semibold ${TONE_CLASS[tone]}`}>
          {/* Icon + word, so status never depends on colour alone. */}
          <StatusIcon className="w-3 h-3 shrink-0" aria-hidden="true" />
          <span className="truncate">{value}</span>
        </div>
        {detail && (
          <div className="text-[10px] text-slate-500 dark:text-slate-400 truncate">
            {detail}
          </div>
        )}
      </div>
    </div>
  )
}

/**
 * Operational status strip.
 *
 * The important judgement here: a dataset with no new observation is NOT a
 * system failure. A 16-day composite publishes nothing on most days, so
 * "NO NEW DATA" is reported as an ordinary state and only a genuine ingestion
 * failure turns anything red.
 */
export default function SystemStatusBar({
  overview,
  ingestion,
  isLoading,
  isError,
}: {
  overview?: Overview
  ingestion?: IngestionStatusResponse
  isLoading: boolean
  isError: boolean
}) {
  if (isLoading) {
    return (
      <div className="bg-slate-50 dark:bg-slate-950 border-b border-slate-200 dark:border-slate-800">
        <div className="max-w-[1600px] mx-auto px-4 py-2 flex items-center gap-2 text-xs text-slate-500 dark:text-slate-400">
          <Loader2 className="w-3.5 h-3.5 animate-spin" aria-hidden="true" />
          Checking system status…
        </div>
      </div>
    )
  }

  const states = ingestion?.datasets ?? []
  const failed = states.filter((d) => d.state === 'FAILED')
  const stale = states.filter((d) => d.state === 'NO NEW DATA')
  const empty = states.filter((d) => d.state === 'NO DATA')

  // GEE connectivity is inferred from whether ingestion has produced anything,
  // not asserted: the dashboard has no direct line to Earth Engine, and
  // claiming "CONNECTED" without evidence would be exactly the kind of
  // unsupported status this system is supposed to avoid.
  const hasObservations = (overview?.coverage.observations ?? 0) > 0
  const geeTone: Tone = isError ? 'bad' : hasObservations ? 'ok' : 'idle'
  const geeValue = isError
    ? 'UNKNOWN'
    : hasObservations
      ? 'DATA FLOWING'
      : 'NO DATA YET'

  const dbTone: Tone = isError ? 'bad' : 'ok'
  const dbValue = isError ? 'UNREACHABLE' : 'HEALTHY'

  let ingestionTone: Tone = 'ok'
  let ingestionValue = 'HEALTHY'
  let ingestionDetail: string | undefined
  if (failed.length > 0) {
    ingestionTone = 'bad'
    ingestionValue = 'FAILED'
    ingestionDetail = `${failed.map((d) => d.dataset).join(', ')}`
  } else if (empty.length === states.length && states.length > 0) {
    ingestionTone = 'idle'
    ingestionValue = 'NOT RUN'
  } else if (stale.length > 0) {
    ingestionTone = 'warn'
    ingestionValue = 'NO NEW OBSERVATION'
    ingestionDetail = `${stale.map((d) => d.dataset).join(', ')}`
  } else if (states.length > 0) {
    ingestionDetail = `${states.length} dataset(s) current`
  }

  const age = overview?.freshness.age_days ?? null
  const freshnessTone: Tone =
    age === null ? 'idle' : age <= 10 ? 'ok' : age <= 30 ? 'warn' : 'bad'

  return (
    <div className="bg-slate-50 dark:bg-slate-950 border-b border-slate-200 dark:border-slate-800">
      <div className="max-w-[1600px] mx-auto px-1 grid grid-cols-2 md:grid-cols-4 divide-x divide-slate-200 dark:divide-slate-800">
        <Item
          icon={Satellite}
          label="Earth Engine"
          value={geeValue}
          tone={geeTone}
          detail={
            hasObservations
              ? `${overview?.coverage.observations.toLocaleString()} observations stored`
              : 'pipeline has not stored data'
          }
        />
        <Item icon={Database} label="Database" value={dbValue} tone={dbTone} detail="timestampdb" />
        <Item
          icon={HardDrive}
          label="Ingestion"
          value={ingestionValue}
          tone={ingestionTone}
          detail={ingestionDetail}
        />
        <Item
          icon={Clock}
          label="Data freshness"
          value={age === null ? 'NO DATA' : 'LATEST AVAILABLE'}
          tone={freshnessTone}
          detail={age === null ? undefined : `observed ${relativeDays(age)}`}
        />
      </div>
    </div>
  )
}
