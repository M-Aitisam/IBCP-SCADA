// packages/dashboard/src/app/page.tsx
'use client'

// The public landing page.
//
// Design premise: show the instrument, not stock imagery.
//
// Almost every platform in this field opens with a photograph of a flood and a
// sentence about saving lives. That is interchangeable — swap the logo and it
// sells anything. What is not interchangeable is the machinery: the acquisition
// geometry, the arithmetic that turns a reflectance value into an alert, the
// brutal asymmetry between a daily rainfall grid and a 16-day vegetation
// composite. So those are the hero images here, drawn live rather than
// photographed.
//
// The honesty rules from the rest of the system apply to this page too. A
// signed-out visitor has no session, so there are no live readings anywhere on
// it. Every number is either a documented constant of the deployment or is
// labelled as an illustration of the method.
import Link from 'next/link'
import { useEffect, useState } from 'react'
import {
  ArrowRight,
  CloudRain,
  Droplets,
  Layers,
  Radio,
  Sprout,
} from 'lucide-react'
import AnomalySignal from '@/components/landing/AnomalySignal'
import HeroCanvas from '@/components/landing/HeroCanvas'
import HeroVideo from '@/components/landing/HeroVideo'
import LandingNav from '@/components/landing/LandingNav'
import RevisitTimeline from '@/components/landing/RevisitTimeline'
import { useAuth } from '@/context/AuthContext'

const SOURCES = [
  {
    id: 'COPERNICUS/S2_SR_HARMONIZED',
    name: 'Sentinel-2 MSI',
    agency: 'ESA Copernicus',
    band: 'Optical · 13 bands',
    resolution: '10 m',
    reads: 'Surface reflectance, NDVI, vegetation condition',
    icon: Sprout,
    tone: 'text-domain-agriculture',
  },
  {
    id: 'COPERNICUS/S1_GRD',
    name: 'Sentinel-1 SAR',
    agency: 'ESA Copernicus',
    band: 'C-band radar · VV/VH',
    resolution: '10 m',
    reads: 'Backscatter, open-water fraction — sees through cloud',
    icon: Droplets,
    tone: 'text-accent',
  },
  {
    id: 'UCSB-CHG/CHIRPS/DAILY',
    name: 'CHIRPS',
    agency: 'UCSB Climate Hazards',
    band: 'Blended gauge · satellite',
    resolution: '5 km',
    reads: 'Precipitation and rainfall deficit against climatology',
    icon: CloudRain,
    tone: 'text-domain-water',
  },
  {
    id: 'MODIS/061/MOD13Q1',
    name: 'MODIS Vegetation',
    agency: 'NASA',
    band: '16-day composite',
    resolution: '250 m',
    reads: 'NDVI and EVI, long-baseline vegetation trend',
    icon: Layers,
    tone: 'text-sev-watch',
  },
  {
    id: 'MODIS/061/MOD11A2',
    name: 'MODIS LST',
    agency: 'NASA',
    band: 'Thermal · 8-day',
    resolution: '1 km',
    reads: 'Land surface temperature and heat stress',
    icon: Radio,
    tone: 'text-domain-disaster',
  },
]

const PIPELINE = [
  {
    n: '01',
    title: 'Acquire',
    body: 'Earth Engine is queried per district, per dataset. Scenes are reduced server-side — raw imagery never crosses the wire.',
  },
  {
    n: '02',
    title: 'Score quality',
    body: 'Every observation carries a 0–100 score built from named, additive penalties: cloud fraction, partial footprint, stale acquisition. Nothing is imputed.',
  },
  {
    n: '03',
    title: 'Compare',
    body: 'Each reading is placed against that district’s own monthly climatology. Under three years of history yields no z-score at all, rather than a fabricated one.',
  },
  {
    n: '04',
    title: 'Assess',
    body: 'Published, deterministic formulas combine the anomalies into drought, flood, heat and crop-health indices. Every score decomposes into its inputs.',
  },
  {
    n: '05',
    title: 'Track',
    body: 'Events move through detected → confirmed → escalating → peak → declining → resolved, with hysteresis, so a noisy signal cannot flap an alert on and off.',
  },
  {
    n: '06',
    title: 'Alert',
    body: 'Alerts require persistence and a minimum confidence, then are deduplicated and rate-limited so nobody is paged twice for one event.',
  },
]

