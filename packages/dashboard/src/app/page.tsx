// packages/dashboard/src/app/page.tsx
'use client'

import Link from 'next/link'
import { useRouter } from 'next/navigation'
import { 
  LayoutDashboard, 
  Satellite, 
  Droplets, 
  Sprout,
  ArrowRight,
  Activity,
  Map,
  CloudRain,
  ChevronRight,
  Zap,
  Shield,
  Globe,
  Users,
  BarChart3,
  Clock,
  CheckCircle,
  TrendingUp,
  AlertTriangle,
  Menu,
  X,
  Github,
  Twitter,
  Linkedin,
  Mail,
  Phone,
  MapPin
} from 'lucide-react'
import { useState, useEffect } from 'react'

export default function Home() {
  const router = useRouter()
  const [isMenuOpen, setIsMenuOpen] = useState(false)
  const [scrolled, setScrolled] = useState(false)

  useEffect(() => {
    const handleScroll = () => {
      setScrolled(window.scrollY > 50)
    }
    window.addEventListener('scroll', handleScroll)
    return () => window.removeEventListener('scroll', handleScroll)
  }, [])

  const projects = [
    {
      icon: Satellite,
      title: 'GeoVision AI',
      description: 'AI-powered remote sensing for drought & flood prediction with 2-week advance warning.',
      color: 'from-emerald-500 to-green-600',
      bgColor: 'bg-emerald-50',
      textColor: 'text-emerald-600',
      href: '/',
      features: ['148 Tehsils Monitored', '5 Severity Levels', '2-Week Prediction', 'Real-time Alerts'],
      iconBg: 'bg-emerald-100'
    },
    {
      icon: Droplets,
      title: 'Flood SCADA',
      description: 'Automated barrage gate control & flood diversion system with 24/7 monitoring.',
      color: 'from-blue-500 to-cyan-600',
      bgColor: 'bg-blue-50',
      textColor: 'text-blue-600',
      href: '/',
      features: ['24/7 Monitoring', 'Real-time Gates', 'Early Warning', 'Automated Response'],
      iconBg: 'bg-blue-100'
    },
    {
      icon: Sprout,
      title: 'Soil Monitoring',
      description: 'Salinity tracking & land degradation monitoring for sustainable agriculture.',
      color: 'from-amber-500 to-yellow-600',
      bgColor: 'bg-amber-50',
      textColor: 'text-amber-600',
      href: '/',
      features: ['Salinity Maps', 'Pump Control', 'Land Health', 'Degradation Tracking'],
      iconBg: 'bg-amber-100'
    },
  ]

  const stats = [
    { value: '3', label: 'Integrated Projects', icon: LayoutDashboard, color: 'text-blue-500' },
    { value: '148+', label: 'Tehsils Monitored', icon: Map, color: 'text-emerald-500' },
    { value: '5', label: 'Alert Types', icon: CloudRain, color: 'text-yellow-500' },
    { value: '24/7', label: 'Real-time Monitoring', icon: Clock, color: 'text-purple-500' },
  ]

  const benefits = [
    { icon: Shield, title: 'Secure & Reliable', description: 'Enterprise-grade security with JWT authentication and role-based access control.' },
    { icon: Zap, title: 'Real-time Intelligence', description: 'Satellite data processed in real-time with AI-powered predictions and alerts.' },
    { icon: Globe, title: 'Nationwide Coverage', description: 'Monitoring 148+ tehsils across Pakistan\'s Indus Basin irrigation network.' },
    { icon: Users, title: 'Team Collaboration', description: 'Three integrated teams working together on a unified cyber-physical SCADA system.' },
    { icon: BarChart3, title: 'Data-Driven Decisions', description: 'Actionable insights from satellite imagery, ML models, and sensor data.' },
    { icon: TrendingUp, title: 'Sustainable Agriculture', description: 'Optimize water distribution, reduce losses, and improve crop yields.' },
  ]

  const navLinks = [
    { name: 'Features', href: '#features' },
    { name: 'Projects', href: '#projects' },
    { name: 'About', href: '#about' },
    { name: 'Contact', href: '#contact' },
  ]

  return (
    <div className="min-h-screen bg-white">
      {/* ============================================
      NAVIGATION BAR
      ============================================ */}
      <nav className={`fixed top-0 left-0 right-0 z-50 transition-all duration-300 ${
        scrolled ? 'bg-white/95 backdrop-blur-md shadow-lg' : 'bg-transparent'
      }`}>
        <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8">
          <div className="flex items-center justify-between h-16 md:h-20">
            <Link href="/" className="flex items-center gap-2 group">
              <div className="w-10 h-10 bg-gradient-to-br from-blue-600 to-indigo-600 rounded-xl flex items-center justify-center shadow-lg shadow-blue-500/25 group-hover:scale-105 transition-transform duration-300">
                <LayoutDashboard className="w-5 h-5 text-white" />
              </div>
              <div>
                <span className="text-xl font-bold text-gray-900">IBCP-SCADA</span>
                <span className="hidden sm:block text-[10px] text-gray-500 -mt-1">Indus Basin System</span>
              </div>
            </Link>

            <div className="hidden md:flex items-center gap-8">
              {navLinks.map((link) => (
                <Link 
                  key={link.name}
                  href={link.href}
                  className="text-sm text-gray-600 hover:text-blue-600 transition-colors font-medium"
                >
                  {link.name}
                </Link>
              ))}
              <button 
                onClick={() => router.push('/login')}
                className="flex items-center gap-2 px-5 py-2.5 bg-gradient-to-r from-blue-600 to-indigo-600 text-white rounded-xl font-medium shadow-lg shadow-blue-500/25 hover:shadow-xl hover:shadow-blue-500/35 transition-all duration-300 hover:-translate-y-0.5"
              >
                Sign In
                <ChevronRight className="w-4 h-4" />
              </button>
            </div>

            <button 
              onClick={() => setIsMenuOpen(!isMenuOpen)}
              className="md:hidden p-2 rounded-lg hover:bg-gray-100 transition-colors"
            >
              {isMenuOpen ? <X className="w-6 h-6 text-gray-600" /> : <Menu className="w-6 h-6 text-gray-600" />}
            </button>
          </div>
        </div>

        {/* Mobile Menu */}
        <div className={`md:hidden fixed inset-x-0 top-16 bg-white/95 backdrop-blur-md border-b border-gray-100 transition-all duration-300 overflow-hidden ${
          isMenuOpen ? 'max-h-96 opacity-100' : 'max-h-0 opacity-0'
        }`}>
          <div className="px-4 py-4 space-y-3">
            {navLinks.map((link) => (
              <Link 
                key={link.name}
                href={link.href} 
                className="block py-2 text-gray-600 hover:text-blue-600 transition-colors" 
                onClick={() => setIsMenuOpen(false)}
              >
                {link.name}
              </Link>
            ))}
            <button 
              onClick={() => {
                setIsMenuOpen(false)
                router.push('/login')
              }}
              className="w-full flex items-center justify-center gap-2 px-5 py-3 bg-gradient-to-r from-blue-600 to-indigo-600 text-white rounded-xl font-medium"
            >
              Sign In
              <ChevronRight className="w-4 h-4" />
            </button>
          </div>
        </div>
      </nav>

      {/* ============================================
      HERO SECTION
      ============================================ */}
      <section className="relative pt-32 pb-20 md:pt-40 md:pb-32 overflow-hidden">
        <div className="absolute inset-0 bg-gradient-to-br from-blue-50 via-white to-indigo-50">
          <div className="absolute top-20 left-10 w-72 h-72 bg-blue-200/30 rounded-full blur-3xl animate-pulse" />
          <div className="absolute bottom-20 right-10 w-96 h-96 bg-indigo-200/30 rounded-full blur-3xl animate-pulse delay-1000" />
          <div className="absolute top-1/2 left-1/2 -translate-x-1/2 -translate-y-1/2 w-[600px] h-[600px] bg-cyan-200/20 rounded-full blur-3xl animate-pulse delay-500" />
        </div>

        <div className="relative max-w-7xl mx-auto px-4 sm:px-6 lg:px-8">
          <div className="grid lg:grid-cols-2 gap-12 items-center">
            <div>
              <div className="inline-flex items-center gap-2 px-4 py-2 bg-blue-50 border border-blue-200 rounded-full text-sm text-blue-700 font-medium mb-6 animate-fade-in-up">
                <Zap className="w-4 h-4 text-blue-500" />
                Pakistan&apos;s First Cyber-Physical SCADA System
              </div>

              <h1 className="text-4xl md:text-5xl lg:text-6xl font-bold text-gray-900 leading-[1.1] mb-6 animate-fade-in-up animation-delay-200">
                Unified Mega System for
                <span className="block text-transparent bg-clip-text bg-gradient-to-r from-blue-600 via-indigo-600 to-cyan-600">
                  Water & Agricultural Intelligence
                </span>
              </h1>

              <p className="text-lg text-gray-600 mb-8 animate-fade-in-up animation-delay-400 max-w-lg">
                Pakistan&apos;s first integrated cyber-physical SCADA system for flood management,
                water distribution, and agricultural monitoring across the Indus Basin.
              </p>

              <div className="flex flex-col sm:flex-row gap-4 animate-fade-in-up animation-delay-600">
                <button 
                  onClick={() => router.push('/login')}
                  className="flex items-center justify-center gap-2 px-8 py-4 bg-gradient-to-r from-blue-600 to-indigo-600 text-white rounded-2xl font-semibold shadow-xl shadow-blue-500/30 hover:shadow-2xl hover:shadow-blue-500/40 transition-all duration-300 hover:-translate-y-1"
                >
                  Get Started
                  <ArrowRight className="w-5 h-5" />
                </button>
                <Link 
                  href="#projects"
                  className="flex items-center justify-center gap-2 px-8 py-4 bg-white text-gray-700 rounded-2xl font-semibold border border-gray-200 hover:border-blue-300 hover:shadow-lg transition-all duration-300"
                >
                  View Projects
                </Link>
              </div>
            </div>

            <div className="hidden lg:block animate-fade-in-up animation-delay-400">
              <div className="relative">
                <div className="absolute inset-0 bg-gradient-to-r from-blue-400 to-indigo-400 rounded-3xl blur-2xl opacity-20" />
                <div className="relative bg-white/70 backdrop-blur-sm rounded-3xl p-8 border border-gray-200/50 shadow-2xl">
                  <div className="grid grid-cols-2 gap-4">
                    <div className="bg-emerald-50 p-4 rounded-2xl border border-emerald-100">
                      <Satellite className="w-8 h-8 text-emerald-500 mb-2" />
                      <p className="font-semibold text-gray-900 text-sm">GeoVision AI</p>
                      <p className="text-xs text-gray-500">Drought & Flood</p>
                    </div>
                    <div className="bg-blue-50 p-4 rounded-2xl border border-blue-100">
                      <Droplets className="w-8 h-8 text-blue-500 mb-2" />
                      <p className="font-semibold text-gray-900 text-sm">Flood SCADA</p>
                      <p className="text-xs text-gray-500">Gate Control</p>
                    </div>
                    <div className="bg-amber-50 p-4 rounded-2xl border border-amber-100">
                      <Sprout className="w-8 h-8 text-amber-500 mb-2" />
                      <p className="font-semibold text-gray-900 text-sm">Soil Monitoring</p>
                      <p className="text-xs text-gray-500">Salinity & Health</p>
                    </div>
                    <div className="bg-purple-50 p-4 rounded-2xl border border-purple-100">
                      <Activity className="w-8 h-8 text-purple-500 mb-2" />
                      <p className="font-semibold text-gray-900 text-sm">Real-time</p>
                      <p className="text-xs text-gray-500">24/7 Monitoring</p>
                    </div>
                  </div>
                </div>
              </div>
            </div>
          </div>
        </div>
      </section>

      {/* ============================================
      STATS SECTION
      ============================================ */}
      <section className="py-16 bg-white border-y border-gray-100">
        <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8">
          <div className="grid grid-cols-2 md:grid-cols-4 gap-6">
            {stats.map((stat, index) => (
              <div 
                key={index}
                className="text-center p-6 rounded-2xl hover:bg-gray-50 transition-all duration-300 group"
              >
                <stat.icon className={`w-8 h-8 ${stat.color} mx-auto mb-2 group-hover:scale-110 transition-transform duration-300`} />
                <div className="text-3xl md:text-4xl font-bold text-gray-900">{stat.value}</div>
                <div className="text-sm text-gray-500">{stat.label}</div>
              </div>
            ))}
          </div>
        </div>
      </section>

      {/* ============================================
      FEATURES / BENEFITS
      ============================================ */}
      <section id="features" className="py-20 bg-gray-50">
        <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8">
          <div className="text-center mb-16">
            <h2 className="text-3xl md:text-4xl font-bold text-gray-900 mb-4">
              Why <span className="text-transparent bg-clip-text bg-gradient-to-r from-blue-600 to-indigo-600">IBCP-SCADA?</span>
            </h2>
            <p className="text-lg text-gray-600 max-w-2xl mx-auto">
              Built for Pakistan&apos;s agricultural future with cutting-edge technology
            </p>
          </div>

          <div className="grid md:grid-cols-3 gap-8">
            {benefits.map((benefit, index) => (
              <div 
                key={index}
                className="bg-white p-8 rounded-3xl border border-gray-200/50 shadow-sm hover:shadow-xl transition-all duration-300 hover:-translate-y-1 group"
              >
                <div className="w-14 h-14 bg-blue-50 rounded-2xl flex items-center justify-center mb-5 group-hover:scale-110 transition-transform duration-300">
                  <benefit.icon className="w-7 h-7 text-blue-500" />
                </div>
                <h3 className="text-xl font-bold text-gray-900 mb-2">{benefit.title}</h3>
                <p className="text-gray-600 text-sm leading-relaxed">{benefit.description}</p>
              </div>
            ))}
          </div>
        </div>
      </section>

      {/* ============================================
      PROJECTS SECTION
      ============================================ */}
      <section id="projects" className="py-20 bg-white">
        <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8">
          <div className="text-center mb-16">
            <h2 className="text-3xl md:text-4xl font-bold text-gray-900 mb-4">
              Our <span className="text-transparent bg-clip-text bg-gradient-to-r from-blue-600 to-indigo-600">Projects</span>
            </h2>
            <p className="text-lg text-gray-600 max-w-2xl mx-auto">
              Three integrated subsystems working together as one unified ecosystem
            </p>
          </div>

          <div className="grid md:grid-cols-3 gap-8">
            {projects.map((project, index) => (
              <Link
                key={index}
                href={project.href}
                className="group bg-white rounded-3xl border border-gray-200/50 shadow-sm hover:shadow-2xl transition-all duration-500 overflow-hidden hover:-translate-y-2"
              >
                <div className={`h-1.5 bg-gradient-to-r ${project.color}`} />
                <div className="p-8">
                  <div className={`w-14 h-14 ${project.iconBg} rounded-2xl flex items-center justify-center mb-5 group-hover:scale-110 transition-transform duration-300`}>
                    <project.icon className={`w-7 h-7 ${project.textColor}`} />
                  </div>
                  <h3 className="text-xl font-bold text-gray-900 mb-2 group-hover:text-blue-600 transition-colors">
                    {project.title}
                  </h3>
                  <p className="text-gray-600 text-sm leading-relaxed mb-4">
                    {project.description}
                  </p>
                  <ul className="space-y-2 mb-4">
                    {project.features.map((feature, i) => (
                      <li key={i} className="flex items-center gap-2 text-sm text-gray-600">
                        <CheckCircle className="w-4 h-4 text-green-500 flex-shrink-0" />
                        {feature}
                      </li>
                    ))}
                  </ul>
                  <div className={`inline-flex items-center text-sm font-medium ${project.textColor} group-hover:translate-x-1 transition-transform duration-300`}>
                    Learn More
                    <ChevronRight className="w-4 h-4 ml-1" />
                  </div>
                </div>
              </Link>
            ))}
          </div>
        </div>
      </section>

      {/* ============================================
      ABOUT / CONTACT / FOOTER
      ============================================ */}
      <section id="about" className="relative py-16 bg-gray-900 text-white">
        <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8">
          <div className="grid md:grid-cols-4 gap-8">
            <div className="md:col-span-1">
              <div className="flex items-center gap-3 mb-4">
                <div className="w-10 h-10 bg-gradient-to-br from-blue-500 to-indigo-500 rounded-xl flex items-center justify-center">
                  <LayoutDashboard className="w-5 h-5 text-white" />
                </div>
                <span className="text-xl font-bold">IBCP-SCADA</span>
              </div>
              <p className="text-gray-400 text-sm">
                Indus Basin Cyber-Physical SCADA & Early-Warning Infrastructure.
              </p>
            </div>

            <div>
              <h4 className="font-semibold text-white mb-3">Quick Links</h4>
              <ul className="space-y-2 text-sm text-gray-400">
                <li><Link href="/" className="hover:text-white transition-colors">GeoVision AI</Link></li>
                <li><Link href="/" className="hover:text-white transition-colors">Flood SCADA</Link></li>
                <li><Link href="/" className="hover:text-white transition-colors">Soil Monitoring</Link></li>
              </ul>
            </div>

            <div>
              <h4 className="font-semibold text-white mb-3">Contact</h4>
              <ul className="space-y-2 text-sm text-gray-400">
                <li className="flex items-center gap-2"><Mail className="w-4 h-4" /> info@ibcp-scada.edu.pk</li>
                <li className="flex items-center gap-2"><Phone className="w-4 h-4" /> +92 00 000000-00</li>
                <li className="flex items-center gap-2"><MapPin className="w-4 h-4" /> Air University, Islamabad</li>
              </ul>
            </div>

            <div>
              <h4 className="font-semibold text-white mb-3">Follow Us</h4>
              <div className="flex gap-3">
                <a href="#" className="w-10 h-10 bg-gray-800 rounded-lg flex items-center justify-center hover:bg-gray-700 transition-colors">
                  <Github className="w-5 h-5 text-gray-400" />
                </a>
                <a href="#" className="w-10 h-10 bg-gray-800 rounded-lg flex items-center justify-center hover:bg-gray-700 transition-colors">
                  <Twitter className="w-5 h-5 text-gray-400" />
                </a>
                <a href="#" className="w-10 h-10 bg-gray-800 rounded-lg flex items-center justify-center hover:bg-gray-700 transition-colors">
                  <Linkedin className="w-5 h-5 text-gray-400" />
                </a>
              </div>
            </div>
          </div>

          <div className="border-t border-gray-800 mt-12 pt-6 text-center text-sm text-gray-500">
            © 2026 IBCP-SCADA • Air University Islamabad • All rights reserved.
          </div>
        </div>
      </section>

      <style jsx>{`
        @keyframes fadeInUp {
          from { opacity: 0; transform: translateY(30px); }
          to { opacity: 1; transform: translateY(0); }
        }
        .animate-fade-in-up {
          animation: fadeInUp 0.8s ease-out forwards;
          opacity: 0;
        }
        .animation-delay-200 { animation-delay: 200ms; }
        .animation-delay-400 { animation-delay: 400ms; }
        .animation-delay-600 { animation-delay: 600ms; }
      `}</style>
    </div>
  )
}