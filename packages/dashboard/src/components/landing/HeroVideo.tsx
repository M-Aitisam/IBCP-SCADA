// packages/dashboard/src/components/landing/HeroVideo.tsx
'use client'

// The hero background video system.
//
// A background video is the single easiest way to make a page feel slow, drain
// a phone battery, and make text unreadable. This component is the answer to
// each of those, in layers, so the page is correct even when the video never
// plays:
//
//   1. CSS backdrop      always painted — the page is never blank
//   2. poster image      shown while the video buffers
//   3. video             fades in only once it can actually play
//   4. gradient scrims   guarantee text contrast whatever the frame shows
//
// Three conditions skip the video entirely and leave the backdrop showing:
//
//   - prefers-reduced-motion: a full-bleed moving background is exactly the
//     motion this setting exists to suppress
//   - small viewports: mobile data and battery are not worth a decorative loop
//   - saveData / slow connection, where the browser reports it
//
// Nothing here is decorative-only: the backdrop is a real design, so the
// fallback is a finished look rather than a degraded one.
import { useEffect, useRef, useState } from 'react'

export interface HeroVideoProps {
  /** Path under /public. Absent file → backdrop only, no console noise. */
  src?: string
  webmSrc?: string
  poster?: string
  /** Below this width the video is skipped entirely. */
  mobileBreakpoint?: number
  className?: string
}

// Deliberately not `preload="auto"`: metadata is enough to start playback
// decisions without committing to the whole file on first paint.
const PRELOAD: 'none' | 'metadata' | 'auto' = 'metadata'

export default function HeroVideo({
  src = '/videos/geovision-hero.mp4',
  webmSrc = '/videos/geovision-hero.webm',
  poster = '/videos/geovision-hero-poster.jpg',
  mobileBreakpoint = 768,
  className = '',
}: HeroVideoProps) {
  const videoRef = useRef<HTMLVideoElement | null>(null)
  const [shouldPlay, setShouldPlay] = useState(false)
  const [ready, setReady] = useState(false)
  const [failed, setFailed] = useState(false)

  // Decide whether the video is appropriate at all. Runs in an effect so the
  // server-rendered markup is identical for everyone — deciding during render
  // would cause a hydration mismatch.
  useEffect(() => {
    if (typeof window === 'undefined') return

    const motionQuery = window.matchMedia('(prefers-reduced-motion: reduce)')
    const widthQuery = window.matchMedia(`(min-width: ${mobileBreakpoint}px)`)

    // Not in every browser; treated as "no opinion" when absent rather than
    // assumed to be false.
    const connection = (
      navigator as Navigator & {
        connection?: { saveData?: boolean; effectiveType?: string }
      }
    ).connection
    const frugal =
      connection?.saveData === true ||
      ['slow-2g', '2g'].includes(connection?.effectiveType ?? '')

    const decide = () => {
      setShouldPlay(!motionQuery.matches && widthQuery.matches && !frugal)
    }

    decide()
    motionQuery.addEventListener('change', decide)
    widthQuery.addEventListener('change', decide)
    return () => {
      motionQuery.removeEventListener('change', decide)
      widthQuery.removeEventListener('change', decide)
    }
  }, [mobileBreakpoint])

  // Autoplay can still be refused (iOS low-power mode, browser policy) even
  // with muted+playsInline. A rejected play() is a normal outcome, not an
  // error, so it falls back silently rather than logging.
  useEffect(() => {
    const video = videoRef.current
    if (!video || !shouldPlay) return

    const attempt = video.play()
    if (attempt && typeof attempt.catch === 'function') {
      attempt.catch(() => setFailed(true))
    }
  }, [shouldPlay])

  // Stop decoding entirely when the hero scrolls away. A video playing behind
  // eight sections of content is pure battery cost with nothing on screen.
  useEffect(() => {
    const video = videoRef.current
    if (!video || !shouldPlay) return

    const observer = new IntersectionObserver(
      ([entry]) => {
        if (entry.isIntersecting) {
          video.play().catch(() => {})
        } else {
          video.pause()
        }
      },
      { threshold: 0.05 }
    )
    observer.observe(video)
    return () => observer.disconnect()
  }, [shouldPlay])

  const showVideo = shouldPlay && !failed

  return (
    <div
      className={`absolute inset-0 overflow-hidden bg-[hsl(var(--gv-overlay))] ${className}`}
      aria-hidden="true"
    >
      {/* Layer 1 — the always-present backdrop.
          A designed scene, not a placeholder: deep-space gradient, coordinate
          grid, and two soft light sources suggesting limb and terminator. */}
      <div className="absolute inset-0 bg-gradient-to-br from-[#04101f] via-[#071b2e] to-[#020a14]" />
      <div className="absolute inset-0 gv-grid opacity-[0.14]" />
      <div
        className="absolute -left-[15%] top-[-20%] h-[70vh] w-[70vh] rounded-full blur-3xl"
        style={{
          background:
            'radial-gradient(circle, hsl(var(--gv-accent) / 0.18), transparent 62%)',
        }}
      />
      <div
        className="absolute -right-[10%] bottom-[-25%] h-[60vh] w-[60vh] rounded-full blur-3xl"
        style={{
          background:
            'radial-gradient(circle, hsl(var(--gv-primary) / 0.22), transparent 62%)',
        }}
      />

      {/* Layer 2/3 — poster, then video fading in over it once playable. */}
      {showVideo && (
        <video
          ref={videoRef}
          className={`absolute inset-0 h-full w-full object-cover transition-opacity duration-1000 ease-gv ${
            ready ? 'opacity-60' : 'opacity-0'
          }`}
          poster={poster}
          preload={PRELOAD}
          autoPlay
          muted
          loop
          playsInline
          // Stops older iOS Safari promoting the video to a fullscreen player.
          // React passes hyphenated attributes through to the DOM verbatim.
          webkit-playsinline="true"
          disablePictureInPicture
          onCanPlay={() => setReady(true)}
          onError={() => setFailed(true)}
        >
          {/* WebM first: smaller at equal quality where supported. The browser
              takes the first source it can decode, so ordering is the whole
              negotiation. A missing file simply falls through to the next. */}
          <source src={webmSrc} type="video/webm" />
          <source src={src} type="video/mp4" />
        </video>
      )}

      {/* Layer 4 — contrast scrims.
          Two directional gradients plus a vignette. Applied unconditionally so
          text contrast does not depend on which frame is showing, or on the
          video having loaded at all. */}
      <div className="absolute inset-0 bg-gradient-to-r from-[hsl(var(--gv-overlay))] via-[hsl(var(--gv-overlay)/0.72)] to-[hsl(var(--gv-overlay)/0.45)]" />
      <div className="absolute inset-0 bg-gradient-to-t from-[hsl(var(--gv-overlay))] via-transparent to-[hsl(var(--gv-overlay)/0.55)]" />
      <div className="absolute inset-0 gv-vignette" />

      {/* Faint sweep, tying the hero to the pipeline diagram further down.
          Suppressed under reduced-motion by the rule in tokens.css. */}
      <div className="absolute inset-0 gv-scanline" />
    </div>
  )
}
