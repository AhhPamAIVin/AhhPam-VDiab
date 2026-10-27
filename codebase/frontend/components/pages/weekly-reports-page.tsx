'use client'

import { useCallback, useEffect, useState } from 'react'
import { ArrowDown, ArrowUp, CalendarRange, Lightbulb, Loader2, Minus, RefreshCw, UtensilsCrossed } from 'lucide-react'
import { api, type WeeklyReport, type WeeklyReportListItem, type WeeklyReportStats } from '@/lib/api'
import { useToast } from '@/components/ui/toast'
import { TtsPlayButton } from '@/components/tts-play-button'
import { useTtsPlayback } from '@/hooks/use-tts-playback'

const CONTEXT_LABELS: Record<string, string> = {
  FASTING: 'Lúc đói',
  BEFORE_MEAL: 'Trước ăn',
  AFTER_MEAL: 'Sau ăn',
  BEFORE_SLEEP: 'Trước khi ngủ',
  RANDOM: 'Khác',
  EVENT_RECOVERY: 'Theo dõi lại',
}

const formatDay = (iso: string) => {
  const [, month, day] = iso.split('-')
  return `${day}/${month}`
}
const weekLabel = (report: WeeklyReportListItem) => `${formatDay(report.week_start)} – ${formatDay(report.week_end)}`
const num = (value: number | null | undefined, digits = 0) =>
  value === null || value === undefined ? '—' : value.toFixed(digits).replace('.', ',')

function hasStats(stats: WeeklyReport['stats']): stats is WeeklyReportStats {
  return 'glucose' in stats
}

// Change vs last week, always in words + icon (never colour alone).
function Change({ delta, unit, betterWhen }: { delta: number | null; unit: string; betterWhen: 'up' | 'down' }) {
  if (delta === null) return <span className='text-sm text-muted-foreground'>chưa có tuần trước để so sánh</span>
  if (Math.abs(delta) < 0.5) return <span className='inline-flex items-center gap-1 text-sm'><Minus className='h-4 w-4' aria-hidden /> gần như không đổi so với tuần trước</span>
  const up = delta > 0
  const Icon = up ? ArrowUp : ArrowDown
  const better = (up && betterWhen === 'up') || (!up && betterWhen === 'down')
  return (
    <span className='inline-flex items-center gap-1 text-sm'>
      <Icon className='h-4 w-4' aria-hidden /> {up ? 'tăng' : 'giảm'} {num(Math.abs(delta))} {unit} so với tuần trước
      {better ? ' (tốt hơn)' : ''}
    </span>
  )
}

// This week vs last week as two labelled bars (values written out too).
function CompareBars({ label, current, previous, max, unit }: { label: string; current: number | null; previous: number | null; max: number; unit: string }) {
  const width = (value: number | null) => `${value === null ? 0 : Math.min(100, (value / max) * 100)}%`
  return (
    <div className='space-y-1.5'>
      <p className='text-sm font-medium'>{label}</p>
      {([['Tuần này', current, 'bg-primary'], ['Tuần trước', previous, 'bg-muted-foreground/40']] as const).map(([name, value, color]) => (
        <div key={name} className='flex items-center gap-2 text-sm'>
          <span className='w-20 shrink-0 text-muted-foreground'>{name}</span>
          <div className='h-3 flex-1 rounded-full bg-muted' aria-hidden><div className={`h-3 rounded-full ${color}`} style={{ width: width(value) }} /></div>
          <span className='w-24 shrink-0 text-right font-medium'>{value === null ? '—' : `${num(value)} ${unit}`}</span>
        </div>
      ))}
    </div>
  )
}

