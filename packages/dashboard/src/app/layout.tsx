// packages/dashboard/src/app/layout.tsx
import type { Metadata } from 'next'
import { Inter } from 'next/font/google'
import '../styles/tokens.css'
import './globals.css'
import '../styles/theme.css'
import { AuthProvider } from '@/context/AuthContext'
import { ThemeProvider } from '@/context/ThemeContext'
import QueryProvider from '@/components/providers/QueryProvider'

const inter = Inter({ subsets: ['latin'], variable: '--font-inter' })

export const metadata: Metadata = {
  title: 'IBCP-SCADA - Indus Basin Cyber-Physical System',
  description: 'Unified Mega System for Flood Management, Water Distribution, and Agricultural Intelligence',
}

export default function RootLayout({
  children,
}: {
  children: React.ReactNode
}) {
  return (
    <html lang="en" suppressHydrationWarning>
      <body className={`${inter.variable} ${inter.className}`}>
        <ThemeProvider>
          <QueryProvider>
            <AuthProvider>
              {children}
            </AuthProvider>
          </QueryProvider>
        </ThemeProvider>
      </body>
    </html>
  )
}