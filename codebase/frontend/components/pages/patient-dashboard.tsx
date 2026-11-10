'use client'

import { useCallback, useEffect, useRef, useState } from 'react'
import Link from 'next/link'
import {
  Droplets, Heart, Activity, Scale, Footprints,
  Plus, CheckCircle2, Circle, Bot, ChevronRight, TrendingUp,
  Syringe, Pill, UtensilsCrossed, Dumbbell, Loader2, AlertTriangle, X, Bell, SkipForward, CalendarDays,
  FileBarChart,
} from 'lucide-react'
import { HealthMetricCard } from '@/components/health-metric-card'
import { AgentSummaryCard } from '@/components/agent-summary-card'
import { WeeklyReportBadge } from '@/components/weekly-report-badge'
import { mockPatient } from '@/lib/mock-data'
import { useAuth } from '@/lib/auth-context'
import { useToast } from '@/components/ui/toast'
import { cn } from '@/lib/utils'
import { api, type Appointment, type BloodPressureRecord, type GlucoseRecord, type MedicationRecord, type NotificationRecord, type NotificationType } from '@/lib/api'
import { GlucoseTrendChart, glucoseRecordsInRange, RANGE_DAYS, RANGE_LABELS, type GlucoseRange } from '@/components/glucose-trend-chart'

const notificationIcons: Record<NotificationType, React.ReactNode> = {
  glucose_reminder:         <Droplets className="w-4 h-4 text-[#43B5AE]" />,
  blood_pressure_reminder:  <Heart className="w-4 h-4 text-red-500" />,
  medication_reminder:      <Pill className="w-4 h-4 text-purple-500" />,
  custom:                   <Bell className="w-4 h-4 text-blue-500" />,
  glucose_alert:            <AlertTriangle className="w-4 h-4 text-orange-600" />,
  weekly_report:            <FileBarChart className="w-4 h-4 text-[#2D9D97]" />,
}

// Local (browser) yyyy-mm-dd — matches what the backend's `on_date` filter and
// each Notification's `slot_date` mean in practice, since reminders are
// generated in the patient-facing reminder_timezone (see
// notifications/scheduler.py) and patients using this app are expected to be
// in that same timezone.
function todayDateString(): string {
  const now = new Date()
  return `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, '0')}-${String(now.getDate()).padStart(2, '0')}`
}

// Visualizes the AI-predicted personalized band (predicted_min_mg_dl–
// predicted_max_mg_dl) for the patient's latest glucose reading, with a
// marker showing where that reading landed. The band is specific to that
// one reading's context (meal timing, recent history — see
// health/threshold_features.py), not a fixed target, hence "gần nhất"
// (most recent) rather than implying it's a static goal.
function PersonalizedGlucoseBand({
  value, min, max, status,
}: {
  value: number
  min: number
  max: number
  status: GlucoseRecord['threshold_status']
}) {
  const padding = Math.max((max - min) * 0.4, 10)
  const domainMin = Math.min(min - padding, value)
  const domainMax = Math.max(max + padding, value)
  const toPercent = (n: number) => ((n - domainMin) / (domainMax - domainMin)) * 100
  const bandLeft = toPercent(min)
  const bandWidth = toPercent(max) - bandLeft
  const markerLeft = toPercent(value)
  const markerColor = status === 'DANGER' ? 'bg-red-500' : status === 'WATCH' ? 'bg-amber-500' : 'bg-[#43B5AE]'
  const statusLabel = status === 'DANGER' ? 'Nguy hiểm' : status === 'WATCH' ? 'Ngoài dải' : 'Trong dải'
  const statusColor = status === 'DANGER' ? 'text-red-700' : status === 'WATCH' ? 'text-amber-700' : 'text-[#238D87]'

  return (
    <div className="mt-4 border-t border-current/10 pt-4">
      <div className="flex items-center justify-between gap-2 mb-2">
        <span className="text-[11px] font-semibold uppercase tracking-wide opacity-75 whitespace-nowrap">Dải cá nhân (AI dự đoán)</span>
        <span className={cn('text-[11px] font-semibold whitespace-nowrap', statusColor)}>{statusLabel}</span>
      </div>
      <div className="relative h-2 rounded-full bg-muted">
        <div className="absolute inset-y-0 rounded-full bg-[#43B5AE]/30" style={{ left: `${bandLeft}%`, width: `${bandWidth}%` }} />
        <div
          className={cn('absolute top-1/2 h-3.5 w-3.5 -translate-y-1/2 -translate-x-1/2 rounded-full border-2 border-white shadow', markerColor)}
          style={{ left: `${markerLeft}%` }}
        />
      </div>
      <div className="mt-1.5 flex justify-between text-[11px] text-muted-foreground">
        <span>{Math.round(min)} mg/dL</span>
        <span>{Math.round(max)} mg/dL</span>
      </div>
    </div>
  )
}

