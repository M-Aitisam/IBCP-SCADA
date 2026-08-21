// packages/dashboard/src/app/geovision/page.tsx
'use client'

import { useState } from 'react'
import {
  AlertTriangle,
  CloudRain,
  Database,
  Leaf,
  RefreshCw,
  Thermometer,
} from 'lucide-react'
import Navigation from '@/components/shared/Navigation'
import ProtectedRoute from '@/components/shared/ProtectedRoute'
import useGeovisionData from '@/hooks/useGeovisionData'

const WINDOW_OPTIONS = [
  { label: '30 days', value: 30 },
  { label: '90 days', value: 90 },
  { label: '1 year', value: 365 },
]

function average(values: number[]): number | null {
  if (values.length === 0) return null
  return values.reduce((sum, v) => sum + v, 0) / values.length
}

function StatCard({
  label,
  value,
  detail,
  icon: Icon,
  color,
  bg,
}: {
  label: string
  value: string
  detail: string
  icon: typeof Leaf
  color: string
  bg: string
}) {
  return (
    <div className="bg-white dark:bg-gray-800 rounded-xl border border-gray-200/50 dark:border-gray-700 p-4 shadow-sm">
      <div className={`${bg} p-2 rounded-lg w-fit`}>
        <Icon className={`w-5 h-5 ${color}`} />
      </div>
      <div className="mt-3">
        <div className="text-2xl font-bold text-gray-900 dark:text-gray-100">{value}</div>
        <div className="text-xs text-gray-500 dark:text-gray-400">{label}</div>
        <div className="text-xs text-gray-400 dark:text-gray-500 mt-1">{detail}</div>
      </div>
    </div>
  )
}