function ReportDetail({ report }: { report: WeeklyReport }) {
  const tts = useTtsPlayback()
  const stats = report.stats
  const readAloud = [report.summary, report.tip].filter(Boolean).join(' ')
  const playing = tts.playingId === report.id ? tts.state : 'idle'

  return (
    <article className='space-y-4'>
      <div className='rounded-2xl border border-border bg-card p-5'>
        <div className='flex flex-wrap items-start justify-between gap-3'>
          <div>
            <p className='flex items-center gap-2 text-sm text-muted-foreground'><CalendarRange className='h-4 w-4' aria-hidden /> Tuần {weekLabel(report)}</p>
            <h2 className='mt-1 text-xl font-bold'>Báo cáo sức khỏe tuần</h2>
          </div>
          <TtsPlayButton state={playing} onClick={() => (playing === 'idle' || playing === 'error' ? void tts.play(report.id, readAloud) : tts.stop())} />
        </div>
        <p className='mt-4 whitespace-pre-line text-base leading-relaxed'>{report.summary}</p>
        {report.tip && (
          <div className='mt-4 flex gap-3 rounded-xl bg-accent/60 p-4'>
            <Lightbulb className='mt-0.5 h-5 w-5 shrink-0 text-primary' aria-hidden />
            <div><p className='text-sm font-semibold'>Việc nên làm tuần tới</p><p className='mt-1 text-base'>{report.tip}</p></div>
          </div>
        )}
      </div>

      {hasStats(stats) && stats.glucose.count > 0 && (
        <section className='space-y-4 rounded-2xl border border-border bg-card p-5'>
          <h3 className='text-lg font-semibold'>Đường huyết</h3>
          <div className='grid gap-3 sm:grid-cols-2'>
            <div className='rounded-xl border border-border p-4'>
              <p className='text-sm text-muted-foreground'>Trung bình ({stats.glucose.count} lần đo)</p>
              <p className='mt-1 text-2xl font-bold'>{num(stats.glucose.avg)} <span className='text-base font-normal'>mg/dL</span></p>
              <Change delta={stats.change.avg_delta} unit='mg/dL' betterWhen='down' />
            </div>
            <div className='rounded-xl border border-border p-4'>
              <p className='text-sm text-muted-foreground'>Trong mục tiêu bác sĩ đặt</p>
              <p className='mt-1 text-2xl font-bold'>{stats.glucose.in_target_pct === null ? 'Chưa đánh giá' : `${num(stats.glucose.in_target_pct)}%`}</p>
              {stats.glucose.in_target_pct !== null && <Change delta={stats.change.in_target_pct_delta} unit='điểm %' betterWhen='up' />}
            </div>
          </div>
          {stats.glucose.low_count > 0 && (
            <p className='rounded-xl border border-orange-300 bg-orange-50 p-3 text-sm text-orange-900'>
              Có <strong>{stats.glucose.low_count}</strong> lần đường huyết dưới 70 mg/dL trong tuần.
            </p>
          )}
          <CompareBars label='Đường huyết trung bình' current={stats.glucose.avg} previous={stats.previous_week.avg} max={300} unit='mg/dL' />
          {stats.glucose.in_target_pct !== null && (
            <CompareBars label='Tỷ lệ trong mục tiêu' current={stats.glucose.in_target_pct} previous={stats.previous_week.in_target_pct} max={100} unit='%' />
          )}
          <ul className='grid gap-2 text-sm sm:grid-cols-2'>
            {Object.entries(stats.glucose.by_context).map(([context, s]) => (
              <li key={context} className='flex justify-between gap-2 rounded-lg bg-muted/50 px-3 py-2'>
                <span>{CONTEXT_LABELS[context] ?? context} ({s.count})</span>
                <span className='font-medium'>TB {num(s.avg)} · {num(s.min)}–{num(s.max)} mg/dL</span>
              </li>
            ))}
          </ul>
        </section>
      )}

      {hasStats(stats) && (
        <section className='grid gap-3 sm:grid-cols-3'>
          <div className='rounded-2xl border border-border bg-card p-4'><p className='text-sm text-muted-foreground'>Số ngày có ghi nhật ký</p><p className='mt-1 text-2xl font-bold'>{stats.days_logged}/7</p></div>
          <div className='rounded-2xl border border-border bg-card p-4'><p className='text-sm text-muted-foreground'>Bữa ăn đã ghi</p><p className='mt-1 text-2xl font-bold'>{stats.meal_count}</p></div>
          <div className='rounded-2xl border border-border bg-card p-4'><p className='text-sm text-muted-foreground'>Vận động</p><p className='mt-1 text-2xl font-bold'>{stats.activity_minutes} <span className='text-base font-normal'>phút</span></p></div>
        </section>
      )}

      {hasStats(stats) && stats.blood_pressure.count > 0 && (
        <section className='rounded-2xl border border-border bg-card p-5'>
          <h3 className='text-lg font-semibold'>Huyết áp</h3>
          <p className='mt-2 text-base'>Trung bình {num(stats.blood_pressure.systolic_avg)}/{num(stats.blood_pressure.diastolic_avg)} mmHg ({stats.blood_pressure.count} lần đo)</p>
        </section>
      )}

      {hasStats(stats) && stats.meals.top_foods.length > 0 && (
        <section className='rounded-2xl border border-border bg-card p-5'>
          <h3 className='flex items-center gap-2 text-lg font-semibold'><UtensilsCrossed className='h-5 w-5 text-primary' aria-hidden /> Món thường đi kèm đường huyết sau ăn cao</h3>
          <ul className='mt-3 space-y-2'>
            {stats.meals.top_foods.map((food) => (
              <li key={food.name} className='rounded-xl bg-muted/50 p-3 text-base'>
                <strong>{food.name}</strong>: {food.n} bữa, đường huyết sau ăn trung bình {num(food.avg_post_mg_dl)} mg/dL, {num(food.pct_over_target)}% số bữa vượt 180 mg/dL
              </li>
            ))}
          </ul>
          <p className='mt-2 text-sm text-muted-foreground'>Tính trong {stats.meals.lookback_days} ngày qua. Đây là mối liên quan, không phải nguyên nhân.</p>
        </section>
      )}

      <p className='text-sm text-muted-foreground'>Báo cáo được tạo tự động từ dữ liệu bạn đã ghi, chỉ để tham khảo; không thay thế tư vấn của bác sĩ.</p>
    </article>
  )
}

