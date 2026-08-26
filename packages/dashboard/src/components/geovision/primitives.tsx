// packages/dashboard/src/components/geovision/primitives.tsx
'use client'

// Shared building blocks for the command centre.
//
// Two rules are enforced here rather than left to each component:
//
//  - Every panel has an explicit loading / empty / error state. A blank area is
//    never an acceptable rendering, because the user cannot tell a slow request
//    from an empty database from a broken one.
//  - Status is never carried by colour alone. Each badge pairs its colour with
//    an icon and a word, so it survives greyscale and colour-blind vision.
import {
  AlertCircle,
  AlertTriangle,
  CheckCircle2,
  CircleSlash,
  Clock,
  Info,
  Loader2,
  Minus,
  TrendingDown,
  TrendingUp,
  XCircle,
} from 'lucide-react'
import type { ReactNode } from 'react'

import type { DatasetState, WatchPriority } from '@/services/api/geovisionApi'

// ---------------------------------------------------------------------------
// Layout
// ---------------------------------------------------------------------------

export function Panel({
  title,
  subtitle,
  actions,
  children,
  className = '',
  bodyClassName = '',
}: {
  title?: string
  subtitle?: ReactNode
  actions?: ReactNode
  children: ReactNode
  className?: string
  bodyClassName?: string
}) {
  return (
    <section
      className={`bg-white dark:bg-slate-900 border border-slate-200 dark:border-slate-800 rounded-lg shadow-sm ${className}`}
    >
      {(title || actions) && (
        <header className="flex items-start justify-between gap-3 px-4 py-3 border-b border-slate-200 dark:border-slate-800">
          <div className="min-w-0">
            {title && (
              <h2 className="text-sm font-semibold text-slate-900 dark:text-slate-100 tracking-tight">
                {title}
              </h2>
            )}
            {subtitle && (
              <p className="text-xs text-slate-500 dark:text-slate-400 mt-0.5">
                {subtitle}
              </p>
            )}
          </div>
          {actions && <div className="flex items-center gap-2 shrink-0">{actions}</div>}
        </header>
      )}
      <div className={bodyClassName || 'p-4'}>{children}</div>
    </section>
  )
}

/** Table/chart containers must scroll themselves; the page never scrolls sideways. */
export function ScrollX({ children }: { children: ReactNode }) {
  return <div className="overflow-x-auto">{children}</div>
}

// ---------------------------------------------------------------------------
// Loading / empty / error
// ---------------------------------------------------------------------------

export function LoadingState({ label = 'Loading' }: { label?: string }) {
  return (
    <div
      className="flex items-center justify-center gap-2 py-8 text-sm text-slate-500 dark:text-slate-400"
      role="status"
      aria-live="polite"
    >
      <Loader2 className="w-4 h-4 animate-spin" aria-hidden="true" />
      <span>{label}…</span>
    </div>
  )
}

export function EmptyState({
  title = 'No data available',
  detail,
  icon: Icon = CircleSlash,
}: {
  title?: string
  detail?: ReactNode
  icon?: typeof CircleSlash
}) {
  return (
    <div className="flex flex-col items-center justify-center py-8 px-4 text-center">
      <Icon className="w-7 h-7 text-slate-300 dark:text-slate-600 mb-2" aria-hidden="true" />
      <p className="text-sm font-medium text-slate-700 dark:text-slate-300">{title}</p>
      {detail && (
        <p className="text-xs text-slate-500 dark:text-slate-400 mt-1 max-w-md">{detail}</p>
      )}
    </div>
  )
}

export function ErrorState({
  title = 'Unable to load data',
  detail,
  onRetry,
}: {
  title?: string
  detail?: ReactNode
  onRetry?: () => void
}) {
  return (
    <div className="flex flex-col items-center justify-center py-8 px-4 text-center" role="alert">
      <AlertCircle className="w-7 h-7 text-rose-500 mb-2" aria-hidden="true" />
      <p className="text-sm font-medium text-slate-700 dark:text-slate-300">{title}</p>
      {detail && (
        <p className="text-xs text-slate-500 dark:text-slate-400 mt-1 max-w-md">{detail}</p>
      )}
      {onRetry && (
        <button
          type="button"
          onClick={onRetry}
          className="mt-3 px-3 py-1.5 text-xs font-medium rounded border border-slate-300 dark:border-slate-600 text-slate-700 dark:text-slate-200 hover:bg-slate-50 dark:hover:bg-slate-800 focus:outline-none focus-visible:ring-2 focus-visible:ring-blue-500"
        >
          Retry
        </button>
      )}
    </div>
  )
}

/**
 * Renders the right state for a query, or the children when data is usable.
 *
 * Centralising this is what guarantees no panel can silently render nothing:
 * a component either goes through here or has to make the same four decisions
 * itself, and this way they cannot drift apart.
 */
