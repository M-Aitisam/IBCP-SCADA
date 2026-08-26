// packages/dashboard/src/components/geovision/KpiGrid.test.tsx
//
// Guards the project's central rule: the dashboard shows real backend data or
// an explicit absence, never a stand-in number.
import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'

import type { Kpi, Overview } from '@/services/api/geovisionApi'
import KpiGrid from './KpiGrid'
import { NO_VALUE } from './primitives'

function kpi(over: Partial<Kpi> = {}): Kpi {
  return {
    metric: 'ndvi',
    label: 'NDVI',
    unit: 'index',
    description: 'Normalized Difference Vegetation Index',
    value: 0.434,
    observation_date: '2026-08-18',
    dataset: 'mod13q1',
    observations: 8,
    regions: 5,
    aggregation: 'mean',
    change_pct: 4.2,
    direction: 'up',
    interpretation: 'improving',
    trend_status: 'ok',
    status: 'ok',
    ...over,
  }
}

function overview(kpis: Record<string, Kpi>): Overview {
  return {
    period: { start: '2025-08-25', end: '2026-08-24', label: '1y', bucket: 'week' },
    filters: {},
    kpis,
    coverage: { regions_monitored: 5, provinces: 2, observations: 5778 },
    freshness: {
      latest_observation: '2026-08-18',
      last_ingested_at: '2026-08-20T23:48:45Z',
      age_days: 6,
      as_of: '2026-08-24',
      status: 'ok',
    },
    datasets: { total: 5, healthy: 3, failed: 0, states: {} },
    data_source: 'timestampdb',
  }
}

describe('KpiGrid', () => {
  it('shows the value with its observation date and source', () => {
    render(<KpiGrid overview={overview({ ndvi: kpi() })} isLoading={false} />)

    expect(screen.getByText('0.434')).toBeInTheDocument()
    // The observation date is what stops a reader assuming the value is today's.
    expect(screen.getByText(/Observed 18 Aug 2026/)).toBeInTheDocument()
    expect(screen.getByText(/mod13q1/)).toBeInTheDocument()
  })

  it('renders a dash, not a zero, when a metric has no observation', () => {
    render(
      <KpiGrid
        overview={overview({
          lst_night_c: kpi({
            metric: 'lst_night_c',
            label: 'Land surface temperature (night)',
            value: null,
            observation_date: null,
            status: 'no_data',
            observations: 0,
            regions: 0,
          }),
        })}
        isLoading={false}
      />
    )
    expect(screen.getAllByText(NO_VALUE).length).toBeGreaterThan(0)
    expect(screen.getAllByText(/No observation available/).length).toBeGreaterThan(0)
    // Nothing may invent a reading for a metric that has none.
    expect(screen.queryByText('0.0')).not.toBeInTheDocument()
  })

  it('reports insufficient data rather than a fabricated trend', () => {
    render(
      <KpiGrid
        overview={overview({
          ndvi: kpi({ change_pct: null, direction: null, trend_status: 'insufficient_data' }),
        })}
        isLoading={false}
      />
    )
    expect(screen.getByText(/insufficient data/i)).toBeInTheDocument()
  })

  it('shows no satellite numbers at all when the overview is missing', () => {
    // An unloaded dashboard must be visibly empty, not plausibly populated.
    const { container } = render(<KpiGrid overview={undefined} isLoading={false} />)
    expect(container.textContent).toContain(NO_VALUE)
    expect(container.textContent).not.toMatch(/\d+\.\d+/)
  })

  it('shows a loading state instead of empty cards while fetching', () => {
    render(<KpiGrid overview={undefined} isLoading />)
    expect(screen.getByRole('status')).toBeInTheDocument()
  })

  it('labels crop condition as derived from NDVI', () => {
    render(
      <KpiGrid
        overview={overview({
          crop_condition: kpi({
            metric: 'crop_condition',
            label: 'Crop condition',
            unit: 'class',
            value: 'Healthy',
            numeric_basis: 0.48,
            basis_metric: 'ndvi',
          }),
        })}
        isLoading={false}
      />
    )
    expect(screen.getByText('Healthy')).toBeInTheDocument()
  })
})
