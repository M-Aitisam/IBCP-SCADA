// packages/dashboard/src/components/shared/Navigation.tsx
'use client'

import Link from 'next/link'
import { usePathname } from 'next/navigation'
import { 
  LayoutDashboard, 
  Satellite, 
  Droplets, 
  Sprout,
  Bell,
  User,
  ChevronLeft
} from 'lucide-react'

const navItems = [
  { name: 'Dashboard', href: '/', icon: LayoutDashboard },
  { name: 'GeoVision AI', href: '/geovision', icon: Satellite },
  { name: 'Flood SCADA', href: '/flood', icon: Droplets },
  { name: 'Soil Monitoring', href: '/soil', icon: Sprout },
]

export default function Navigation() {
  const pathname = usePathname()

  return (
    <nav className="bg-white/80 backdrop-blur-sm border-b border-gray-200/50 px-4 py-3 sticky top-0 z-50">
      <div className="max-w-7xl mx-auto flex items-center justify-between">
        <div className="flex items-center gap-6">
          <div className="flex items-center gap-2">
            <div className="w-8 h-8 bg-gradient-to-br from-blue-600 to-indigo-600 rounded-lg flex items-center justify-center shadow-md shadow-blue-500/25">
              <LayoutDashboard className="w-4 h-4 text-white" />
            </div>
            <span className="font-semibold text-gray-900 text-sm">IBCP-SCADA</span>
          </div>

          <div className="hidden md:flex items-center gap-1">
            {navItems.map((item) => {
              const isActive = pathname === item.href || 
                             (item.href !== '/' && pathname?.startsWith(item.href))
              return (
                <Link
                  key={item.name}
                  href={item.href}
                  className={`flex items-center gap-2 px-3 py-1.5 rounded-lg text-sm font-medium transition-all duration-200
                    ${isActive 
                      ? 'bg-blue-50 text-blue-700 shadow-sm' 
                      : 'text-gray-600 hover:bg-gray-50 hover:text-gray-900'
                    }`}
                >
                  <item.icon className={`w-4 h-4 ${isActive ? 'text-blue-500' : 'text-gray-400'}`} />
                  <span>{item.name}</span>
                </Link>
              )
            })}
          </div>
        </div>

        <div className="flex items-center gap-3">
          <button className="relative p-2 text-gray-400 hover:text-gray-600 rounded-lg hover:bg-gray-50 transition-colors">
            <Bell className="w-5 h-5" />
            <span className="absolute top-1.5 right-1.5 w-2 h-2 bg-red-500 rounded-full"></span>
          </button>
          <button className="flex items-center gap-2 p-2 text-gray-400 hover:text-gray-600 rounded-lg hover:bg-gray-50 transition-colors">
            <User className="w-5 h-5" />
          </button>
        </div>
      </div>

      {/* Mobile Navigation */}
      <div className="md:hidden flex justify-between mt-2 pt-2 border-t border-gray-100">
        {navItems.map((item) => {
          const isActive = pathname === item.href
          return (
            <Link
              key={item.name}
              href={item.href}
              className={`flex flex-col items-center gap-1 px-3 py-1 rounded-lg text-xs font-medium transition-all
                ${isActive ? 'text-blue-600' : 'text-gray-500'}`}
            >
              <item.icon className={`w-5 h-5 ${isActive ? 'text-blue-500' : 'text-gray-400'}`} />
              <span className={isActive ? 'text-blue-600' : ''}>{item.name}</span>
            </Link>
          )
        })}
      </div>
    </nav>
  )
}