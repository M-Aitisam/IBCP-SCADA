// packages/dashboard/src/components/landing/LandingNav.tsx
'use client'

// Transparent over the hero, solid once the page scrolls past it. Sitting a
// solid bar on top of the hero from the start wastes the one moment the video
// has to make an impression; leaving it transparent forever makes the links
// unreadable over the lighter sections below.
import Link from 'next/link'
import { useEffect, useState } from 'react'
import { Menu, X } from 'lucide-react'
import { cn } from '@/lib/cn'
import { useAuth } from '@/context/AuthContext'

const LINKS = [
  { label: 'Capabilities', href: '#capabilities' },
  { label: 'Pipeline', href: '#pipeline' },
  { label: 'Data sources', href: '#sources' },
  { label: 'Coverage', href: '#coverage' },
]

export default function LandingNav() {
  const [scrolled, setScrolled] = useState(false)
  const [open, setOpen] = useState(false)
  const { isAuthenticated } = useAuth()

  useEffect(() => {
    const onScroll = () => setScrolled(window.scrollY > 24)
    onScroll()
    // Passive: this listener never calls preventDefault, and saying so lets the
    // browser keep scrolling off the main thread.
    window.addEventListener('scroll', onScroll, { passive: true })
    return () => window.removeEventListener('scroll', onScroll)
  }, [])

  return (
    <header
      className={cn(
        'fixed inset-x-0 top-0 z-header transition-colors duration-gv ease-gv',
        scrolled
          ? 'border-b border-white/10 bg-[hsl(var(--gv-overlay)/0.85)] backdrop-blur'
          : 'bg-transparent'
      )}
    >
      <div className="mx-auto flex h-14 max-w-7xl items-center gap-6 px-4 sm:px-6">
        <Link href="/" className="flex items-center gap-2">
          <span
            aria-hidden="true"
            className="flex h-7 w-7 items-center justify-center rounded-sm bg-brand text-[11px] font-bold text-brand-fg"
          >
            GV
          </span>
          <span className="text-sm font-semibold tracking-tight text-white">
            GeoVision<span className="text-white/40"> / PIDAS</span>
          </span>
        </Link>

        <nav aria-label="Sections" className="ml-auto hidden items-center gap-1 md:flex">
          {LINKS.map((link) => (
            <a
              key={link.href}
              href={link.href}
              className="rounded px-3 py-1.5 text-label text-white/70 transition-colors duration-gv hover:bg-white/10 hover:text-white"
            >
              {link.label}
            </a>
          ))}
        </nav>

        <div className="ml-auto flex items-center gap-2 md:ml-0">
          <Link
            href={isAuthenticated ? '/dashboard' : '/login'}
            className="rounded bg-white px-3.5 py-1.5 text-label font-semibold text-[hsl(var(--gv-overlay))] transition-colors duration-gv hover:bg-white/90"
          >
            {isAuthenticated ? 'Open console' : 'Sign in'}
          </Link>
          <button
            type="button"
            onClick={() => setOpen((o) => !o)}
            aria-expanded={open}
            aria-label={open ? 'Close menu' : 'Open menu'}
            className="rounded p-1.5 text-white/80 hover:bg-white/10 md:hidden"
          >
            {open ? <X className="h-4 w-4" /> : <Menu className="h-4 w-4" />}
          </button>
        </div>
      </div>

      {open && (
        <nav
          aria-label="Sections"
          className="border-t border-white/10 bg-[hsl(var(--gv-overlay)/0.97)] px-4 py-2 md:hidden"
        >
          {LINKS.map((link) => (
            <a
              key={link.href}
              href={link.href}
              onClick={() => setOpen(false)}
              className="block rounded px-2 py-2 text-sm text-white/80 hover:bg-white/10"
            >
              {link.label}
            </a>
          ))}
        </nav>
      )}
    </header>
  )
}
