// packages/dashboard/src/app/auth/callback/page.tsx
// this page is for testing
'use client'

import { Suspense, useEffect, useState } from 'react'
import { useRouter, useSearchParams } from 'next/navigation'
import axios from 'axios'
import { useAuth } from '@/context/AuthContext'
import { Loader2 } from 'lucide-react'

const API_URL = process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000/api/v1'

const ERROR_MESSAGES: Record<string, string> = {
  account_exists_use_password: 'An account with this email already exists. Please sign in with your username and password. ', 
}

function AuthCallbackInner() {
  const router = useRouter()
  const searchParams = useSearchParams()
  const { loginWithToken } = useAuth()
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    const oauthError = searchParams.get('error')
    if (oauthError) {
      setError(ERROR_MESSAGES[oauthError] || 'Authentication failed')
      return
    }

    const code = searchParams.get('code')
    if (!code) {
      setError('Invalid callback — missing authorization code')
      return
    }

    axios
      .post(`${API_URL}/auth/google/exchange`, { code })
      .then((response) => {
        const { access_token, user } = response.data
        loginWithToken(access_token, user)
      })
      .catch(() => {
        setError('This sign-in link has expired. Please try again.')
      })
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [searchParams])

  if (error) {
    return (
      <div className="min-h-screen flex items-center justify-center bg-gradient-to-br from-blue-50 to-indigo-100">
        <div className="text-center max-w-sm px-4">
          <div className="text-red-500 text-5xl mb-4">⚠️</div>
          <h1 className="text-xl font-bold text-gray-900 mb-2">Authentication Failed</h1>
          <p className="text-gray-500 text-sm">{error}</p>
          <button onClick={() => router.push('/login')} className="mt-6 px-4 py-2 bg-blue-600 text-white rounded-lg font-medium hover:bg-blue-700 transition">
            Back to Login
          </button>
        </div>
      </div>
    )
  }

  return (
    <div className="min-h-screen flex items-center justify-center bg-gradient-to-br from-blue-50 to-indigo-100">
      <div className="text-center">
        <Loader2 className="w-10 h-10 text-blue-600 animate-spin mx-auto mb-4" />
        <p className="text-gray-600 font-medium">Completing sign-in...</p>
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
