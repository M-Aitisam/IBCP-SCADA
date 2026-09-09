// packages/dashboard/src/store/geovisionFilters.test.ts
import { beforeEach, describe, expect, it } from 'vitest'

import {
  queryKey,
  toQuery,
  useGeovisionFilters,
  windowLabel,
} from './geovisionFilters'

const store = () => useGeovisionFilters.getState()

beforeEach(() => {
  store().reset()
})

describe('cascading geography', () => {
  it('clears narrower levels when a broader one changes', () => {
    // Otherwise a district from the previous province would survive the change
    // and every panel would query an impossible combination.
    store().setProvince('Sindh')
    store().setDistrict('Hyderabad District')
    store().selectRegion('23683')
    expect(store().district).toBe('Hyderabad District')

    store().setProvince('Punjab')
    expect(store().district).toBeNull()
    expect(store().tehsil).toBeNull()
    expect(store().selectedRegionId).toBeNull()
  })

  it('clears the tehsil and selection when the district changes', () => {
    store().setProvince('Sindh')
    store().setDistrict('Hyderabad District')
    store().setTehsil('Latifabad')
    store().selectRegion('23683')

    store().setDistrict('Larkana District')
    expect(store().tehsil).toBeNull()
    expect(store().selectedRegionId).toBeNull()
  })

  it('keeps the geography when only the selected region changes', () => {
    // Selecting a region opens the detail panel; it must not reset the filters
    // the rest of the dashboard is showing.
    store().setProvince('Sindh')
    store().selectRegion('23683')
    expect(store().province).toBe('Sindh')
  })
})

describe('time window', () => {
  it('drops a custom window when a named range is chosen', () => {
    store().setCustomWindow('2020-01-01', '2020-12-31')
    expect(toQuery(store())).toMatchObject({ start: '2020-01-01', end: '2020-12-31' })

    store().setRange('30d')
    const q = toQuery(store())
    expect(q.range).toBe('30d')
    expect(q.start).toBeUndefined()
    expect(q.end).toBeUndefined()
  })

  it('ignores a half-filled custom window', () => {
    // A date picker mid-edit must not silently query from the beginning of time.
    store().setRange('1y')
    store().setCustomWindow('2020-01-01', null)
    const q = toQuery(store())
    expect(q.range).toBe('1y')
    expect(q.start).toBeUndefined()
  })

  it('labels both named and custom windows', () => {
    store().setRange('10y')
    expect(windowLabel(store())).toBe('Last 10 years')
    store().setCustomWindow('2020-01-01', '2020-12-31')
    expect(windowLabel(store())).toBe('2020-01-01 → 2020-12-31')
  })
})

describe('query key', () => {
  it('changes when a filter that affects observations changes', () => {
    const before = queryKey(store())
    store().setProvince('Sindh')
    expect(queryKey(store())).not.toBe(before)
  })

  it('does NOT change when only the selected region changes', () => {
    // This is what stops opening the detail panel from re-fetching the map,
    // the KPI row and every chart.
    const before = queryKey(store())
    store().selectRegion('23683')
    expect(queryKey(store())).toBe(before)
  })

  it('does not change when the map layer changes', () => {
    // Switching thematic layer re-colours existing data; it needs no new fetch.
    const before = queryKey(store())
    store().setMapLayer('rainfall_mm')
    expect(queryKey(store())).toBe(before)
  })
})

describe('query serialisation', () => {
  it('carries the active geography', () => {
    store().setProvince('Balochistan')
    store().setDistrict('Sibi District')
    expect(toQuery(store())).toMatchObject({
      province: 'Balochistan',
      district: 'Sibi District',
    })
  })
})
