// packages/dashboard/src/components/geovision/VegetationTable.tsx
'use client'

import { ArrowUpDown } from 'lucide-react'
import { useMemo, useState } from 'react'

import { useRegions } from '@/hooks/useGeovision'
import { useGeovisionFilters } from '@/store/geovisionFilters'
import type { RegionSummary } from '@/services/api/geovisionApi'
import {
  ConditionBadge,
  formatDate,
  formatValue,
  NO_VALUE,
  Panel,
  QueryBoundary,
  ScrollX,
} from './primitives'

type SortKey = 'name' | 'ndvi' | 'evi' | 'rainfall' | 'lst' | 'observed'

const TH =
  'text-left px-3 py-2 font-medium text-[10px] uppercase tracking-wider text-slate-500 dark:text-slate-400 whitespace-nowrap'
const TD = 'px-3 py-2 text-xs text-slate-700 dark:text-slate-300 whitespace-nowrap'

function sortValue(region: RegionSummary, key: SortKey): string | number | null {
  switch (key) {
    case 'name':
      return region.name?.toLowerCase() ?? ''
    case 'ndvi':
      return region.metrics.ndvi?.value ?? null
    case 'evi':
      return region.metrics.evi?.value ?? null
    case 'rainfall':
      return region.rainfall_window_total?.value ?? null
    case 'lst':
      return region.metrics.lst_day_c?.value ?? null
    case 'observed':
      return region.latest_observation ?? ''
  }
}

function SortButton({
  label,
  sortKey,
  active,
  direction,
  onSort,
  align = 'left',
}: {
  label: string
  sortKey: SortKey
  active: boolean
  direction: 'asc' | 'desc'
  onSort: (key: SortKey) => void
  align?: 'left' | 'right'
}) {
  return (
    // aria-sort belongs on the column header, not on the button inside it:
    // the button's implicit role does not support the attribute, and screen
    // readers announce sort state from the columnheader.
    <th
      scope="col"
      aria-sort={active ? (direction === 'asc' ? 'ascending' : 'descending') : 'none'}
      className={`${TH} ${align === 'right' ? 'text-right' : ''}`}
    >
      <button
        type="button"
        onClick={() => onSort(sortKey)}
        className={`inline-flex items-center gap-1 hover:text-slate-800 dark:hover:text-slate-200 focus:outline-none focus-visible:ring-2 focus-visible:ring-blue-500 rounded ${
          align === 'right' ? 'flex-row-reverse' : ''
        }`}
      >
        {label}
        <ArrowUpDown
          className={`w-3 h-3 ${active ? 'text-slate-700 dark:text-slate-200' : 'text-slate-300 dark:text-slate-600'}`}
          aria-hidden="true"
        />
      </button>
    </th>
  )
}

/**
 * Vegetation health by region.
 *
 * Reads the same /regions payload the choropleth uses, so the table and the map
 * can never show different numbers for the same district. Clicking a row
 * selects the region, which drives the map and the detail panel.
 */
