// packages/dashboard/src/app/login/page.tsx
'use client'

// Split-screen sign-in: credentials on the left, system context on the right.
//
// The right panel is not decoration. Someone signing into an operational
// console should be able to confirm, before authenticating, that they are on
// the right system — which one, covering what, reading from where. On narrow
// viewports it is dropped entirely rather than stacked, because a form the
// user has to scroll past a banner to reach is worse than no banner.
//
// The auth flow itself is unchanged: the same useAuth().login and the same
// backend Google OAuth redirect as before.
import { useState } from 'react'
import Link from 'next/link'
import { Eye, EyeOff } from 'lucide-react'
import { useAuth } from '@/context/AuthContext'
import Button from '@/components/ui/Button'
import { Field, Input } from '@/components/ui/Field'

import { API_URL } from '@/utils/axios'

export default function LoginPage() {
  const { login } = useAuth()
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [showPassword, setShowPassword] = useState(false)
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(false)

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    setError('')
    setLoading(true)

    try {
      await login(username, password)
    } catch (err: any) {
      setError(err.response?.data?.detail || 'Login failed')
    } finally {
      setLoading(false)
    }
  }

  const handleGoogleLogin = () => {
    window.location.href = `${API_URL}/auth/google`
  }

  return (
    <div className="flex min-h-screen bg-canvas text-content">
      {/* ---- Credentials ------------------------------------------------ */}
      <div className="flex w-full flex-col justify-center px-6 py-12 lg:w-[46%] lg:px-16">
        <div className="mx-auto w-full max-w-sm">
          <Link href="/" className="mb-10 inline-flex items-center gap-2">
            <span
              aria-hidden="true"
              className="flex h-8 w-8 items-center justify-center rounded-sm bg-brand text-xs font-bold text-brand-fg"
            >
              GV
            </span>
            <span className="text-sm font-semibold tracking-tight">
              GeoVision<span className="text-content-subtle"> / PIDAS</span>
            </span>
          </Link>

          <h1 className="text-display-sm font-semibold tracking-tight">Sign in</h1>
          <p className="mt-2 text-caption text-content-muted">
            Operational console access. Contact an administrator if you need an account
            provisioned.
          </p>

          {error && (
            // role="alert" so the failure is announced, not only recoloured.
            <div
              role="alert"
              className="mt-6 rounded border border-sev-critical/30 bg-sev-critical-soft px-3 py-2 text-caption text-sev-critical"
            >
              {error}
            </div>
          )}

          <form onSubmit={handleSubmit} className="mt-6 space-y-4">
            <Field label="Username" required>
              {({ id }) => (
                <Input
                  id={id}
                  type="text"
                  value={username}
                  onChange={(e) => setUsername(e.target.value)}
                  autoComplete="username"
                  placeholder="operator"
                  required
                />
              )}
            </Field>

            <Field label="Password" required>
              {({ id }) => (
                <div className="relative">
                  <Input
                    id={id}
                    type={showPassword ? 'text' : 'password'}
                    value={password}
                    onChange={(e) => setPassword(e.target.value)}
                    autoComplete="current-password"
                    placeholder="••••••••"
                    className="pr-10"
                    required
                  />
                  <button
                    type="button"
                    onClick={() => setShowPassword((s) => !s)}
                    // Labelled: an unlabelled icon button is an unnamed control
                    // to a screen reader.
                    aria-label={showPassword ? 'Hide password' : 'Show password'}
                    className="absolute right-2 top-1/2 -translate-y-1/2 rounded-sm p-1 text-content-subtle transition-colors hover:text-content"
                  >
                    {showPassword ? (
                      <EyeOff className="h-4 w-4" />
                    ) : (
                      <Eye className="h-4 w-4" />
                    )}
                  </button>
                </div>
              )}
            </Field>

            <Button type="submit" loading={loading} size="lg" className="w-full">
              {loading ? 'Signing in' : 'Sign in'}
            </Button>
          </form>

          <div className="relative my-6">
            <div className="absolute inset-0 flex items-center" aria-hidden="true">
              <div className="w-full border-t border-line" />
            </div>
            <div className="relative flex justify-center">
              <span className="bg-canvas px-3 text-micro font-semibold uppercase tracking-widest text-content-subtle">
                or
              </span>
            </div>
          </div>

          <Button
            type="button"
            variant="secondary"
            size="lg"
            onClick={handleGoogleLogin}
            disabled={loading}
            className="w-full"
          >
            <svg className="h-4 w-4" viewBox="0 0 24 24" aria-hidden="true">
              <path
                fill="#4285F4"
                d="M23.52 12.27c0-.85-.08-1.67-.22-2.45H12v4.64h6.47c-.28 1.5-1.13 2.77-2.4 3.62v3h3.88c2.27-2.09 3.57-5.17 3.57-8.81z"
              />
              <path
                fill="#34A853"
                d="M12 24c3.24 0 5.96-1.07 7.95-2.92l-3.88-3c-1.08.72-2.45 1.15-4.07 1.15-3.13 0-5.78-2.11-6.73-4.96H1.27v3.09C3.25 21.3 7.31 24 12 24z"
              />
              <path
                fill="#FBBC05"
                d="M5.27 14.27a7.2 7.2 0 0 1 0-4.54v-3.1H1.27a12 12 0 0 0 0 10.74z"
              />
              <path
                fill="#EA4335"
                d="M12 4.75c1.77 0 3.35.61 4.6 1.8l3.44-3.44C17.95 1.19 15.24 0 12 0 7.31 0 3.25 2.7 1.27 6.63l3.99 3.1C6.22 6.86 8.87 4.75 12 4.75z"
              />
            </svg>
            Continue with Google
          </Button>

          <p className="mt-8 text-caption text-content-subtle">
            Don&apos;t have an account?{' '}
            <Link href="/register" className="font-medium text-brand hover:underline">
              Request access
            </Link>
          </p>
        </div>
      </div>

      {/* ---- System context --------------------------------------------
          Hidden below lg. Purely orienting content: nothing here is required
          to sign in.
         ---------------------------------------------------------------- */}
      <div
        className="relative hidden flex-1 overflow-hidden bg-[hsl(var(--gv-overlay))] lg:block"
        aria-hidden="true"
      >
        <div className="absolute inset-0 bg-gradient-to-br from-[#04101f] via-[#071b2e] to-[#020a14]" />
        <div className="absolute inset-0 gv-grid opacity-[0.12]" />
        <div
          className="absolute -right-1/4 top-1/4 h-[60vh] w-[60vh] rounded-full blur-3xl"
          style={{
            background:
              'radial-gradient(circle, hsl(var(--gv-accent) / 0.16), transparent 62%)',
          }}
        />
        <div className="gv-scanline absolute inset-0" />

        <div className="relative flex h-full flex-col justify-end p-16">
          <p className="text-micro font-semibold uppercase tracking-widest text-[hsl(var(--gv-accent))]">
            Pakistan Integrated Disaster &amp; Agriculture Intelligence
          </p>
          <p className="mt-4 max-w-md text-lg leading-relaxed text-white/80">
            Optical and radar observations across 119 districts, scored against each
            district&rsquo;s own multi-year baseline.
          </p>
          <dl className="mt-10 grid max-w-md grid-cols-3 gap-6 border-t border-white/10 pt-8">
            {[
              { k: '5', v: 'collections' },
              { k: '119', v: 'districts' },
              { k: '10 m', v: 'resolution' },
            ].map((s) => (
              <div key={s.v}>
                <dt className="gv-numeric text-xl font-semibold text-white">{s.k}</dt>
                <dd className="mt-0.5 text-caption text-white/40">{s.v}</dd>
              </div>
            ))}
          </dl>
        </div>
      </div>
    </div>
  )
}