export function WeeklyReportsPage() {
  const { showToast } = useToast()
  const [reports, setReports] = useState<WeeklyReportListItem[]>([])
  const [selected, setSelected] = useState<WeeklyReport | null>(null)
  const [loading, setLoading] = useState(true)
  const [generating, setGenerating] = useState(false)
  const [notify, setNotify] = useState<boolean | null>(null)

  const open = useCallback(async (reportId: string) => {
    try {
      const report = await api.weeklyReport(reportId)
      setSelected(report)
      if (!report.read_at) {
        const read = await api.markWeeklyReportRead(reportId)
        setReports((list) => list.map((r) => (r.id === reportId ? { ...r, read_at: read.read_at } : r)))
        window.dispatchEvent(new Event('vdiab:notifications-updated'))
      }
    } catch (error) {
      showToast(error instanceof Error ? error.message : 'Không mở được báo cáo', 'error')
    }
  }, [showToast])

  useEffect(() => {
    let cancelled = false
    api.me().then((me) => { if (!cancelled) setNotify(me.weekly_report_enabled) }).catch(() => {})
    api.weeklyReports()
      .then((list) => {
        if (cancelled) return
        setReports(list)
        if (list[0]) void open(list[0].id)
      })
      .catch((error) => showToast(error instanceof Error ? error.message : 'Không tải được báo cáo', 'error'))
      .finally(() => { if (!cancelled) setLoading(false) })
    return () => { cancelled = true }
  }, [open, showToast])

  const generate = async () => {
    setGenerating(true)
    try {
      const report = await api.generateWeeklyReport()
      setReports((list) => (list.some((r) => r.id === report.id) ? list : [report, ...list]))
      await open(report.id)
    } catch (error) {
      showToast(error instanceof Error ? error.message : 'Chưa tạo được báo cáo', 'error')
    } finally {
      setGenerating(false)
    }
  }

  const toggleNotify = async () => {
    if (notify === null) return
    try {
      const me = await api.updateMe({ weekly_report_enabled: !notify })
      setNotify(me.weekly_report_enabled)
    } catch (error) {
      showToast(error instanceof Error ? error.message : 'Không lưu được lựa chọn', 'error')
    }
  }

  if (loading) return <div className='flex justify-center py-10'><Loader2 className='h-5 w-5 animate-spin text-primary' /></div>

  return (
    <div className='mx-auto max-w-3xl space-y-5'>
      <div className='flex flex-wrap items-center justify-between gap-3'>
        <div>
          <h1 className='text-xl font-bold'>Báo cáo tuần</h1>
          <p className='text-sm text-muted-foreground'>Tự động gửi mỗi sáng Chủ nhật cho tuần vừa qua.</p>
        </div>
        {notify !== null && (
          <label className='flex items-center gap-2 text-sm'>
            <input type='checkbox' checked={notify} onChange={toggleNotify} className='h-4 w-4' />
            Nhận thông báo khi có báo cáo mới
          </label>
        )}
      </div>

      {reports.length === 0 ? (
        <div className='rounded-2xl border border-border bg-card p-8 text-center'>
          <p className='text-base'>Chưa có báo cáo tuần nào.</p>
          <button type='button' disabled={generating} onClick={generate} className='mx-auto mt-4 flex items-center gap-2 rounded-xl bg-primary px-4 py-2 text-sm font-medium text-primary-foreground disabled:opacity-60'>
            {generating ? <Loader2 className='h-4 w-4 animate-spin' /> : <RefreshCw className='h-4 w-4' />} Tạo báo cáo tuần trước
          </button>
        </div>
      ) : (
        <>
          <nav aria-label='Chọn tuần' className='flex gap-2 overflow-x-auto pb-1'>
            {reports.map((report) => (
              <button
                key={report.id}
                type='button'
                onClick={() => void open(report.id)}
                aria-current={selected?.id === report.id}
                className={`shrink-0 rounded-xl border px-3 py-2 text-sm ${selected?.id === report.id ? 'border-primary bg-primary text-primary-foreground' : 'border-border bg-card'}`}
              >
                {weekLabel(report)}{!report.read_at && ' · mới'}
              </button>
            ))}
          </nav>
          {selected ? <ReportDetail key={selected.id} report={selected} /> : <div className='flex justify-center py-10'><Loader2 className='h-5 w-5 animate-spin text-primary' /></div>}
        </>
      )}
    </div>
  )
}
