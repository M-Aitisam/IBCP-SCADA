// packages/dashboard/src/components/providers/QueryProvider.tsx
'use client'

import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { useState, type ReactNode } from 'react'

/**
 * React Query provider for the whole app.
 *
 * Additive: components that do not use React Query are unaffected, so this
 * cannot regress the existing Dashboard, Flood SCADA or Soil pages.
 *
 * The defaults matter for this dashboard specifically. A single GeoVision page
 * load asks for the overview, regions, catalog, ingestion status, watch and
 * several trend series at once, and several panels want the same underlying
 * query. Deduplication and a sane staleTime are what stop that becoming a
 * request storm every time a filter moves.
 */
export default function QueryProvider({ children }: { children: ReactNode }) {
  // Created in state, not at module scope: a module-level client would be
  // shared across users during SSR/prerender and leak one request's data into
  // another's cache.
  const [client] = useState(
    () =>
      new QueryClient({
        defaultOptions: {
          queries: {
            // Satellite observations change at most once a night, so treating
            // a result as fresh for a minute is generous and eliminates the
            // refetch storm from tab focus changes.
            staleTime: 60_000,
            gcTime: 900_000,
            refetchOnWindowFocus: false,
            // A 401 is terminal — the axios interceptor is already redirecting
            // to login, and retrying would just queue three more failures.
            retry: (failureCount, error: unknown) => {
              const status = (error as { response?: { status?: number } })?.response
                ?.status
              if (status === 401 || status === 403 || status === 404) return false
              if (status && status >= 400 && status < 500) return false
              return failureCount < 2
            },
            retryDelay: (attempt) => Math.min(1000 * 2 ** attempt, 8000),
          },
        },
      })
  )

  return <QueryClientProvider client={client}>{children}</QueryClientProvider>
}
