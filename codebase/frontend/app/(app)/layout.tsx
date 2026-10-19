'use client'

import { useEffect } from 'react'
import { useRouter, usePathname } from 'next/navigation'
import { useAuth } from '@/lib/auth-context'
import { AppShell } from '@/components/app-shell'

const PAGE_TITLES: Record<string, string> = {
  '/appointments': 'L\u1ecbch kh\u00e1m',
  '/dashboard':    'Tổng quan',
  '/health':       'Theo dõi sức khỏe',
  '/meals':        'Bữa ăn',
  '/medication':   'Thuốc',
  '/ai':           'Trợ lý AI',
  '/notifications':'Thông báo',
  '/profile':      'Hồ sơ',
  '/doctor':       'Bảng điều khiển bác sĩ',
  '/doctor/account': 'Tài khoản bác sĩ',
  '/admin':        'Quản trị hệ thống',
  '/admin/account': 'Tài khoản quản trị',
}

export default function AppLayout({ children }: { children: React.ReactNode }) {
  const { user, loading } = useAuth()
  const router = useRouter()
  const pathname = usePathname()

  // Doctor/admin cannot access patient pages and vice-versa
  // /notifications is shared: patients see reminders, doctors see assigned-patient alerts.
  const isPatientRoute = ['/dashboard', '/health', '/meals', '/medication', '/appointments', '/ai', '/profile'].some(
    (p) => pathname === p || pathname.startsWith(p + '/'),
  )
  const isDoctorRoute  = pathname === '/doctor' || pathname.startsWith('/doctor/')
  const isAdminRoute   = pathname === '/admin'  || pathname.startsWith('/admin/')
  let redirectTarget: string | null = null

  if (!loading && !user) redirectTarget = '/login'
  else if (user && user.role !== 'patient' && isPatientRoute) redirectTarget = user.role === 'doctor' ? '/doctor' : '/admin'
  else if (user && user.role !== 'doctor' && isDoctorRoute) redirectTarget = user.role === 'patient' ? '/dashboard' : '/admin'
  else if (user && user.role !== 'admin' && isAdminRoute) redirectTarget = user.role === 'patient' ? '/dashboard' : '/doctor'

  const redirectKey = `${pathname}|${redirectTarget ?? ''}`

  useEffect(() => {
    if (redirectTarget) router.replace(redirectTarget)
  }, [user, loading, redirectKey])

  if (loading || !user || redirectTarget) {
    return (
      <div className="min-h-screen flex items-center justify-center bg-background">
        <div className="w-10 h-10 rounded-full border-4 border-primary border-t-transparent animate-spin" />
      </div>
    )
  }

  const title = PAGE_TITLES[pathname] ??
    (pathname.startsWith('/doctor/patients/') ? 'Chi tiết bệnh nhân' : undefined)

  return <AppShell title={title}>{children}</AppShell>
}
