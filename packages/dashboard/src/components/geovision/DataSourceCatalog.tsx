// packages/dashboard/src/components/geovision/DataSourceCatalog.tsx
'use client'

import { useDatasetCatalog } from '@/hooks/useGeovision'
import {
  DatasetStateBadge,
  formatDate,
  NO_VALUE,
  Panel,
  QueryBoundary,
  ScrollX,
} from './primitives'

const TH =
  'text-left px-3 py-2 font-medium text-[10px] uppercase tracking-wider text-slate-500 dark:text-slate-400 whitespace-nowrap'
const TD = 'px-3 py-2 text-xs text-slate-700 dark:text-slate-300 align-top'

/**
 * Data-source catalogue.
 *
 * Provider, collection, resolution and cadence are declared configuration;
 * counts and date spans are measured from the database. Nothing on this table
 * is estimated, which is why there is no "expected coverage" column.
 */
export default function DataSourceCatalog() {
  const query = useDatasetCatalog()
  const datasets = query.data?.datasets ?? []
  const boundary = query.data?.boundary_source

  return (
    <Panel
      title="Data source catalog"
      subtitle="Configured collections and their measured coverage"
      bodyClassName=""
    >
      <QueryBoundary
        isLoading={query.isLoading}
        isError={query.isError}
        error={query.error}
        isEmpty={datasets.length === 0}
        loadingLabel="Loading catalog"
        emptyTitle="No datasets configured"
        onRetry={() => query.refetch()}
      >
        <>
          <ScrollX>
            <table className="w-full border-collapse">
              <caption className="sr-only">
                Catalog of configured Earth Engine collections
              </caption>
              <thead className="bg-slate-50 dark:bg-slate-950/50">
                <tr>
                  <th scope="col" className={TH}>Dataset</th>
                  <th scope="col" className={TH}>Provider / platform</th>
                  <th scope="col" className={TH}>GEE collection</th>
                  <th scope="col" className={TH}>Purpose</th>
                  <th scope="col" className={TH}>Resolution</th>
                  <th scope="col" className={TH}>Cadence</th>
                  <th scope="col" className={TH}>Metrics</th>
                  <th scope="col" className={TH}>Historical coverage</th>
                  <th scope="col" className={TH}>Status</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-100 dark:divide-slate-800">
                {datasets.map((d) => (
                  <tr key={d.dataset}>
                    <td className={`${TD} font-medium text-slate-900 dark:text-slate-100 whitespace-nowrap`}>
                      {d.dataset}
                      {d.version && (
                        <span className="block text-[10px] text-slate-400">
                          {d.version}
                        </span>
                      )}
                    </td>
                    <td className={TD}>
                      {d.provider ?? NO_VALUE}
                      <span className="block text-[10px] text-slate-400">
                        {d.platform ?? ''}
                      </span>
                    </td>
                    <td className={`${TD} font-mono text-[11px] whitespace-nowrap`}>
                      {d.gee_collection}
                    </td>
                    <td className={`${TD} max-w-[16rem]`}>{d.purpose ?? NO_VALUE}</td>
                    <td className={`${TD} tabular-nums whitespace-nowrap`}>
                      {d.spatial_resolution_m >= 1000
                        ? `${(d.spatial_resolution_m / 1000).toFixed(d.spatial_resolution_m % 1000 === 0 ? 0 : 2)} km`
                        : `${d.spatial_resolution_m} m`}
                    </td>
                    <td className={`${TD} whitespace-nowrap`}>
                      {d.native_cadence}
                      <span className="block text-[10px] text-slate-400 max-w-[12rem] whitespace-normal">
                        {d.revisit ?? ''}
                      </span>
                    </td>
                    <td className={`${TD} max-w-[14rem]`}>
                      <span className="flex flex-wrap gap-1">
                        {d.metrics.map((m) => (
                          <span
                            key={m.metric}
                            className="inline-block px-1.5 py-0.5 rounded bg-slate-100 dark:bg-slate-800 text-[10px]"
                            // Derived products are marked, so a computed index
                            // is never mistaken for a measured band.
                            title={m.derived ? 'Derived index' : 'Measured band'}
                          >
                            {m.metric}
                            {m.derived && '*'}
                          </span>
                        ))}
                      </span>
                    </td>
                    <td className={`${TD} tabular-nums whitespace-nowrap`}>
                      {d.coverage.earliest_observation ? (
                        <>
                          {formatDate(d.coverage.earliest_observation)}
                          {' → '}
                          {formatDate(d.coverage.latest_observation)}
                          <span className="block text-[10px] text-slate-400">
                            {d.coverage.observations.toLocaleString()} obs ·{' '}
                            {d.coverage.regions} regions
                          </span>
                        </>
                      ) : (
                        NO_VALUE
                      )}
                    </td>
                    <td className={TD}>
                      <DatasetStateBadge state={d.health.state} />
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </ScrollX>

          <div className="px-4 py-3 border-t border-slate-200 dark:border-slate-800 space-y-1">
            <p className="text-[10px] text-slate-500 dark:text-slate-400">
              * derived index, computed from source bands during acquisition.
            </p>
            {boundary && (
              // Boundary provenance stated plainly: the vintage affects which
              // province names and regions appear on the map.
              <p className="text-[10px] text-slate-500 dark:text-slate-400">
                Boundaries: <span className="font-mono">{boundary.asset}</span> (
                {boundary.region_type} level). {boundary.vintage_note}
              </p>
            )}
          </div>
        </>
      </QueryBoundary>
    </Panel>
  )
}
