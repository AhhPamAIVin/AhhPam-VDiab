'use client'

import { useCallback, useEffect, useRef, useState } from 'react'
import { TimePicker } from 'antd'
import dayjs from 'dayjs'
import Link from 'next/link'
import { useRouter } from 'next/navigation'
import { AlertTriangle, Bell, Check, CheckCheck, CheckCircle2, Clock3, Copy, Droplets, ExternalLink, Heart, Loader2, MessageCircleMore, Pencil, Pill, Plus, RotateCcw, Settings2, SkipForward, Trash2, X, FileBarChart } from 'lucide-react'
import { api, type DoctorAlert, type NotificationPreference, type NotificationRecord, type NotificationStatus, type NotificationType, type ZaloLinkCode } from '@/lib/api'
import { useToast } from '@/components/ui/toast'
import { ReminderSuggestionsCard } from '@/components/reminder-suggestions-card'
import { cn, parseUtcTimestamp } from '@/lib/utils'
import { useAuth } from '@/lib/auth-context'

type Filter = 'all' | NotificationStatus

const filters: { id: Filter; label: string }[] = [
  { id: 'all', label: 'Tất cả' },
  { id: 'pending', label: 'Chưa xử lý' },
  { id: 'completed', label: 'Hoàn thành' },
]

// Snoozed reads as "will surface again later, not blocking" — grouped with
// completed. Skipped reads as "still needs a decision" — grouped with
// pending. Collapses the 4 raw statuses into the 2 buckets the filter tabs
// above actually offer.
function statusBucket(status: NotificationStatus): 'pending' | 'completed' {
  return status === 'completed' || status === 'snoozed' ? 'completed' : 'pending'
}

const configs: Record<NotificationType, { icon: typeof Bell; color: string; bg: string; href: string }> = {
  glucose_reminder: { icon: Droplets, color: 'text-[#2D9D97]', bg: 'bg-[#DFF7F3]', href: '/health?record=glucose' },
  blood_pressure_reminder: { icon: Heart, color: 'text-red-500', bg: 'bg-red-50', href: '/health?record=bp' },
  medication_reminder: { icon: Pill, color: 'text-purple-500', bg: 'bg-purple-50', href: '/medication' },
  custom: { icon: Bell, color: 'text-blue-500', bg: 'bg-blue-50', href: '/dashboard' },
  glucose_alert: { icon: AlertTriangle, color: 'text-orange-600', bg: 'bg-orange-100', href: '/health?record=glucose' },
  weekly_report: { icon: FileBarChart, color: 'text-[#2D9D97]', bg: 'bg-[#DFF7F3]', href: '/reports' },
}

export function NotificationsPage() {
  const { user } = useAuth()
  if (!user) return <div className='flex justify-center py-10'><Loader2 className='h-5 w-5 animate-spin text-primary' /></div>
  if (user.role === 'admin') return <AdminNotificationsRedirect />
  return user.role === 'doctor' ? <DoctorNotificationsPage /> : <PatientNotificationsPage />
}

function AdminNotificationsRedirect() {
  const router = useRouter()
  useEffect(() => router.replace('/admin'), [router])
  return <div className='flex justify-center py-10'><Loader2 className='h-5 w-5 animate-spin text-primary' /></div>
}

