// packages/dashboard/src/components/geovision/IngestionMonitor.tsx
'use client'

import { useIngestionStatus } from '@/hooks/useGeovision'
import {
  DatasetStateBadge,
  formatDate,
  formatDateTime,
  NO_VALUE,
  Panel,
  QueryBoundary,
  ScrollX,
} from './primitives'

const TH =
  'text-left px-3 py-2 font-medium text-[10px] uppercase tracking-wider text-slate-500 dark:text-slate-400 whitespace-nowrap'
const TD = 'px-3 py-2 text-xs text-slate-700 dark:text-slate-300 whitespace-nowrap'

/**
 * Per-dataset ingestion monitor.
 *
 * The column that matters most is the pair "latest observation" and "last
 * ingested": they are different events, and showing only one of them is how a
 * dashboard ends up implying a 3-week-old composite is live data.
 */
export default function IngestionMonitor() {
  const query = useIngestionStatus()
  const datasets = query.data?.datasets ?? []
  const runs = query.data?.recent_runs ?? []

  return (
    <Panel
      title="Dataset ingestion monitor"
      subtitle="Per-dataset pipeline health. A dataset with nothing new is not a failure."
      bodyClassName=""
    >
      <QueryBoundary
        isLoading={query.isLoading}
        isError={query.isError}
        error={query.error}
        isEmpty={datasets.length === 0}
        loadingLabel="Loading ingestion status"
        emptyTitle="No ingestion history"
        emptyDetail="The acquisition pipeline has not recorded any runs yet."
        onRetry={() => query.refetch()}
      >
        <>
          <ScrollX>
            <table className="w-full border-collapse">
              <caption className="sr-only">
                Ingestion status for each configured satellite dataset
              </caption>
              <thead className="bg-slate-50 dark:bg-slate-950/50">
                <tr>
                  <th scope="col" className={TH}>Dataset</th>
                  <th scope="col" className={TH}>Status</th>
                  <th scope="col" className={TH}>Latest observation</th>
                  <th scope="col" className={TH}>Last ingested</th>
                  <th scope="col" className={`${TH} text-right`}>Observations</th>
                  <th scope="col" className={`${TH} text-right`}>Regions</th>
                  <th scope="col" className={TH}>Historical coverage</th>
                  <th scope="col" className={TH}>Backfill</th>
                  <th scope="col" className={TH}>Last error</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-100 dark:divide-slate-800">
                {datasets.map((d) => (
                  <tr key={d.dataset}>
                    <td className={`${TD} font-medium text-slate-900 dark:text-slate-100`}>
                      {d.dataset}
                      <span className="block text-[10px] text-slate-400">
                        {d.native_cadence}
                      </span>
                    </td>
                    <td className={TD}>
                      <DatasetStateBadge state={d.state} />
                      <span className="block text-[10px] text-slate-400 mt-0.5 max-w-[15rem] whitespace-normal">
                        {d.detail}
                      </span>
                    </td>
                    <td className={`${TD} tabular-nums`}>
                      {formatDate(d.latest_observation)}
                    </td>
                    <td className={`${TD} tabular-nums text-slate-500 dark:text-slate-400`}>
                      {formatDateTime(d.last_ingested_at)}
                    </td>
                    <td className={`${TD} text-right tabular-nums`}>
                      {d.observations.toLocaleString()}
                    </td>
                    <td className={`${TD} text-right tabular-nums`}>{d.regions}</td>
                    <td className={`${TD} tabular-nums text-slate-500 dark:text-slate-400`}>
                      {d.earliest_observation
                        ? `${formatDate(d.earliest_observation)} → ${formatDate(d.latest_observation)}`
                        : NO_VALUE}
                      {/* Requested vs actually available, kept visibly apart. */}
                      {d.latest_available_at_source && (
                        <span className="block text-[10px] text-slate-400">
                          source has to {formatDate(d.latest_available_at_source)}
                        </span>
                      )}
                    </td>
                    <td className={TD}>
                      {d.backfill_complete ? (
                        <span className="text-emerald-600 dark:text-emerald-400">
                          Complete
                        </span>
                      ) : (
                        <span className="text-amber-600 dark:text-amber-400">
                          Incomplete
                          {d.backfill_cursor && (
                            <span className="block text-[10px] text-slate-400">
                              at {formatDate(d.backfill_cursor)}
                            </span>
                          )}
                        </span>
                      )}
                    </td>
                    <td className={`${TD} max-w-[16rem] whitespace-normal`}>
                      {d.last_error ? (
                        <span className="text-rose-600 dark:text-rose-400">
                          {d.last_error}
                        </span>
                      ) : (
                        <span className="text-slate-400">none</span>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </ScrollX>

          {runs.length > 0 && (
            <div className="px-4 py-3 border-t border-slate-200 dark:border-slate-800">
              <h3 className="text-[10px] uppercase tracking-wider text-slate-500 dark:text-slate-400 mb-2">
                Recent runs
              </h3>
              <ul className="space-y-1">
                {runs.slice(0, 5).map((run) => (
                  <li
                    key={run.run_id}
                    className="flex flex-wrap items-center gap-x-3 gap-y-0.5 text-[11px]"
                  >
                    <span className="font-mono text-slate-500 dark:text-slate-400">
                      {run.run_id}
                    </span>
                    <span className="text-slate-700 dark:text-slate-200">{run.mode}</span>
                    <span
                      className={
                        run.status === 'success'
                          ? 'text-emerald-600 dark:text-emerald-400'
                          : run.status === 'failed'
                            ? 'text-rose-600 dark:text-rose-400'
                            : 'text-amber-600 dark:text-amber-400'
                      }
                    >
                      {run.status}
                    </span>
                    <span className="text-slate-400 tabular-nums">
                      +{run.records_inserted.toLocaleString()} inserted
                      {run.records_rejected > 0 &&
                        ` · ${run.records_rejected.toLocaleString()} rejected`}
                    </span>
                    <span className="text-slate-400">
                      {formatDateTime(run.started_at)}
                    </span>
                  </li>
                ))}
              </ul>
            </div>
          )}
        </>
      </QueryBoundary>
    </Panel>
  )
}
