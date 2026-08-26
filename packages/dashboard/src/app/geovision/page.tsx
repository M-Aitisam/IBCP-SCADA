// packages/dashboard/src/app/geovision/page.tsx
'use client'

import { Database, LayoutGrid, Siren } from 'lucide-react'
import { useState } from 'react'

import Navigation from '@/components/shared/Navigation'
import ProtectedRoute from '@/components/shared/ProtectedRoute'
import CommandHeader from '@/components/geovision/CommandHeader'
import DataSourceCatalog from '@/components/geovision/DataSourceCatalog'
import GisMapPanel from '@/components/geovision/GisMapPanel'
import GlobalFilters from '@/components/geovision/GlobalFilters'
import IngestionMonitor from '@/components/geovision/IngestionMonitor'
import KpiGrid from '@/components/geovision/KpiGrid'
import RegionDetailPanel from '@/components/geovision/RegionDetailPanel'
import SatelliteWatch from '@/components/geovision/SatelliteWatch'
import SituationCenter from '@/components/geovision/SituationCenter'
import SystemStatusBar from '@/components/geovision/SystemStatusBar'
import TrendPanel from '@/components/geovision/TrendPanel'
import VegetationTable from '@/components/geovision/VegetationTable'
import { EmptyState, ErrorState, errorMessage } from '@/components/geovision/primitives'
import {
  useHierarchy,
  useIngestionStatus,
  useOverview,
  useRefreshAll,
} from '@/hooks/useGeovision'

/**
 * GeoVision AI — Satellite & Multi-Hazard Intelligence Command Center.
 *
 * Layout follows the operational hierarchy: identity and system state first,
 * then the filters that scope everything below, then the headline indicators,
 * then the map as the primary visual, then analysis, then the operational
 * panels an operator checks when something looks wrong.
 *
 * Every number on this page comes from the backend, which derives it from
 * observations the ingestion pipeline actually stored. There is no mock data,
 * no placeholder series and no hardcoded satellite value anywhere in the tree —
 * where data is unavailable, components render an explicit empty state.
 */
function GeoVisionContent() {
  const overview = useOverview()
  const hierarchy = useHierarchy()
  const ingestion = useIngestionStatus()
  const refreshAll = useRefreshAll()
  const [refreshing, setRefreshing] = useState(false)
  // Two views over one filter state, rather than a second page: the existing
  // monitoring dashboard is unchanged and the operational view sits beside it,
  // so nothing that already worked is disturbed.
  const [view, setView] = useState<'monitoring' | 'situation'>('monitoring')

  async function handleRefresh() {
    setRefreshing(true)
    try {
      await refreshAll()
    } finally {
      setRefreshing(false)
    }
  }

  // An empty database is a distinct state from a broken one, and gets its own
  // message telling the operator exactly how to populate it.
  const hasData =
    overview.data !== undefined &&
    overview.data.data_source !== 'no_data' &&
    overview.data.coverage.observations > 0

  return (
    <div className="min-h-screen bg-slate-50 dark:bg-slate-950">
      <Navigation />

      <CommandHeader
        freshness={overview.data?.freshness}
        isRefreshing={refreshing || overview.isFetching}
        onRefresh={handleRefresh}
      />

      <SystemStatusBar
        overview={overview.data}
        ingestion={ingestion.data}
        isLoading={overview.isLoading || ingestion.isLoading}
        isError={overview.isError}
      />

      <GlobalFilters hierarchy={hierarchy.data} isLoading={hierarchy.isLoading} />

      <div className="max-w-[1600px] mx-auto px-4 pt-3">
        <div
          className="inline-flex rounded border border-slate-300 dark:border-slate-700 overflow-hidden"
          role="tablist"
          aria-label="GeoVision view"
        >
          {(
            [
              ['monitoring', 'Monitoring', LayoutGrid],
              ['situation', 'Situation Center', Siren],
            ] as const
          ).map(([key, label, Icon]) => (
            <button
              key={key}
              type="button"
              role="tab"
              aria-selected={view === key}
              onClick={() => setView(key)}
              className={`inline-flex items-center gap-1.5 px-3 py-1.5 text-xs font-medium border-r last:border-r-0 border-slate-300 dark:border-slate-700 focus:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-blue-500 ${
                view === key
                  ? 'bg-slate-900 text-white dark:bg-slate-100 dark:text-slate-900'
                  : 'bg-white text-slate-600 hover:bg-slate-50 dark:bg-slate-900 dark:text-slate-300 dark:hover:bg-slate-800'
              }`}
            >
              <Icon className="w-3.5 h-3.5" aria-hidden="true" />
              {label}
            </button>
          ))}
        </div>
      </div>

      <main className="max-w-[1600px] mx-auto px-4 py-4 space-y-4">
        {view === 'situation' && <SituationCenter />}

        {view === 'monitoring' && overview.isError && (
          <div className="bg-white dark:bg-slate-900 border border-rose-200 dark:border-rose-900 rounded-lg">
            <ErrorState
              title="Unable to load the command centre"
              detail={errorMessage(overview.error)}
              onRetry={() => overview.refetch()}
            />
          </div>
        )}

        {view === 'monitoring' && !overview.isLoading && !overview.isError && !hasData && (
          <div className="bg-white dark:bg-slate-900 border border-slate-200 dark:border-slate-800 rounded-lg">
            <EmptyState
              icon={Database}
              title="No satellite observations stored yet"
              detail={
                <>
                  The Earth Engine acquisition pipeline has not stored any data.
                  From{' '}
                  <code className="px-1 py-0.5 rounded bg-slate-100 dark:bg-slate-800 text-[11px]">
                    packages/backend
                  </code>
                  , run{' '}
                  <code className="px-1 py-0.5 rounded bg-slate-100 dark:bg-slate-800 text-[11px]">
                    python -m app.ingestion.cli check-config
                  </code>{' '}
                  then{' '}
                  <code className="px-1 py-0.5 rounded bg-slate-100 dark:bg-slate-800 text-[11px]">
                    python -m app.ingestion.cli daily
                  </code>
                  .
                </>
              }
            />
          </div>
        )}

        {view === 'monitoring' && (
          <KpiGrid overview={overview.data} isLoading={overview.isLoading} />
        )}

        {/* The map stays visible in BOTH views: an operator reading the
            situation summary still needs to see where the regions are. */}
        <div className="grid grid-cols-1 xl:grid-cols-[minmax(0,1fr)_360px] gap-4 items-start">
          <GisMapPanel />
          <RegionDetailPanel />
        </div>

        {view === 'monitoring' && (
          <>
            <TrendPanel />
            <SatelliteWatch />
            <IngestionMonitor />
            <VegetationTable />
            <DataSourceCatalog />
          </>
        )}

        <footer className="pt-2 pb-6 text-[10px] text-slate-400 dark:text-slate-500">
          GeoVision AI is a satellite monitoring and analysis system. Indicators
          shown here are derived from Earth observation data and are not official
          disaster warnings.
        </footer>
      </main>
    </div>
  )
}

export default function GeoVisionPage() {
  return (
    <ProtectedRoute>
      <GeoVisionContent />
    </ProtectedRoute>
  )
}
