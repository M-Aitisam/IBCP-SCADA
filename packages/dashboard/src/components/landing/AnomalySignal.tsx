// packages/dashboard/src/components/landing/AnomalySignal.tsx
'use client'

// The method, drawn.
//
// Most platforms in this field show you a red map and ask you to trust it.
// This shows the actual arithmetic: a district's own monthly climatology as a
// band, this season's observations as a line, and the moment the line leaves
// the band as the thing that triggers an alert.
//
// The series here is ILLUSTRATIVE and labelled as such on the page. It is a
// diagram of the method, not a reading from any district — inventing a named
// district's drought history to decorate a landing page is exactly the kind of
// fabrication this system is built to avoid.
import { useEffect, useRef, useState } from 'react'

const MONTHS = ['Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov']

// mean = climatology, sd = spread of the baseline years, obs = this season.
const SERIES = [
  { mean: 0.52, sd: 0.06, obs: 0.54 },
  { mean: 0.58, sd: 0.06, obs: 0.57 },
  { mean: 0.61, sd: 0.05, obs: 0.56 },
  { mean: 0.63, sd: 0.05, obs: 0.5 },
  { mean: 0.6, sd: 0.06, obs: 0.42 },
  { mean: 0.55, sd: 0.06, obs: 0.33 },
  { mean: 0.48, sd: 0.07, obs: 0.26 },
  { mean: 0.42, sd: 0.07, obs: 0.24 },
]

const W = 760
const H = 300
const PAD = { top: 28, right: 116, bottom: 34, left: 44 }
const PLOT_W = W - PAD.left - PAD.right
const PLOT_H = H - PAD.top - PAD.bottom

const Y_MIN = 0.15
const Y_MAX = 0.78

const x = (i: number) => PAD.left + (i / (SERIES.length - 1)) * PLOT_W
const y = (v: number) => PAD.top + (1 - (v - Y_MIN) / (Y_MAX - Y_MIN)) * PLOT_H

const line = (get: (d: (typeof SERIES)[number]) => number) =>
  SERIES.map((d, i) => `${i === 0 ? 'M' : 'L'}${x(i).toFixed(1)},${y(get(d)).toFixed(1)}`).join(' ')

// The ±1σ envelope, drawn as one closed path: forward along the upper edge,
// back along the lower.
const band = [
  ...SERIES.map((d, i) => `${i === 0 ? 'M' : 'L'}${x(i).toFixed(1)},${y(d.mean + d.sd).toFixed(1)}`),
  ...SERIES.slice()
    .reverse()
    .map((d, i) => {
      const idx = SERIES.length - 1 - i
      return `L${x(idx).toFixed(1)},${y(d.mean - d.sd).toFixed(1)}`
    }),
  'Z',
].join(' ')

// The first month the observation falls more than 2σ below climatology. This is
// computed, not hand-placed, so the annotation cannot drift out of sync with
// the series it is annotating.
const breachIndex = SERIES.findIndex((d) => (d.obs - d.mean) / d.sd <= -2)
const breach = SERIES[breachIndex]
const breachZ = (breach.obs - breach.mean) / breach.sd

