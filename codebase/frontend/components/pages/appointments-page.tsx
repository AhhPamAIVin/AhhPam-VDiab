'use client'

import { useEffect, useMemo, useState } from 'react'
import { CalendarClock, CalendarDays, CheckCircle2, CircleX, Clock3, FileText, RefreshCw, Stethoscope } from 'lucide-react'
import { api, type Appointment } from '@/lib/api'
import { cn } from '@/lib/utils'

type Filter = 'all' | Appointment['status']

const filters: { id: Filter; label: string }[] = [
  { id: 'all', label: 'T\u1ea5t c\u1ea3' },
  { id: 'scheduled', label: 'S\u1eafp t\u1edbi' },
  { id: 'completed', label: '\u0110\u00e3 ho\u00e0n th\u00e0nh' },
  { id: 'cancelled', label: '\u0110\u00e3 h\u1ee7y' },
]

const statusConfig = {
  scheduled: { label: 'S\u1eafp t\u1edbi', icon: Clock3, badge: 'bg-blue-50 text-blue-700 border-blue-200', accent: 'bg-blue-500', iconBox: 'bg-blue-50 text-blue-600' },
  completed: { label: '\u0110\u00e3 ho\u00e0n th\u00e0nh', icon: CheckCircle2, badge: 'bg-emerald-50 text-emerald-700 border-emerald-200', accent: 'bg-emerald-500', iconBox: 'bg-emerald-50 text-emerald-600' },
  cancelled: { label: '\u0110\u00e3 h\u1ee7y', icon: CircleX, badge: 'bg-red-50 text-red-700 border-red-200', accent: 'bg-red-400', iconBox: 'bg-red-50 text-red-600' },
} as const

const dateFormatter = new Intl.DateTimeFormat('vi-VN', { weekday: 'long', day: '2-digit', month: '2-digit', year: 'numeric' })
const timeFormatter = new Intl.DateTimeFormat('vi-VN', { hour: '2-digit', minute: '2-digit', hour12: false })

function AppointmentCard({ appointment }: { appointment: Appointment }) {
  const config = statusConfig[appointment.status]
  const StatusIcon = config.icon
  const scheduledAt = new Date(appointment.scheduled_at)

  return (
    <article className="relative overflow-hidden rounded-2xl border border-border bg-card shadow-sm transition-shadow hover:shadow-md">
      <div className={cn('absolute inset-y-0 left-0 w-1', config.accent)} />
      <div className="p-4 pl-5 sm:p-5 sm:pl-6">
        <div className="flex items-start justify-between gap-3">
          <div className="flex min-w-0 items-start gap-3">
            <div className={cn('flex h-11 w-11 shrink-0 items-center justify-center rounded-xl', config.iconBox)}>
              <CalendarDays className="h-5 w-5" />
            </div>
            <div className="min-w-0">
              <p className="capitalize text-sm font-semibold text-foreground">{dateFormatter.format(scheduledAt)}</p>
              <p className="mt-1 flex items-center gap-1.5 text-sm font-medium text-primary">
                <Clock3 className="h-3.5 w-3.5" /> {timeFormatter.format(scheduledAt)}
              </p>
            </div>
          </div>
          <span className={cn('inline-flex shrink-0 items-center gap-1.5 rounded-full border px-2.5 py-1 text-xs font-semibold', config.badge)}>
            <StatusIcon className="h-3.5 w-3.5" /> {config.label}
          </span>
        </div>

        <div className="mt-4 border-t border-border pt-4">
          <div className="flex items-start gap-2.5">
            <Stethoscope className="mt-0.5 h-4 w-4 shrink-0 text-muted-foreground" />
            <div>
              <p className="text-xs text-muted-foreground">N&#7897;i dung kh&#225;m</p>
              <p className="mt-0.5 text-sm font-medium text-foreground">{appointment.reason || 'Kh\u00e1m v\u00e0 theo d\u00f5i \u0111\u1ecbnh k\u1ef3'}</p>
            </div>
          </div>
        </div>

        {appointment.notes && (
          <div className="mt-4 flex items-start gap-2 rounded-xl bg-muted/70 p-3">
            <FileText className="mt-0.5 h-4 w-4 shrink-0 text-muted-foreground" />
            <div>
              <p className="text-xs font-medium text-muted-foreground">Ghi ch&#250; t&#7915; b&#225;c s&#297;</p>
              <p className="mt-1 text-sm leading-relaxed text-foreground/80">{appointment.notes}</p>
            </div>
          </div>
        )}
      </div>
    </article>
  )
}