function PatientNotificationsPage() {
  const { showToast } = useToast()
  const [items, setItems] = useState<NotificationRecord[]>([])
  const [filter, setFilter] = useState<Filter>('all')
  const [loading, setLoading] = useState(true)
  const [updatingId, setUpdatingId] = useState<string | null>(null)
  const [preferences, setPreferences] = useState<NotificationPreference[]>([])
  const [planOpen, setPlanOpen] = useState(false)
  const [preferenceLoading, setPreferenceLoading] = useState(false)
  const [editingPreference, setEditingPreference] = useState<NotificationPreference | null>(null)
  const [preferenceLabel, setPreferenceLabel] = useState('')
  const [preferenceTime, setPreferenceTime] = useState('08:00')
  const [zaloLinked, setZaloLinked] = useState<boolean | null>(null)
  const [zaloModalOpen, setZaloModalOpen] = useState(false)
  const [zaloCode, setZaloCode] = useState<ZaloLinkCode | null>(null)
  const [zaloRequesting, setZaloRequesting] = useState(false)
  const zaloPollRef = useRef<ReturnType<typeof setInterval> | null>(null)

  useEffect(() => {
    api.zaloLinkStatus().then((value) => setZaloLinked(value.linked)).catch(() => setZaloLinked(null))
  }, [])

  const stopZaloPolling = useCallback(() => {
    if (zaloPollRef.current) { clearInterval(zaloPollRef.current); zaloPollRef.current = null }
  }, [])

  useEffect(() => stopZaloPolling, [stopZaloPolling])

  const openZaloModal = async () => {
    setZaloModalOpen(true)
    setZaloRequesting(true)
    setZaloCode(null)
    try {
      const code = await api.createZaloLinkCode()
      setZaloCode(code)
      stopZaloPolling()
      zaloPollRef.current = setInterval(async () => {
        try {
          const value = await api.zaloLinkStatus()
          if (value.linked) { setZaloLinked(true); stopZaloPolling() }
        } catch { /* transient error — keep polling */ }
      }, 3000)
    } catch (error) {
      showToast(error instanceof Error ? error.message : 'Không thể tạo mã kết nối Zalo', 'error')
      setZaloModalOpen(false)
    } finally {
      setZaloRequesting(false)
    }
  }

  const closeZaloModal = () => {
    setZaloModalOpen(false)
    setZaloCode(null)
    stopZaloPolling()
  }

  const load = useCallback(() => {
    setLoading(true)
    api.notifications().then(setItems).catch((error) => {
      showToast(error instanceof Error ? error.message : 'Không thể tải thông báo', 'error')
    }).finally(() => setLoading(false))
  }, [showToast])

  useEffect(() => load(), [load])

  const loadPreferences = useCallback(() => {
    setPreferenceLoading(true)
    api.notificationPreferences().then(setPreferences).catch((error) => {
      showToast(error instanceof Error ? error.message : 'Không thể tải kế hoạch nhắc nhở', 'error')
    }).finally(() => setPreferenceLoading(false))
  }, [showToast])

  useEffect(() => { if (planOpen) loadPreferences() }, [loadPreferences, planOpen])

  const resetPreferenceForm = () => {
    setEditingPreference(null)
    setPreferenceLabel('')
    setPreferenceTime('08:00')
  }

  const savePreference = async () => {
    const label = preferenceLabel.trim()
    if (!label || !/^([01]\d|2[0-3]):[0-5]\d$/.test(preferenceTime)) {
      showToast('Vui lòng nhập tên và thời gian hợp lệ.', 'warning')
      return
    }
    setUpdatingId(editingPreference?.id ?? 'new-preference')
    try {
      const saved = editingPreference
        ? await api.updateNotificationPreference(editingPreference.id, { label, time_of_day: `${preferenceTime}:00` })
        : await api.createNotificationPreference({ label, time_of_day: `${preferenceTime}:00`, notification_type: 'custom' })
      setPreferences((current) => editingPreference ? current.map((item) => item.id === saved.id ? saved : item) : [...current, saved].sort((a, b) => a.time_of_day.localeCompare(b.time_of_day)))
      resetPreferenceForm()
      showToast(editingPreference ? 'Đã cập nhật nhắc nhở.' : 'Đã thêm nhắc nhở tùy chỉnh.', 'success')
    } catch (error) {
      showToast(error instanceof Error ? error.message : 'Không thể lưu nhắc nhở', 'error')
    } finally { setUpdatingId(null) }
  }

  const togglePreference = async (item: NotificationPreference) => {
    setUpdatingId(item.id)
    try {
      const updated = await api.updateNotificationPreference(item.id, { is_enabled: !item.is_enabled })
      setPreferences((current) => current.map((value) => value.id === updated.id ? updated : value))
    } catch (error) { showToast(error instanceof Error ? error.message : 'Không thể cập nhật nhắc nhở', 'error') }
    finally { setUpdatingId(null) }
  }

  const deletePreference = async (item: NotificationPreference) => {
    if (!item.is_custom || !window.confirm(`Xóa nhắc nhở “${item.label}”?`)) return
    setUpdatingId(item.id)
    try {
      await api.deleteNotificationPreference(item.id)
      setPreferences((current) => current.filter((value) => value.id !== item.id))
      if (editingPreference?.id === item.id) resetPreferenceForm()
      showToast('Đã xóa nhắc nhở tùy chỉnh.', 'success')
    } catch (error) { showToast(error instanceof Error ? error.message : 'Không thể xóa nhắc nhở', 'error') }
    finally { setUpdatingId(null) }
  }

  const pendingCount = items.filter((item) => statusBucket(item.status) === 'pending').length
  const filtered = filter === 'all' ? items : items.filter((item) => statusBucket(item.status) === filter)

  const updateStatus = async (item: NotificationRecord, status: NotificationStatus, snoozeMinutes?: number) => {
    if (updatingId) return
    setUpdatingId(item.id)
    try {
      const updated = await api.updateNotification(item.id, status, snoozeMinutes)
      setItems((current) => current.map((value) => value.id === updated.id ? updated : value))
      window.dispatchEvent(new Event('vdiab:notifications-updated'))
      showToast(status === 'completed' ? 'Đã hoàn thành nhắc nhở.' : status === 'snoozed' ? 'Sẽ nhắc lại sau 30 phút.' : 'Đã bỏ qua nhắc nhở.', 'success')
    } catch (error) {
      showToast(error instanceof Error ? error.message : 'Không thể cập nhật thông báo', 'error')
    } finally {
      setUpdatingId(null)
    }
  }

  const completeAll = async () => {
    // Matches pendingCount's bucketing (skipped counts as "chưa xử lý" now)
    // so the "Hoàn thành tất cả" button doesn't stay lit after running it.
    const pending = items.filter((item) => statusBucket(item.status) === 'pending')
    for (const item of pending) {
      try {
        const updated = await api.updateNotification(item.id, 'completed')
        setItems((current) => current.map((value) => value.id === updated.id ? updated : value))
      } catch {
        showToast('Một số thông báo chưa thể cập nhật.', 'error')
        break
      }
    }
    window.dispatchEvent(new Event('vdiab:notifications-updated'))
  }

  return (
    <div className='mx-auto max-w-2xl space-y-4'>
      <ReminderSuggestionsCard onChanged={() => { if (planOpen) loadPreferences() }} />
      <div className='flex items-start justify-between gap-3'>
        <div><h2 className='font-semibold'>Thông báo</h2><p className='mt-0.5 text-xs text-muted-foreground'>{pendingCount ? `${pendingCount} nhắc nhở chưa xử lý` : 'Bạn đã xử lý tất cả nhắc nhở'}</p></div>
        <div className='flex flex-wrap justify-end gap-2'>
          <button type='button' onClick={openZaloModal} className={cn('flex items-center gap-1.5 rounded-xl border px-3 py-2 text-xs font-medium', zaloLinked ? 'border-emerald-200 bg-emerald-50 text-emerald-700' : 'border-border hover:bg-muted')}>
            {zaloLinked ? <CheckCircle2 className='h-4 w-4' /> : <MessageCircleMore className='h-4 w-4' />}
            {zaloLinked ? 'Đã kết nối Zalo' : 'Kết nối Zalo'}
          </button>
          <button type='button' onClick={() => setPlanOpen((value) => !value)} className='flex items-center gap-1.5 rounded-xl border border-border px-3 py-2 text-xs font-medium hover:bg-muted'><Settings2 className='h-4 w-4' /> Kế hoạch nhắc</button>{pendingCount > 0 && <button type='button' onClick={completeAll} className='flex items-center gap-1.5 px-2 py-2 text-xs font-medium text-primary hover:underline'><CheckCheck className='h-4 w-4' /> Hoàn thành tất cả</button>}</div>
      </div>

      {zaloModalOpen && <ZaloConnectModal code={zaloCode} loading={zaloRequesting} linked={zaloLinked === true} onClose={closeZaloModal} onCopied={() => showToast('Đã sao chép mã.', 'success')} onCopyError={() => showToast('Không thể sao chép mã.', 'error')} />}

      {planOpen && <section className='space-y-4 rounded-2xl border border-border bg-card p-5'>
        <div className='flex items-start justify-between gap-3'><div><h3 className='font-semibold'>Kế hoạch nhắc nhở</h3><p className='mt-1 text-xs text-muted-foreground'>Thêm nhắc nhở riêng hoặc điều chỉnh các mốc có sẵn.</p></div><button type='button' onClick={() => { setPlanOpen(false); resetPreferenceForm() }} className='rounded-lg p-1.5 text-muted-foreground hover:bg-muted'><X className='h-4 w-4' /></button></div>
        <div className='grid gap-3 rounded-xl bg-muted/50 p-4 sm:grid-cols-[1fr_120px_auto]'>
          <div><label className='mb-1 block text-xs font-medium text-muted-foreground'>Tên nhắc nhở</label><input value={preferenceLabel} onChange={(event) => setPreferenceLabel(event.target.value)} maxLength={200} placeholder='VD: Kiểm tra bàn chân' className='w-full rounded-xl border border-border bg-background px-3 py-2.5 text-sm outline-none focus:border-primary' /></div>
          <div><label className='mb-1 block text-xs font-medium text-muted-foreground'>Thời gian</label><TimePicker value={preferenceTime && dayjs(`2000-01-01T${preferenceTime}:00`).isValid() ? dayjs(`2000-01-01T${preferenceTime}:00`) : null} onChange={(value) => setPreferenceTime(value?.format('HH:mm') ?? '')} format='HH:mm' allowClear={false} minuteStep={5} size='large' className='w-full' placeholder='Chọn giờ' /></div>
          <div className='flex items-end gap-2'><button type='button' disabled={updatingId === 'new-preference' || updatingId === editingPreference?.id} onClick={savePreference} className='flex h-[42px] items-center gap-1.5 rounded-xl bg-primary px-4 text-sm font-medium text-primary-foreground disabled:opacity-50'>{editingPreference ? <Pencil className='h-4 w-4' /> : <Plus className='h-4 w-4' />}{editingPreference ? 'Lưu' : 'Thêm'}</button>{editingPreference && <button type='button' onClick={resetPreferenceForm} className='h-[42px] rounded-xl border border-border px-3 text-xs'>Hủy</button>}</div>
        </div>
        {preferenceLoading ? <div className='flex justify-center py-6'><Loader2 className='h-5 w-5 animate-spin text-primary' /></div> : <div className='divide-y divide-border overflow-hidden rounded-xl border border-border'>
          {preferences.map((item) => <div key={item.id} className={cn('flex items-center gap-3 p-3', !item.is_enabled && 'opacity-55')}><span className='flex h-9 w-9 shrink-0 items-center justify-center rounded-xl bg-blue-50 text-blue-500'><Bell className='h-4 w-4' /></span><div className='min-w-0 flex-1'><div className='flex items-center gap-2'><p className='truncate text-sm font-medium'>{item.label}</p><span className={cn('rounded-full px-2 py-0.5 text-[10px]', item.is_custom ? 'bg-blue-50 text-blue-600' : 'bg-muted text-muted-foreground')}>{item.is_custom ? 'Tùy chỉnh' : 'Mặc định'}</span></div><p className='mt-0.5 text-xs text-muted-foreground'>{item.time_of_day.slice(0, 5)} · {item.is_enabled ? 'Đang bật' : 'Đã tắt'}</p></div><button type='button' role='switch' aria-checked={item.is_enabled} disabled={updatingId === item.id} onClick={() => togglePreference(item)} className={cn('relative h-6 w-11 rounded-full transition-colors disabled:opacity-50', item.is_enabled ? 'bg-primary' : 'bg-muted')}><span className={cn('absolute top-1 h-4 w-4 rounded-full bg-white shadow transition-transform', item.is_enabled ? 'translate-x-1' : '-translate-x-4')} /></button><button type='button' onClick={() => { setEditingPreference(item); setPreferenceLabel(item.label); setPreferenceTime(item.time_of_day.slice(0, 5)) }} className='rounded-lg p-2 text-muted-foreground hover:bg-muted'><Pencil className='h-4 w-4' /></button>{item.is_custom && <button type='button' disabled={updatingId === item.id} onClick={() => deletePreference(item)} className='rounded-lg p-2 text-red-500 hover:bg-red-50 disabled:opacity-50'><Trash2 className='h-4 w-4' /></button>}</div>)}
        </div>}
        <p className='text-[11px] text-muted-foreground'>Có thể đổi tên, đổi giờ hoặc bật/tắt mọi nhắc nhở. Chỉ nhắc nhở tùy chỉnh mới có thể xóa.</p>
      </section>}

      <div className='flex gap-2 overflow-x-auto pb-1'>
        {filters.map((item) => <button type='button' key={item.id} onClick={() => setFilter(item.id)} className={cn('whitespace-nowrap rounded-xl border px-3 py-1.5 text-xs font-medium', filter === item.id ? 'border-primary bg-primary text-primary-foreground' : 'border-border bg-card text-muted-foreground')}>{item.label}{item.id === 'pending' && pendingCount > 0 && <span className='ml-1.5 rounded-full bg-red-500 px-1.5 py-0.5 text-[10px] text-white'>{pendingCount}</span>}</button>)}
      </div>

      {loading ? <div className='flex justify-center rounded-2xl border border-border bg-card p-10'><Loader2 className='h-5 w-5 animate-spin text-primary' /></div> : filtered.length === 0 ? <div className='flex flex-col items-center gap-3 rounded-2xl border border-border bg-card p-10 text-center'><span className='rounded-2xl bg-muted p-3'><Bell className='h-6 w-6 text-muted-foreground' /></span><p className='font-medium'>Không có thông báo</p><p className='text-sm text-muted-foreground'>Không có nhắc nhở phù hợp với bộ lọc này.</p></div> : <div className='space-y-2'>
        {filtered.map((item) => {
          const config = configs[item.notification_type]
          const Icon = config.icon
          const active = item.status === 'pending'
          const busy = updatingId === item.id
          return <article key={item.id} className={cn('rounded-2xl border bg-card p-4', active ? 'border-primary/30 shadow-sm' : 'border-border opacity-75')}>
            <div className='flex gap-3'><span className={cn('flex h-10 w-10 shrink-0 items-center justify-center rounded-xl', config.bg)}><Icon className={cn('h-4 w-4', config.color)} /></span><div className='min-w-0 flex-1'><div className='flex items-start justify-between gap-2'><h3 className='text-sm font-semibold'>{item.title}</h3>{active && <span className='mt-1 h-2 w-2 shrink-0 rounded-full bg-red-500' />}</div><p className='mt-1 text-xs leading-relaxed text-muted-foreground'>{item.message}</p><p className='mt-2 flex items-center gap-1 text-[11px] text-muted-foreground'><Clock3 className='h-3 w-3' /> {new Date(item.scheduled_for).toLocaleString('vi-VN')}{item.status === 'snoozed' && item.snoozed_until ? ` · Nhắc lại ${parseUtcTimestamp(item.snoozed_until).toLocaleTimeString('vi-VN', { hour: '2-digit', minute: '2-digit' })}` : ''}</p>
              {active && <div className='mt-3 flex flex-wrap gap-2'><button disabled={busy} onClick={() => updateStatus(item, 'completed')} className='flex items-center gap-1.5 rounded-xl bg-primary px-3 py-1.5 text-xs font-medium text-primary-foreground disabled:opacity-50'><Check className='h-3.5 w-3.5' /> Hoàn thành</button><button disabled={busy} onClick={() => updateStatus(item, 'snoozed', 30)} className='flex items-center gap-1.5 rounded-xl border border-border px-3 py-1.5 text-xs font-medium disabled:opacity-50'><RotateCcw className='h-3.5 w-3.5' /> Nhắc sau 30 phút</button><button disabled={busy} onClick={() => updateStatus(item, 'skipped')} className='flex items-center gap-1.5 rounded-xl border border-border px-3 py-1.5 text-xs font-medium text-muted-foreground disabled:opacity-50'><SkipForward className='h-3.5 w-3.5' /> Bỏ qua</button><Link href={config.href} className='rounded-xl px-3 py-1.5 text-xs font-medium text-primary hover:bg-accent'>Thực hiện ngay</Link></div>}
            </div></div>
          </article>
        })}
      </div>}
    </div>
  )
}