export default function VegetationTable() {
  const query = useRegions()
  const selectedRegionId = useGeovisionFilters((s) => s.selectedRegionId)
  const selectRegion = useGeovisionFilters((s) => s.selectRegion)

  const [sortKey, setSortKey] = useState<SortKey>('ndvi')
  const [direction, setDirection] = useState<'asc' | 'desc'>('desc')

  function onSort(key: SortKey) {
    if (key === sortKey) {
      setDirection((d) => (d === 'asc' ? 'desc' : 'asc'))
    } else {
      setSortKey(key)
      setDirection(key === 'name' ? 'asc' : 'desc')
    }
  }

  const rows = useMemo(() => {
    const regions = [...(query.data?.regions ?? [])]
    regions.sort((a, b) => {
      const av = sortValue(a, sortKey)
      const bv = sortValue(b, sortKey)
      // Regions with no observation always sink to the bottom, whichever way
      // the sort runs — a missing value is not "low", it is unknown.
      if (av === null && bv === null) return 0
      if (av === null) return 1
      if (bv === null) return -1
      if (typeof av === 'string' || typeof bv === 'string') {
        const cmp = String(av).localeCompare(String(bv))
        return direction === 'asc' ? cmp : -cmp
      }
      return direction === 'asc' ? av - bv : bv - av
    })
    return regions
  }, [query.data, sortKey, direction])

  return (
    <Panel
      title="Vegetation health by region"
      subtitle={`${rows.length} region(s) in the current selection`}
      bodyClassName=""
    >
      <QueryBoundary
        isLoading={query.isLoading}
        isError={query.isError}
        error={query.error}
        isEmpty={rows.length === 0}
        loadingLabel="Loading regions"
        emptyTitle="No regions with observations"
        emptyDetail="No stored observations match the current filters."
        onRetry={() => query.refetch()}
      >
        <ScrollX>
          <table className="w-full border-collapse">
            <caption className="sr-only">
              Latest vegetation and climate values for each region
            </caption>
            <thead className="bg-slate-50 dark:bg-slate-950/50">
              <tr>
                <SortButton label="Region" sortKey="name" active={sortKey === 'name'} direction={direction} onSort={onSort} />
                <th scope="col" className={TH}>Province</th>
                <SortButton label="NDVI" sortKey="ndvi" active={sortKey === 'ndvi'} direction={direction} onSort={onSort} align="right" />
                <SortButton label="EVI" sortKey="evi" active={sortKey === 'evi'} direction={direction} onSort={onSort} align="right" />
                <SortButton label="Rainfall" sortKey="rainfall" active={sortKey === 'rainfall'} direction={direction} onSort={onSort} align="right" />
                <SortButton label="LST day" sortKey="lst" active={sortKey === 'lst'} direction={direction} onSort={onSort} align="right" />
                <th scope="col" className={TH}>Condition</th>
                <th scope="col" className={TH}>Status</th>
                <th scope="col" className={TH}>Crop condition</th>
                <SortButton label="Observed" sortKey="observed" active={sortKey === 'observed'} direction={direction} onSort={onSort} />
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-100 dark:divide-slate-800">
              {rows.map((r) => {
                const selected = r.region_id === selectedRegionId
                return (
                  <tr
                    key={r.region_id}
                    onClick={() => selectRegion(selected ? null : r.region_id)}
                    className={`cursor-pointer ${
                      selected
                        ? 'bg-blue-50 dark:bg-blue-950/40'
                        : 'hover:bg-slate-50 dark:hover:bg-slate-800/50'
                    }`}
                  >
                    <td className={`${TD} font-medium text-slate-900 dark:text-slate-100`}>
                      <button
                        type="button"
                        // A real button so the row is keyboard reachable, not
                        // just clickable with a mouse.
                        onClick={(e) => {
                          e.stopPropagation()
                          selectRegion(selected ? null : r.region_id)
                        }}
                        className="text-left hover:underline focus:outline-none focus-visible:ring-2 focus-visible:ring-blue-500 rounded"
                      >
                        {r.name}
                      </button>
                    </td>
                    <td className={TD}>{r.province ?? NO_VALUE}</td>
                    <td className={`${TD} text-right tabular-nums`}>
                      {formatValue(r.metrics.ndvi?.value ?? null, '', 3)}
                    </td>
                    <td className={`${TD} text-right tabular-nums`}>
                      {formatValue(r.metrics.evi?.value ?? null, '', 3)}
                    </td>
                    <td className={`${TD} text-right tabular-nums`}>
                      {formatValue(r.rainfall_window_total?.value ?? null, 'mm', 1)}
                    </td>
                    <td className={`${TD} text-right tabular-nums`}>
                      {formatValue(r.metrics.lst_day_c?.value ?? null, '°C', 1)}
                    </td>
                    <td className={TD}>
                      <ConditionBadge
                        level={r.overall_condition}
                        title={r.conditions?.ndvi?.detail}
                      />
                    </td>
                    <td className={TD}>{r.vegetation_status ?? NO_VALUE}</td>
                    <td className={TD}>{r.crop_condition ?? NO_VALUE}</td>
                    <td className={`${TD} text-slate-500 dark:text-slate-400 tabular-nums`}>
                      {formatDate(r.latest_observation)}
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </ScrollX>
      </QueryBoundary>
    </Panel>
  )
}
