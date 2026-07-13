// packages/dashboard/src/app/dashboard/page.tsx
'use client'

import { useAuth } from '@/context/AuthContext'
import Navigation from '@/components/shared/Navigation'
import Link from 'next/link'
import { Satellite, Droplets, Sprout, ArrowRight } from 'lucide-react'

export default function DashboardPage() {
  const { user } = useAuth()

  const projects = [
    {
      name: 'GeoVision AI',
      description: 'AI-powered remote sensing for drought & flood prediction',
      icon: Satellite,
      href: '/geovision',
      color: 'from-emerald-500 to-green-600',
      bgColor: 'bg-emerald-50',
      textColor: 'text-emerald-600',
    },
    {
      name: 'Flood SCADA',
      description: 'Automated barrage gate control & flood diversion',
      icon: Droplets,
      href: '/flood',
      color: 'from-blue-500 to-cyan-600',
      bgColor: 'bg-blue-50',
      textColor: 'text-blue-600',
    },
    {
      name: 'Soil Monitoring',
      description: 'Salinity tracking & land degradation monitoring',
      icon: Sprout,
      href: '/soil',
      color: 'from-amber-500 to-yellow-600',
      bgColor: 'bg-amber-50',
      textColor: 'text-amber-600',
    },
  ]

  return (
    <div className="min-h-screen bg-gray-50">
      <Navigation />
      <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 py-8">
        <div className="flex items-center justify-between mb-8">
          <div>
            <h1 className="text-2xl font-bold text-gray-900">Dashboard</h1>
            <p className="text-sm text-gray-500">
              Welcome back, {user?.full_name || user?.username}!
            </p>
          </div>
          <div className="flex items-center gap-2">
            <span className="px-3 py-1 bg-blue-100 text-blue-700 rounded-full text-xs font-medium">
              {user?.role || 'User'}
            </span>
            {user?.team && (
              <span className="px-3 py-1 bg-green-100 text-green-700 rounded-full text-xs font-medium">
                {user.team}
              </span>
            )}
          </div>
        </div>

        <div className="grid md:grid-cols-3 gap-6">
          {projects.map((project) => (
            <Link
              key={project.name}
              href={project.href}
              className="group bg-white rounded-2xl border border-gray-200/50 shadow-sm hover:shadow-xl transition-all duration-300 overflow-hidden hover:-translate-y-1"
            >
              <div className={`h-2 bg-gradient-to-r ${project.color}`}></div>
              <div className="p-6">
                <div className="flex items-start justify-between mb-4">
                  <div className={`w-12 h-12 ${project.bgColor} rounded-xl flex items-center justify-center`}>
                    <project.icon className={`w-6 h-6 ${project.textColor}`} />
                  </div>
                  <ArrowRight className="w-5 h-5 text-gray-300 group-hover:text-blue-500 transition-colors" />
                </div>
                <h3 className="text-lg font-semibold text-gray-900 mb-2">{project.name}</h3>
                <p className="text-sm text-gray-600">{project.description}</p>
              </div>
            </Link>
          ))}
        </div>
      </div>
    </div>
  )
}