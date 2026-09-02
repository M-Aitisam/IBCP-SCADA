// packages/dashboard/src/components/ui/Card.tsx
import type { HTMLAttributes, ReactNode } from 'react'
import { cn } from '@/lib/cn'

// Panels, not cards. Flat surfaces separated by a hairline border rather than
// floated on drop shadows — a dense operational layout with twelve shadowed
// cards reads as clutter, and the shadow budget is reserved for things that
// genuinely sit above the page (drawers, modals, tooltips).

export interface PanelProps extends HTMLAttributes<HTMLDivElement> {
  /** Removes internal padding, for panels holding a map or a full-bleed table. */
  flush?: boolean
}

export function Panel({ className, flush, ...props }: PanelProps) {
  return (
    <div
      className={cn(
        'rounded border border-line bg-surface',
        flush ? 'overflow-hidden' : 'p-4',
        className
      )}
      {...props}
    />
  )
}

export interface PanelHeaderProps {
  title: ReactNode
  /** Secondary line under the title — units, coverage, "as of" timestamps. */
  subtitle?: ReactNode
  /** Right-aligned controls: filters, toggles, a menu. */
  actions?: ReactNode
  className?: string
}

export function PanelHeader({
  title,
  subtitle,
  actions,
  className,
}: PanelHeaderProps) {
  return (
    <div
      className={cn(
        'flex items-start justify-between gap-3 border-b border-line-subtle px-4 py-2.5',
        className
      )}
    >
      <div className="min-w-0">
        <h2 className="truncate text-label font-semibold uppercase tracking-wider text-content-muted">
          {title}
        </h2>
        {subtitle && (
          <p className="mt-0.5 truncate text-caption text-content-subtle">
            {subtitle}
          </p>
        )}
      </div>
      {actions && <div className="flex shrink-0 items-center gap-1.5">{actions}</div>}
    </div>
  )
}

export function PanelBody({ className, ...props }: HTMLAttributes<HTMLDivElement>) {
  return <div className={cn('p-4', className)} {...props} />
}
