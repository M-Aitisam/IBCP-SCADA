// packages/dashboard/src/components/ui/Metric.tsx
import type { ReactNode } from 'react'
import { cn } from '@/lib/cn'

export interface MetricProps {
  label: string
  /** `null`/`undefined` renders an em dash — never 0, never "N/A" styled as a value. */
  value: number | string | null | undefined
  unit?: string
  /** Signed change vs the comparison window. */
  delta?: number | null
  /** Whether a rise is good. NDVI up is good; drought index up is not. */
  higherIsBetter?: boolean
  hint?: ReactNode
  className?: string
}

const NO_VALUE = '\u2014'

export default function Metric({
  label,
  value,
  unit,
  delta,
  higherIsBetter = true,
  hint,
  className,
}: MetricProps) {
  const missing = value === null || value === undefined || value === ''
  const display =
    missing
      ? NO_VALUE
      : typeof value === 'number'
        ? // Four significant-ish digits: indices sit in 0-1, counts in the
          // thousands, and both must stay readable in the same grid.
          Math.abs(value) >= 100
          ? value.toFixed(0)
          : value.toFixed(2)
        : value

  // A delta is only meaningful when there is a value to compare against.
  const showDelta = !missing && delta !== null && delta !== undefined && delta !== 0
  const improving = showDelta && (delta > 0) === higherIsBetter

  return (
    <div className={cn('min-w-0', className)}>
      <div className="truncate text-micro font-semibold uppercase tracking-wider text-content-subtle">
        {label}
      </div>
      <div className="mt-1 flex items-baseline gap-1.5">
        <span
          className={cn(
            'gv-numeric text-metric font-semibold',
            missing ? 'text-content-subtle' : 'text-content'
          )}
        >
          {display}
        </span>
        {unit && !missing && (
          <span className="text-caption text-content-subtle">{unit}</span>
        )}
      </div>
      {showDelta && (
        <div
          className={cn(
            'gv-numeric mt-0.5 text-caption font-medium',
            improving ? 'text-sev-ok' : 'text-sev-high'
          )}
        >
          {delta > 0 ? '\u25b2' : '\u25bc'} {Math.abs(delta).toFixed(2)}
        </div>
      )}
      {missing && (
        <div className="mt-0.5 text-caption text-content-subtle">
          no data in window
        </div>
      )}
      {hint && <div className="mt-1 text-caption text-content-subtle">{hint}</div>}
    </div>
  )
}
