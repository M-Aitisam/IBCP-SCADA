// packages/dashboard/src/components/landing/RevisitTimeline.tsx
'use client'

// Revisit cadence, to scale.
//
// A table saying "5 days / 16 days" tells you very little. Laid out on a shared
// 30-day axis, the asymmetry becomes the point: CHIRPS delivers thirty times in
// the window that MODIS vegetation delivers twice. That gap is *why* the system
// scores confidence per observation instead of treating every input as equal.
//
// The intervals are real properties of the collections. The tick positions are
// therefore honest about cadence — but they are a schedule, not an acquisition
// log: a real pass can be missed, and cloud can void an optical scene. That
// caveat is stated on the page rather than buried here.
import { useEffect, useRef, useState } from 'react'

const DAYS = 30

interface Lane {
  name: string
  interval: number
  offset: number
  colour: string
  note: string
}

const LANES: Lane[] = [
  {
    name: 'CHIRPS',
    interval: 1,
    offset: 0,
    colour: 'var(--gv-domain-water)',
    note: 'daily',
  },
  {
    name: 'Sentinel-2',
    interval: 5,
    offset: 2,
    colour: 'var(--gv-domain-agriculture)',
    note: '5 d',
  },
  {
    name: 'Sentinel-1',
    interval: 12,
    offset: 4,
    colour: 'var(--gv-accent)',
    note: '6-12 d',
  },
  {
    name: 'MODIS LST',
    interval: 8,
    offset: 1,
    colour: 'var(--gv-domain-disaster)',
    note: '8 d',
  },
  {
    name: 'MODIS NDVI',
    interval: 16,
    offset: 6,
    colour: 'var(--gv-watch)',
    note: '16 d',
  },
]

const W = 760
const LANE_H = 34
const PAD_L = 104
const PAD_R = 20
const PAD_T = 26
const H = PAD_T + LANES.length * LANE_H + 26
const TRACK = W - PAD_L - PAD_R

const dayX = (d: number) => PAD_L + (d / DAYS) * TRACK

export default function RevisitTimeline() {
  const ref = useRef<HTMLDivElement | null>(null)
  const [shown, setShown] = useState(false)

  useEffect(() => {
    const el = ref.current
    if (!el) return
    if (window.matchMedia('(prefers-reduced-motion: reduce)').matches) {
      setShown(true)
      return
    }
    const observer = new IntersectionObserver(
      ([entry]) => {
        if (entry.isIntersecting) {
          setShown(true)
          observer.disconnect()
        }
      },
      { threshold: 0.3 }
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
          'Chart comparing satellite revisit intervals over a 30-day window: ' +
          LANES.map((l) => `${l.name} every ${l.note}`).join(', ') + '.'
        }
      >
        {/* Week gridlines. */}
        {[0, 7, 14, 21, 28].map((d) => (
          <g key={d}>
            <line
              x1={dayX(d)}
              x2={dayX(d)}
              y1={PAD_T - 8}
              y2={PAD_T + LANES.length * LANE_H}
              stroke="hsl(var(--gv-border))"
              strokeWidth="1"
            />
            <text
              x={dayX(d)}
              y={H - 8}
              textAnchor="middle"
              fontSize="9"
              className="gv-numeric"
              fill="hsl(var(--gv-text-subtle))"
            >
              d{d}
            </text>
          </g>
        ))}

        {LANES.map((lane, li) => {
          const cy = PAD_T + li * LANE_H + LANE_H / 2
          const passes: number[] = []
          for (let d = lane.offset; d <= DAYS; d += lane.interval) passes.push(d)

          return (
            <g key={lane.name}>
              <text
                x={PAD_L - 12}
                y={cy + 3}
                textAnchor="end"
                fontSize="10.5"
                fontWeight="500"
                fill="hsl(var(--gv-text))"
              >
                {lane.name}
              </text>

              {/* The lane track. */}
              <line
                x1={PAD_L}
                x2={PAD_L + TRACK}
                y1={cy}
                y2={cy}
                stroke="hsl(var(--gv-border-subtle))"
                strokeWidth="1"
              />

              {/* Each acquisition. Bars rather than dots: a 30-tick daily lane
                  would smear into a line at dot size. */}
              {passes.map((d, i) => (
                <rect
                  key={d}
                  x={dayX(d) - 1}
                  y={cy - 7}
                  width="2"
                  height="14"
                  rx="1"
                  fill={`hsl(${lane.colour})`}
                  style={{
                    opacity: shown ? 0.9 : 0,
                    transform: shown ? 'none' : 'scaleY(0.2)',
                    transformOrigin: `${dayX(d)}px ${cy}px`,
                    // Lane-then-tick stagger, so the eye reads each cadence
                    // filling in at its own rate.
                    transition: `opacity 300ms ${li * 130 + i * 22}ms var(--gv-ease),
                                 transform 300ms ${li * 130 + i * 22}ms var(--gv-ease)`,
                  }}
                />
              ))}

              <text
                x={PAD_L + TRACK + 6}
                y={cy + 3}
                fontSize="8.5"
                className="gv-numeric"
                fill="hsl(var(--gv-text-subtle))"
              >
                {passes.length}
              </text>
            </g>
          )
        })}

        <text
          x={PAD_L}
          y={12}
          fontSize="9"
          letterSpacing="0.08em"
          fill="hsl(var(--gv-text-subtle))"
        >
          NOMINAL REVISIT OVER A 30-DAY WINDOW
        </text>
      </svg>
    </div>
  )
}