function ZaloConnectModal({ code, loading, linked, onClose, onCopied, onCopyError }: {
  code: ZaloLinkCode | null
  loading: boolean
  linked: boolean
  onClose: () => void
  onCopied: () => void
  onCopyError: () => void
}) {
  const copyCode = async () => {
    if (!code) return
    try {
      await navigator.clipboard.writeText(code.code)
      onCopied()
    } catch {
      onCopyError()
    }
  }

  return <div className='fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4' onClick={onClose}>
    <div className='w-full max-w-sm space-y-4 rounded-2xl border border-border bg-card p-5' onClick={(event) => event.stopPropagation()}>
      <div className='flex items-start justify-between gap-3'>
        <h3 className='font-semibold'>Kết nối Zalo</h3>
        <button type='button' onClick={onClose} className='rounded-lg p-1.5 text-muted-foreground hover:bg-muted'><X className='h-4 w-4' /></button>
      </div>

      {linked ? <div className='flex flex-col items-center gap-3 py-4 text-center'>
        <CheckCircle2 className='h-10 w-10 text-emerald-500' />
        <p className='text-sm font-medium'>Đã kết nối thành công!</p>
        <p className='text-xs text-muted-foreground'>Bạn sẽ nhận được nhắc nhở qua Zalo từ giờ.</p>
        <button type='button' onClick={onClose} className='mt-1 rounded-xl bg-primary px-4 py-2 text-xs font-medium text-primary-foreground'>Đóng</button>
      </div> : loading || !code ? <div className='flex justify-center py-8'><Loader2 className='h-5 w-5 animate-spin text-primary' /></div> : <div className='space-y-4'>
        <ol className='list-inside list-decimal space-y-1 text-xs text-muted-foreground'>
          <li>Mở đoạn chat với Bot VDiaB trên Zalo</li>
          <li>Gửi đúng mã bên dưới cho bot</li>
        </ol>

        <div className='flex items-center gap-2 rounded-xl border border-dashed border-primary/40 bg-primary/5 px-4 py-3'>
          <span className='flex-1 text-center font-mono text-lg font-semibold tracking-[0.3em]'>{code.code}</span>
          <button type='button' onClick={copyCode} className='shrink-0 rounded-lg p-2 text-primary hover:bg-primary/10' title='Sao chép mã'><Copy className='h-4 w-4' /></button>
        </div>

        {code.chat_url ? <a href={code.chat_url} target='_blank' rel='noopener noreferrer' className='flex items-center justify-center gap-1.5 rounded-xl bg-primary px-4 py-2.5 text-sm font-medium text-primary-foreground'><ExternalLink className='h-4 w-4' /> Mở Zalo</a> : <p className='rounded-xl bg-muted/60 px-3 py-2 text-xs text-muted-foreground'>Tìm và nhắn tin cho “Bot VDiaB - AI hỗ trợ đái tháo đường” trong ứng dụng Zalo.</p>}

        <div className='flex items-center justify-center gap-1.5 text-[11px] text-muted-foreground'><Loader2 className='h-3 w-3 animate-spin' /> Đang chờ xác nhận từ Zalo…</div>
      </div>}
    </div>
  </div>
}

