// packages/dashboard/src/components/ui/States.tsx
//
// Loading, empty and error states.
//
// These matter more here than in most products. The satellite pipeline has
// genuine gaps — cloud cover, scene footprints that cover only part of the ROI,
// regions with fewer than three years of baseline — so "no data" is a normal,
// frequent, *correct* answer. It must be visibly distinct from "zero" and from
// "everything is fine", or the interface will quietly imply an all-clear over a
// district nobody has actually observed.
import type { ReactNode } from 'react'
import { cn } from '@/lib/cn'
import Button from './Button'

/** Shimmer block sized by the caller. Never shown for more than one screenful. */
export function Skeleton({ className }: { className?: string }) {
  return (
    <div
      aria-hidden="true"
      className={cn('animate-pulse rounded-sm bg-surface-sunken', className)}
    />
  )
}

export function PanelLoading({ rows = 3 }: { rows?: number }) {
  return (
    <div className="space-y-2 p-4" role="status" aria-label="Loading">
      {Array.from({ length: rows }).map((_, i) => (
        <Skeleton
          key={i}
          className="h-4"
          // Ragged widths read as text loading; equal bars read as a stalled
          // progress bar.
        />
      ))}
    </div>
  )
}

export interface EmptyStateProps {
  title: string
  /** Say *why* it is empty. "No alerts" and "not yet ingested" are different facts. */
  description?: ReactNode
  icon?: ReactNode
  action?: { label: string; onClick: () => void }
  className?: string
}

export function EmptyState({
  title,
  description,
  icon,
  action,
  className,
}: EmptyStateProps) {
  return (
    <div
      className={cn(
        'flex flex-col items-center justify-center gap-2 px-6 py-10 text-center',
        className
      )}
    >
      {icon && <div className="text-content-subtle">{icon}</div>}
      <p className="text-sm font-medium text-content">{title}</p>
      {description && (
        <p className="max-w-sm text-caption leading-relaxed text-content-subtle">
          {description}
        </p>
      )}
      {action && (
        <Button variant="secondary" size="sm" onClick={action.onClick} className="mt-1">
          {action.label}
        </Button>
      )}
    </div>
  )
}

export interface ErrorStateProps {
  title?: string
  /** The real message. Swallowing it makes field debugging impossible. */
  message?: string
  onRetry?: () => void
  className?: string
}

export function ErrorState({
  title = 'Could not load this panel',
  message,
  onRetry,
  className,
}: ErrorStateProps) {
  return (
    <div
      role="alert"
      className={cn(
        'flex flex-col items-center justify-center gap-2 px-6 py-10 text-center',
        className
      )}
    >
      <span
        aria-hidden="true"
        className="flex h-8 w-8 items-center justify-center rounded-full bg-sev-critical-soft text-sev-critical"
      >
        !
      </span>
      <p className="text-sm font-medium text-content">{title}</p>
      {message && (
        <p className="max-w-sm break-words text-caption leading-relaxed text-content-subtle">
          {message}
        </p>
      )}
      {onRetry && (
        <Button variant="secondary" size="sm" onClick={onRetry} className="mt-1">
          Retry
        </Button>
      )}
    </div>
  )
}

/**
 * The state that distinguishes this platform from a generic dashboard: the
 * request succeeded, but there is no observation to report. Grey, explicit, and
 * never rendered as a zero.
 */
export function NoDataState({
  reason,
  className,
}: {
  reason?: string
  className?: string
}) {
  return (
    <div
      className={cn(
        'flex flex-col items-center justify-center gap-1.5 px-6 py-10 text-center',
        className
      )}
    >
      <span className="rounded-sm border border-sev-unknown/30 bg-sev-unknown-soft px-1.5 py-0.5 text-micro font-semibold uppercase tracking-wider text-sev-unknown">
        No data
      </span>
      <p className="max-w-sm text-caption leading-relaxed text-content-subtle">
        {reason ??
          'No usable observations in the selected window. This is a gap in coverage, not a reading of zero.'}
      </p>
    </div>
  )
}