const TICKER = [
  'COPERNICUS/S2_SR_HARMONIZED',
  'FAO/GAUL/2015/level2',
  'COPERNICUS/S1_GRD',
  'UCSB-CHG/CHIRPS/DAILY',
  'MODIS/061/MOD13Q1',
  'MODIS/061/MOD11A2',
]

/** UTC clock. Real time from the visitor's own machine — not a server reading. */
function UtcClock() {
  const [now, setNow] = useState<string | null>(null)

  useEffect(() => {
    const tick = () =>
      setNow(
        new Date().toISOString().slice(11, 19)
      )
    tick()
    const id = setInterval(tick, 1000)
    return () => clearInterval(id)
  }, [])

  // Renders nothing until mounted: the server has no clock the client agrees
  // with, and a mismatched first paint is a hydration error.
  return (
    <span className="gv-numeric tabular-nums">{now ?? '--:--:--'} UTC</span>
  )
}

function SectionLabel({ index, children }: { index: string; children: string }) {
  return (
    <div className="flex items-center gap-3">
      <span className="gv-index text-micro font-bold text-brand">{index}</span>
      <span className="h-px w-8 bg-line-strong" aria-hidden="true" />
      <span className="text-micro font-semibold uppercase tracking-[0.22em] text-content-subtle">
        {children}
      </span>
    </div>
  )
}