export function QueryBoundary({
  isLoading,
  isError,
  error,
  isEmpty,
  loadingLabel,
  emptyTitle,
  emptyDetail,
  onRetry,
  children,
}: {
  isLoading: boolean
  isError: boolean
  error?: unknown
  isEmpty?: boolean
  loadingLabel?: string
  emptyTitle?: string
  emptyDetail?: ReactNode
  onRetry?: () => void
  children: ReactNode
}) {
  if (isLoading) return <LoadingState label={loadingLabel} />
  if (isError) {
    return (
      <ErrorState
        detail={errorMessage(error)}
        onRetry={onRetry}
      />
    )
  }
  if (isEmpty) return <EmptyState title={emptyTitle} detail={emptyDetail} />
  return <>{children}</>
}

export function errorMessage(error: unknown): string {
  const err = error as {
    response?: { status?: number; data?: { detail?: unknown } }
    message?: string
  }
  const detail = err?.response?.data?.detail
  if (typeof detail === 'string') return detail
  if (err?.response?.status === 503) {
    return 'The data source is temporarily unavailable.'
  }
  if (!err?.response && err?.message) {
    return 'Cannot reach the server. Check that the backend is running.'
  }
  return err?.message ?? 'Unknown error'
}

// ---------------------------------------------------------------------------
// Status badges
// ---------------------------------------------------------------------------

const DATASET_STATE_STYLE: Record<
  DatasetState,
  { className: string; icon: typeof CheckCircle2 }
> = {
  HEALTHY: {
    className:
      'bg-emerald-50 text-emerald-700 border-emerald-200 dark:bg-emerald-950 dark:text-emerald-300 dark:border-emerald-900',
    icon: CheckCircle2,
  },
  WARNING: {
    className:
      'bg-amber-50 text-amber-700 border-amber-200 dark:bg-amber-950 dark:text-amber-300 dark:border-amber-900',
    icon: AlertTriangle,
  },
  // Deliberately neutral, not red: a 16-day composite has nothing new on 15
  // days out of 16, and painting that as a fault makes the panel meaningless.
  'NO NEW DATA': {
    className:
      'bg-slate-100 text-slate-700 border-slate-300 dark:bg-slate-800 dark:text-slate-300 dark:border-slate-700',
    icon: Clock,
  },
  'NO DATA': {
    className:
      'bg-slate-100 text-slate-600 border-slate-300 dark:bg-slate-800 dark:text-slate-400 dark:border-slate-700',
    icon: CircleSlash,
  },
  FAILED: {
    className:
      'bg-rose-50 text-rose-700 border-rose-200 dark:bg-rose-950 dark:text-rose-300 dark:border-rose-900',
    icon: XCircle,
  },
}

export function DatasetStateBadge({ state }: { state: DatasetState }) {
  const style = DATASET_STATE_STYLE[state] ?? DATASET_STATE_STYLE['NO DATA']
  const Icon = style.icon
  return (
    <span
      className={`inline-flex items-center gap-1.5 px-2 py-0.5 rounded border text-[11px] font-medium whitespace-nowrap ${style.className}`}
    >
      <Icon className="w-3 h-3" aria-hidden="true" />
      {state}
    </span>
  )
}

const PRIORITY_STYLE: Record<
  WatchPriority,
  { className: string; icon: typeof Info }
> = {
  INFO: {
    className:
      'bg-sky-50 text-sky-700 border-sky-200 dark:bg-sky-950 dark:text-sky-300 dark:border-sky-900',
    icon: Info,
  },
  WATCH: {
    className:
      'bg-amber-50 text-amber-700 border-amber-200 dark:bg-amber-950 dark:text-amber-300 dark:border-amber-900',
    icon: AlertTriangle,
  },
  HIGH: {
    className:
      'bg-orange-50 text-orange-700 border-orange-300 dark:bg-orange-950 dark:text-orange-300 dark:border-orange-900',
    icon: AlertTriangle,
  },
  CRITICAL: {
    className:
      'bg-rose-50 text-rose-700 border-rose-300 dark:bg-rose-950 dark:text-rose-300 dark:border-rose-900',
    icon: AlertCircle,
  },
}

export function PriorityBadge({ priority }: { priority: WatchPriority }) {
  const style = PRIORITY_STYLE[priority] ?? PRIORITY_STYLE.INFO
  const Icon = style.icon
  return (
    <span
      className={`inline-flex items-center gap-1.5 px-2 py-0.5 rounded border text-[11px] font-semibold tracking-wide ${style.className}`}
    >
      <Icon className="w-3 h-3" aria-hidden="true" />
      {priority}
    </span>
  )
}

// ---------------------------------------------------------------------------
// Condition badge
// ---------------------------------------------------------------------------

const CONDITION_STYLE: Record<
  string,
  { className: string; icon: typeof CheckCircle2; label: string }
