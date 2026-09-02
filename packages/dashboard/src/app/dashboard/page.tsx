// packages/dashboard/src/app/dashboard/page.tsx
'use client'

// The post-sign-in landing surface.
//
// Previously this was three links to the sub-systems. An operator opening the
// console wants to know whether anything needs their attention before they pick
// a destination, so the headline coverage and freshness figures come first and
// the navigation follows.
//
// Every figure here is read from the existing /geovision overview endpoint. No
// new API surface, and nothing is displayed unless the backend returned it.
import Link from 'next/link'
import { ArrowRight, Droplets, Globe2, Sprout } from 'lucide-react'
import AppShell from '@/components/shell/AppShell'
import ProtectedRoute from '@/components/shared/ProtectedRoute'
import { useAuth } from '@/context/AuthContext'
import { useOverview } from '@/hooks/useGeovision'
import { Panel, PanelHeader } from '@/components/ui/Card'
import Metric from '@/components/ui/Metric'
import { ErrorState, PanelLoading } from '@/components/ui/States'

const DESTINATIONS = [
  {
    name: 'GIS Command Centre',
    href: '/geovision',
    icon: Globe2,
    accent: 'text-domain-water',
    description:
      'District-level hazard conditions, active events, satellite coverage and ingestion health on one map.',
    status: 'live' as const,
  },
  {
    name: 'Flood SCADA',
    href: '/flood',
    icon: Droplets,
    accent: 'text-domain-disaster',
    description:
      'Barrage gate control and water level telemetry. Awaiting a SCADA endpoint.',
    status: 'pending' as const,
  },
  {
    name: 'Soil & Crop',
    href: '/soil',
    icon: Sprout,
    accent: 'text-domain-agriculture',
    description:
      'Salinity and land degradation from ground stations. Awaiting an in-situ feed.',
    status: 'pending' as const,
  },
]

function DashboardContent() {
  const { user } = useAuth()
  const overview = useOverview()

  const coverage = overview.data?.coverage

  return (
    <AppShell title="Overview">
      <div className="mx-auto max-w-6xl space-y-4">
        <div>
          <h2 className="text-display-sm font-semibold tracking-tight">
            {user?.full_name || user?.username}
          </h2>
          <p className="mt-1 text-caption text-content-muted">
            Signed in as {user?.role}
            {user?.team ? ` · ${user.team}` : ''}
          </p>
        </div>

        <Panel flush>
          <PanelHeader
            title="Observation coverage"
            subtitle="Across all ingested satellite collections"
          />
          {overview.isLoading ? (
            <PanelLoading rows={2} />
          ) : overview.isError ? (
            <ErrorState
              message="The overview endpoint did not respond."
              onRetry={() => overview.refetch()}
            />
          ) : (
            <div className="grid gap-6 p-4 sm:grid-cols-3">
              {/* Counts, so higherIsBetter is left at its default: more stored
                  observations is unambiguously better coverage. */}
              <Metric label="Observations stored" value={coverage?.observations ?? null} />
              <Metric label="Regions monitored" value={coverage?.regions_monitored ?? null} />
              <Metric label="Provinces" value={coverage?.provinces ?? null} />
            </div>
          )}
        </Panel>

        <div className="grid gap-3 md:grid-cols-3">
          {DESTINATIONS.map((d) => (
            <Link
              key={d.name}
              href={d.href}
              className="group flex flex-col rounded border border-line bg-surface p-5 transition-colors duration-gv ease-gv hover:border-line-strong hover:bg-surface-raised"
            >
              <div className="flex items-start justify-between">
                <d.icon className={`h-5 w-5 ${d.accent}`} aria-hidden="true" />
                <ArrowRight
                  className="h-4 w-4 text-content-subtle transition-colors group-hover:text-brand"
                  aria-hidden="true"
                />
              </div>
              <h3 className="mt-4 text-sm font-semibold">{d.name}</h3>
              <p className="mt-1.5 flex-1 text-caption leading-relaxed text-content-muted">
                {d.description}
              </p>
              {/* An honest label, so an operator is not sent to an empty page
                  expecting live telemetry. */}
              <span
                className={`mt-4 inline-flex w-fit items-center gap-1.5 rounded-sm border px-1.5 py-0.5 text-micro font-semibold uppercase tracking-wider ${
                  d.status === 'live'
                    ? 'border-sev-ok/30 bg-sev-ok-soft text-sev-ok'
                    : 'border-sev-unknown/30 bg-sev-unknown-soft text-sev-unknown'
                }`}
              >
                {d.status === 'live' ? 'Live' : 'Not connected'}
              </span>
            </Link>
          ))}
        </div>
      </div>
    </AppShell>
  )
}

export default function DashboardPage() {
  return (
    <ProtectedRoute>
      <DashboardContent />
    </ProtectedRoute>
  )
}
