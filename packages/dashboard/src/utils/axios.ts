// packages/dashboard/src/utils/axios.ts
import axios, { AxiosError, InternalAxiosRequestConfig } from 'axios'

export const API_URL =
  process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000/api/v1'

export const TOKEN_KEY = 'access_token'
export const USER_KEY = 'user'

// `trailingSlash: true` in next.config.js means /login without the slash
// causes an extra redirect hop.
export const LOGIN_PATH = '/login/'

/** sessionStorage is unavailable during SSG/prerender, so never touch it directly. */
export function getStoredToken(): string | null {
  if (typeof window === 'undefined') return null
  try {
    return window.sessionStorage.getItem(TOKEN_KEY)
  } catch {
    // Private browsing modes can throw on storage access.
    return null
  }
}

export function clearSession(): void {
  if (typeof window === 'undefined') return
  try {
    window.sessionStorage.removeItem(TOKEN_KEY)
    window.sessionStorage.removeItem(USER_KEY)
  } catch {
    /* nothing useful to do */
  }
}

const apiClient = axios.create({
  baseURL: API_URL,
  headers: { 'Content-Type': 'application/json' },
})

apiClient.interceptors.request.use(
  (config: InternalAxiosRequestConfig) => {
    const token = getStoredToken()
    if (token) {
      config.headers.Authorization = `Bearer ${token}`
    }
    return config
  },
  (error) => Promise.reject(error)
)

apiClient.interceptors.response.use(
  (response) => response,
  (error: AxiosError) => {
    // Only a 401 means the token is bad. A 403 is a live session lacking a
    // role, so signing the user out there would be wrong.
    if (error.response?.status === 401 && typeof window !== 'undefined') {
      clearSession()
      const onAuthPage =
        window.location.pathname.startsWith('/login') ||
        window.location.pathname.startsWith('/register') ||
        window.location.pathname.startsWith('/auth/')
      if (!onAuthPage) {
        window.location.href = LOGIN_PATH
      }
    }
    return Promise.reject(error)
  }
)

/** Pull a human-readable message out of a FastAPI error response. */
export function apiErrorMessage(error: unknown, fallback = 'Something went wrong'): string {
  if (axios.isAxiosError(error)) {
    const detail = error.response?.data?.detail
    if (typeof detail === 'string') return detail
    // FastAPI 422 returns a list of validation errors.
    if (Array.isArray(detail) && detail.length > 0) {
      const first = detail[0]
      if (typeof first?.msg === 'string') return first.msg
    }
    if (!error.response) {
      return 'Cannot reach the server. Check that the backend is running.'
    }
  }
  if (error instanceof Error && error.message) return error.message
  return fallback
}

export default apiClient
