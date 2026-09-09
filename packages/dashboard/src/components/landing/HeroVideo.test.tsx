// packages/dashboard/src/components/landing/HeroVideo.test.tsx
//
// The fallback ladder is the whole point of this component, so it is the part
// that gets tested. Each case asserts that the video element is genuinely
// absent from the DOM — not merely hidden with CSS, which would still download
// and decode the file.
import { act, render, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import HeroVideo from './HeroVideo'

/**
 * Install a matchMedia stub driven by a predicate over the query string.
 * jsdom has no media query engine at all, so without this every query reports
 * `matches: false` and the component would look like it works for the wrong
 * reason.
 */
function mockMatchMedia(matches: (query: string) => boolean) {
  vi.stubGlobal(
    'matchMedia',
    vi.fn((query: string) => ({
      matches: matches(query),
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

const REDUCED = '(prefers-reduced-motion: reduce)'

beforeEach(() => {
  // jsdom implements neither of these; without stubs the component throws
  // rather than falling back.
  vi.stubGlobal(
    'IntersectionObserver',
    class {
      observe = vi.fn()
      disconnect = vi.fn()
      unobserve = vi.fn()
      takeRecords = vi.fn(() => [])
      root = null
      rootMargin = ''
      thresholds = []
    }
  )
  // jsdom's HTMLMediaElement.play is not implemented and throws.
  window.HTMLMediaElement.prototype.play = vi.fn().mockResolvedValue(undefined)
  window.HTMLMediaElement.prototype.pause = vi.fn()
})

afterEach(() => {
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
})

const video = () => document.querySelector('video')

describe('HeroVideo fallbacks', () => {
  it('plays on a wide viewport with motion allowed', async () => {
    mockMatchMedia((q) => q !== REDUCED) // width matches, reduced-motion does not
    render(<HeroVideo />)
    await waitFor(() => expect(video()).not.toBeNull())
  })

  it('renders no video element under prefers-reduced-motion', async () => {
    mockMatchMedia(() => true) // both width AND reduced-motion match
    render(<HeroVideo />)
    // Give the decision effect a chance to run before asserting absence,
    // otherwise this would pass even if the logic were broken.
    await waitFor(() => expect(document.querySelector('.gv-vignette')).not.toBeNull())
    expect(video()).toBeNull()
  })

  it('renders no video element below the mobile breakpoint', async () => {
    mockMatchMedia(() => false) // width query fails, reduced-motion also false
    render(<HeroVideo />)
    await waitFor(() => expect(document.querySelector('.gv-vignette')).not.toBeNull())
    expect(video()).toBeNull()
  })

  it('renders no video element when the browser reports Save-Data', async () => {
    mockMatchMedia((q) => q !== REDUCED)
    Object.defineProperty(navigator, 'connection', {
      value: { saveData: true },
      configurable: true,
    })
    render(<HeroVideo />)
    await waitFor(() => expect(document.querySelector('.gv-vignette')).not.toBeNull())
    expect(video()).toBeNull()
    // @ts-expect-error -- removing the stub property again
    delete (navigator as Navigator).connection
  })

  it('renders no video element on a 2g connection', async () => {
    mockMatchMedia((q) => q !== REDUCED)
    Object.defineProperty(navigator, 'connection', {
      value: { effectiveType: '2g' },
      configurable: true,
    })
    render(<HeroVideo />)
    await waitFor(() => expect(document.querySelector('.gv-vignette')).not.toBeNull())
    expect(video()).toBeNull()
    // @ts-expect-error -- removing the stub property again
    delete (navigator as Navigator).connection
  })

  // The backdrop and scrims are what make the hero text readable. They must be
  // present in every branch, including the ones where no video ever loads.
  it.each([
    ['reduced motion', () => true],
    ['mobile', () => false],
    ['desktop', (q: string) => q !== REDUCED],
  ])('always paints the contrast scrim (%s)', async (_label, predicate) => {
    mockMatchMedia(predicate as (q: string) => boolean)
    const { container } = render(<HeroVideo />)
    await waitFor(() => {
      expect(container.querySelector('.gv-vignette')).not.toBeNull()
    })
    expect(container.querySelector('.gv-grid')).not.toBeNull()
  })

  it('is hidden from assistive technology, being purely decorative', () => {
    mockMatchMedia(() => false)
    const { container } = render(<HeroVideo />)
    expect(container.firstElementChild).toHaveAttribute('aria-hidden', 'true')
  })

  it('falls back to the backdrop when the video element errors', async () => {
    mockMatchMedia((q) => q !== REDUCED)
    render(<HeroVideo />)
    await waitFor(() => expect(video()).not.toBeNull())

    // Simulate the file being missing or undecodable.
    const el = video()!
    // act(): the error handler sets state, and React warns about updates that
    // happen outside its batching window.
    act(() => {
      el.dispatchEvent(new Event('error'))
    })

    await waitFor(() => expect(video()).toBeNull())
  })

  it('offers webm before mp4, so the smaller file wins where supported', async () => {
    mockMatchMedia((q) => q !== REDUCED)
    render(<HeroVideo />)
    await waitFor(() => expect(video()).not.toBeNull())

    const types = Array.from(video()!.querySelectorAll('source')).map((s) =>
      s.getAttribute('type')
    )
    expect(types).toEqual(['video/webm', 'video/mp4'])
  })

  it('is muted, looping and inline, the preconditions for autoplay', async () => {
    mockMatchMedia((q) => q !== REDUCED)
    render(<HeroVideo />)
    await waitFor(() => expect(video()).not.toBeNull())

    const el = video()!
    // A non-muted background video is blocked by every modern browser.
    expect(el).toHaveAttribute('loop')
    expect(el).toHaveAttribute('playsinline')
    expect(el.muted).toBe(true)
    // A poster means the frame is filled before the first video frame decodes.
    expect(el).toHaveAttribute('poster')
    // Not "auto": the hero must not compete with the page for bandwidth.
    expect(el).toHaveAttribute('preload', 'metadata')
  })
})
