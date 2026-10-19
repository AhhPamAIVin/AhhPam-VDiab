'use client'

import { useEffect, useState } from 'react'
import { Sidebar } from '@/components/sidebar'
import { MobileBottomNav } from '@/components/mobile-bottom-nav'
import { AppHeader } from '@/components/app-header'
import { useAuth } from '@/lib/auth-context'
import { applyPatientFontSize, getPatientFontSize } from '@/lib/patient-font-size'

interface AppShellProps {
  children: React.ReactNode
  title?: string
}

export function AppShell({ children, title }: AppShellProps) {
  const [sidebarOpen, setSidebarOpen] = useState(false)
  const { user } = useAuth()

  useEffect(() => {
    if (user?.role === 'patient') applyPatientFontSize(getPatientFontSize())
    else document.documentElement.style.removeProperty('font-size')

    return () => {
      document.documentElement.style.removeProperty('font-size')
    }
  }, [user?.role])

  return (
    <div className="flex min-h-screen bg-background">
      {/* Desktop Sidebar */}
      <div className="hidden lg:sticky lg:top-0 lg:flex lg:h-screen lg:w-64 lg:flex-shrink-0 lg:self-start">
        <Sidebar />
      </div>

      {/* Mobile Sidebar Drawer */}
      {sidebarOpen && (
        <>
          <div
            className="fixed inset-0 z-40 bg-black/30 lg:hidden"
            onClick={() => setSidebarOpen(false)}
          />
          <div className="fixed inset-y-0 left-0 z-50 w-72 lg:hidden">
            <Sidebar onClose={() => setSidebarOpen(false)} />
          </div>
        </>
      )}

      {/* Main */}
      <div className="flex flex-col flex-1 min-w-0">
        <AppHeader
          title={title}
          onMenuClick={() => setSidebarOpen(true)}
        />
        <main className="flex-1 overflow-auto p-4 md:p-6 pb-24 lg:pb-6">
          {children}
        </main>
      </div>

      {/* Mobile Bottom Nav — patient only */}
      {user?.role === 'patient' && <MobileBottomNav />}
    </div>
  )
}