> = {
  healthy: {
    className:
      'bg-emerald-50 text-emerald-700 border-emerald-200 dark:bg-emerald-950 dark:text-emerald-300 dark:border-emerald-900',
    icon: CheckCircle2,
    label: 'Healthy',
  },
  watch: {
    className:
      'bg-amber-50 text-amber-800 border-amber-200 dark:bg-amber-950 dark:text-amber-300 dark:border-amber-900',
    icon: AlertTriangle,
    label: 'Watch',
  },
  stressed: {
    className:
      'bg-orange-50 text-orange-800 border-orange-300 dark:bg-orange-950 dark:text-orange-300 dark:border-orange-900',
    icon: AlertTriangle,
    label: 'Stressed',
  },
  critical: {
    className:
      'bg-rose-50 text-rose-700 border-rose-300 dark:bg-rose-950 dark:text-rose-300 dark:border-rose-900',
    icon: AlertCircle,
    label: 'Critical',
  },
  unknown: {
    className:
      'bg-slate-100 text-slate-600 border-slate-300 dark:bg-slate-800 dark:text-slate-400 dark:border-slate-700',
    icon: CircleSlash,
    label: 'No basis',
  },
}

/**
 * Severity as a word plus an icon, not only a colour.
 *
 * The map communicates condition through fill colour, which is fine for
 * scanning but fails in greyscale and for colour-blind viewers. Everywhere the
 * condition appears outside the map it is spelled out.
 */
export function ConditionBadge({
  level,
  title,
}: {
  level?: string | null
  title?: string
}) {
  const style = CONDITION_STYLE[level ?? 'unknown'] ?? CONDITION_STYLE.unknown
  const Icon = style.icon
  return (
    <span
      title={title}
      className={`inline-flex items-center gap-1 px-1.5 py-0.5 rounded border text-[10px] font-medium whitespace-nowrap ${style.className}`}
    >
      <Icon className="w-2.5 h-2.5" aria-hidden="true" />
      {style.label}
    </span>
  )
}

// ---------------------------------------------------------------------------
// Trend indicator
// ---------------------------------------------------------------------------

/**
 * Direction plus its reading.
 *
 * "Up" is a fact; whether up is good depends on the metric, and for rainfall
 * and temperature there is no context-free answer — so the backend leaves
 * `interpretation` null and this renders a neutral colour rather than guessing.
 */
export function TrendIndicator({
  changePct,
  direction,
  interpretation,
  status,
}: {
  changePct: number | null
  direction: 'up' | 'down' | 'stable' | null
  interpretation?: 'improving' | 'declining' | null
  status?: 'ok' | 'insufficient_data'
}) {
  if (status === 'insufficient_data' || direction === null) {
    return (
      <span className="inline-flex items-center gap-1 text-[11px] text-slate-400 dark:text-slate-500">
        <Minus className="w-3 h-3" aria-hidden="true" />
        Insufficient data
      </span>
    )
  }

  const Icon =
    direction === 'up' ? TrendingUp : direction === 'down' ? TrendingDown : Minus

  const tone =
    interpretation === 'improving'
      ? 'text-emerald-600 dark:text-emerald-400'
      : interpretation === 'declining'
        ? 'text-rose-600 dark:text-rose-400'
        : 'text-slate-500 dark:text-slate-400'

  return (
    <span className={`inline-flex items-center gap-1 text-[11px] font-medium ${tone}`}>
      <Icon className="w-3 h-3" aria-hidden="true" />
      {changePct === null ? 'n/a' : `${changePct > 0 ? '+' : ''}${changePct}%`}
      {interpretation && <span className="sr-only"> ({interpretation})</span>}
    </span>
  )
}

// ---------------------------------------------------------------------------
// Formatting
// ---------------------------------------------------------------------------

/** Em-dash for absent values — never 0, which would read as a measurement. */
export const NO_VALUE = '—'

export function formatValue(
  value: number | string | null | undefined,
  unit?: string,
  decimals?: number
): string {
  if (value === null || value === undefined) return NO_VALUE
  if (typeof value === 'string') return value
  const shown =
    decimals !== undefined ? value.toFixed(decimals) : String(value)
  return unit && unit !== 'index' && unit !== 'class' ? `${shown} ${unit}` : shown
}

/** Short, unambiguous observation date. Always the real acquisition date. */
export function formatDate(iso: string | null | undefined): string {
  if (!iso) return NO_VALUE
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return iso
  return d.toLocaleDateString('en-GB', {
    day: '2-digit',
    month: 'short',
    year: 'numeric',
  })
}

export function formatDateTime(iso: string | null | undefined): string {
  if (!iso) return NO_VALUE
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return iso
  return d.toLocaleString('en-GB', {
    day: '2-digit',
    month: 'short',
    year: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
  })
}

export function relativeDays(days: number | null | undefined): string {
  if (days === null || days === undefined) return NO_VALUE
  if (days <= 0) return 'today'
  if (days === 1) return '1 day ago'
  return `${days} days ago`
}
