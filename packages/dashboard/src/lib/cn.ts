// packages/dashboard/src/lib/cn.ts
import { clsx, type ClassValue } from 'clsx'
import { twMerge } from 'tailwind-merge'

/**
 * Merge class names, letting later Tailwind utilities win over earlier ones.
 *
 * Plain concatenation leaves both `px-3` and `px-6` in the string and the
 * winner is then decided by stylesheet order, not by call order — which makes
 * a component's `className` prop unreliable for overriding its own defaults.
 * twMerge resolves the conflict in favour of the caller.
 */
export function cn(...inputs: ClassValue[]): string {
  return twMerge(clsx(inputs))
}
