// packages/dashboard/src/components/geovision/primitives.test.tsx
import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'

import {
  DatasetStateBadge,
  EmptyState,
  ErrorState,
  formatDate,
  formatValue,
  LoadingState,
  NO_VALUE,
  QueryBoundary,
  TrendIndicator,
  errorMessage,
  relativeDays,
} from './primitives'

describe('value formatting', () => {
  it('renders an absent value as a dash, never as zero', () => {
    // A zero would read as "we measured zero"; the dash says "no observation".
    expect(formatValue(null)).toBe(NO_VALUE)
    expect(formatValue(undefined)).toBe(NO_VALUE)
    expect(formatValue(0, 'mm', 1)).toBe('0.0 mm')
  })

  it('appends real units but not pseudo-units', () => {
    expect(formatValue(12.345, 'mm', 1)).toBe('12.3 mm')
    // "index" and "class" are not units anyone wants printed after a number.
    expect(formatValue(0.4321, 'index', 3)).toBe('0.432')
    expect(formatValue('Healthy', 'class')).toBe('Healthy')
  })

  it('formats and guards dates', () => {
    expect(formatDate(null)).toBe(NO_VALUE)
    expect(formatDate('2026-08-18')).toBe('18 Aug 2026')
    // A malformed date is echoed rather than rendered as "Invalid Date".
    expect(formatDate('not-a-date')).toBe('not-a-date')
  })

  it('describes freshness in human terms', () => {
    expect(relativeDays(null)).toBe(NO_VALUE)
    expect(relativeDays(0)).toBe('today')
    expect(relativeDays(1)).toBe('1 day ago')
    expect(relativeDays(24)).toBe('24 days ago')
  })
})

describe('error messages', () => {
  it('prefers the API detail', () => {
    expect(
      errorMessage({ response: { status: 400, data: { detail: 'unknown metric' } } })
    ).toBe('unknown metric')
  })

  it('explains an unreachable backend rather than echoing a network error', () => {
    expect(errorMessage({ message: 'Network Error' })).toContain('Cannot reach the server')
  })

  it('explains a 503 as a data-source problem', () => {
    expect(errorMessage({ response: { status: 503 } })).toContain('temporarily unavailable')
  })
})

describe('dataset state badges', () => {
  it('pairs every state with a readable word, not colour alone', () => {
    for (const state of ['HEALTHY', 'WARNING', 'NO NEW DATA', 'NO DATA', 'FAILED'] as const) {
      const { unmount } = render(<DatasetStateBadge state={state} />)
      expect(screen.getByText(state)).toBeInTheDocument()
      unmount()
    }
  })
})

describe('trend indicator', () => {
  it('says "insufficient data" instead of showing a misleading comparison', () => {
    render(
      <TrendIndicator changePct={null} direction={null} status="insufficient_data" />
    )
    expect(screen.getByText(/insufficient data/i)).toBeInTheDocument()
  })

  it('signs the percentage', () => {
    const { unmount } = render(
      <TrendIndicator changePct={10.3} direction="up" interpretation="improving" />
    )
    expect(screen.getByText('+10.3%')).toBeInTheDocument()
    unmount()

    render(<TrendIndicator changePct={-4.2} direction="down" interpretation="declining" />)
    expect(screen.getByText('-4.2%')).toBeInTheDocument()
  })
})

describe('QueryBoundary', () => {
  const child = <p>real content</p>

  it('shows loading first', () => {
    render(
      <QueryBoundary isLoading isError={false}>
        {child}
      </QueryBoundary>
    )
    expect(screen.getByRole('status')).toBeInTheDocument()
    expect(screen.queryByText('real content')).not.toBeInTheDocument()
  })

  it('shows an error with the detail', () => {
    render(
      <QueryBoundary
        isLoading={false}
        isError
        error={{ response: { status: 500, data: { detail: 'boom' } } }}
      >
        {child}
      </QueryBoundary>
    )
    expect(screen.getByRole('alert')).toBeInTheDocument()
    expect(screen.getByText('boom')).toBeInTheDocument()
  })

  it('distinguishes empty from error', () => {
    render(
      <QueryBoundary isLoading={false} isError={false} isEmpty emptyTitle="No NDVI">
        {child}
      </QueryBoundary>
    )
    expect(screen.getByText('No NDVI')).toBeInTheDocument()
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
  })

  it('renders children only when data is usable', () => {
    render(
      <QueryBoundary isLoading={false} isError={false} isEmpty={false}>
        {child}
      </QueryBoundary>
    )
    expect(screen.getByText('real content')).toBeInTheDocument()
  })

  it('never renders nothing', () => {
    // The whole point: every combination produces some visible state.
    for (const props of [
      { isLoading: true, isError: false },
      { isLoading: false, isError: true },
      { isLoading: false, isError: false, isEmpty: true },
      { isLoading: false, isError: false, isEmpty: false },
    ]) {
      const { container, unmount } = render(
        <QueryBoundary {...props}>{child}</QueryBoundary>
      )
      expect(container.textContent?.trim().length).toBeGreaterThan(0)
      unmount()
    }
  })
})

describe('standalone states', () => {
  it('renders loading, empty and error', () => {
    const { unmount } = render(<LoadingState label="Loading NDVI" />)
    expect(screen.getByText(/loading ndvi/i)).toBeInTheDocument()
    unmount()

    const e = render(<EmptyState title="No NDVI observation available" />)
    expect(screen.getByText('No NDVI observation available')).toBeInTheDocument()
    e.unmount()

    render(<ErrorState title="Unable to retrieve NDVI" />)
    expect(screen.getByText('Unable to retrieve NDVI')).toBeInTheDocument()
  })
})
