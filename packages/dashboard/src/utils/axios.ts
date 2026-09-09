// packages/dashboard/src/utils/axios.ts
import axios, { AxiosError, InternalAxiosRequestConfig } from 'axios'

export const API_URL =
  process.env.NEXT_PUBLIC_API_URL?.trim().replace(/\/+$/, '') ||
  (process.env.NODE_ENV === 'production' ? '/api/v1' : 'http://localhost:8000/api/v1')

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

// Without a timeout, axios waits forever: a hung backend leaves every panel
// spinning with no way for the user to tell that it has failed. 30s is well
// past the slowest normal response while still failing in human time.
export const DEFAULT_TIMEOUT_MS = 30_000
// The region-boundary export is the one legitimately slow call — a cold
// deployment may have to build it from Earth Engine before it is persisted.
export const LONG_TIMEOUT_MS = 90_000

const apiClient = axios.create({
  baseURL: API_URL,
  timeout: DEFAULT_TIMEOUT_MS,
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

/**
 * Pull a human-readable message out of a FastAPI error response.
 *
 * The distinctions here are the ones a user can act on: a timeout, an
 * unreachable server and a server-side fault need different responses, and
 * collapsing them into "Something went wrong" hides which one happened.
 */
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
      // No response at all: timeout, DNS failure, connection refused or CORS.
      if (error.code === 'ECONNABORTED' || error.code === 'ETIMEDOUT') {
        return 'The request timed out. The server may be busy or still starting up.'
      }
      return `Cannot reach the API at ${API_URL}. Check that the backend is running and reachable.`
    }

    const status = error.response.status
    if (status === 401) return 'Your session has expired. Please sign in again.'
    if (status === 403) return 'You do not have permission to perform this action.'
    if (status === 404) return 'That resource does not exist.'
    if (status === 409) return 'That operation conflicts with work already in progress.'
    if (status === 503) return 'That data source is temporarily unavailable.'
    if (status === 504) {
      return 'The server took too long to respond. Try again in a moment.'
    }
    if (status >= 500) return `The server returned an error (${status}).`
  }
  if (error instanceof Error && error.message) return error.message
  return fallback
}

export default apiClient