export default function Home() {
  const { isAuthenticated } = useAuth()

  return (
    <div className="min-h-screen bg-canvas text-content">
      <LandingNav />

      {/* ==================================================================
          HERO — video backdrop, live acquisition field, then the type.
         ================================================================== */}
      <section className="relative flex min-h-screen flex-col overflow-hidden">
        <HeroVideo />

        {/* The acquisition field sits between the video and the text. Masked
            to the right so it never fights the headline for contrast. */}
        <div
          aria-hidden="true"
          className="pointer-events-none absolute inset-0 z-[1] opacity-90"
          style={{
            maskImage:
              'radial-gradient(ellipse 75% 85% at 78% 45%, black 20%, transparent 78%)',
            WebkitMaskImage:
              'radial-gradient(ellipse 75% 85% at 78% 45%, black 20%, transparent 78%)',
          }}
        >
          <HeroCanvas />
        </div>

        <div className="relative z-panel mx-auto flex w-full max-w-7xl flex-1 flex-col justify-center px-4 pb-10 pt-28 sm:px-6">
          <div className="max-w-3xl">
            <div className="mb-8 flex flex-wrap items-center gap-x-4 gap-y-2">
              <span className="inline-flex items-center gap-2 rounded-sm border border-white/15 bg-white/[0.06] px-2.5 py-1 backdrop-blur">
                <span
                  aria-hidden="true"
                  className="gv-pulse h-1.5 w-1.5 rounded-full bg-sev-ok"
                />
                <span className="text-micro font-semibold uppercase tracking-[0.2em] text-white/75">
                  Earth observation
                </span>
              </span>
              <span className="text-micro tracking-[0.2em] text-white/35">
                <UtcClock />
              </span>
              <span className="text-micro tracking-[0.2em] text-white/35">
                24&#176;N–37&#176;N / 61&#176;E–77&#176;E
              </span>
            </div>

            <h1 className="text-display font-semibold leading-[0.98] tracking-[-0.04em] text-white">
              We do not
              <br />
              guess where
              <br />
              <span className="relative inline-block">
                <span className="bg-gradient-to-r from-[hsl(var(--gv-accent))] via-[hsl(var(--gv-accent))] to-[hsl(var(--gv-primary))] bg-clip-text text-transparent">
                  it is failing.
                </span>
                {/* Underscore rule, the width of the phrase. */}
                <span
                  aria-hidden="true"
                  className="absolute -bottom-2 left-0 h-px w-full bg-gradient-to-r from-[hsl(var(--gv-accent))] to-transparent"
                />
              </span>
            </h1>

            <p className="mt-9 max-w-xl text-base leading-relaxed text-white/65">
              GeoVision reads optical and radar satellite observations across every
              district of Pakistan, scores each one against that district&rsquo;s own
              multi-year baseline, and reports drought, flood, heat and crop stress as
              traceable indices. Not forecasts. Measurements, and the distance from
              normal.
            </p>

            <div className="mt-10 flex flex-wrap items-center gap-3">
              <Link
                href={isAuthenticated ? '/geovision' : '/login'}
                className="group inline-flex items-center gap-2.5 rounded bg-white px-6 py-3.5 text-sm font-semibold text-[hsl(var(--gv-overlay))] transition-all duration-gv hover:gap-4"
              >
                {isAuthenticated ? 'Open command centre' : 'Sign in to the console'}
                <ArrowRight className="h-4 w-4" aria-hidden="true" />
              </Link>
              <a
                href="#method"
                className="inline-flex items-center gap-2 rounded border border-white/20 px-6 py-3.5 text-sm font-semibold text-white/90 transition-colors duration-gv hover:bg-white/10"
              >
                See the method
              </a>
            </div>
          </div>
        </div>

        {/* Constants of the deployment, pinned to the base of the hero. */}
        <div className="relative z-panel border-t border-white/10 bg-[hsl(var(--gv-overlay)/0.5)] backdrop-blur">
          <dl className="mx-auto grid max-w-7xl grid-cols-2 divide-x divide-white/10 px-4 sm:px-6 md:grid-cols-4">
            {[
              { k: '119', v: 'districts in the ROI', s: 'FAO GAUL level 2' },
              { k: '5', v: 'satellite collections', s: 'optical · radar · thermal' },
              { k: '10 m', v: 'finest resolution', s: 'Sentinel-1 / -2' },
              { k: '3 yr', v: 'minimum baseline', s: 'before a z-score is issued' },
            ].map((stat, i) => (
              <div key={stat.v} className={`px-5 py-6 ${i === 0 ? 'pl-0 sm:pl-5' : ''}`}>
                <dt className="gv-numeric text-metric font-semibold leading-none text-white">
                  {stat.k}
                </dt>
                <dd className="mt-2 text-caption text-white/55">{stat.v}</dd>
                <dd className="mt-0.5 text-micro tracking-wider text-white/30">{stat.s}</dd>
              </div>
            ))}
          </dl>
        </div>
      </section>

      {/* ==================================================================
          TICKER — the actual collection identifiers, scrolling.
         ================================================================== */}
      <div className="overflow-hidden border-y border-line bg-surface py-2.5">
        <div className="gv-ticker flex w-max gap-10" aria-hidden="true">
          {/* Rendered twice so the -50% wrap is seamless. */}
          {[0, 1].map((copy) => (
            <div key={copy} className="flex shrink-0 gap-10">
              {TICKER.map((id) => (
                <span
                  key={id}
                  className="flex items-center gap-3 whitespace-nowrap text-micro tracking-[0.18em] text-content-subtle"
                >
                  <span className="h-1 w-1 rounded-full bg-brand" />
                  {id}
                </span>
              ))}
            </div>
          ))}
        </div>
        {/* The scrolling text is decorative duplication; this is what a screen
            reader gets instead. */}
        <p className="sr-only">
          Ingested collections: {TICKER.join(', ')}.
        </p>
      </div>

      {/* ==================================================================
          METHOD — the anomaly diagram. The centrepiece of the page.
         ================================================================== */}
      <section id="method" className="relative overflow-hidden bg-canvas py-24">
        <div className="mx-auto max-w-7xl px-4 sm:px-6">
          <SectionLabel index="01">The method</SectionLabel>

          <div className="mt-8 grid gap-12 lg:grid-cols-[minmax(0,0.85fr)_minmax(0,1.15fr)] lg:items-center">
            <div>
              <h2 className="text-display-sm font-semibold tracking-[-0.025em]">
                A district is only ever compared
                <span className="text-brand"> against itself</span>.
              </h2>
              <p className="mt-5 text-sm leading-relaxed text-content-muted">
                An NDVI of 0.31 is catastrophic in the Punjab plain and unremarkable in
                Chagai. Absolute thresholds cannot tell those apart, so the system never
                uses them. Every reading is expressed as its distance from that
                district&rsquo;s own monthly climatology, in standard deviations.
              </p>
              <p className="mt-4 text-sm leading-relaxed text-content-muted">
                Which is also why a district with fewer than three years of usable
                history returns no score at all. There is no baseline to depart from, so
                there is no anomaly to report — and the map shows grey, not green.
              </p>

              <div className="mt-8 grid grid-cols-3 gap-px overflow-hidden rounded border border-line bg-line">
                {[
                  { k: 'σ', v: 'z-score', d: 'departure from normal' },
                  { k: '3 yr', v: 'baseline', d: 'minimum history' },
                  { k: '0–100', v: 'quality', d: 'per observation' },
                ].map((m) => (
                  <div key={m.v} className="bg-surface p-4">
                    <div className="gv-numeric text-lg font-semibold text-brand">{m.k}</div>
                    <div className="mt-1 text-caption font-medium">{m.v}</div>
                    <div className="mt-0.5 text-micro text-content-subtle">{m.d}</div>
                  </div>
                ))}
              </div>
            </div>

            <div className="gv-bracket rounded border border-line bg-surface p-5">
              <AnomalySignal />
              <p className="mt-3 border-t border-line-subtle pt-3 text-micro leading-relaxed text-content-subtle">
                Illustrative series demonstrating the detection rule. Not a reading from
                any district — this is the arithmetic, drawn.
              </p>
            </div>
          </div>
        </div>
      </section>

      {/* ==================================================================
          CADENCE — revisit intervals to scale.
         ================================================================== */}
      <section className="relative overflow-hidden border-t border-line bg-surface py-24">
        <div className="absolute inset-0 gv-grid opacity-30" aria-hidden="true" />
        <div className="relative mx-auto max-w-7xl px-4 sm:px-6">
          <SectionLabel index="02">Cadence</SectionLabel>

          <div className="mt-8 grid gap-12 lg:grid-cols-[minmax(0,1.15fr)_minmax(0,0.85fr)] lg:items-center">
            <div className="gv-bracket order-2 rounded border border-line bg-canvas p-5 lg:order-1">
              <RevisitTimeline />
              <p className="mt-3 border-t border-line-subtle pt-3 text-micro leading-relaxed text-content-subtle">
                Nominal schedule, not an acquisition log. A pass can be missed, and cloud
                can void an optical scene entirely — which is why radar carries the flood
                signal.
              </p>
            </div>

            <div className="order-1 lg:order-2">
              <h2 className="text-display-sm font-semibold tracking-[-0.025em]">
                Thirty observations, or two.
              </h2>
              <p className="mt-5 text-sm leading-relaxed text-content-muted">
                In the same thirty days, rainfall arrives daily and the MODIS vegetation
                composite arrives twice. Treating those as equally reliable evidence
                would let the sparsest input drive the loudest alert.
              </p>
              <p className="mt-4 text-sm leading-relaxed text-content-muted">
                So confidence is computed, not assumed: the product of data completeness,
                baseline depth and observation quality. Thin evidence stays visibly thin
                all the way to the operator&rsquo;s screen.
              </p>
            </div>
          </div>
        </div>
      </section>

      {/* ==================================================================
          PIPELINE
         ================================================================== */}
      <section id="pipeline" className="border-t border-line bg-canvas py-24">
        <div className="mx-auto max-w-7xl px-4 sm:px-6">
          <SectionLabel index="03">Pipeline</SectionLabel>
          <h2 className="mt-8 max-w-2xl text-display-sm font-semibold tracking-[-0.025em]">
            From orbit to alert, in six auditable stages
          </h2>

          <ol className="mt-12 grid gap-px overflow-hidden rounded border border-line bg-line md:grid-cols-2 lg:grid-cols-3">
            {PIPELINE.map((s) => (
              <li key={s.n} className="group relative bg-surface p-6 transition-colors duration-gv hover:bg-surface-raised">
                <div className="flex items-baseline gap-3">
                  <span className="gv-index text-caption font-bold text-brand">{s.n}</span>
                  <span className="h-px flex-1 bg-line" aria-hidden="true" />
                </div>
                <h3 className="mt-4 text-base font-semibold tracking-tight">{s.title}</h3>
                <p className="mt-2 text-caption leading-relaxed text-content-muted">
                  {s.body}
                </p>
              </li>
            ))}
          </ol>

          <div className="mt-10 gv-bracket rounded border border-line bg-surface p-6">
            <p className="max-w-3xl text-sm leading-relaxed text-content-muted">
              <span className="font-semibold text-content">
                Missing observations stay missing.
              </span>{' '}
              Cloud cover, partial scene footprints and short baselines are reported as
              gaps in coverage. They are never substituted with zero, and never rendered
              as an all-clear — an unobserved district shows grey, because grey means
              &ldquo;no basis to judge&rdquo; and green means &ldquo;assessed and
              clear&rdquo;.
            </p>
          </div>
        </div>
      </section>

      {/* ==================================================================
          SOURCES
         ================================================================== */}
      <section id="sources" className="border-t border-line bg-surface py-24">
        <div className="mx-auto max-w-7xl px-4 sm:px-6">
          <SectionLabel index="04">Sources</SectionLabel>
          <h2 className="mt-8 max-w-2xl text-display-sm font-semibold tracking-[-0.025em]">
            Public Earth observation, read at source
          </h2>

          <div className="mt-12 divide-y divide-line border-y border-line">
            {SOURCES.map((s) => (
              <div
                key={s.id}
                className="group grid grid-cols-1 gap-3 py-5 transition-colors duration-gv hover:bg-surface-sunken md:grid-cols-[minmax(0,2.2fr)_minmax(0,1fr)_minmax(0,0.7fr)_minmax(0,2.4fr)] md:items-center md:gap-6 md:px-3"
              >
                <div className="flex items-center gap-3.5">
                  <s.icon className={`h-5 w-5 shrink-0 ${s.tone}`} aria-hidden="true" />
                  <div className="min-w-0">
                    <div className="text-sm font-semibold tracking-tight">{s.name}</div>
                    <code className="text-micro tracking-wide text-content-subtle">
                      {s.id}
                    </code>
                  </div>
                </div>
                <div className="text-caption text-content-muted">{s.band}</div>
                <div className="gv-numeric text-caption font-medium text-content">
                  {s.resolution}
                </div>
                <div className="text-caption leading-relaxed text-content-muted">
                  {s.reads}
                </div>
              </div>
            ))}
          </div>

          <p className="mt-6 text-caption text-content-subtle">
            All five are queried through Google Earth Engine and reduced server-side to
            district statistics. Credentials stay on the server; the browser never sees
            them.
          </p>
        </div>
      </section>

      {/* ==================================================================
          COVERAGE
         ================================================================== */}
      <section
        id="coverage"
        className="relative overflow-hidden border-t border-line bg-[hsl(var(--gv-overlay))] py-24 text-white"
      >
        <div className="absolute inset-0 gv-grid opacity-[0.07]" aria-hidden="true" />
        <div
          aria-hidden="true"
          className="gv-drift absolute -right-[10%] top-1/4 h-[50vh] w-[50vh] rounded-full blur-3xl"
          style={{
            background:
              'radial-gradient(circle, hsl(var(--gv-primary) / 0.28), transparent 65%)',
          }}
        />

        <div className="relative mx-auto max-w-7xl px-4 sm:px-6">
          <div className="flex items-center gap-3">
            <span className="gv-index text-micro font-bold text-[hsl(var(--gv-accent))]">
              05
            </span>
            <span className="h-px w-8 bg-white/25" aria-hidden="true" />
            <span className="text-micro font-semibold uppercase tracking-[0.22em] text-white/45">
              Coverage
            </span>
          </div>

          <div className="mt-8 grid gap-12 lg:grid-cols-2 lg:items-start">
            <div>
              <h2 className="text-display-sm font-semibold tracking-[-0.025em]">
                Stated as what it is.
              </h2>
              <p className="mt-5 text-sm leading-relaxed text-white/65">
                Administrative boundaries come from FAO GAUL level 2. That vintage
                predates several boundary changes and excludes Gilgit-Baltistan and Azad
                Jammu &amp; Kashmir, so the geometry covers roughly 89% of Pakistan by
                area.
              </p>
              <p className="mt-4 text-sm leading-relaxed text-white/45">
                Which is what it says. Rounding that up to &ldquo;nationwide&rdquo; would
                be the first small lie, and an operator who finds one stops trusting the
                rest.
              </p>
            </div>

            <div className="grid grid-cols-2 gap-px overflow-hidden rounded border border-white/10 bg-white/10">
              {[
                { k: '119', v: 'districts monitored' },
                { k: '789,335', v: 'km² of boundary geometry' },
                { k: '89%', v: 'of national area' },
                { k: '4', v: 'hazard domains fused' },
              ].map((s) => (
                <div key={s.v} className="bg-[hsl(var(--gv-overlay))] p-6">
                  <div className="gv-numeric text-metric font-semibold leading-none">
                    {s.k}
                  </div>
                  <div className="mt-2 text-caption text-white/45">{s.v}</div>
                </div>
              ))}
            </div>
          </div>
        </div>
      </section>

      {/* ==================================================================
          CTA
         ================================================================== */}
      <section className="border-t border-line bg-canvas py-24">
        <div className="mx-auto max-w-3xl px-4 text-center sm:px-6">
          <h2 className="text-display-sm font-semibold tracking-[-0.03em]">
            Open the command centre
          </h2>
          <p className="mt-4 text-sm leading-relaxed text-content-muted">
            District-level hazard conditions, active events, satellite coverage and
            ingestion health, on one map. Access requires an account.
          </p>
          <Link
            href={isAuthenticated ? '/geovision' : '/login'}
            className="group mt-9 inline-flex items-center gap-2.5 rounded bg-brand px-7 py-3.5 text-sm font-semibold text-brand-fg transition-all duration-gv hover:gap-4 hover:bg-brand-hover"
          >
            {isAuthenticated ? 'Open command centre' : 'Sign in'}
            <ArrowRight className="h-4 w-4" aria-hidden="true" />
          </Link>
        </div>
      </section>

      <footer className="border-t border-line bg-surface py-10">
        <div className="mx-auto flex max-w-7xl flex-col gap-5 px-4 sm:px-6">
          <div className="gv-rule" aria-hidden="true" />
          <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
            <div className="flex items-center gap-2.5">
              <span
                aria-hidden="true"
                className="flex h-6 w-6 items-center justify-center rounded-sm bg-brand text-[10px] font-bold text-brand-fg"
              >
                GV
              </span>
              <span className="text-label font-medium">GeoVision AI / PIDAS</span>
            </div>
            <p className="max-w-xl text-micro leading-relaxed text-content-subtle">
              Built on public Earth observation data from ESA Copernicus, NASA and UCSB
              CHG. Indicators are derived from satellite measurement and are not official
              disaster warnings.
            </p>
          </div>
        </div>
      </footer>
    </div>
  )
}