export default function AnomalySignal() {
  const ref = useRef<HTMLDivElement | null>(null)
  const [shown, setShown] = useState(false)

  useEffect(() => {
    const el = ref.current
    if (!el) return

    // Reduced motion: show the finished diagram immediately. The content is
    // the point; the draw-on is decoration.
    if (window.matchMedia('(prefers-reduced-motion: reduce)').matches) {
      setShown(true)
      return
    }

    const observer = new IntersectionObserver(
      ([entry]) => {
        // One-way: re-animating every time the user scrolls back is a fidget,
        // not a feature.
        if (entry.isIntersecting) {
          setShown(true)
          observer.disconnect()
        }
      },
      { threshold: 0.35 }
    )
    observer.observe(el)
    return () => observer.disconnect()
  }, [])

  return (
    <div ref={ref} className="w-full overflow-x-auto">
      <svg
        viewBox={`0 0 ${W} ${H}`}
        className="h-auto w-full min-w-[38rem]"
        role="img"
        aria-label={
          'Diagram: a vegetation index tracked against its multi-year monthly climatology. ' +
          'The observed line falls below the plus-or-minus one sigma baseline band from July onward, ' +
          `reaching ${breachZ.toFixed(1)} standard deviations below normal in ${MONTHS[breachIndex]}, ` +
          'which is the departure that raises an alert.'
        }
      >
        <defs>
          <linearGradient id="gv-band" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor="hsl(var(--gv-ok))" stopOpacity="0.16" />
            <stop offset="100%" stopColor="hsl(var(--gv-ok))" stopOpacity="0.05" />
          </linearGradient>
          <linearGradient id="gv-obs" x1="0" y1="0" x2="1" y2="0">
            <stop offset="0%" stopColor="hsl(var(--gv-accent))" />
            <stop offset="55%" stopColor="hsl(var(--gv-watch))" />
            <stop offset="100%" stopColor="hsl(var(--gv-critical))" />
          </linearGradient>
        </defs>

        {/* Horizontal rules, faint enough to read as graph paper. */}
        {[0.2, 0.35, 0.5, 0.65].map((v) => (
          <g key={v}>
            <line
              x1={PAD.left}
              x2={PAD.left + PLOT_W}
              y1={y(v)}
              y2={y(v)}
              stroke="hsl(var(--gv-border))"
              strokeWidth="1"
            />
            <text
              x={PAD.left - 10}
              y={y(v) + 3}
              textAnchor="end"
              className="gv-numeric"
              fontSize="9"
              fill="hsl(var(--gv-text-subtle))"
            >
              {v.toFixed(2)}
            </text>
          </g>
        ))}

        {MONTHS.map((m, i) => (
          <text
            key={m}
            x={x(i)}
            y={H - 12}
            textAnchor="middle"
            fontSize="9"
            fill="hsl(var(--gv-text-subtle))"
          >
            {m}
          </text>
        ))}

        {/* Baseline band and its centreline. */}
        <path
          d={band}
          fill="url(#gv-band)"
          stroke="none"
          style={{
            opacity: shown ? 1 : 0,
            transition: 'opacity 700ms var(--gv-ease)',
          }}
        />
        <path
          d={line((d) => d.mean)}
          fill="none"
          stroke="hsl(var(--gv-ok))"
          strokeWidth="1.25"
          strokeDasharray="4 4"
          style={{
            opacity: shown ? 0.75 : 0,
            transition: 'opacity 700ms 150ms var(--gv-ease)',
          }}
        />

        {/* Observed series, drawn on with a dash offset. pathLength normalises
            the geometry to 1 so the dash values need no measurement. */}
        <path
          d={line((d) => d.obs)}
          fill="none"
          stroke="url(#gv-obs)"
          strokeWidth="2.25"
          strokeLinecap="round"
          strokeLinejoin="round"
          pathLength={1}
          strokeDasharray={1}
          style={{
            strokeDashoffset: shown ? 0 : 1,
            transition: 'stroke-dashoffset 1800ms 300ms var(--gv-ease)',
          }}
        />

        {SERIES.map((d, i) => {
          const z = (d.obs - d.mean) / d.sd
          const severe = z <= -2
          return (
            <circle
              key={i}
              cx={x(i)}
              cy={y(d.obs)}
              r={severe ? 4 : 2.5}
              fill={severe ? 'hsl(var(--gv-critical))' : 'hsl(var(--gv-surface))'}
              stroke={severe ? 'hsl(var(--gv-critical))' : 'hsl(var(--gv-accent))'}
              strokeWidth="1.5"
              style={{
                opacity: shown ? 1 : 0,
                // Staggered so the markers appear to follow the line being drawn.
                transition: `opacity 400ms ${400 + i * 170}ms var(--gv-ease)`,
              }}
            />
          )
        })}

        {/* Departure callout: the vertical drop from climatology to observed. */}
        <g
          style={{
            opacity: shown ? 1 : 0,
            transition: 'opacity 600ms 2100ms var(--gv-ease)',
          }}
        >
          <line
            x1={x(breachIndex)}
            x2={x(breachIndex)}
            y1={y(breach.mean)}
            y2={y(breach.obs)}
            stroke="hsl(var(--gv-critical))"
            strokeWidth="1"
            strokeDasharray="2 3"
          />
          <rect
            x={x(breachIndex) + 10}
            y={y(breach.obs) - 16}
            width="96"
            height="32"
            rx="3"
            fill="hsl(var(--gv-critical) / 0.12)"
            stroke="hsl(var(--gv-critical) / 0.4)"
          />
          <text
            x={x(breachIndex) + 18}
            y={y(breach.obs) - 3}
            fontSize="11"
            fontWeight="600"
            className="gv-numeric"
            fill="hsl(var(--gv-critical))"
          >
            {breachZ.toFixed(1)}&#963; below
          </text>
          <text
            x={x(breachIndex) + 18}
            y={y(breach.obs) + 9}
            fontSize="8.5"
            fill="hsl(var(--gv-text-subtle))"
          >
            alert raised
          </text>
        </g>

        <text
          x={PAD.left}
          y={16}
          fontSize="9"
          fill="hsl(var(--gv-text-subtle))"
          letterSpacing="0.08em"
        >
          NDVI &#183; OBSERVED vs 3-YEAR MONTHLY CLIMATOLOGY &#177;1&#963;
        </text>
      </svg>
    </div>
  )
}