function GeoVisionContent() {
  const [days, setDays] = useState(90)
  const { data, loading, error, hasAnyData, refresh } = useGeovisionData(days)

  const ndviValues = data.vegetation?.data.map((d) => d.value) ?? []
  const meanNdvi = average(ndviValues)
  const rainfallTotal = data.rainfall?.data.reduce(
    (sum, r) => sum + r.total_rainfall_mm,
    0
  )
  const meanTemp = average(data.temperature?.data.map((d) => d.value) ?? [])
  const regionCount = new Set([
    ...(data.vegetation?.data.map((d) => d.region_id) ?? []),
    ...(data.rainfall?.data.map((d) => d.region_id) ?? []),
  ]).size

  return (
    <div className="min-h-screen bg-gray-50 dark:bg-gray-900">
      <Navigation />

      <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 py-6">
        <div className="flex flex-wrap items-center justify-between gap-4 mb-6">
          <div>
            <h1 className="text-2xl font-bold text-gray-900 dark:text-gray-100">
              GeoVision AI
            </h1>
            <p className="text-sm text-gray-500 dark:text-gray-400">
              Satellite observations from Google Earth Engine
            </p>
          </div>

          <div className="flex items-center gap-2">
            <select
              value={days}
              onChange={(e) => setDays(Number(e.target.value))}
              className="px-3 py-2 text-sm border border-gray-300 dark:border-gray-600 dark:bg-gray-800 dark:text-gray-100 rounded-lg outline-none focus:ring-2 focus:ring-blue-500"
            >
              {WINDOW_OPTIONS.map((o) => (
                <option key={o.value} value={o.value}>
                  {o.label}
                </option>
              ))}
            </select>
            <button
              onClick={refresh}
              disabled={loading}
              className="flex items-center gap-2 px-4 py-2 bg-blue-600 text-white rounded-lg text-sm font-medium hover:bg-blue-700 transition-colors shadow-sm disabled:opacity-50"
            >
              <RefreshCw className={`w-4 h-4 ${loading ? 'animate-spin' : ''}`} />
              Refresh
            </button>
          </div>
        </div>

        {error && (
          <div className="mb-6 p-4 bg-amber-50 dark:bg-amber-900/20 border border-amber-200 dark:border-amber-800 rounded-lg flex items-start gap-3">
            <AlertTriangle className="w-5 h-5 text-amber-500 flex-shrink-0 mt-0.5" />
            <p className="text-sm text-amber-700 dark:text-amber-300">{error}</p>
          </div>
        )}

        {/* An empty database is reported plainly rather than shown as zeros,
            which would read as "measured zero" instead of "not ingested". */}
        {!loading && !hasAnyData && (
          <div className="mb-6 p-6 bg-white dark:bg-gray-800 border border-gray-200 dark:border-gray-700 rounded-xl text-center">
            <Database className="w-12 h-12 mx-auto text-gray-300 dark:text-gray-600 mb-3" />
            <h2 className="font-semibold text-gray-900 dark:text-gray-100">
              No satellite observations stored yet
            </h2>
            <p className="text-sm text-gray-500 dark:text-gray-400 mt-1 max-w-lg mx-auto">
              The Earth Engine acquisition pipeline has not run. From{' '}
              <code className="px-1 py-0.5 bg-gray-100 dark:bg-gray-700 rounded text-xs">
                packages/backend
              </code>
              , run{' '}
              <code className="px-1 py-0.5 bg-gray-100 dark:bg-gray-700 rounded text-xs">
                python -m app.ingestion.cli check-config
              </code>{' '}
              to get started.
            </p>
          </div>
        )}

        <div className="grid grid-cols-2 md:grid-cols-4 gap-4 mb-6">
          <StatCard
            label="Mean NDVI"
            value={meanNdvi !== null ? meanNdvi.toFixed(3) : '—'}
            detail={`${ndviValues.length} region(s), MOD13Q1`}
            icon={Leaf}
            color="text-emerald-500"
            bg="bg-emerald-50 dark:bg-emerald-900/30"
          />
          <StatCard
            label="Total rainfall"
            value={rainfallTotal ? `${rainfallTotal.toFixed(0)} mm` : '—'}
            detail={`Summed over ${days} days, CHIRPS`}
            icon={CloudRain}
            color="text-blue-500"
            bg="bg-blue-50 dark:bg-blue-900/30"
          />
          <StatCard
            label="Mean land surface temp"
            value={meanTemp !== null ? `${meanTemp.toFixed(1)} °C` : '—'}
            detail="MOD11A2 daytime LST"
            icon={Thermometer}
            color="text-amber-500"
            bg="bg-amber-50 dark:bg-amber-900/30"
          />
          <StatCard
            label="Regions monitored"
            value={regionCount > 0 ? String(regionCount) : '—'}
            detail="With stored observations"
            icon={Database}
            color="text-indigo-500"
            bg="bg-indigo-50 dark:bg-indigo-900/30"
          />
        </div>

        {data.coverage.length > 0 && (
          <div className="bg-white dark:bg-gray-800 rounded-xl border border-gray-200/50 dark:border-gray-700 shadow-sm mb-6 overflow-hidden">
            <div className="px-5 py-3 border-b border-gray-100 dark:border-gray-700">
              <h2 className="font-semibold text-gray-900 dark:text-gray-100 text-sm">
                Ingestion coverage
              </h2>
            </div>
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead className="bg-gray-50 dark:bg-gray-900/50 text-gray-500 dark:text-gray-400">
                  <tr>
                    <th className="text-left px-5 py-2 font-medium">Dataset</th>
                    <th className="text-right px-5 py-2 font-medium">Observations</th>
                    <th className="text-right px-5 py-2 font-medium">Regions</th>
                    <th className="text-left px-5 py-2 font-medium">Earliest</th>
                    <th className="text-left px-5 py-2 font-medium">Latest</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-gray-100 dark:divide-gray-700">
                  {data.coverage.map((row) => (
                    <tr key={row.dataset}>
                      <td className="px-5 py-2 font-medium text-gray-900 dark:text-gray-100">
                        {row.dataset}
                      </td>
                      <td className="px-5 py-2 text-right text-gray-600 dark:text-gray-300">
                        {row.observations.toLocaleString()}
                      </td>
                      <td className="px-5 py-2 text-right text-gray-600 dark:text-gray-300">
                        {row.regions}
                      </td>
                      <td className="px-5 py-2 text-gray-500 dark:text-gray-400">
                        {row.earliest ?? '—'}
                      </td>
                      <td className="px-5 py-2 text-gray-500 dark:text-gray-400">
                        {row.latest ?? '—'}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        )}

        {(data.vegetation?.count ?? 0) > 0 && (
          <div className="bg-white dark:bg-gray-800 rounded-xl border border-gray-200/50 dark:border-gray-700 shadow-sm overflow-hidden">
            <div className="px-5 py-3 border-b border-gray-100 dark:border-gray-700">
              <h2 className="font-semibold text-gray-900 dark:text-gray-100 text-sm">
                Vegetation health by region
              </h2>
            </div>
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead className="bg-gray-50 dark:bg-gray-900/50 text-gray-500 dark:text-gray-400">
                  <tr>
                    <th className="text-left px-5 py-2 font-medium">Region</th>
                    <th className="text-left px-5 py-2 font-medium">Province</th>
                    <th className="text-right px-5 py-2 font-medium">NDVI</th>
                    <th className="text-left px-5 py-2 font-medium">Status</th>
                    <th className="text-left px-5 py-2 font-medium">Observed</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-gray-100 dark:divide-gray-700">
                  {data.vegetation?.data.map((row) => (
                    <tr key={row.region_id}>
                      <td className="px-5 py-2 font-medium text-gray-900 dark:text-gray-100">
                        {row.tehsil ?? row.district ?? row.region_id}
                      </td>
                      <td className="px-5 py-2 text-gray-600 dark:text-gray-300">
                        {row.province ?? '—'}
                      </td>
                      <td className="px-5 py-2 text-right tabular-nums text-gray-900 dark:text-gray-100">
                        {row.value.toFixed(3)}
                      </td>
                      <td className="px-5 py-2 text-gray-600 dark:text-gray-300">
                        {row.health_status}
                      </td>
                      <td className="px-5 py-2 text-gray-500 dark:text-gray-400">
                        {row.observation_date}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        )}
      </div>
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
