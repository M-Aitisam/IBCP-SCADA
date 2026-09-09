// packages/dashboard/src/components/shared/ProtectedRoute.tsx
'use client'

import { useEffect, ReactNode } from 'react'
import { useRouter } from 'next/navigation'
import { Loader2 } from 'lucide-react'
import { useAuth } from '@/context/AuthContext'

/**
 * Client-side auth gate.
 *
 * `output: 'export'` in next.config.js rules out Next middleware, so route
 * protection has to happen in the browser. It is a UX guard, not a security
 * boundary — the API enforces authorisation independently on every request.
 */
export default function ProtectedRoute({
  children,
  roles,
}: {
  children: ReactNode
  /** If given, the user's role must be in this list. */
  roles?: string[]
}) {
  const { isAuthenticated, loading, user } = useAuth()
  const router = useRouter()

  const roleAllowed = !roles || (user !== null && roles.includes(user.role))

  useEffect(() => {
    // Wait for session verification to finish, otherwise a page reload would
    // bounce an authenticated user to /login before /auth/me has answered.
    if (loading) return
    if (!isAuthenticated) {
      router.replace('/login')
    }
  }, [loading, isAuthenticated, router])

  if (loading) {
    return (
      <div className="min-h-screen flex items-center justify-center bg-gray-50 dark:bg-gray-900">
        <div className="text-center">
          <Loader2 className="w-8 h-8 text-blue-600 animate-spin mx-auto mb-3" />
          <p className="text-sm text-gray-500 dark:text-gray-400">Loading…</p>
        </div>
      </div>
    )
  }

  if (!isAuthenticated) {
    // The effect above is already redirecting; render nothing meanwhile.
    return null
  }

  if (!roleAllowed) {
    return (
      <div className="min-h-screen flex items-center justify-center bg-gray-50 dark:bg-gray-900 px-4">
        <div className="text-center max-w-sm">
          <h1 className="text-xl font-bold text-gray-900 dark:text-gray-100 mb-2">
            Access restricted
          </h1>
          <p className="text-sm text-gray-500 dark:text-gray-400">
            This page requires one of these roles: {roles?.join(', ')}. Your role
            is <span className="font-medium">{user?.role}</span>.
          </p>
          <button
            onClick={() => router.push('/dashboard')}
            className="mt-6 px-4 py-2 bg-blue-600 text-white rounded-lg font-medium hover:bg-blue-700 transition"
          >
            Back to dashboard
          </button>
        </div>
      </div>
    )
  }

  return <>{children}</>
}
