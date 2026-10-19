'use client'

import Image from 'next/image'
import { useCallback, useEffect, useRef, useState } from 'react'
import { AlertTriangle, Bell, Bot, CheckCheck, Loader2, Menu } from 'lucide-react'
import { useAuth } from '@/lib/auth-context'
import { useRouter } from 'next/navigation'
import { VDiabLogo } from '@/components/vdiab-logo'
import { api, type DoctorAlert } from '@/lib/api'
import { cn, parseUtcTimestamp } from '@/lib/utils'
import { PatientThemeToggle } from '@/components/patient-theme-toggle'

interface AppHeaderProps {
  title?: string
  onMenuClick?: () => void
  onOpenAI?: () => void
}

export function AppHeader({ title, onMenuClick, onOpenAI }: AppHeaderProps) {
  const { user } = useAuth()
  const router = useRouter()
  const accountPath = user?.role === 'doctor' ? '/doctor/account' : user?.role === 'admin' ? '/admin/account' : '/profile'
  const avatarSrc = user?.role === 'doctor' ? '/doctor.png' : user?.role === 'admin' ? '/administrator.png' : '/patient.png'
  const [pendingNotifications, setPendingNotifications] = useState(0)
  const [doctorAlerts, setDoctorAlerts] = useState<DoctorAlert[]>([])
  const [notificationOpen, setNotificationOpen] = useState(false)
  const [notificationLoading, setNotificationLoading] = useState(false)
  const notificationRef = useRef<HTMLDivElement>(null)

  const loadPendingNotifications = useCallback(() => {
    if (user?.role === 'patient') api.notifications('pending').then((items) => setPendingNotifications(items.length)).catch(() => setPendingNotifications(0))
    else if (user?.role === 'doctor') api.doctorAlerts().then((items) => { setDoctorAlerts(items); setPendingNotifications(items.filter((item) => item.severity === 'critical').length) }).catch(() => { setDoctorAlerts([]); setPendingNotifications(0) })
    else setPendingNotifications(0)
  }, [user?.role])

  useEffect(() => {
    loadPendingNotifications()
    window.addEventListener('vdiab:notifications-updated', loadPendingNotifications)
    return () => window.removeEventListener('vdiab:notifications-updated', loadPendingNotifications)
  }, [loadPendingNotifications])
  useEffect(() => {
    if (!notificationOpen) return
    const closeOnOutsideClick = (event: MouseEvent) => {
      if (!notificationRef.current?.contains(event.target as Node)) setNotificationOpen(false)
    }
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key === 'Escape') setNotificationOpen(false)
    }
    document.addEventListener('mousedown', closeOnOutsideClick)
    document.addEventListener('keydown', closeOnEscape)
    return () => {
      document.removeEventListener('mousedown', closeOnOutsideClick)
      document.removeEventListener('keydown', closeOnEscape)
    }
  }, [notificationOpen])

  const toggleNotifications = () => {
    if (user?.role === 'patient') {
      router.push('/notifications')
      return
    }

    const nextOpen = !notificationOpen
    setNotificationOpen(nextOpen)
    if (nextOpen && user?.role === 'doctor') {
      setNotificationLoading(true)
      api.doctorAlerts()
        .then((items) => {
          setDoctorAlerts(items)
          setPendingNotifications(items.filter((item) => item.severity === 'critical').length)
        })
        .catch(() => setDoctorAlerts([]))
        .finally(() => setNotificationLoading(false))
    }
  }

  return (
    <header className="bg-card border-b border-border px-4 md:px-6 h-16 flex items-center justify-between flex-shrink-0 sticky top-0 z-30">
      <div className="flex items-center gap-3">
        <button
          onClick={onMenuClick}
          className="lg:hidden p-2 rounded-xl hover:bg-muted text-muted-foreground"
          aria-label="Mở menu"
        >
          <Menu className="w-5 h-5" />
        </button>
        <div className="lg:hidden">
          <VDiabLogo small />
        </div>
        {title && <h1 className="hidden lg:block text-lg font-semibold text-foreground">{title}</h1>}
      </div>

      <div className="flex items-center gap-2">
        {user?.role === 'patient' && <PatientThemeToggle />}
        {user?.role === 'patient' && (
          <button
            onClick={() => router.push('/ai')}
            className="flex items-center gap-2 px-3 py-2 rounded-xl bg-accent text-accent-foreground text-sm font-medium hover:bg-primary hover:text-primary-foreground transition-colors"
          >
            <Bot className="w-4 h-4" />
            <span className="hidden sm:inline">Trợ lý AI</span>
          </button>
        )}
        <div ref={notificationRef} className="relative">
          <button
            onClick={toggleNotifications}
            className={cn('relative rounded-xl p-2 text-muted-foreground hover:bg-muted', notificationOpen && user?.role !== 'patient' && 'bg-muted text-foreground')}
            aria-label="Thông báo"
            aria-haspopup={user?.role !== 'patient' ? 'dialog' : undefined}
            aria-expanded={user?.role !== 'patient' ? notificationOpen : undefined}
          >
            <Bell className="h-5 w-5" />
            {(user?.role === 'patient' || user?.role === 'doctor') && pendingNotifications > 0 && <span className="absolute -right-1 -top-1 flex h-4 min-w-4 items-center justify-center rounded-full bg-red-500 px-1 text-[10px] font-semibold text-white">{pendingNotifications > 9 ? '9+' : pendingNotifications}</span>}
          </button>

          {notificationOpen && user?.role !== 'patient' && (
            <div role="dialog" aria-label="Thông báo" className="absolute right-0 top-12 z-50 w-[min(22rem,calc(100vw-2rem))] overflow-hidden rounded-2xl border border-border bg-card shadow-2xl">
              <div className="flex items-center justify-between border-b border-border px-4 py-3">
                <div>
                  <p className="text-sm font-semibold">{user?.role === 'doctor' ? 'Cảnh báo bệnh nhân' : 'Thông báo hệ thống'}</p>
                  <p className="mt-0.5 text-[11px] text-muted-foreground">{user?.role === 'doctor' ? `${doctorAlerts.length} cảnh báo gần đây` : 'Dành cho quản trị viên'}</p>
                </div>
                <Bell className="h-4 w-4 text-primary" />
              </div>

              <div className="max-h-80 overflow-y-auto p-2">
                {notificationLoading ? (
                  <div className="flex justify-center py-10"><Loader2 className="h-5 w-5 animate-spin text-primary" /></div>
                ) : user?.role === 'doctor' && doctorAlerts.length > 0 ? (
                  doctorAlerts.slice(0, 5).map((item) => (
                    <button key={item.id} type="button" onClick={() => { setNotificationOpen(false); router.push(item.action_path) }} className="flex w-full gap-3 rounded-xl p-3 text-left hover:bg-muted">
                      <span className={cn('mt-0.5 rounded-lg p-2', item.severity === 'critical' ? 'bg-red-50 text-red-600' : 'bg-orange-50 text-orange-600')}><AlertTriangle className="h-4 w-4" /></span>
                      <span className="min-w-0 flex-1">
                        <span className="block truncate text-xs font-semibold">{item.patient_name} · {item.title}</span>
                        <span className="mt-1 line-clamp-2 block text-[11px] text-muted-foreground">{item.message}</span>
                        <span className="mt-1 block text-[10px] text-muted-foreground">{parseUtcTimestamp(item.occurred_at).toLocaleString('vi-VN')}</span>
                      </span>
                    </button>
                  ))
                ) : (
                  <div className="flex flex-col items-center px-4 py-9 text-center">
                    <span className="rounded-2xl bg-accent p-3"><CheckCheck className="h-5 w-5 text-primary" /></span>
                    <p className="mt-3 text-sm font-medium">Chưa có thông báo mới</p>
                    <p className="mt-1 text-xs text-muted-foreground">{user?.role === 'doctor' ? 'Không có cảnh báo bệnh nhân cần xử lý.' : 'Hệ thống hiện đang hoạt động bình thường.'}</p>
                  </div>
                )}
              </div>

              {user?.role === 'doctor' && (
                <button type="button" onClick={() => { setNotificationOpen(false); router.push('/notifications') }} className="w-full border-t border-border px-4 py-3 text-center text-xs font-semibold text-primary hover:bg-muted">Xem tất cả cảnh báo</button>
              )}
            </div>
          )}
        </div>
        <button
          onClick={() => router.push(accountPath)}
          className="relative w-9 h-9 overflow-hidden rounded-full bg-primary/20"
          aria-label="Hồ sơ"
        >
          <Image src={avatarSrc} alt="Ảnh đại diện" fill sizes="36px" className="object-cover" />
        </button>
      </div>
    </header>
  )
}
