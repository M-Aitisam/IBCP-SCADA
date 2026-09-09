// packages/dashboard/src/app/auth/callback/page.tsx
'use client'

import { Suspense, useEffect, useRef, useState } from 'react'
import { useRouter, useSearchParams } from 'next/navigation'
import { Loader2 } from 'lucide-react'
import apiClient from '@/utils/axios'
import { useAuth, User } from '@/context/AuthContext'

/** Error codes the backend redirects here with. */
const ERROR_MESSAGES: Record<string, string> = {
  account_exists_use_password:
    'An account with this email already exists. Please sign in with your username and password.',
  invalid_state:
    'This sign-in link has expired or was already used. Please try again.',
  missing_code_or_state: 'The sign-in link was incomplete. Please try again.',
  token_exchange_failed: 'Google could not confirm the sign-in. Please try again.',
  profile_fetch_failed: 'Could not read your Google profile. Please try again.',
  google_unreachable: 'Could not reach Google. Check your connection and try again.',
  email_not_verified:
    'Your Google account has no verified email address, so it cannot be used to sign in.',
  account_creation_failed: 'Could not create your account. Please try again.',
  access_denied: 'Sign-in was cancelled.',
}

function AuthCallbackInner() {
  const router = useRouter()
  const searchParams = useSearchParams()
  const { loginWithToken } = useAuth()
  const [error, setError] = useState<string | null>(null)
  // The exchange code is single-use, so React 18 StrictMode's double effect
  // invocation in development would burn it and fail the second call.
  const exchangeStarted = useRef(false)

  useEffect(() => {
    const oauthError = searchParams.get('error')
    if (oauthError) {
      setError(ERROR_MESSAGES[oauthError] ?? 'Authentication failed.')
      return
    }

    const code = searchParams.get('code')
    if (!code) {
      setError('Invalid callback — no authorization code was provided.')
      return
    }

    if (exchangeStarted.current) return
    exchangeStarted.current = true

    apiClient
      .post<{ access_token: string; user: User }>('/auth/google/exchange', { code })
      .then((response) => {
        loginWithToken(response.data.access_token, response.data.user)
      })
      .catch(() => {
        setError('This sign-in link has expired. Please try again.')
      })
  }, [searchParams, loginWithToken])

  if (error) {
    return (
      <div className="min-h-screen flex items-center justify-center bg-gradient-to-br from-blue-50 to-indigo-100 dark:from-gray-900 dark:to-gray-800">
        <div className="text-center max-w-sm px-4">
          <div className="text-red-500 text-5xl mb-4" aria-hidden="true">
            ⚠️
          </div>
          <h1 className="text-xl font-bold text-gray-900 dark:text-gray-100 mb-2">
            Authentication failed
          </h1>
          <p className="text-gray-500 dark:text-gray-400 text-sm">{error}</p>
          <button
            onClick={() => router.push('/login')}
            className="mt-6 px-4 py-2 bg-blue-600 text-white rounded-lg font-medium hover:bg-blue-700 transition"
          >
            Back to login
          </button>
        </div>
      </div>
    )
  }

  return (
    <div className="min-h-screen flex items-center justify-center bg-gradient-to-br from-blue-50 to-indigo-100 dark:from-gray-900 dark:to-gray-800">
      <div className="text-center">
        <Loader2 className="w-10 h-10 text-blue-600 animate-spin mx-auto mb-4" />
        <p className="text-gray-600 dark:text-gray-300 font-medium">
          Completing sign-in…
        </p>
      </div>
    </div>
  )
}

export default function AuthCallbackPage() {
  return (
    <Suspense fallback={null}>
      <AuthCallbackInner />
    </Suspense>
  )
}
