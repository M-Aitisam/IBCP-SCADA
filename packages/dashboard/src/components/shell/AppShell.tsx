// packages/dashboard/src/components/shell/AppShell.tsx
'use client'

// The signed-in application frame: a persistent left rail, a thin top bar, and
// a scrolling content region.
//
// The rail is vertical rather than a top nav because the primary surface is a
// map. Horizontal space is worth more than vertical space to a marketing page;
// for a full-bleed map the reverse is true, and a 56px top bar plus a 240px
// rail leaves the map the whole remaining rectangle.
import Link from 'next/link'
import { usePathname } from 'next/navigation'
import { useEffect, useState, type ReactNode } from 'react'
import { LogOut, Menu, PanelLeftClose, PanelLeft, X } from 'lucide-react'
import { useAuth } from '@/context/AuthContext'
import { cn } from '@/lib/cn'
import ThemeToggle from '@/components/shared/ThemeToggle'
import Badge from '@/components/ui/Badge'
import Button from '@/components/ui/Button'
import { visibleSections } from './navigation'

export interface AppShellProps {
  children: ReactNode
  /** Page title shown in the top bar. */
  title?: string
  /** Right-aligned page-level controls. */
  actions?: ReactNode
  /** Full-bleed pages (the map) manage their own scrolling and padding. */
  flush?: boolean
}

export default function AppShell({ children, title, actions, flush }: AppShellProps) {
  const { user, logout } = useAuth()
  const pathname = usePathname()
  const [collapsed, setCollapsed] = useState(false)
  const [mobileOpen, setMobileOpen] = useState(false)

  const sections = visibleSections(user?.role)

  // Close the mobile drawer on navigation; leaving it open over the destination
  // is the classic mobile-nav bug.
  useEffect(() => {
    setMobileOpen(false)
  }, [pathname])

  // Escape closes the drawer, as expected of anything modal.
  useEffect(() => {
    if (!mobileOpen) return
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') setMobileOpen(false)
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [mobileOpen])

  const isActive = (href: string) => pathname === href.split('#')[0]

  const rail = (
    <nav aria-label="Primary" className="flex h-full flex-col gap-4 overflow-y-auto py-3">
      {sections.map((section) => (
        <div key={section.label}>
          {!collapsed && (
            <p className="px-3 pb-1.5 text-micro font-semibold uppercase tracking-widest text-content-subtle">
              {section.label}
            </p>
          )}
          <ul className="space-y-0.5 px-2">
            {section.items.map((item) => {
              const active = isActive(item.href)
              return (
                <li key={item.name}>
                  <Link
                    href={item.href}
                    // The description doubles as the tooltip when collapsed, so
                    // an icon-only rail is still identifiable.
                    title={collapsed ? item.name + ' - ' + item.description : item.description}
                    aria-current={active ? 'page' : undefined}
                    className={cn(
                      'group relative flex items-center gap-2.5 rounded px-2.5 py-1.5 text-label transition-colors duration-gv ease-gv',
                      active
                        ? 'bg-brand/10 font-medium text-brand'
                        : 'text-content-muted hover:bg-surface-sunken hover:text-content',
                      collapsed && 'justify-center px-0'
                    )}
                  >
                    {/* Active marker on the rail edge, legible even collapsed. */}
                    {active && (
                      <span
                        aria-hidden="true"
                        className="absolute inset-y-1 left-0 w-0.5 rounded-full bg-brand"
                      />
                    )}
                    <item.icon className="h-4 w-4 shrink-0" aria-hidden="true" />
                    {!collapsed && <span className="truncate">{item.name}</span>}
                  </Link>
                </li>
              )
            })}
          </ul>
        </div>
      ))}
    </nav>
  )

  return (
    <div className="flex min-h-screen bg-canvas text-content">
      {/* Desktop rail */}
      <aside
        className={cn(
          'sticky top-0 hidden h-screen shrink-0 flex-col border-r border-line bg-surface transition-[width] duration-gv ease-gv lg:flex',
          collapsed ? 'w-sidebar-collapsed' : 'w-sidebar'
        )}
      >
        <div
          className={cn(
            'flex h-header shrink-0 items-center gap-2 border-b border-line px-3',
            collapsed && 'justify-center px-0'
          )}
        >
          <Link href="/" className="flex items-center gap-2 overflow-hidden">
            <span
              aria-hidden="true"
              className="flex h-7 w-7 shrink-0 items-center justify-center rounded-sm bg-brand text-[11px] font-bold text-brand-fg"
            >
              GV
            </span>
            {!collapsed && (
              <span className="truncate text-sm font-semibold tracking-tight">GeoVision</span>
            )}
          </Link>
        </div>

        <div className="min-h-0 flex-1">{rail}</div>

        <div className="shrink-0 border-t border-line p-2">
          <Button
            variant="ghost"
            size="sm"
            onClick={() => setCollapsed((c) => !c)}
            aria-label={collapsed ? 'Expand sidebar' : 'Collapse sidebar'}
            className="w-full justify-center"
          >
            {collapsed ? (
              <PanelLeft className="h-4 w-4" />
            ) : (
              <>
                <PanelLeftClose className="h-4 w-4" />
                <span>Collapse</span>
              </>
            )}
          </Button>
        </div>
      </aside>

      {/* Mobile drawer */}
      {mobileOpen && (
        <div className="fixed inset-0 z-drawer lg:hidden">
          <button
            aria-label="Close navigation"
            onClick={() => setMobileOpen(false)}
            className="absolute inset-0 bg-black/60"
          />
          <div
            role="dialog"
            aria-modal="true"
            aria-label="Navigation"
            className="absolute inset-y-0 left-0 flex w-sidebar flex-col border-r border-line bg-surface shadow-lg"
          >
            <div className="flex h-header shrink-0 items-center justify-between border-b border-line px-3">
              <span className="text-sm font-semibold">GeoVision</span>
              <Button
                variant="ghost"
                size="icon"
                onClick={() => setMobileOpen(false)}
                aria-label="Close navigation"
              >
                <X className="h-4 w-4" />
              </Button>
            </div>
            <div className="min-h-0 flex-1">{rail}</div>
          </div>
        </div>
      )}

      {/* Content column */}
      <div className="flex min-w-0 flex-1 flex-col">
        <header className="sticky top-0 z-header flex h-header shrink-0 items-center gap-3 border-b border-line bg-surface/90 px-3 backdrop-blur">
          <Button
            variant="ghost"
            size="icon"
            className="lg:hidden"
            onClick={() => setMobileOpen(true)}
            aria-label="Open navigation"
          >
            <Menu className="h-4 w-4" />
          </Button>

          {title && <h1 className="truncate text-sm font-semibold tracking-tight">{title}</h1>}

          <div className="ml-auto flex items-center gap-2">
            {actions}
            <ThemeToggle />
            {user && (
              <>
                <div className="hidden items-center gap-2 sm:flex">
                  <span className="max-w-[12rem] truncate text-label text-content-muted">
                    {user.full_name || user.username}
                  </span>
                  {/* Role is shown, not merely enforced: an operator should be
                      able to tell at a glance why a control is missing. */}
                  <Badge tone="brand">{user.role}</Badge>
                </div>
                <Button variant="ghost" size="icon" onClick={logout} aria-label="Sign out">
                  <LogOut className="h-4 w-4" />
                </Button>
              </>
            )}
          </div>
        </header>

        <main className={cn('min-w-0 flex-1', flush ? '' : 'p-4')}>{children}</main>
      </div>
    </div>
  )
}
