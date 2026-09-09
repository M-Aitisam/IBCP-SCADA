// packages/dashboard/src/components/geovision/GlobalFilters.tsx
'use client'

import { Search, X } from 'lucide-react'
import { useMemo, useRef, useState } from 'react'

import type { HierarchyResponse } from '@/services/api/geovisionApi'
import { useGeovisionFilters } from '@/store/geovisionFilters'

/**
 * Global geography filters plus region search.
 *
 * Cascading: choosing a province narrows the district list to that province,
 * and choosing a district narrows the tehsils. The options come from the
 * hierarchy the backend derives from stored observations, so the filter can
 * never offer a region that has no data behind it.
 */
export default function GlobalFilters({
  hierarchy,
  isLoading,
}: {
  hierarchy?: HierarchyResponse
  isLoading: boolean
}) {
  const province = useGeovisionFilters((s) => s.province)
  const district = useGeovisionFilters((s) => s.district)
  const tehsil = useGeovisionFilters((s) => s.tehsil)
  const setProvince = useGeovisionFilters((s) => s.setProvince)
  const setDistrict = useGeovisionFilters((s) => s.setDistrict)
  const setTehsil = useGeovisionFilters((s) => s.setTehsil)
  const selectRegion = useGeovisionFilters((s) => s.selectRegion)
  const reset = useGeovisionFilters((s) => s.reset)

  const [search, setSearch] = useState('')
  const [open, setOpen] = useState(false)
  const blurTimer = useRef<ReturnType<typeof setTimeout> | null>(null)

  // Memoised because the `?? []` fallback creates a fresh array on every
  // render, which would invalidate both useMemo hooks below every time.
  const provinces = useMemo(() => hierarchy?.provinces ?? [], [hierarchy])

  const districts = useMemo(() => {
    if (!province) {
      // No province chosen: offer every district, so search-by-district still
      // works without forcing a province selection first.
      return provinces.flatMap((p) => p.districts.map((d) => d.district))
    }
    return provinces.find((p) => p.province === province)?.districts.map((d) => d.district) ?? []
  }, [provinces, province])

  const tehsils = useMemo(() => {
    if (!district) return []
    for (const p of provinces) {
      const match = p.districts.find((d) => d.district === district)
      if (match) return match.tehsils
    }
    return []
  }, [provinces, district])

  // Search matches any level, so "Hyderabad" finds the district whether the
  // user thinks of it as a district or a region name.
  const matches = useMemo(() => {
    const term = search.trim().toLowerCase()
    if (term.length < 2 || !hierarchy) return []
    return hierarchy.regions
      .filter((r) =>
        [r.name, r.district, r.province, r.tehsil]
          .filter(Boolean)
          .some((field) => field!.toLowerCase().includes(term))
      )
      .slice(0, 8)
  }, [search, hierarchy])

  const hasFilters = Boolean(province || district || tehsil)

  function pick(regionId: string, prov: string | null, dist: string | null) {
    // Set the geography to match the chosen region, then open its panel — the
    // "search → zoom → select → detail" flow in one action.
    if (prov) setProvince(prov)
    if (dist) setDistrict(dist)
    selectRegion(regionId)
    setSearch('')
    setOpen(false)
  }

  const selectClass =
    'px-2.5 py-1.5 text-xs rounded border border-line-strong bg-surface text-content disabled:opacity-50 focus:outline-none focus-visible:ring-2 focus-visible:ring-brand min-w-[9rem]'

  return (
    <div className="bg-surface border-b border-line">
      <div className="max-w-[1600px] mx-auto px-4 py-2.5 flex flex-wrap items-center gap-2">
        <div className="relative">
          <Search
            className="absolute left-2 top-1/2 -translate-y-1/2 w-3.5 h-3.5 text-content-subtle"
            aria-hidden="true"
          />
          <input
            type="search"
            value={search}
            onChange={(e) => {
              setSearch(e.target.value)
              setOpen(true)
            }}
            onFocus={() => setOpen(true)}
            onBlur={() => {
              // Delay so a click on a result lands before the list unmounts.
              blurTimer.current = setTimeout(() => setOpen(false), 150)
            }}
            placeholder="Search region…"
            aria-label="Search for a region"
            className="pl-7 pr-2 py-1.5 text-xs rounded border border-line-strong bg-surface text-content focus:outline-none focus-visible:ring-2 focus-visible:ring-brand w-52"
          />
          {open && matches.length > 0 && (
            <ul
              className="absolute z-[1200] mt-1 w-72 max-h-64 overflow-y-auto rounded border border-line bg-surface shadow-lg"
              role="listbox"
            >
              {matches.map((r) => (
                <li key={r.region_id}>
                  <button
                    type="button"
                    onMouseDown={(e) => e.preventDefault()}
                    onClick={() => {
                      if (blurTimer.current) clearTimeout(blurTimer.current)
                      pick(r.region_id, r.province, r.district)
                    }}
                    className="w-full text-left px-3 py-2 text-xs hover:bg-surface-sunken focus:outline-none focus-visible:bg-surface-sunken"
                  >
                    <span className="font-medium text-content">
                      {r.name}
                    </span>
                    <span className="block text-[11px] text-content-subtle">
                      {[r.district, r.province].filter(Boolean).join(' · ')}
                      {r.observations > 0 && ` · ${r.observations.toLocaleString()} obs`}
                    </span>
                  </button>
                </li>
              ))}
            </ul>
          )}
        </div>

        <label className="sr-only" htmlFor="gv-province">
          Province
        </label>
        <select
          id="gv-province"
          value={province ?? ''}
          onChange={(e) => setProvince(e.target.value || null)}
          disabled={isLoading}
          className={selectClass}
        >
          <option value="">All provinces</option>
          {provinces.map((p) => (
            <option key={p.province} value={p.province}>
              {p.province}
            </option>
          ))}
        </select>

        <label className="sr-only" htmlFor="gv-district">
          District
        </label>
        <select
          id="gv-district"
          value={district ?? ''}
          onChange={(e) => setDistrict(e.target.value || null)}
          disabled={isLoading || districts.length === 0}
          className={selectClass}
        >
          <option value="">All districts</option>
          {districts.map((d) => (
            <option key={d} value={d}>
              {d}
            </option>
          ))}
        </select>

        <label className="sr-only" htmlFor="gv-tehsil">
          Tehsil
        </label>
        <select
          id="gv-tehsil"
          value={tehsil ?? ''}
          onChange={(e) => setTehsil(e.target.value || null)}
          disabled={isLoading || tehsils.length === 0}
          className={selectClass}
          // Disabled with an explanatory title rather than hidden: the tier
          // exists in the model, the current boundary source just has no ADM3
          // for Pakistan, and hiding it would misrepresent that.
          title={
            tehsils.length === 0
              ? 'No tehsil boundaries in the configured region source'
              : undefined
          }
        >
          <option value="">
            {tehsils.length === 0 ? 'Tehsil (none available)' : 'All tehsils'}
          </option>
          {tehsils.map((t) => (
            <option key={t} value={t}>
              {t}
            </option>
          ))}
        </select>

        {hasFilters && (
          <button
            type="button"
            onClick={reset}
            className="inline-flex items-center gap-1 px-2 py-1.5 text-xs rounded border border-line-strong text-content-muted hover:bg-surface-sunken focus:outline-none focus-visible:ring-2 focus-visible:ring-brand"
          >
            <X className="w-3 h-3" aria-hidden="true" />
            Clear
          </button>
        )}
      </div>
    </div>
  )
}
