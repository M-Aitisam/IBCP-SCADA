// packages/dashboard/src/components/ui/Button.tsx
'use client'

import { cva, type VariantProps } from 'class-variance-authority'
import { forwardRef, type ButtonHTMLAttributes } from 'react'
import { cn } from '@/lib/cn'

// Square-ish, tight radii, uppercase micro-labels on the smaller sizes. This is
// an instrument control, not a marketing CTA — the visual weight comes from
// contrast and border, not from gradients or large rounded pills.
const button = cva(
  [
    'inline-flex items-center justify-center gap-2 whitespace-nowrap',
    'font-medium transition-colors duration-gv ease-gv',
    'disabled:pointer-events-none disabled:opacity-50',
    // The global :focus-visible ring in tokens.css covers keyboard focus.
  ].join(' '),
  {
    variants: {
      variant: {
        primary: 'bg-brand text-brand-fg hover:bg-brand-hover',
        secondary:
          'bg-surface-raised text-content border border-line hover:border-line-strong hover:bg-surface-sunken',
        ghost: 'text-content-muted hover:bg-surface-sunken hover:text-content',
        outline:
          'border border-line-strong text-content hover:bg-surface-sunken',
        // Destructive uses the critical severity hue on purpose: the same red
        // that means "critical" on the map means "irreversible" on a control.
        danger: 'bg-sev-critical text-white hover:brightness-110',
      },
      size: {
        sm: 'h-7 rounded-sm px-2.5 text-caption',
        md: 'h-9 rounded px-3.5 text-label',
        lg: 'h-11 rounded px-6 text-sm',
        icon: 'h-9 w-9 rounded p-0',
      },
    },
    defaultVariants: { variant: 'primary', size: 'md' },
  }
)

export interface ButtonProps
  extends ButtonHTMLAttributes<HTMLButtonElement>,
    VariantProps<typeof button> {
  loading?: boolean
}

const Button = forwardRef<HTMLButtonElement, ButtonProps>(function Button(
  { className, variant, size, loading, disabled, children, ...props },
  ref
) {
  return (
    <button
      ref={ref}
      // A loading button that is still clickable submits twice.
      disabled={disabled || loading}
      // Announced to screen readers; the spinner alone is invisible to them.
      aria-busy={loading || undefined}
      className={cn(button({ variant, size }), className)}
      {...props}
    >
      {loading && (
        <span
          aria-hidden="true"
          className="h-3.5 w-3.5 animate-spin rounded-full border-2 border-current border-t-transparent"
        />
      )}
      {children}
    </button>
  )
})

export default Button
