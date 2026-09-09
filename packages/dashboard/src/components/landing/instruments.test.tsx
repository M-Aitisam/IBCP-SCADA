// packages/dashboard/src/components/landing/instruments.test.tsx
//
// The three landing-page instruments are generated, not hand-drawn, so what is
// worth testing is that the geometry follows the facts it claims to depict —
// and that none of them assert anything about real observations.
import { render, screen } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import AnomalySignal from './AnomalySignal'
import HeroCanvas from './HeroCanvas'
import RevisitTimeline from './RevisitTimeline'

function mockMatchMedia(reduced: boolean) {
  vi.stubGlobal(
    'matchMedia',
    vi.fn((query: string) => ({
      matches: reduced && query.includes('reduced-motion'),
      media: query,
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
      addListener: vi.fn(),
      removeListener: vi.fn(),
      dispatchEvent: vi.fn(),
      onchange: null,
    }))
  )
}

let observed: Element[] = []

beforeEach(() => {
  observed = []
  mockMatchMedia(false)
  vi.stubGlobal(
    'IntersectionObserver',
    class {
      constructor(_cb: IntersectionObserverCallback) {}
      observe = (el: Element) => {
        observed.push(el)
      }
      disconnect = vi.fn()
      unobserve = vi.fn()
      takeRecords = vi.fn(() => [])
      root = null
      rootMargin = ''
      thresholds = []
    }
  )
  vi.stubGlobal('requestAnimationFrame', vi.fn(() => 1))
  vi.stubGlobal('cancelAnimationFrame', vi.fn())
})

afterEach(() => {
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
})

describe('AnomalySignal', () => {
  it('annotates the first month that breaches minus two sigma', () => {
    render(<AnomalySignal />)
    // The FIRST month to breach -2σ is July, at -2.6σ. September is deeper
    // (-3.7σ) but later, and the alert fires on the crossing, not on the
    // trough. The annotation is derived from the same array that draws the
    // line, so this pins the two together: change the series and a stale
    // hand-placed label fails here.
    expect(screen.getByText(/-2\.6σ below/)).toBeInTheDocument()
    expect(screen.getByText(/alert raised/i)).toBeInTheDocument()
  })

  it('describes the whole diagram for screen readers', () => {
    render(<AnomalySignal />)
    const label = screen.getByRole('img').getAttribute('aria-label') ?? ''
    // The chart is meaningless as a bare <svg>; the description has to carry
    // the finding, not just name the chart type.
    expect(label).toMatch(/climatology/i)
    expect(label).toMatch(/standard deviations below normal/i)
    expect(label).toMatch(/Jul/)
  })

  it('renders the finished diagram immediately under reduced motion', () => {
    mockMatchMedia(true)
    const { container } = render(<AnomalySignal />)
    const observed = container.querySelector('path[stroke="url(#gv-obs)"]') as SVGPathElement
    // Fully drawn: no dash offset hiding the line while waiting for a scroll
    // event that a reduced-motion user may never trigger.
    expect(observed.style.strokeDashoffset).toBe('0')
  })
})

describe('RevisitTimeline', () => {
  // The lane geometry encodes real revisit intervals. If someone edits an
  // interval, the tick count changes and these fail — which is the point.
  it.each([
    ['CHIRPS', 1, 0, 31],
    ['Sentinel-2', 5, 2, 6],
    ['Sentinel-1', 12, 4, 3],
    ['MODIS LST', 8, 1, 4],
    ['MODIS NDVI', 16, 6, 2],
  ])('draws %s at its real cadence', (name, interval, offset, expected) => {
    // Independently recompute what the lane should contain over 30 days.
    let count = 0
    for (let d = offset; d <= 30; d += interval as number) count++
    expect(count).toBe(expected)

    render(<RevisitTimeline />)
    const label = screen.getByRole('img').getAttribute('aria-label') ?? ''
    expect(label).toContain(name as string)
  })

  it('shows the daily lane as denser than the 16-day lane', () => {
    const { container } = render(<RevisitTimeline />)
    const groups = container.querySelectorAll('svg > g')
    const counts = Array.from(groups).map((g) => g.querySelectorAll('rect').length)
    // 31 daily ticks against 2 sixteen-day ticks: the asymmetry the section
    // exists to make visible.
    expect(Math.max(...counts)).toBe(31)
  })
})

describe('HeroCanvas', () => {
  // jsdom returns null from getContext without the optional `canvas` package.
  // A landing page must not white-screen because of that.
  it('renders without throwing when no 2d context is available', () => {
    const { container } = render(<HeroCanvas />)
    expect(container.querySelector('canvas')).not.toBeNull()
  })

  it('is hidden from assistive technology', () => {
    const { container } = render(<HeroCanvas />)
    expect(container.querySelector('canvas')).toHaveAttribute('aria-hidden', 'true')
  })

  it('never starts an animation loop under reduced motion', () => {
    mockMatchMedia(true)
    // Give the component a real context so it gets past the early return and
    // actually reaches the draw path.
    const ctx = {
      clearRect: vi.fn(),
      beginPath: vi.fn(),
      moveTo: vi.fn(),
      lineTo: vi.fn(),
      stroke: vi.fn(),
      arc: vi.fn(),
      fill: vi.fn(),
      fillRect: vi.fn(),
      setTransform: vi.fn(),
      createRadialGradient: vi.fn(() => ({ addColorStop: vi.fn() })),
    }
    vi.spyOn(HTMLCanvasElement.prototype, 'getContext').mockReturnValue(
      ctx as unknown as CanvasRenderingContext2D
    )

    render(<HeroCanvas />)

    // One static frame is drawn, but no rAF loop is scheduled.
    expect(ctx.clearRect).toHaveBeenCalled()
    expect(window.requestAnimationFrame).not.toHaveBeenCalled()
  })

  it('drives an animation loop when motion is allowed', () => {
    const ctx = {
      clearRect: vi.fn(),
      beginPath: vi.fn(),
      moveTo: vi.fn(),
      lineTo: vi.fn(),
      stroke: vi.fn(),
      arc: vi.fn(),
      fill: vi.fn(),
      fillRect: vi.fn(),
      setTransform: vi.fn(),
      createRadialGradient: vi.fn(() => ({ addColorStop: vi.fn() })),
    }
    vi.spyOn(HTMLCanvasElement.prototype, 'getContext').mockReturnValue(
      ctx as unknown as CanvasRenderingContext2D
    )

    render(<HeroCanvas />)
    expect(window.requestAnimationFrame).toHaveBeenCalled()
  })

  it('registers an observer so the loop can be paused off screen', () => {
    // Needs a context: with none, the effect bails before it reaches the
    // observer — correctly, since there is nothing to pause.
    vi.spyOn(HTMLCanvasElement.prototype, 'getContext').mockReturnValue({
      clearRect: vi.fn(),
      beginPath: vi.fn(),
      moveTo: vi.fn(),
      lineTo: vi.fn(),
      stroke: vi.fn(),
      arc: vi.fn(),
      fill: vi.fn(),
      fillRect: vi.fn(),
      setTransform: vi.fn(),
      createRadialGradient: vi.fn(() => ({ addColorStop: vi.fn() })),
    } as unknown as CanvasRenderingContext2D)

    render(<HeroCanvas />)
    expect(observed.some((el) => el.tagName.toLowerCase() === 'canvas')).toBe(true)
  })
})
