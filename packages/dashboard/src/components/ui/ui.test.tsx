// packages/dashboard/src/components/ui/ui.test.tsx
//
// These tests cover the safety-relevant behaviour of the primitives, not their
// styling. The rules being locked down here all come from the same principle:
// the interface must never present an absence of data as a reading.
import { render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import Badge, { severityTone } from './Badge'
import Button from './Button'
import { Field, Input } from './Field'
import Metric from './Metric'
import { NoDataState } from './States'

describe('severityTone', () => {
  it('maps known backend severities onto the ordered scale', () => {
    expect(severityTone('NONE')).toBe('ok')
    expect(severityTone('MINOR')).toBe('watch')
    expect(severityTone('MAJOR')).toBe('high')
    expect(severityTone('CRITICAL')).toBe('critical')
  })

  it('is case insensitive, since severities arrive from several endpoints', () => {
    expect(severityTone('critical')).toBe('critical')
  })

  // The important one. A missing, null or unrecognised severity must never be
  // optimistically shown as green: that would render an unobserved district as
  // an all-clear.
  it.each([null, undefined, '', 'SOMETHING_NEW'])(
    'falls back to unknown, not ok, for %p',
    (input) => {
      expect(severityTone(input as string | null | undefined)).toBe('unknown')
    }
  )
})

describe('Badge', () => {
  it('carries a text label so severity is not conveyed by colour alone', () => {
    render(<Badge tone="critical">Critical</Badge>)
    expect(screen.getByText('Critical')).toBeInTheDocument()
  })
})

describe('Metric', () => {
  it('renders an em dash for a missing value rather than a zero', () => {
    render(<Metric label="NDVI" value={null} />)
    expect(screen.getByText('\u2014')).toBeInTheDocument()
    expect(screen.queryByText('0.00')).not.toBeInTheDocument()
    expect(screen.getByText(/no data in window/i)).toBeInTheDocument()
  })

  it('renders a real zero as a value, distinctly from missing', () => {
    render(<Metric label="Alerts" value={0} />)
    expect(screen.getByText('0.00')).toBeInTheDocument()
    expect(screen.queryByText(/no data in window/i)).not.toBeInTheDocument()
  })

  it('suppresses the delta when there is no value to compare against', () => {
    render(<Metric label="NDVI" value={null} delta={0.4} />)
    expect(screen.queryByText(/0\.40/)).not.toBeInTheDocument()
  })

  // Direction of "good" is per-metric: NDVI rising is healthy, a drought index
  // rising is not. A single hardcoded green-for-up would be wrong half the time.
  it('honours higherIsBetter when colouring the delta', () => {
    const { container: up } = render(
      <Metric label="NDVI" value={0.5} delta={0.1} higherIsBetter />
    )
    expect(up.querySelector('.text-sev-ok')).not.toBeNull()

    const { container: down } = render(
      <Metric label="Drought" value={0.5} delta={0.1} higherIsBetter={false} />
    )
    expect(down.querySelector('.text-sev-high')).not.toBeNull()
  })
})

describe('Button', () => {
  it('cannot be clicked while loading, so a submit cannot fire twice', async () => {
    const onClick = vi.fn()
    render(
      <Button loading onClick={onClick}>
        Run ingestion
      </Button>
    )
    const button = screen.getByRole('button', { name: /run ingestion/i })
    expect(button).toBeDisabled()
    expect(button).toHaveAttribute('aria-busy', 'true')
    button.click()
    expect(onClick).not.toHaveBeenCalled()
  })
})

describe('Field', () => {
  it('wires label, control and error together for screen readers', () => {
    render(
      <Field label="Email" error="Enter a valid address">
        {({ id, describedBy, invalid }) => (
          <Input id={id} aria-describedby={describedBy} aria-invalid={invalid} />
        )}
      </Field>
    )

    const input = screen.getByLabelText('Email')
    expect(input).toHaveAttribute('aria-invalid', 'true')
    // The message must be reachable from the input, not merely on screen.
    const describedBy = input.getAttribute('aria-describedby')
    expect(describedBy).toBeTruthy()
    expect(document.getElementById(describedBy!)).toHaveTextContent(
      'Enter a valid address'
    )
  })
})

describe('NoDataState', () => {
  it('states that a gap is coverage, not a zero reading', () => {
    render(<NoDataState />)
    expect(screen.getByText(/not a reading of zero/i)).toBeInTheDocument()
  })
})
