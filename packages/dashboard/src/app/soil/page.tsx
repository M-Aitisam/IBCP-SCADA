// packages/dashboard/src/app/soil/page.tsx
'use client'

import Link from 'next/link'
import { Sprout } from 'lucide-react'
import AppShell from '@/components/shell/AppShell'
import ProtectedRoute from '@/components/shared/ProtectedRoute'
import { Panel, PanelHeader } from '@/components/ui/Card'
import { EmptyState } from '@/components/ui/States'

function SoilContent() {
  return (
    <AppShell title="Soil & Crop Monitoring">
      <div className="mx-auto max-w-5xl space-y-4">
        <Panel flush>
          <PanelHeader
            title="Salinity and land degradation"
            subtitle="Ground-station soil measurements"
          />
          <EmptyState
            icon={<Sprout className="h-8 w-8" aria-hidden="true" />}
            title="No ground-station soil feed is configured"
            description={
              <>
                Salinity and degradation tracking needs in-situ measurements that this
                deployment does not yet receive. Rather than model them, the panel stays
                empty. Satellite vegetation condition (NDVI and EVI, scored against each
                district&rsquo;s baseline) is live in the{' '}
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

export default function SoilPage() {
  return (
    <ProtectedRoute>
      <SoilContent />
    </ProtectedRoute>
  )
}