function DoctorNotificationsPage() {
  const { showToast } = useToast()
  const [alerts, setAlerts] = useState<DoctorAlert[]>([])
  const [loading, setLoading] = useState(true)
  const [filter, setFilter] = useState<'all' | 'critical' | 'warning'>('all')
  useEffect(() => {
    api.doctorAlerts().then(setAlerts).catch((error) => showToast(error instanceof Error ? error.message : 'Không thể tải cảnh báo bác sĩ', 'error')).finally(() => setLoading(false))
  }, [showToast])
  const visible = filter === 'all' ? alerts : alerts.filter((item) => item.severity === filter)
  const criticalCount = alerts.filter((item) => item.severity === 'critical').length
  return <div className='mx-auto max-w-3xl space-y-4'>
    <div><h2 className='font-semibold'>Cảnh báo bệnh nhân</h2><p className='mt-1 text-xs text-muted-foreground'>{criticalCount ? `${criticalCount} cảnh báo cần ưu tiên xử lý` : 'Không có cảnh báo khẩn cấp'}</p></div>
    <div className='flex gap-2'>{([{ id: 'all', label: 'Tất cả' }, { id: 'critical', label: 'Khẩn cấp' }, { id: 'warning', label: 'Cần theo dõi' }] as const).map((item) => <button key={item.id} type='button' onClick={() => setFilter(item.id)} className={cn('rounded-xl border px-3 py-1.5 text-xs font-medium', filter === item.id ? 'border-primary bg-primary text-primary-foreground' : 'border-border bg-card text-muted-foreground')}>{item.label}</button>)}</div>
    {loading ? <div className='flex justify-center rounded-2xl border border-border bg-card p-10'><Loader2 className='h-5 w-5 animate-spin text-primary' /></div> : visible.length === 0 ? <div className='rounded-2xl border border-border bg-card p-10 text-center'><CheckCheck className='mx-auto h-7 w-7 text-primary' /><p className='mt-2 font-medium'>Không có cảnh báo</p><p className='mt-1 text-sm text-muted-foreground'>Các bệnh nhân được phân công hiện không có cảnh báo thuộc nhóm này.</p></div> : <div className='space-y-2'>{visible.map((item) => <article key={item.id} className={cn('rounded-2xl border bg-card p-4', item.severity === 'critical' ? 'border-red-200 bg-red-50/40' : 'border-orange-200 bg-orange-50/30')}><div className='flex gap-3'><span className={cn('flex h-10 w-10 shrink-0 items-center justify-center rounded-xl', item.severity === 'critical' ? 'bg-red-100 text-red-600' : 'bg-orange-100 text-orange-600')}><AlertTriangle className='h-5 w-5' /></span><div className='min-w-0 flex-1'><div className='flex flex-wrap items-start justify-between gap-2'><div><p className='text-xs font-medium text-primary'>{item.patient_name}</p><h3 className='mt-0.5 text-sm font-semibold'>{item.title}</h3></div><span className={cn('rounded-full px-2 py-1 text-[10px] font-medium', item.severity === 'critical' ? 'bg-red-100 text-red-700' : 'bg-orange-100 text-orange-700')}>{item.severity === 'critical' ? 'Khẩn cấp' : 'Cần theo dõi'}</span></div><p className='mt-1 text-xs text-muted-foreground'>{item.message}</p><div className='mt-3 flex items-center justify-between gap-3'><span className='flex items-center gap-1 text-[11px] text-muted-foreground'><Clock3 className='h-3 w-3' />{parseUtcTimestamp(item.occurred_at).toLocaleString('vi-VN')}</span><Link href={item.action_path} className='rounded-xl bg-primary px-3 py-1.5 text-xs font-medium text-primary-foreground'>Xem bệnh nhân</Link></div></div></div></article>)}</div>}
  </div>
}
