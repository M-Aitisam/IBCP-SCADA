// packages/dashboard/src/app/flood/page.tsx
'use client'

import Link from 'next/link'
import { Droplets } from 'lucide-react'
import AppShell from '@/components/shell/AppShell'
import ProtectedRoute from '@/components/shared/ProtectedRoute'
import { Panel, PanelHeader } from '@/components/ui/Card'
import { EmptyState } from '@/components/ui/States'

function FloodContent() {
  return (
    <AppShell title="Flood SCADA">
      <div className="mx-auto max-w-5xl space-y-4">
        <Panel flush>
          <PanelHeader
            title="Barrage gate control"
            subtitle="Automated flood diversion and telemetry"
          />
          {/* Says what is missing and where the working capability lives,
              rather than a bare "coming soon" with no next step. */}
          <EmptyState
            icon={<Droplets className="h-8 w-8" aria-hidden="true" />}
            title="SCADA telemetry is not yet connected"
            description={
              <>
                This surface will carry live barrage gate state and water level
                telemetry. No SCADA endpoint is configured, so there is nothing to
                display — deliberately blank rather than filled with sample readings.
                Satellite-derived flood conditions are already available in the{' '}
                <Link href="/geovision" className="font-medium text-brand hover:underline">
                  GIS command centre
                </Link>
                .
              </>
            }
          />
        </Panel>
      </div>
    </AppShell>
  )
}

export default function FloodPage() {
  return (
    <ProtectedRoute>
      <FloodContent />
    </ProtectedRoute>
  )
}
