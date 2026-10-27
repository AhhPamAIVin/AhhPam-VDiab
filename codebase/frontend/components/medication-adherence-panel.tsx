'use client'

import { useEffect, useState } from 'react'
import { Check, HelpCircle, Loader2, Minus, X } from 'lucide-react'
import { api, type AdherenceDayStatus, type MedicationAdherence } from '@/lib/api'

// Every day status has its own icon and label, never colour alone.
export const DAY_DISPLAY: Record<AdherenceDayStatus, { label: string; icon: typeof Check; className: string }> = {
  taken: { label: 'Đủ thuốc', icon: Check, className: 'bg-[#DFF7F3] text-[#1F7F79]' },
  missed: { label: 'Có quên / bỏ qua', icon: X, className: 'bg-orange-100 text-orange-800' },
  unconfirmed: { label: 'Không xác nhận', icon: HelpCircle, className: 'bg-yellow-50 text-yellow-800' },
  no_data: { label: 'Chưa có nhắc', icon: Minus, className: 'bg-muted text-muted-foreground' },
}

const dayLabel = (iso: string) => {
  const [, month, day] = iso.split('-')
  return `${day}/${month}`
}

// Medication adherence over 7 or 30 days (Epic 3.4). Shared by the patient's
// medication page and the doctor's patient page (missed dates listed too).
export function MedicationAdherencePanel({ patientId, audience }: { patientId?: string; audience: 'patient' | 'doctor' }) {
  const [days, setDays] = useState<7 | 30>(7)
  const [data, setData] = useState<MedicationAdherence | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    let cancelled = false
    api.medicationAdherence(patientId, days)
      .then((result) => { if (!cancelled) { setData(result); setError(null) } })
      .catch((err) => { if (!cancelled) setError(err instanceof Error ? err.message : 'Không tải được dữ liệu') })
    return () => { cancelled = true }
  }, [patientId, days])

  const forgotten = data?.medications.filter((m) => m.missed_notes > 0 && !m.as_needed) ?? []

  return (
    <section className='space-y-4 rounded-2xl border border-border bg-card p-5'>
      <div className='flex flex-wrap items-center justify-between gap-2'>
        <h3 className='font-semibold'>{audience === 'patient' ? 'Việc uống thuốc của bạn' : 'Tuân thủ thuốc'}</h3>
        <div className='flex gap-1 rounded-lg bg-muted p-1 text-xs' role='group' aria-label='Khoảng thời gian'>
          {([7, 30] as const).map((value) => (
            <button key={value} type='button' aria-pressed={days === value} onClick={() => setDays(value)} className={`rounded-md px-3 py-1 ${days === value ? 'bg-card font-medium shadow-sm' : 'text-muted-foreground'}`}>
              {value} ngày
            </button>
          ))}
        </div>
      </div>

      {error && <p className='text-sm text-muted-foreground'>{error}</p>}
      {!data && !error && <div className='flex justify-center py-4'><Loader2 className='h-5 w-5 animate-spin text-primary' /></div>}

      {data && (
        <>
          {data.day_rate_pct === null ? (
            <p className='text-sm text-muted-foreground'>Chưa đủ dữ liệu: cần trả lời nhắc uống thuốc (Đã uống / Bỏ qua) hoặc ghi nhận khi quên thuốc.</p>
          ) : (
            <div className='flex flex-wrap items-baseline gap-x-3 gap-y-1'>
              <p className='text-3xl font-bold'>{Math.round(data.day_rate_pct)}%</p>
              <p className='text-sm text-muted-foreground'>số ngày đủ thuốc · {data.days_taken} ngày đủ, {data.days_missed} ngày có quên/bỏ qua{data.days_unconfirmed > 0 && `, ${data.days_unconfirmed} ngày không xác nhận`}</p>
            </div>
          )}

          <ul className={`grid gap-1 ${days === 7 ? 'grid-cols-7' : 'grid-cols-7 sm:grid-cols-10'}`} aria-label='Từng ngày'>
            {data.daily.map((day) => {
              const display = DAY_DISPLAY[day.status]
              const Icon = display.icon
              return (
                <li key={day.date} title={`${dayLabel(day.date)}: ${display.label}`} className={`flex flex-col items-center gap-0.5 rounded-lg py-1.5 text-[10px] ${display.className}`}>
                  <Icon className='h-3.5 w-3.5' aria-hidden />
                  <span>{dayLabel(day.date)}</span>
                  <span className='sr-only'>{display.label}</span>
                </li>
              )
            })}
          </ul>
          <ul className='flex flex-wrap gap-x-4 gap-y-1 text-xs text-muted-foreground'>
            {Object.entries(DAY_DISPLAY).map(([status, display]) => {
              const Icon = display.icon
              return <li key={status} className='inline-flex items-center gap-1'><Icon className='h-3 w-3' aria-hidden /> {display.label}</li>
            })}
          </ul>

          {forgotten.length > 0 && (
            <p className='text-sm'>{audience === 'patient' ? 'Bạn đã ghi nhận quên: ' : 'Bệnh nhân tự ghi nhận quên: '}{forgotten.map((m) => `${m.drug_name} (${m.missed_notes} lần)`).join(', ')}</p>
          )}
          {data.glucose_by_adherence && (
            <p className='text-sm'>
              Đường huyết trung bình ngày đủ thuốc <strong>{Math.round(data.glucose_by_adherence.taken_avg_mg_dl)} mg/dL</strong> ({data.glucose_by_adherence.taken_days} ngày), ngày có quên <strong>{Math.round(data.glucose_by_adherence.missed_avg_mg_dl)} mg/dL</strong> ({data.glucose_by_adherence.missed_days} ngày). Đây là mối liên quan, không phải nguyên nhân.
            </p>
          )}
          {audience === 'doctor' && data.missed_dates.length > 0 && (
            <p className='text-sm text-muted-foreground'>Ngày có quên/bỏ qua: {data.missed_dates.map(dayLabel).join(', ')}</p>
          )}
          <p className='text-xs text-muted-foreground'>
            {audience === 'patient'
              ? 'Tính từ các lần bạn bấm trả lời nhắc uống thuốc và ghi nhận quên thuốc. Đừng tự đổi liều hay ngừng thuốc — hãy hỏi bác sĩ nếu bạn gặp khó khăn khi dùng thuốc.'
              : 'Nhắc uống thuốc đi kèm từng nhắc đo đường huyết (không theo từng thuốc), nên chỉ số chính tính theo ngày; tỷ lệ theo nhắc: ' + (data.dose_rate_pct === null ? '—' : `${Math.round(data.dose_rate_pct)}%`) + '. Thuốc dùng khi cần không tính.'}
          </p>
        </>
      )}
    </section>
  )
}
