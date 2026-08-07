// packages/dashboard/src/components/shared/ThemeToggle.tsx
'use client'

import { useTheme } from '@/hooks/useTheme'
import { Sun, Moon, Monitor } from 'lucide-react'

export default function ThemeToggle() {
  const { theme, resolvedTheme, toggleTheme } = useTheme()

  const Icon = theme === 'system' ? Monitor : resolvedTheme === 'dark' ? Moon : Sun
  const label = theme === 'system' ? 'System' : resolvedTheme === 'dark' ? 'Dark' : 'Light'

  return (
    <button
      onClick={toggleTheme}
      className="flex items-center gap-2 px-3 py-1.5 text-sm font-medium text-gray-600 dark:text-gray-300 hover:bg-gray-100 dark:hover:bg-gray-800 rounded-lg transition-colors"
      title={`Theme: ${label} (click to change)`}
      aria-label="Toggle theme"
    >
      <Icon className="w-4 h-4" />
      <span className="hidden sm:inline">{label}</span>
    </button>
  )
}
