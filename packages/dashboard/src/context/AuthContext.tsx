// packages/dashboard/src/context/AuthContext.tsx
'use client'

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  ReactNode,
} from 'react'
import { useRouter } from 'next/navigation'
import apiClient, {
  apiErrorMessage,
  clearSession,
  TOKEN_KEY,
  USER_KEY,
} from '@/utils/axios'

export interface User {
  id: string
  username: string
  email: string
  full_name: string | null
  role: string
  team: string | null
}

export interface RegisterData {
  username: string
  email: string
  full_name?: string
  password: string
}

interface AuthContextType {
  user: User | null
  /** True until the stored session has been verified against the server. */
  loading: boolean
  login: (username: string, password: string) => Promise<void>
  loginWithToken: (token: string, user: User) => void
  register: (data: RegisterData) => Promise<void>
  logout: () => void
  isAuthenticated: boolean
}

const AuthContext = createContext<AuthContextType | undefined>(undefined)

function readStoredUser(): User | null {
  if (typeof window === 'undefined') return null
  try {
    const raw = window.sessionStorage.getItem(USER_KEY)
    return raw ? (JSON.parse(raw) as User) : null
  } catch {
    // Corrupt JSON in storage should not brick the app.
    return null
  }
}

function writeSession(token: string, user: User): void {
  if (typeof window === 'undefined') return
  window.sessionStorage.setItem(TOKEN_KEY, token)
  window.sessionStorage.setItem(USER_KEY, JSON.stringify(user))
}

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<User | null>(null)
  const [loading, setLoading] = useState(true)
  const router = useRouter()

  useEffect(() => {
    let cancelled = false

    async function restoreSession() {
      const stored = readStoredUser()
      if (!stored) {
        // Nothing to verify; we are definitively signed out.
        if (!cancelled) setLoading(false)
        return
      }

      // Optimistically render the stored user so a reload does not flash the
      // signed-out UI, but keep `loading` true until the server confirms.
      if (!cancelled) setUser(stored)

      try {
        const { data } = await apiClient.get<User>('/auth/me')
        if (!cancelled) {
          setUser(data)
          writeSession(window.sessionStorage.getItem(TOKEN_KEY) ?? '', data)
        }
      } catch {
        // Token expired or revoked. The 401 interceptor already cleared
        // storage; make sure in-memory state agrees.
        if (!cancelled) {
          clearSession()
          setUser(null)
        }
      } finally {
        // Previously this ran synchronously alongside a floating promise, so
        // consumers saw loading:false while verification was still in flight
        // and briefly rendered an authenticated UI for a revoked token.
        if (!cancelled) setLoading(false)
      }
    }

    void restoreSession()
    return () => {
      cancelled = true
    }
  }, [])

  const login = useCallback(
    async (username: string, password: string) => {
      // The token endpoint is OAuth2 password flow: form-encoded, not JSON.
      const form = new URLSearchParams()
      form.append('username', username)
      form.append('password', password)

      const { data } = await apiClient.post('/auth/token', form, {
        headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
      })
      writeSession(data.access_token, data.user)
      setUser(data.user)
      router.push('/dashboard')
    },
    [router]
  )

  const loginWithToken = useCallback(
    (token: string, userData: User) => {
      writeSession(token, userData)
      setUser(userData)
      router.push('/dashboard')
    },
    [router]
  )

  const register = useCallback(
    async (data: RegisterData) => {
      try {
        await apiClient.post('/auth/register', data)
      } catch (error) {
        throw new Error(apiErrorMessage(error, 'Registration failed'))
      }
      // Sign in with the same credentials so the user lands authenticated.
      try {
        await login(data.username, data.password)
      } catch (error) {
        throw new Error(
          apiErrorMessage(error, 'Account created, but automatic sign-in failed.')
        )
      }
    },
    [login]
  )

  const logout = useCallback(() => {
    clearSession()
    setUser(null)
    // Fire-and-forget: logout is client-side, so a network failure here must
    // not keep the user signed in locally.
    void apiClient.post('/auth/logout').catch(() => undefined)
    router.push('/login')
  }, [router])

  const value = useMemo<AuthContextType>(
    () => ({
      user,
      loading,
      login,
      loginWithToken,
      register,
      logout,
      isAuthenticated: user !== null,
    }),
    [user, loading, login, loginWithToken, register, logout]
  )

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>
}

export function useAuth(): AuthContextType {
  const context = useContext(AuthContext)
  if (context === undefined) {
    throw new Error('useAuth must be used within an AuthProvider')
  }
  return context
}
