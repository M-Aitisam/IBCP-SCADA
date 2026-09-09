// packages/dashboard/src/components/landing/HeroCanvas.tsx
'use client'

// The acquisition field.
//
// 119 nodes, one per district in the region of interest, with satellite swaths
// sweeping across them at an orbital inclination. A node brightens as a swath
// crosses it and decays afterwards — which is literally what the ingestion
// pipeline does: a scene footprint passes over a district, an observation is
// recorded, and the reading ages until the next pass.
//
// Deliberately NOT a map of Pakistan. Two reasons. Drawing a national outline
// on this platform would be making a territorial claim — the ROI is FAO GAUL
// level 2, which excludes Gilgit-Baltistan and Azad Jammu & Kashmir, and a
// hand-simplified border would misstate that. And an abstract field is honest
// about what it is: a depiction of the acquisition process, not a data readout.
// Nothing here is a measurement, so nothing here can misrepresent one.
import { useEffect, useRef } from 'react'

const NODE_COUNT = 119 // districts in the ROI
const SWATH_COUNT = 3
const CONNECTIONS_PER_NODE = 2

interface Node {
  x: number // 0..1, resolution independent
  y: number
  r: number
  /** 0..1, how recently a swath crossed this node. */
  charge: number
  links: number[]
}

interface Swath {
  /** Position along the sweep axis, 0..1, wrapping. */
  t: number
  speed: number
  width: number
}

/**
 * Deterministic PRNG (mulberry32).
 *
 * Math.random would give a different constellation on every mount, so the
 * layout would change between the server-rendered frame and hydration, and
 * again on every visit. A fixed seed makes this a designed composition rather
 * than a lottery.
 */
function seeded(seed: number) {
  return function next() {
    seed |= 0
    seed = (seed + 0x6d2b79f5) | 0
    let t = Math.imul(seed ^ (seed >>> 15), 1 | seed)
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296
  }
}

function buildNodes(): Node[] {
  const rand = seeded(0x5eed)
  const nodes: Node[] = []

  // Three loose clusters on a diagonal, echoing how districts actually
  // distribute: dense in the irrigated belt, sparse across the arid west.
  const clusters = [
    { cx: 0.3, cy: 0.62, spread: 0.26, weight: 0.42 },
    { cx: 0.58, cy: 0.4, spread: 0.22, weight: 0.36 },
    { cx: 0.8, cy: 0.66, spread: 0.18, weight: 0.22 },
  ]

  for (let i = 0; i < NODE_COUNT; i++) {
    const roll = rand()
    let acc = 0
    let cluster = clusters[0]
    for (const c of clusters) {
      acc += c.weight
      if (roll <= acc) {
        cluster = c
        break
      }
    }

    // Box-Muller for a gaussian scatter: uniform-in-a-box looks like static,
    // gaussian-around-a-centre looks like settlement.
    const u = Math.max(rand(), 1e-6)
    const v = rand()
    const mag = Math.sqrt(-2 * Math.log(u)) * cluster.spread * 0.5
    const x = cluster.cx + mag * Math.cos(2 * Math.PI * v)
    const y = cluster.cy + mag * Math.sin(2 * Math.PI * v) * 0.72

    nodes.push({
      x: Math.min(0.97, Math.max(0.03, x)),
      y: Math.min(0.95, Math.max(0.05, y)),
      r: 0.9 + rand() * 1.6,
      charge: rand() * 0.3,
      links: [],
    })
  }

  // Nearest-neighbour links. O(n^2) at n=119 is ~14k comparisons, run once.
  for (let i = 0; i < nodes.length; i++) {
    const distances = nodes
      .map((n, j) => ({ j, d: (n.x - nodes[i].x) ** 2 + (n.y - nodes[i].y) ** 2 }))
      .filter((e) => e.j !== i)
      .sort((a, b) => a.d - b.d)
    nodes[i].links = distances.slice(0, CONNECTIONS_PER_NODE).map((e) => e.j)
  }

  return nodes
}

/** Read a design token off the document so the canvas matches the palette. */
function token(name: string, fallback: string): string {
  if (typeof window === 'undefined') return fallback
  const value = getComputedStyle(document.documentElement).getPropertyValue(name).trim()
  return value || fallback
}

