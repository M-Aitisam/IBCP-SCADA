// packages/dashboard/src/components/ui/Field.tsx
'use client'

import { forwardRef, useId, type InputHTMLAttributes, type ReactNode, type SelectHTMLAttributes } from 'react'
import { cn } from '@/lib/cn'

const control =
  'w-full rounded border border-line bg-surface px-3 text-sm text-content placeholder:text-content-subtle ' +
  'transition-colors duration-gv ease-gv hover:border-line-strong ' +
  'disabled:cursor-not-allowed disabled:opacity-60 ' +
  'aria-[invalid=true]:border-sev-critical'

export interface FieldProps {
  label: string
  /** Rendered and wired via aria-describedby, not just coloured red. */
  error?: string
  hint?: ReactNode
  required?: boolean
  children: (ids: { id: string; describedBy?: string; invalid: boolean }) => ReactNode
  className?: string
}

/**
 * Label + control + message, with the accessibility wiring done once.
 *
 * The render-prop shape exists so the ids generated here reach the control:
 * a label whose `htmlFor` does not match anything is decoration, and an error
 * message that is not referenced by `aria-describedby` is invisible to a screen
 * reader even though it is on screen.
 */
export function Field({ label, error, hint, required, children, className }: FieldProps) {
  const id = useId()
  const errorId = `${id}-error`
  const hintId = `${id}-hint`
  const describedBy =
    [error ? errorId : null, hint ? hintId : null].filter(Boolean).join(' ') || undefined

  return (
    <div className={cn('space-y-1.5', className)}>
      <label
        htmlFor={id}
        className="block text-label font-medium text-content-muted"
      >
        {label}
        {required && (
          <span className="ml-0.5 text-sev-critical" aria-hidden="true">
            *
          </span>
        )}
      </label>
      {children({ id, describedBy, invalid: Boolean(error) })}
      {hint && !error && (
        <p id={hintId} className="text-caption text-content-subtle">
          {hint}
        </p>
      )}
      {error && (
        <p id={errorId} className="text-caption text-sev-critical">
          {error}
        </p>
      )}
    </div>
  )
}

export const Input = forwardRef<HTMLInputElement, InputHTMLAttributes<HTMLInputElement>>(
  function Input({ className, ...props }, ref) {
    return <input ref={ref} className={cn(control, 'h-9', className)} {...props} />
  }
)

export const Select = forwardRef<
  HTMLSelectElement,
  SelectHTMLAttributes<HTMLSelectElement>
>(function Select({ className, ...props }, ref) {
  return <select ref={ref} className={cn(control, 'h-9 pr-8', className)} {...props} />
})
