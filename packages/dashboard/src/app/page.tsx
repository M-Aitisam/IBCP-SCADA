// packages/dashboard/src/app/page.tsx
'use client'

import Link from 'next/link'
import { 
  LayoutDashboard, 
  Satellite, 
  Droplets, 
  Sprout,
  ArrowRight,
  Activity,
  Map,
  CloudRain,
  Thermometer
} from 'lucide-react'

export default function Home() {
  const projects = [
    {
      name: 'GeoVision AI',
      description: 'AI-powered remote sensing for drought & flood prediction',
      icon: Satellite,
      href: '/geovision',
      color: 'from-emerald-500 to-green-600',
      bgColor: 'bg-emerald-50',
      textColor: 'text-emerald-600',
      stats: ['148 Tehsils', '5 Severity Levels', '2-Week Prediction'],
    },
    {
      name: 'Flood SCADA',
      description: 'Automated barrage gate control & flood diversion',
      icon: Droplets,
      href: '/flood',
      color: 'from-blue-500 to-cyan-600',
      bgColor: 'bg-blue-50',
      textColor: 'text-blue-600',
      stats: ['24/7 Monitoring', 'Real-time Gates', 'Early Warning'],
    },
    {
      name: 'Soil Monitoring',
      description: 'Salinity tracking & land degradation monitoring',
      icon: Sprout,
      href: '/soil',
      color: 'from-amber-500 to-yellow-600',
      bgColor: 'bg-amber-50',
      textColor: 'text-amber-600',
      stats: ['Salinity Maps', 'Pump Control', 'Land Health'],
    },
  ]

  return (
    <div className="min-h-screen bg-gradient-to-br from-slate-50 via-white to-blue-50">
      {/* Header */}
      <header className="bg-white/80 backdrop-blur-sm border-b border-gray-200/50">
        <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 py-4">
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-3">
              <div className="w-10 h-10 bg-gradient-to-br from-blue-600 to-indigo-600 rounded-xl flex items-center justify-center shadow-lg shadow-blue-500/25">
                <LayoutDashboard className="w-5 h-5 text-white" />
              </div>
              <div>
                <h1 className="text-xl font-bold text-gray-900">IBCP-SCADA</h1>
                <p className="text-xs text-gray-500">Indus Basin Cyber-Physical System</p>
              </div>
            </div>
            <div className="flex items-center gap-3">
              <span className="px-3 py-1 bg-green-100 text-green-700 rounded-full text-xs font-medium flex items-center gap-1">
                <span className="w-1.5 h-1.5 bg-green-500 rounded-full animate-pulse"></span>
                System Online
              </span>
            </div>
          </div>
        </div>
      </header>

      {/* Hero Section */}
      <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 py-12">
        <div className="text-center mb-12">
          <h2 className="text-3xl sm:text-4xl font-bold text-gray-900">
            Unified Mega System for
            <span className="block text-transparent bg-clip-text bg-gradient-to-r from-blue-600 to-indigo-600">
              Water & Agricultural Intelligence
            </span>
          </h2>
          <p className="mt-4 text-lg text-gray-600 max-w-2xl mx-auto">
            Pakistan's first integrated cyber-physical SCADA system for flood management,
            water distribution, and agricultural monitoring.
          </p>
        </div>

        {/* Stats Overview */}
        <div className="grid grid-cols-2 md:grid-cols-4 gap-4 mb-12">
          {[
            { label: 'Projects', value: '3', icon: LayoutDashboard },
            { label: 'Tehsils Monitored', value: '148', icon: Map },
            { label: 'Alert Types', value: '5', icon: CloudRain },
            { label: 'Real-time Status', value: 'Active', icon: Activity },
          ].map((stat, i) => (
            <div key={i} className="bg-white/70 backdrop-blur-sm rounded-xl border border-gray-200/50 p-4 text-center shadow-sm hover:shadow-md transition-shadow">
              <stat.icon className="w-5 h-5 text-blue-500 mx-auto mb-1" />
              <div className="text-2xl font-bold text-gray-900">{stat.value}</div>
              <div className="text-xs text-gray-500">{stat.label}</div>
            </div>
          ))}
        </div>

        {/* Project Cards */}
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
                <p className="text-sm text-gray-600 mb-4">{project.description}</p>
                <div className="flex flex-wrap gap-2">
                  {project.stats.map((stat, i) => (
                    <span key={i} className="px-2 py-0.5 bg-gray-100 text-gray-600 text-xs rounded-full">
                      {stat}
                    </span>
                  ))}
                </div>
              </div>
            </Link>
          ))}
        </div>

        {/* Footer */}
        <div className="mt-12 text-center text-xs text-gray-400 border-t border-gray-200/50 pt-6">
          © 2026 IBCP-SCADA • Air University Islamabad
        </div>
      </div>
    </div>
  )
}