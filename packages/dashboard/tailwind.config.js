// packages/dashboard/tailwind.config.js
//
// Semantic tokens only. Components should reach for `bg-surface`, `text-muted`
// or `border-strong` rather than `bg-slate-900` — a palette change then happens
// in one CSS file instead of across a hundred class strings.
//
// Every colour is `hsl(var(--token) / <alpha-value>)` so opacity modifiers keep
// working: `bg-surface/60` is only composable if the value is not a fixed hex.
//
// The default Tailwind palette is deliberately left intact. The existing
// GeoVision components are built on slate/emerald/amber classes and work
// correctly; this config is additive so nothing has to be migrated in one
// sweep, and nothing that already works breaks.

/** @type {import('tailwindcss').Config} */
module.exports = {
  darkMode: ['class', '[data-theme="dark"]'],
  content: [
    './src/pages/**/*.{js,ts,jsx,tsx,mdx}',
    './src/components/**/*.{js,ts,jsx,tsx,mdx}',
    './src/app/**/*.{js,ts,jsx,tsx,mdx}',
  ],
  theme: {
    extend: {
      colors: {
        canvas: 'hsl(var(--gv-canvas) / <alpha-value>)',
        surface: {
          DEFAULT: 'hsl(var(--gv-surface) / <alpha-value>)',
          raised: 'hsl(var(--gv-surface-raised) / <alpha-value>)',
          sunken: 'hsl(var(--gv-surface-sunken) / <alpha-value>)',
        },
        line: {
          DEFAULT: 'hsl(var(--gv-border) / <alpha-value>)',
          strong: 'hsl(var(--gv-border-strong) / <alpha-value>)',
          subtle: 'hsl(var(--gv-border-subtle) / <alpha-value>)',
        },
        content: {
          DEFAULT: 'hsl(var(--gv-text) / <alpha-value>)',
          muted: 'hsl(var(--gv-text-muted) / <alpha-value>)',
          subtle: 'hsl(var(--gv-text-subtle) / <alpha-value>)',
          inverse: 'hsl(var(--gv-text-inverse) / <alpha-value>)',
        },
        brand: {
          DEFAULT: 'hsl(var(--gv-primary) / <alpha-value>)',
          hover: 'hsl(var(--gv-primary-hover) / <alpha-value>)',
          fg: 'hsl(var(--gv-primary-fg) / <alpha-value>)',
        },
        accent: {
          DEFAULT: 'hsl(var(--gv-accent) / <alpha-value>)',
          soft: 'hsl(var(--gv-accent-soft) / <alpha-value>)',
        },
        // Domain identity — never used to signal severity.
        domain: {
          disaster: 'hsl(var(--gv-domain-disaster) / <alpha-value>)',
          agriculture: 'hsl(var(--gv-domain-agriculture) / <alpha-value>)',
          water: 'hsl(var(--gv-domain-water) / <alpha-value>)',
        },
        // Severity scale. `unknown` is grey, never green.
        sev: {
          ok: 'hsl(var(--gv-ok) / <alpha-value>)',
          'ok-soft': 'hsl(var(--gv-ok-soft) / <alpha-value>)',
          watch: 'hsl(var(--gv-watch) / <alpha-value>)',
          'watch-soft': 'hsl(var(--gv-watch-soft) / <alpha-value>)',
          high: 'hsl(var(--gv-high) / <alpha-value>)',
          'high-soft': 'hsl(var(--gv-high-soft) / <alpha-value>)',
          critical: 'hsl(var(--gv-critical) / <alpha-value>)',
          'critical-soft': 'hsl(var(--gv-critical-soft) / <alpha-value>)',
          unknown: 'hsl(var(--gv-unknown) / <alpha-value>)',
          'unknown-soft': 'hsl(var(--gv-unknown-soft) / <alpha-value>)',
        },
      },
      borderRadius: {
        DEFAULT: 'var(--gv-radius)',
        sm: 'var(--gv-radius-sm)',
        lg: 'var(--gv-radius-lg)',
      },
      boxShadow: {
        sm: 'var(--gv-shadow-sm)',
        DEFAULT: 'var(--gv-shadow)',
        lg: 'var(--gv-shadow-lg)',
      },
      fontFamily: {
        // Inter is already loaded in layout.tsx via next/font.
        sans: ['var(--font-inter)', 'ui-sans-serif', 'system-ui', 'sans-serif'],
        mono: [
          'ui-monospace',
          'SFMono-Regular',
          'Menlo',
          'Consolas',
          'monospace',
        ],
      },
      fontSize: {
        // Operational scale. Dense by design: a command centre trades
        // whitespace for information, and 11-13px is the working range for
        // labels and table cells at desk viewing distance.
        micro: ['0.625rem', { lineHeight: '0.875rem', letterSpacing: '0.06em' }],
        caption: ['0.6875rem', { lineHeight: '1rem' }],
        label: ['0.75rem', { lineHeight: '1.125rem' }],
        metric: ['1.75rem', { lineHeight: '2rem', letterSpacing: '-0.02em' }],
        'metric-lg': ['2.5rem', { lineHeight: '2.75rem', letterSpacing: '-0.03em' }],
        display: ['clamp(2.5rem, 6vw, 4.5rem)', { lineHeight: '1.04', letterSpacing: '-0.035em' }],
        'display-sm': ['clamp(1.75rem, 3.5vw, 2.5rem)', { lineHeight: '1.15', letterSpacing: '-0.02em' }],
      },
      spacing: {
        // 8px system extensions for shell geometry.
        header: '3.5rem',
        sidebar: '15rem',
        'sidebar-collapsed': '3.5rem',
      },
      transitionTimingFunction: {
        gv: 'var(--gv-ease)',
      },
      transitionDuration: {
        gv: 'var(--gv-duration)',
      },
      zIndex: {
        map: '0',
        panel: '10',
        header: '30',
        drawer: '40',
        modal: '50',
        toast: '60',
      },
      maxWidth: {
        shell: '120rem',
      },
    },
  },
  plugins: [],
}
