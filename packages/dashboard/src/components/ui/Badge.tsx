// packages/dashboard/src/components/ui/Badge.tsx
import { cva, type VariantProps } from 'class-variance-authority'
import type { HTMLAttributes } from 'react'
import { cn } from '@/lib/cn'

// Severity badges carry a text label as well as a colour. Roughly 1 in 12 men
// has a red/green colour vision deficiency, and "is this district red or
// green" is precisely the question this platform exists to answer — so colour
// is never the only channel.
const badge = cva(
  'inline-flex items-center gap-1.5 rounded-sm border px-1.5 py-0.5 text-micro font-semibold uppercase tracking-wider',
  {
    variants: {
      tone: {
        neutral: 'border-line bg-surface-sunken text-content-muted',
        ok: 'border-sev-ok/30 bg-sev-ok-soft text-sev-ok',
        watch: 'border-sev-watch/30 bg-sev-watch-soft text-sev-watch',
        high: 'border-sev-high/30 bg-sev-high-soft text-sev-high',
        critical:
          'border-sev-critical/40 bg-sev-critical-soft text-sev-critical',
        // Grey, never green. "No data" is not "all clear".
        unknown: 'border-sev-unknown/30 bg-sev-unknown-soft text-sev-unknown',
        brand: 'border-brand/30 bg-brand/10 text-brand',
      },
    },
    defaultVariants: { tone: 'neutral' },
  }
)

export type BadgeTone = NonNullable<VariantProps<typeof badge>['tone']>

export interface BadgeProps
  extends HTMLAttributes<HTMLSpanElement>,
    VariantProps<typeof badge> {
  dot?: boolean
}

export default function Badge({ className, tone, dot, children, ...props }: BadgeProps) {
  return (
    <span className={cn(badge({ tone }), className)} {...props}>
      {dot && (
        <span
          aria-hidden="true"
          className="h-1.5 w-1.5 rounded-full bg-current"
        />
      )}
      {children}
    </span>
  )
}

/** Map a backend severity string onto a badge tone. */
export function severityTone(severity: string | null | undefined): BadgeTone {
  switch ((severity ?? '').toUpperCase()) {
    case 'NONE':
    case 'NORMAL':
    case 'OK':
      return 'ok'
    case 'MINOR':
    case 'WATCH':
    case 'MODERATE':
      return 'watch'
    case 'MAJOR':
    case 'HIGH':
    case 'SEVERE':
      return 'high'
    case 'CRITICAL':
    case 'EXTREME':
      return 'critical'
    default:
      // Anything unrecognised falls through to grey rather than being
      // optimistically rendered as fine.
      return 'unknown'
  }
}