export default function HeroCanvas({ className = '' }: { className?: string }) {
  const canvasRef = useRef<HTMLCanvasElement | null>(null)

  useEffect(() => {
    const canvas = canvasRef.current
    if (!canvas) return
    const ctx = canvas.getContext('2d')
    if (!ctx) return

    const nodes = buildNodes()
    const swaths: Swath[] = Array.from({ length: SWATH_COUNT }, (_, i) => ({
      t: i / SWATH_COUNT,
      // Slightly different speeds so the passes never lock into a pattern.
      speed: 0.00008 + i * 0.000025,
      width: 0.1,
    }))

    const accent = token('--gv-accent', '187 92% 55%')
    const ok = token('--gv-ok', '152 58% 48%')

    // Orbital inclination: sun-synchronous orbits cross the equator at a
    // consistent angle, and a swath is perpendicular to the ground track.
    const angle = (-98 * Math.PI) / 180
    const ax = Math.cos(angle)
    const ay = Math.sin(angle)

    let width = 0
    let height = 0
    let frame = 0
    let running = true

    const reduceMotion =
      typeof window !== 'undefined' &&
      window.matchMedia('(prefers-reduced-motion: reduce)').matches

    function resize() {
      if (!canvas) return
      const rect = canvas.getBoundingClientRect()
      // Cap at 2x. A 3x phone panel would quadruple the fill cost for a
      // difference nobody can see on a field of 2px dots.
      const dpr = Math.min(window.devicePixelRatio || 1, 2)
      width = rect.width
      height = rect.height
      canvas.width = Math.floor(width * dpr)
      canvas.height = Math.floor(height * dpr)
      ctx!.setTransform(dpr, 0, 0, dpr, 0, 0)
    }

    function draw(now: number) {
      if (!running) return
      ctx!.clearRect(0, 0, width, height)

      // Project a node onto the sweep axis, normalised to 0..1.
      const project = (n: Node) => (n.x * ax + n.y * ay + 1) / 2

      if (!reduceMotion) {
        for (const s of swaths) {
          s.t = (s.t + s.speed * 16.67) % 1
        }
      }

      // Charge each node by its distance to the nearest swath centre.
      for (const n of nodes) {
        const p = project(n)
        let peak = 0
        for (const s of swaths) {
          // Wrap-aware distance, so a swath crossing the seam still lights
          // nodes on both sides of it.
          const raw = Math.abs(p - s.t)
          const d = Math.min(raw, 1 - raw)
          if (d < s.width) peak = Math.max(peak, 1 - d / s.width)
        }
        if (reduceMotion) {
          n.charge = peak
        } else {
          // Rise fast on a pass, decay slowly: an observation is instant, its
          // freshness is not.
          n.charge = peak > n.charge ? peak : n.charge * 0.985
        }
      }

      // Links first, so nodes sit on top of them.
      ctx!.lineWidth = 0.6
      for (let i = 0; i < nodes.length; i++) {
        const a = nodes[i]
        for (const j of a.links) {
          const b = nodes[j]
          const strength = Math.max(a.charge, b.charge)
          const alpha = 0.045 + strength * 0.28
          ctx!.strokeStyle = `hsl(${accent} / ${alpha})`
          ctx!.beginPath()
          ctx!.moveTo(a.x * width, a.y * height)
          ctx!.lineTo(b.x * width, b.y * height)
          ctx!.stroke()
        }
      }

      // Swath bands: a soft gradient along the sweep axis.
      if (!reduceMotion) {
        for (const s of swaths) {
          const cx = (s.t * 2 - 1) * ax
          const cy = (s.t * 2 - 1) * ay
          const gx = cx * width * 0.5 + width * 0.5
          const gy = cy * height * 0.5 + height * 0.5
          const grad = ctx!.createRadialGradient(gx, gy, 0, gx, gy, width * 0.42)
          grad.addColorStop(0, `hsl(${accent} / 0.1)`)
          grad.addColorStop(1, `hsl(${accent} / 0)`)
          ctx!.fillStyle = grad
          ctx!.fillRect(0, 0, width, height)
        }
      }

      for (const n of nodes) {
        const x = n.x * width
        const y = n.y * height
        const c = n.charge

        if (c > 0.35) {
          // Halo on a fresh acquisition.
          ctx!.fillStyle = `hsl(${ok} / ${(c - 0.35) * 0.28})`
          ctx!.beginPath()
          ctx!.arc(x, y, n.r * (3 + c * 6), 0, Math.PI * 2)
          ctx!.fill()
        }

        ctx!.fillStyle =
          c > 0.5 ? `hsl(${ok} / ${0.5 + c * 0.5})` : `hsl(${accent} / ${0.22 + c * 0.6})`
        ctx!.beginPath()
        ctx!.arc(x, y, n.r * (1 + c * 0.7), 0, Math.PI * 2)
        ctx!.fill()
      }

      if (!reduceMotion) frame = requestAnimationFrame(draw)
    }

    resize()
    draw(0)

    const onResize = () => {
      resize()
      if (reduceMotion) draw(0)
    }
    window.addEventListener('resize', onResize)

    // Stop the loop when the hero is off screen. An unthrottled rAF behind
    // eight sections of content is pure battery cost.
    const observer = new IntersectionObserver(
      ([entry]) => {
        if (entry.isIntersecting) {
          if (!running && !reduceMotion) {
            running = true
            frame = requestAnimationFrame(draw)
          }
        } else {
          running = false
          cancelAnimationFrame(frame)
        }
      },
      { threshold: 0.02 }
    )
    observer.observe(canvas)

    return () => {
      running = false
      cancelAnimationFrame(frame)
      window.removeEventListener('resize', onResize)
      observer.disconnect()
    }
  }, [])

  return (
    <canvas
      ref={canvasRef}
      // Decorative. The information it conveys is stated in prose beside it.
      aria-hidden="true"
      className={`h-full w-full ${className}`}
    />
  )
}