export function AppointmentsPage() {
  const [appointments, setAppointments] = useState<Appointment[]>([])
  const [filter, setFilter] = useState<Filter>('all')
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')

  const loadAppointments = async () => {
    setLoading(true)
    setError('')
    try {
      setAppointments(await api.appointments())
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Kh\u00f4ng th\u1ec3 t\u1ea3i l\u1ecbch kh\u00e1m.')
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => { void loadAppointments() }, [])

  const counts = useMemo(() => ({
    all: appointments.length,
    scheduled: appointments.filter((item) => item.status === 'scheduled').length,
    completed: appointments.filter((item) => item.status === 'completed').length,
    cancelled: appointments.filter((item) => item.status === 'cancelled').length,
  }), [appointments])

  const filtered = useMemo(() => appointments
    .filter((item) => filter === 'all' || item.status === filter)
    .sort((a, b) => {
      if (a.status === 'scheduled' && b.status !== 'scheduled') return -1
      if (a.status !== 'scheduled' && b.status === 'scheduled') return 1
      return a.status === 'scheduled'
        ? new Date(a.scheduled_at).getTime() - new Date(b.scheduled_at).getTime()
        : new Date(b.scheduled_at).getTime() - new Date(a.scheduled_at).getTime()
    }), [appointments, filter])

  return (
    <div className="mx-auto max-w-4xl space-y-5">
      <div>
        <h1 className="text-xl font-semibold text-foreground">L&#7883;ch kh&#225;m c&#7911;a t&#244;i</h1>
        <p className="mt-1 text-sm text-muted-foreground">Theo d&#245;i c&#225;c l&#7883;ch kh&#225;m s&#7855;p t&#7899;i v&#224; l&#7883;ch s&#7917; kh&#225;m c&#7911;a b&#7841;n.</p>
      </div>

      <div className="flex gap-2 overflow-x-auto pb-1">
        {filters.map((item) => (
          <button key={item.id} onClick={() => setFilter(item.id)} className={cn(
            'inline-flex shrink-0 items-center gap-2 rounded-xl border px-3 py-2 text-xs font-medium transition-colors',
            filter === item.id ? 'border-primary bg-primary text-primary-foreground' : 'border-border bg-card text-muted-foreground hover:border-primary/40 hover:text-foreground',
          )}>
            {item.label}
            <span className={cn('rounded-full px-1.5 py-0.5 text-[10px]', filter === item.id ? 'bg-white/20' : 'bg-muted')}>{counts[item.id]}</span>
          </button>
        ))}
      </div>

      {loading ? (
        <div className="space-y-3" aria-label="Loading appointments">
          {[1, 2, 3].map((item) => <div key={item} className="h-40 animate-pulse rounded-2xl border border-border bg-card" />)}
        </div>
      ) : error ? (
        <div className="flex flex-col items-center rounded-2xl border border-red-200 bg-red-50 p-8 text-center">
          <CircleX className="h-9 w-9 text-red-500" />
          <p className="mt-3 font-medium text-red-800">Kh&#244;ng th&#7875; t&#7843;i l&#7883;ch kh&#225;m</p>
          <p className="mt-1 text-sm text-red-700">{error}</p>
          <button onClick={() => void loadAppointments()} className="mt-4 inline-flex items-center gap-2 rounded-xl bg-white px-3 dark:bg-card py-2 text-sm font-medium text-red-700 shadow-sm">
            <RefreshCw className="h-4 w-4" /> Th&#7917; l&#7841;i
          </button>
        </div>
      ) : filtered.length === 0 ? (
        <div className="flex flex-col items-center rounded-2xl border border-border bg-card p-10 text-center">
          <div className="flex h-14 w-14 items-center justify-center rounded-2xl bg-accent"><CalendarClock className="h-7 w-7 text-primary" /></div>
          <p className="mt-4 font-semibold text-foreground">Ch&#432;a c&#243; l&#7883;ch kh&#225;m</p>
          <p className="mt-1 max-w-sm text-sm text-muted-foreground">
            {filter === 'all' ? 'L\u1ecbch kh\u00e1m do b\u00e1c s\u0129 t\u1ea1o s\u1ebd xu\u1ea5t hi\u1ec7n t\u1ea1i \u0111\u00e2y.' : 'Kh\u00f4ng c\u00f3 l\u1ecbch kh\u00e1m \u1edf tr\u1ea1ng th\u00e1i n\u00e0y.'}
          </p>
        </div>
      ) : (
        <div className="grid gap-3 md:grid-cols-2">
          {filtered.map((appointment) => <AppointmentCard key={appointment.id} appointment={appointment} />)}
        </div>
      )}
    </div>
  )
}
