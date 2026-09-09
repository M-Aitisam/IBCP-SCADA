// packages/dashboard/src/components/shell/navigation.test.ts
import { describe, expect, it } from 'vitest'
import { NAV_SECTIONS, visibleSections } from './navigation'

const names = (role: string | null | undefined) =>
  visibleSections(role).flatMap((s) => s.items.map((i) => i.name))

describe('visibleSections', () => {
  it('shows operator-only entries to admins and operators', () => {
    expect(names('admin')).toContain('Data Pipeline')
    expect(names('operator')).toContain('Data Pipeline')
  })

  it('is case insensitive, since role casing varies across the API', () => {
    expect(names('ADMIN')).toContain('Data Pipeline')
  })

  // Failing closed matters: a viewer seeing an ingestion control they cannot
  // use produces a confusing 403 rather than a clean absence.
  it.each([null, undefined, 'viewer', 'nonsense'])(
    'hides operator-only entries from %p',
    (role) => {
      expect(names(role as string | null | undefined)).not.toContain('Data Pipeline')
      expect(names(role as string | null | undefined)).not.toContain('System Status')
    }
  )

  it('still shows unrestricted entries to a viewer', () => {
    expect(names('viewer')).toContain('GIS Command')
    expect(names('viewer')).toContain('Overview')
  })

  it('drops sections that end up empty rather than rendering a bare heading', () => {
    const sections = visibleSections('viewer')
    expect(sections.every((s) => s.items.length > 0)).toBe(true)
  })

  it('gives every entry a description, used as the sidebar tooltip', () => {
    const all = NAV_SECTIONS.flatMap((s) => s.items)
    expect(all.every((i) => i.description.length > 0)).toBe(true)
  })
})
