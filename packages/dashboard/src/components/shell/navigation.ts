// packages/dashboard/src/components/shell/navigation.ts
//
// One navigation definition, consumed by both the sidebar and the mobile drawer.
// Duplicating the list in two components is how a route ends up reachable on
// desktop and invisible on a phone.
import {
  AlertTriangle,
  Database,
  Droplets,
  Globe2,
  LayoutDashboard,
  Radio,
  Satellite,
  Sprout,
} from 'lucide-react'
import type { LucideIcon } from 'lucide-react'

export interface NavItem {
  name: string
  href: string
  icon: LucideIcon
  /**
   * Roles permitted to see the entry. Undefined = visible to any signed-in
   * user. This governs *visibility only* — the backend is still the authority
   * on access, and hiding a link is never treated as a security control.
   */
  roles?: string[]
  description: string
}

export interface NavSection {
  label: string
  items: NavItem[]
}

export const NAV_SECTIONS: NavSection[] = [
  {
    label: 'Situation',
    items: [
      {
        name: 'Overview',
        href: '/dashboard',
        icon: LayoutDashboard,
        description: 'Cross-domain summary of current conditions',
      },
      {
        name: 'GIS Command',
        href: '/geovision',
        icon: Globe2,
        description: 'District-level map of hazard and vegetation conditions',
      },
      {
        name: 'Alerts',
        href: '/geovision#alerts',
        icon: AlertTriangle,
        description: 'Active alerts and their lifecycle',
      },
    ],
  },
  {
    label: 'Domains',
    items: [
      {
        name: 'Flood SCADA',
        href: '/flood',
        icon: Droplets,
        description: 'Water level and flood telemetry',
      },
      {
        name: 'Soil & Crop',
        href: '/soil',
        icon: Sprout,
        description: 'Soil moisture and crop health monitoring',
      },
    ],
  },
  {
    label: 'Sources',
    items: [
      {
        name: 'Satellite',
        href: '/geovision#satellite',
        icon: Satellite,
        description: 'Scene coverage and acquisition freshness',
      },
      {
        name: 'Data Pipeline',
        href: '/geovision#pipeline',
        icon: Database,
        // Operators run and monitor ingestion; viewers do not need it.
        roles: ['admin', 'operator'],
        description: 'Ingestion runs, quality scores and lineage',
      },
      {
        name: 'System Status',
        href: '/geovision#status',
        icon: Radio,
        roles: ['admin', 'operator'],
        description: 'Service health and last successful run',
      },
    ],
  },
]

/**
 * Filter the navigation for a role.
 *
 * A null/unknown role sees only the unrestricted entries — the safe direction
 * to fail, and consistent with the backend's require_operator check.
 */
export function visibleSections(role: string | null | undefined): NavSection[] {
  const normalised = (role ?? '').toLowerCase()
  return NAV_SECTIONS.map((section) => ({
    ...section,
    items: section.items.filter(
      (item) => !item.roles || item.roles.includes(normalised)
    ),
  })).filter((section) => section.items.length > 0)
}