interface PatientDashboardProps {
  onNavigate?: (page: string) => void
  onOpenAI?: () => void
}

export function PatientDashboard({ onNavigate, onOpenAI }: PatientDashboardProps) {
  const { user } = useAuth()
  const { showToast } = useToast()
  const [range, setRange] = useState<GlucoseRange>('7d')
  const [glucoseRecords, setGlucoseRecords] = useState<GlucoseRecord[]>([])
  const [bloodPressureRecords, setBloodPressureRecords] = useState<BloodPressureRecord[]>([])
  const [todayNotifications, setTodayNotifications] = useState<NotificationRecord[]>([])
  const [notificationsLoading, setNotificationsLoading] = useState(true)
  const [activeMedications, setActiveMedications] = useState<MedicationRecord[]>([])
  const [medicationsLoading, setMedicationsLoading] = useState(true)
  const [appointments, setAppointments] = useState<Appointment[]>([])
  const [appointmentsLoading, setAppointmentsLoading] = useState(true)
  const [updatingNotificationId, setUpdatingNotificationId] = useState<string | null>(null)
  const [medicationsOpen, setMedicationsOpen] = useState(false)
  const [appointmentsOpen, setAppointmentsOpen] = useState(false)
  const [quickActionsOpen, setQuickActionsOpen] = useState(false)
  const quickActionsRef = useRef<HTMLDivElement>(null)
  const medicationsRef = useRef<HTMLElement>(null)
  const appointmentsRef = useRef<HTMLElement>(null)

  const openMobileDropdown = (
    isOpen: boolean,
    setOpen: React.Dispatch<React.SetStateAction<boolean>>,
    sectionRef: React.RefObject<HTMLElement | null>,
  ) => {
    const nextOpen = !isOpen
    setOpen(nextOpen)
    if (nextOpen) {
      window.setTimeout(() => {
        sectionRef.current?.scrollIntoView({ behavior: 'smooth', block: 'center' })
      }, 300)
    }
  }

  useEffect(() => {
    let active = true
    Promise.allSettled([api.glucose(), api.bloodPressure()]).then(([glucoseResult, bloodPressureResult]) => {
      if (!active) return
      setGlucoseRecords(glucoseResult.status === 'fulfilled' ? glucoseResult.value : [])
      setBloodPressureRecords(bloodPressureResult.status === 'fulfilled' ? bloodPressureResult.value : [])
    })
    return () => { active = false }
  }, [])

  useEffect(() => {
    let active = true
    api.activeMedications()
      .then((medications) => { if (active) setActiveMedications(medications) })
      .catch(() => { if (active) setActiveMedications([]) })
      .finally(() => { if (active) setMedicationsLoading(false) })
    return () => { active = false }
  }, [])

  useEffect(() => {
    let active = true
    api.appointments()
      .then((items) => { if (active) setAppointments(items) })
      .catch(() => { if (active) setAppointments([]) })
      .finally(() => { if (active) setAppointmentsLoading(false) })
    return () => { active = false }
  }, [])

  // "Lịch hôm nay" is the real reminder plan (see notifications/service.py),
  // not a static mock — logging the underlying record (glucose, blood
  // pressure, medication taken) auto-completes the matching reminder
  // server-side, so this list needs to reflect that live instead of a local
  // toggle. Reloads on 'vdiab:notifications-updated' (same event app-header.tsx's
  // bell badge and the full /notifications page already dispatch/listen for)
  // so a reminder resolved from another tab/page/action shows up here too.
  const loadTodayNotifications = useCallback(() => {
    setNotificationsLoading(true)
    api.notifications(undefined, todayDateString())
      .then(setTodayNotifications)
      .catch(() => setTodayNotifications([]))
      .finally(() => setNotificationsLoading(false))
  }, [])

  useEffect(() => {
    loadTodayNotifications()
    window.addEventListener('vdiab:notifications-updated', loadTodayNotifications)
    return () => window.removeEventListener('vdiab:notifications-updated', loadTodayNotifications)
  }, [loadTodayNotifications])

  useEffect(() => {
    if (!quickActionsOpen) return
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key === 'Escape') setQuickActionsOpen(false)
    }
    const closeOnOutsideClick = (event: MouseEvent) => {
      if (!quickActionsRef.current?.contains(event.target as Node)) setQuickActionsOpen(false)
    }
    window.addEventListener('keydown', closeOnEscape)
    document.addEventListener('mousedown', closeOnOutsideClick)
    return () => {
      window.removeEventListener('keydown', closeOnEscape)
      document.removeEventListener('mousedown', closeOnOutsideClick)
    }
  }, [quickActionsOpen])

  const completeNotification = async (item: NotificationRecord) => {
    if (item.status !== 'pending' && item.status !== 'snoozed') return
    if (updatingNotificationId) return
    setUpdatingNotificationId(item.id)
    try {
      const updated = await api.updateNotification(item.id, 'completed')
      setTodayNotifications((current) => current.map((n) => (n.id === updated.id ? updated : n)))
      window.dispatchEvent(new Event('vdiab:notifications-updated'))
    } catch (error) {
      showToast(error instanceof Error ? error.message : 'Không thể cập nhật nhắc nhở.', 'error')
    } finally {
      setUpdatingNotificationId(null)
    }
  }

  // Unresolved (pending/snoozed) reminders surface first so the list reads as
  // a to-do list, not a chronological log; completed/skipped ones sink to the
  // bottom instead of disappearing, so a patient can see what they already
  // handled today.
  const sortedNotifications = [...todayNotifications].sort((a, b) => {
    const aOpen = a.status === 'pending' || a.status === 'snoozed'
    const bOpen = b.status === 'pending' || b.status === 'snoozed'
    if (aOpen !== bOpen) return aOpen ? -1 : 1
    return new Date(a.scheduled_for).getTime() - new Date(b.scheduled_for).getTime()
  })
  const displayedTasks = sortedNotifications
  const completedCount = todayNotifications.filter((item) => item.status === 'completed').length
  const completionPercent = todayNotifications.length
    ? Math.round((completedCount / todayNotifications.length) * 100)
    : 0
  const validGlucoseRecords = glucoseRecords
    .filter((record) => Number.isFinite(record.value) && Number.isFinite(new Date(record.measured_at).getTime()))
    .sort((a, b) => new Date(b.measured_at).getTime() - new Date(a.measured_at).getTime())
  const latestGlucose = validGlucoseRecords[0] ?? null
  const previousGlucose = validGlucoseRecords[1] ?? null
  const glucosePeriodRecords = glucoseRecordsInRange(glucoseRecords, range)
  const glucoseHighest = glucosePeriodRecords.length ? Math.max(...glucosePeriodRecords.map((record) => record.value)) : null
  const glucoseLowest = glucosePeriodRecords.length ? Math.min(...glucosePeriodRecords.map((record) => record.value)) : null
  const glucoseValue = latestGlucose?.value ?? 126
  const glucoseDifference = latestGlucose && previousGlucose ? Math.round((latestGlucose.value - previousGlucose.value) * 10) / 10 : null
  const glucoseTrend = glucoseDifference === null || glucoseDifference === 0 ? 'stable' : glucoseDifference > 0 ? 'up' : 'down'
  const glucoseTrendValue = glucoseDifference === null
    ? 'Chưa có lần đo trước'
    : glucoseDifference === 0
      ? 'Không đổi'
      : `${glucoseDifference > 0 ? '+' : ''}${glucoseDifference} so với lần trước`
  const glucoseUpdatedAt = latestGlucose
    ? `Cập nhật ${new Intl.DateTimeFormat('vi-VN', { hour: '2-digit', minute: '2-digit', day: '2-digit', month: '2-digit' }).format(new Date(latestGlucose.measured_at))}`
    : 'Chưa có dữ liệu mới'
  // Prefer the backend's personalized threshold_status (doctor-approved DANGER
  // gate + AI-predicted WATCH band, see health/threshold_service.py) when the
  // latest reading has one. Falls back to the old fixed mock thresholds only
  // for records predating this feature or patients with no CarePlanTarget yet
  // (threshold_status null/UNAVAILABLE) so nothing regresses to a blank state.
  const glucoseStatus: 'normal' | 'high' | 'critical' =
    latestGlucose?.threshold_status === 'DANGER'
      ? 'critical'
      : latestGlucose?.threshold_status === 'WATCH'
        ? 'high'
        : latestGlucose?.threshold_status === 'NORMAL'
          ? 'normal'
          : glucoseValue <= mockPatient.alertGlucoseLow || glucoseValue >= mockPatient.alertGlucoseHigh
            ? 'critical'
            : glucoseValue < mockPatient.targetGlucoseMin || glucoseValue > mockPatient.targetGlucoseMax
              ? 'high'
              : 'normal'
  const glucoseStatusLabel = glucoseStatus === 'critical'
    ? 'Nguy hiểm'
    : glucoseStatus === 'high'
      ? 'Cần chú ý'
      : 'Bình thường'
  const hasPersonalizedBand =
    latestGlucose?.predicted_min_mg_dl != null && latestGlucose?.predicted_max_mg_dl != null
  const glucoseColor = glucoseStatus === 'normal'
    ? 'text-[#238D87] dark:text-teal-300'
    : glucoseStatus === 'high'
      ? 'text-amber-700 dark:text-amber-300'
      : 'text-red-700 dark:text-red-300'
  const glucoseIconBg = glucoseStatus === 'normal'
    ? 'bg-[#D7F2EE] dark:bg-teal-900/70'
    : glucoseStatus === 'high'
      ? 'bg-amber-100 dark:bg-amber-900/70'
      : 'bg-red-100 dark:bg-red-900/70'
  const validBloodPressureRecords = [...bloodPressureRecords]
    .filter((record) => Number.isFinite(record.systolic) && Number.isFinite(record.diastolic) && Number.isFinite(new Date(record.measured_at).getTime()))
    .sort((a, b) => new Date(b.measured_at).getTime() - new Date(a.measured_at).getTime())
  const latestBloodPressure = validBloodPressureRecords[0] ?? null
  const previousBloodPressure = validBloodPressureRecords[1] ?? null
  const bloodPressureValue = latestBloodPressure ? `${latestBloodPressure.systolic}/${latestBloodPressure.diastolic}` : '—'
  const bloodPressureStatus: 'normal' | 'high' | 'critical' = !latestBloodPressure
    ? 'normal'
    : latestBloodPressure.systolic >= 180 || latestBloodPressure.diastolic >= 120
      ? 'critical'
      : latestBloodPressure.systolic >= 140 || latestBloodPressure.diastolic >= 90
        ? 'high'
        : 'normal'
  const systolicDifference = latestBloodPressure && previousBloodPressure
    ? Math.round((latestBloodPressure.systolic - previousBloodPressure.systolic) * 10) / 10
    : null
  const bloodPressureTrend = systolicDifference === null || systolicDifference === 0 ? 'stable' : systolicDifference > 0 ? 'up' : 'down'
  const bloodPressureTrendValue = systolicDifference === null
    ? 'Chưa có lần đo trước'
    : systolicDifference === 0
      ? 'Không đổi'
      : `${systolicDifference > 0 ? '+' : ''}${systolicDifference} tâm thu`
  const bloodPressureUpdatedAt = latestBloodPressure
    ? `Cập nhật ${new Intl.DateTimeFormat('vi-VN', { hour: '2-digit', minute: '2-digit', day: '2-digit', month: '2-digit' }).format(new Date(latestBloodPressure.measured_at))}`
    : 'Chưa có dữ liệu huyết áp'
  const quickActions = [
    { icon: <Droplets className="h-5 w-5" />, label: 'Ghi đường huyết', description: 'Nhập kết quả mg/dL', iconColor: 'bg-[#DFF7F3] text-[#238D87]', href: '/health?record=glucose' },
    { icon: <Heart className="h-5 w-5" />, label: 'Ghi huyết áp', description: 'Nhập tâm thu / tâm trương', iconColor: 'bg-red-50 text-red-500 dark:bg-red-950/40', href: '/health?record=bp' },
    { icon: <Scale className="h-5 w-5" />, label: 'Ghi cân nặng', description: 'Cập nhật số kg hiện tại', iconColor: 'bg-blue-50 text-blue-500 dark:bg-blue-950/40', href: '/health?record=weight' },
    { icon: <Pill className="h-5 w-5" />, label: 'Ghi uống thuốc', description: 'Đánh dấu liều đã dùng', iconColor: 'bg-purple-50 text-purple-500 dark:bg-[#272131] dark:text-[#B6A3CA]', href: '/medication' },
    { icon: <UtensilsCrossed className="h-5 w-5" />, label: 'Ghi bữa ăn', description: 'Lưu món ăn và khẩu phần', iconColor: 'bg-orange-50 text-orange-500 dark:bg-orange-950/40', href: '/meals' },
    { icon: <Dumbbell className="h-5 w-5" />, label: 'Ghi hoạt động', description: 'Lưu loại và thời gian tập', iconColor: 'bg-yellow-50 text-yellow-600 dark:bg-yellow-950/40', href: '/health?record=activity' },
  ]
  const upcomingAppointments = appointments
    .filter((appointment) => appointment.status === 'scheduled')
    .sort((a, b) => new Date(a.scheduled_at).getTime() - new Date(b.scheduled_at).getTime())

  return (
    <div className="mx-auto max-w-5xl space-y-3 lg:space-y-6">
      {/* Mobile greeting */}
      <div className="relative overflow-hidden rounded-2xl bg-primary p-4 text-primary-foreground shadow-lg shadow-primary/15 sm:hidden">
        <span className="pointer-events-none absolute -right-7 -top-9 h-24 w-24 rounded-full bg-white/10" />
        <span className="pointer-events-none absolute -bottom-8 right-16 h-16 w-16 rounded-full bg-white/10" />
        <div className="relative">
          <p className="text-sm text-white/80">Xin chào,</p>
          <h2 className="mt-1 text-xl font-bold">{user?.name ?? mockPatient.name}</h2>
          <p className="mt-1 text-xs text-white/85">Hôm nay bạn thế nào?</p>
        </div>
      </div>

      <WeeklyReportBadge />

      {/* Quick actions */}
      <div ref={quickActionsRef} className="relative z-20 hidden sm:block">
        <button
        type="button"
        aria-label="Mở ghi nhanh"
        aria-haspopup="menu"
        aria-expanded={quickActionsOpen}
        onClick={() => setQuickActionsOpen((open) => !open)}
        className="group relative flex w-full items-center gap-4 overflow-hidden rounded-2xl border border-primary/20 bg-primary p-5 text-left text-primary-foreground shadow-lg shadow-primary/15 transition-all duration-300 hover:-translate-y-0.5 hover:shadow-xl hover:shadow-primary/25 focus-visible:outline-none focus-visible:ring-4 focus-visible:ring-primary/25"
      >
        <span className="pointer-events-none absolute -right-8 -top-10 h-28 w-28 rounded-full bg-white/10" />
        <span className="pointer-events-none absolute bottom-[-2.5rem] right-16 h-20 w-20 rounded-full bg-white/10" />
        <span className="relative flex h-12 w-12 shrink-0 items-center justify-center rounded-full bg-white/20 text-white ring-1 ring-white/25 transition-transform duration-300 group-hover:scale-110">
          <Plus className="h-6 w-6 transition-transform duration-300 group-hover:rotate-90" />
        </span>
        <span className="relative min-w-0 flex-1">
          <span className="block text-base font-bold">Ghi nhanh</span>
          <span className="mt-1 block text-sm text-white/80">
            Thêm đường huyết, huyết áp hoặc dữ liệu sức khỏe mới.
          </span>
        </span>
        <ChevronRight className={cn('relative h-5 w-5 shrink-0 text-white/75 transition-transform duration-300 group-hover:text-white', quickActionsOpen ? 'rotate-90' : 'group-hover:translate-x-1')} />
        </button>

        {quickActionsOpen && (
          <section
            role="menu"
            aria-labelledby="quick-actions-title"
            className="absolute left-0 right-0 top-full mt-3 max-h-[min(32rem,70vh)] origin-top overflow-y-auto rounded-2xl border border-border bg-card p-4 shadow-2xl animate-in fade-in slide-in-from-top-2 zoom-in-95 duration-200 sm:p-5"
          >
            <div className="mb-4 flex items-start justify-between gap-4">
              <div className="flex items-start gap-3">
                <span className="flex h-11 w-11 shrink-0 items-center justify-center rounded-2xl bg-primary/10 text-primary">
                  <Plus className="h-5 w-5" />
                </span>
                <div>
                  <h3 id="quick-actions-title" className="font-semibold text-foreground">Bạn muốn ghi gì?</h3>
                  <p className="mt-0.5 text-xs leading-relaxed text-muted-foreground">
                    Chọn một mục để thêm dữ liệu sức khỏe mới.
                  </p>
                </div>
              </div>
              <button
                type="button"
                aria-label="Đóng ghi nhanh"
                onClick={() => setQuickActionsOpen(false)}
                className="flex h-9 w-9 shrink-0 items-center justify-center rounded-full text-muted-foreground transition-colors hover:bg-muted hover:text-foreground"
              >
                <X className="h-5 w-5" />
              </button>
            </div>
            <div className="grid grid-cols-1 gap-2 min-[420px]:grid-cols-2 lg:grid-cols-3">
              {quickActions.map((action) => (
                <Link
                  role="menuitem"
                  key={action.label}
                  href={action.href}
                  onClick={() => setQuickActionsOpen(false)}
                  className="group flex min-w-0 items-center gap-3 rounded-xl border border-border bg-background p-3 text-left transition-all hover:-translate-y-0.5 hover:border-primary/40 hover:bg-accent hover:shadow-sm active:translate-y-0 active:bg-muted"
                >
                  <span className={cn('flex h-10 w-10 shrink-0 items-center justify-center rounded-xl', action.iconColor)}>
                    {action.icon}
                  </span>
                  <span className="min-w-0 flex-1">
                    <span className="block text-sm font-semibold text-foreground">{action.label}</span>
                    <span className="mt-0.5 block truncate text-[11px] text-muted-foreground">{action.description}</span>
                  </span>
                  <ChevronRight className="h-4 w-4 shrink-0 text-muted-foreground transition-transform group-hover:translate-x-0.5 group-hover:text-primary" />
                </Link>
              ))}
            </div>
          </section>
        )}
      </div>

      {/* Metrics grid */}
      <section>
        <h3 className="mb-2 text-xs font-semibold uppercase tracking-wider text-muted-foreground lg:mb-3 lg:text-sm">Chỉ số hôm nay</h3>
        <div className="grid grid-cols-2 gap-2 lg:grid-cols-3 lg:gap-3">
          <div className="col-span-2 lg:col-span-1 lg:row-span-2 [&>button]:h-full">
            <HealthMetricCard
              title="Đường huyết"
              value={glucoseValue}
              unit="mg/dL"
              updatedAt={glucoseUpdatedAt}
              status={glucoseStatus}
              statusLabel={glucoseStatusLabel}
              trend={glucoseTrend}
              trendValue={glucoseTrendValue}
              icon={<Droplets className={cn('w-6 h-6', glucoseColor)} />}
              iconBg={glucoseIconBg}
              featured
              compactMobile
              featuredStats={[
                { label: 'Cao nhất', value: glucoseHighest ?? '—', tone: 'high' },
                { label: 'Thấp nhất', value: glucoseLowest ?? '—', tone: 'low' },
              ]}
              extra={hasPersonalizedBand && latestGlucose ? (
                <PersonalizedGlucoseBand
                  value={latestGlucose.value}
                  min={latestGlucose.predicted_min_mg_dl as number}
                  max={latestGlucose.predicted_max_mg_dl as number}
                  status={latestGlucose.threshold_status}
                />
              ) : undefined}
            />
          </div>
          <HealthMetricCard
            title="Huyết áp"
            value={bloodPressureValue}
            unit="mmHg"
            updatedAt={bloodPressureUpdatedAt}
            status={bloodPressureStatus}
            trend={bloodPressureTrend}
            trendValue={bloodPressureTrendValue}
            icon={<Heart className="w-5 h-5 text-red-500" />}
            iconBg="bg-red-50"
            compactMobile
          />
          <div className="hidden lg:block">
            <HealthMetricCard
              title="Nhịp tim"
              value="76"
              unit="bpm"
              updatedAt="07:30 sáng"
              status="normal"
              trend="stable"
              trendValue="Ổn định"
              icon={<Activity className="w-5 h-5 text-blue-500" />}
              iconBg="bg-blue-50"
            />
          </div>
          <div className="hidden lg:block">
            <HealthMetricCard
              title="Công việc"
              value={`${completedCount}/${todayNotifications.length}`}
              unit="việc"
              updatedAt="Hôm nay"
              status="normal"
              trend="stable"
              trendValue={`${completionPercent}%`}
              icon={<CheckCircle2 className="w-5 h-5 text-green-500" />}
              iconBg="bg-green-50"
            />
          </div>
          <HealthMetricCard
            title="BMI"
            value={mockPatient.bmi}
            unit="kg/m²"
            updatedAt="Theo hồ sơ hiện tại"
            status="normal"
            trend="stable"
            trendValue="Bình thường"
            icon={<Scale className="w-5 h-5 text-purple-500" />}
            iconBg="bg-purple-50"
            compactMobile
          />
        </div>
      </section>

      {/* Chart */}
      <section className="hidden rounded-2xl border border-border bg-card p-5 lg:block">
        <GlucoseTrendChart
          records={glucoseRecords}
          range={range}
          onRangeChange={setRange}
          availableRanges={['24h', '7d', '14d', '1m']}
        />
      </section>

      <h3 className="text-xs font-semibold uppercase tracking-wider text-muted-foreground lg:hidden">Thông tin hỗ trợ</h3>

      <div className="grid gap-4 lg:grid-cols-2">
        {/* Today tasks */}
        <section className="hidden bg-card rounded-2xl p-5 border border-border lg:block">
          <div className="flex items-center justify-between mb-4">
            <div>
              <h3 className="font-semibold text-foreground">Lịch hôm nay</h3>
              <p className="text-xs text-muted-foreground mt-0.5">{completedCount}/{todayNotifications.length} đã hoàn thành</p>
            </div>
            <div className="text-xs font-medium text-primary">{completionPercent}%</div>
          </div>
          {/* Progress bar */}
          <div className="h-1.5 bg-muted rounded-full mb-4">
            <div
              className="h-full bg-primary rounded-full transition-all"
              style={{ width: `${completionPercent}%` }}
            />
          </div>
          {notificationsLoading && (
            <div className="flex items-center justify-center gap-2 py-6 text-sm text-muted-foreground">
              <Loader2 className="w-4 h-4 animate-spin" /> Đang tải lịch hôm nay...
            </div>
          )}
          {!notificationsLoading && todayNotifications.length === 0 && (
            <p className="py-6 text-center text-sm text-muted-foreground">Chưa có nhắc nhở nào cho hôm nay.</p>
          )}
          {!notificationsLoading && todayNotifications.length > 0 && (
            <ul className="space-y-2 max-h-72 overflow-y-auto pr-1">
              {displayedTasks.map((item) => {
                const done = item.status === 'completed'
                const skipped = item.status === 'skipped'
                const busy = updatingNotificationId === item.id
                return (
                  <li key={item.id}>
                    <button
                      onClick={() => completeNotification(item)}
                      disabled={busy || done || skipped}
                      className={cn(
                        'w-full flex items-center gap-3 p-2.5 rounded-xl text-left transition-colors hover:enabled:bg-muted disabled:cursor-default',
                        (done || skipped) && 'opacity-60',
                      )}
                    >
                      {busy
                        ? <Loader2 className="w-5 h-5 text-muted-foreground flex-shrink-0 animate-spin" />
                        : done
                          ? <CheckCircle2 className="w-5 h-5 text-primary flex-shrink-0" />
                          : skipped
                            ? <SkipForward className="w-5 h-5 text-muted-foreground flex-shrink-0" />
                            : <Circle className="w-5 h-5 text-muted-foreground flex-shrink-0" />
                      }
                      <div className="flex-1 min-w-0">
                        <p className={cn('text-sm font-medium', (done || skipped) && 'line-through text-muted-foreground')}>
                          {item.title}
                        </p>
                        <p className="text-xs text-muted-foreground">
                          {new Intl.DateTimeFormat('vi-VN', { hour: '2-digit', minute: '2-digit' }).format(new Date(item.scheduled_for))}
                          {item.status === 'snoozed' && ' · Đã hoãn'}
                        </p>
                      </div>
                      {notificationIcons[item.notification_type]}
                    </button>
                  </li>
                )
              })}
            </ul>
          )}
          {!notificationsLoading && todayNotifications.length > 0 && (
            <Link href="/notifications" className="mt-3 flex items-center justify-center gap-1 text-xs font-medium text-primary hover:underline">
              Xem trang thông báo <ChevronRight className="w-3.5 h-3.5" />
            </Link>
          )}
        </section>

        {/* AI Insight */}
        <AgentSummaryCard
          days={RANGE_DAYS[range]}
          title="Nhận định AI"
          subtitle={`Tổng hợp ${RANGE_LABELS[range].toLowerCase()} gần nhất`}
          collapsible
          mobileOnlyCollapse
          defaultOpen={false}
          actions={
            <div className="flex gap-2">
              <Link
                href='/ai'
                className="flex-1 py-2.5 rounded-xl bg-primary text-primary-foreground text-sm font-medium hover:bg-[#2D9D97] transition-colors flex items-center justify-center gap-2"
              >
                <Bot className="w-4 h-4" /> Hỏi AI
              </Link>
              <button
                type='button'
                disabled
                aria-disabled='true'
                title='Tính năng đang được hoàn thiện'
                className="flex-1 cursor-not-allowed py-2.5 rounded-xl border border-border bg-muted text-sm font-medium text-muted-foreground opacity-60 flex items-center justify-center gap-2"
              >
                Xem phân tích <ChevronRight className="w-4 h-4" />
              </button>
            </div>
          }
        />
      </div>

      {/* Current medications — compact mobile summary */}
      <section ref={medicationsRef} className="overflow-hidden rounded-2xl border border-border bg-card lg:hidden">
        <button
          type="button"
          onClick={() => openMobileDropdown(medicationsOpen, setMedicationsOpen, medicationsRef)}
          aria-expanded={medicationsOpen}
          aria-controls="mobile-medications"
          className="flex w-full items-center gap-3 px-3.5 py-3 text-left"
        >
          <div className="flex min-w-0 items-center gap-3">
            <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg bg-purple-50 text-purple-500 dark:bg-purple-950/40">
              <Pill className="h-4 w-4" />
            </span>
            <div className="min-w-0">
              <h3 className="text-sm font-semibold text-foreground">Thuốc đang sử dụng</h3>
              <p className="text-[11px] text-muted-foreground">{activeMedications.length} thuốc hiện tại</p>
            </div>
          </div>
          <ChevronRight className={cn('ml-auto h-5 w-5 shrink-0 text-muted-foreground transition-transform duration-300 ease-out', medicationsOpen && 'rotate-90')} />
        </button>

        <div
          id="mobile-medications"
          className={cn(
            'grid transition-[grid-template-rows,opacity] duration-300 ease-out',
            medicationsOpen ? 'grid-rows-[1fr] opacity-100' : 'grid-rows-[0fr] opacity-0',
          )}
        >
          <div className="overflow-hidden">
          {medicationsLoading ? (
          <div className="flex items-center justify-center gap-2 border-t border-border px-4 py-4 text-xs text-muted-foreground">
            <Loader2 className="h-4 w-4 animate-spin" /> Đang tải thuốc...
          </div>
        ) : activeMedications.length > 0 ? (
          <div className="divide-y divide-border border-t border-border px-3.5">
            {activeMedications.slice(0, 2).map((medication) => (
              <div key={medication.id} className="min-w-0 py-2.5">
                <p className="truncate text-sm font-medium text-foreground">{medication.drug_name}</p>
                <p className="mt-0.5 truncate text-xs text-muted-foreground">{medication.dose} · {medication.frequency}</p>
              </div>
            ))}
          </div>
        ) : (
          <p className="border-t border-border px-4 py-4 text-center text-xs text-muted-foreground">Chưa có thuốc đang sử dụng.</p>
          )}
          </div>
        </div>
      </section>

      {/* Upcoming appointments — compact mobile summary */}
      <section ref={appointmentsRef} className="overflow-hidden rounded-2xl border border-border bg-card lg:hidden">
        <button
          type="button"
          onClick={() => openMobileDropdown(appointmentsOpen, setAppointmentsOpen, appointmentsRef)}
          aria-expanded={appointmentsOpen}
          aria-controls="mobile-appointments"
          className="flex w-full items-center gap-3 px-3.5 py-3 text-left"
        >
          <div className="flex min-w-0 items-center gap-3">
            <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg bg-blue-50 text-blue-600 dark:bg-blue-950/40">
              <CalendarDays className="h-4 w-4" />
            </span>
            <div className="min-w-0">
              <h3 className="text-sm font-semibold text-foreground">Lịch khám sắp tới</h3>
              <p className="text-[11px] text-muted-foreground">{upcomingAppointments.length} lịch hẹn</p>
            </div>
          </div>
          <ChevronRight className={cn('ml-auto h-5 w-5 shrink-0 text-muted-foreground transition-transform duration-300 ease-out', appointmentsOpen && 'rotate-90')} />
        </button>

        <div
          id="mobile-appointments"
          className={cn(
            'grid transition-[grid-template-rows,opacity] duration-300 ease-out',
            appointmentsOpen ? 'grid-rows-[1fr] opacity-100' : 'grid-rows-[0fr] opacity-0',
          )}
        >
          <div className="overflow-hidden">
          {appointmentsLoading ? (
          <div className="flex items-center justify-center gap-2 border-t border-border px-4 py-4 text-xs text-muted-foreground">
            <Loader2 className="h-4 w-4 animate-spin" /> Đang tải lịch khám...
          </div>
        ) : upcomingAppointments.length > 0 ? (
          <div className="divide-y divide-border border-t border-border px-3.5">
            {upcomingAppointments.slice(0, 2).map((appointment) => {
              const scheduledAt = new Date(appointment.scheduled_at)
              return (
                <div key={appointment.id} className="min-w-0 py-2.5">
                  <p className="truncate text-sm font-medium text-foreground">{appointment.reason || 'Khám và theo dõi định kỳ'}</p>
                  <p className="mt-0.5 truncate text-xs text-muted-foreground">
                    {new Intl.DateTimeFormat('vi-VN', { day: '2-digit', month: '2-digit', year: 'numeric' }).format(scheduledAt)} · {new Intl.DateTimeFormat('vi-VN', { hour: '2-digit', minute: '2-digit', hour12: false }).format(scheduledAt)}
                  </p>
                </div>
              )
            })}
          </div>
        ) : (
          <p className="border-t border-border px-4 py-4 text-center text-xs text-muted-foreground">Chưa có lịch khám sắp tới.</p>
          )}
          </div>
        </div>
      </section>
    </div>
  )
}
