'use client'

import { useEffect, useState } from 'react'
import { DatePicker, Select, TimePicker } from 'antd'
import dayjs from 'dayjs'
import { X, Droplets, Heart, Scale, Footprints, AlertTriangle, PhoneCall } from 'lucide-react'
import { cn } from '@/lib/utils'
import { useToast } from '@/components/ui/toast'
import { api, glucoseTimingValue } from '@/lib/api'
import { useAuth } from '@/lib/auth-context'

export type RecordType = 'glucose' | 'bp' | 'weight' | 'activity'

type NumericRecordValues = {
  glucose: number
  systolic: number
  diastolic: number
  durationMinutes: number
}

export function getUnusualRecordWarning(type: RecordType, values: NumericRecordValues) {
  if (type === 'glucose' && (values.glucose < 20 || values.glucose > 600)) {
    return `Giá trị đường huyết ${values.glucose} mg/dL rất bất thường. Bạn hãy kiểm tra lại số trên máy đo trước khi lưu.`
  }
  if (type === 'bp' && (values.systolic < 70 || values.systolic > 180 || values.diastolic < 40 || values.diastolic > 120)) {
    return `Huyết áp ${values.systolic}/${values.diastolic} mmHg rất bất thường. Bạn hãy đo lại và kiểm tra đúng số tâm thu, tâm trương trước khi lưu.`
  }
  if (type === 'activity' && values.durationMinutes > 480) {
    return `Thời lượng ${values.durationMinutes} phút (hơn 8 giờ) có vẻ không bình thường. Bạn hãy kiểm tra lại trước khi lưu.`
  }
  return null
}

export const isDangerousGlucose = (value: number) => value <= 60 || value >= 180

const recordTypes = [
  { id: 'glucose'    as RecordType, label: 'Đường huyết', icon: Droplets, color: 'text-[#43B5AE]' },
  { id: 'bp'         as RecordType, label: 'Huyết áp',    icon: Heart,    color: 'text-red-500' },
  { id: 'weight'     as RecordType, label: 'Cân nặng',    icon: Scale,    color: 'text-purple-500' },
  { id: 'activity'   as RecordType, label: 'Hoạt động',   icon: Footprints, color: 'text-orange-500' },
]

const activityOptions = [
  { value: 'walking', label: 'Đi bộ' },
  { value: 'brisk_walking', label: 'Đi bộ nhanh' },
  { value: 'cycling', label: 'Đạp xe' },
  { value: 'swimming', label: 'Bơi lội' },
  { value: 'yoga', label: 'Yoga' },
  { value: 'tai_chi', label: 'Thái cực quyền' },
  { value: 'strength_training', label: 'Tập sức mạnh nhẹ' },
  { value: 'aerobics', label: 'Thể dục nhịp điệu' },
  { value: 'stretching', label: 'Giãn cơ' },
  { value: 'housework', label: 'Làm việc nhà' },
]

const timingOptions = ['Lúc đói', 'Trước ăn', 'Sau ăn', 'Trước khi ngủ', 'Khác']

const localDateValue = (date: Date) => {
  const year = date.getFullYear()
  const month = String(date.getMonth() + 1).padStart(2, '0')
  const day = String(date.getDate()).padStart(2, '0')
  return `${year}-${month}-${day}`
}

const localTimeValue = (date: Date) =>
  `${String(date.getHours()).padStart(2, '0')}:${String(date.getMinutes()).padStart(2, '0')}`

interface RecordModalProps {
  open: boolean
  onClose: () => void
  defaultType?: RecordType
  onSaved?: () => void
}

export function RecordModal({ open, onClose, defaultType = 'glucose', onSaved }: RecordModalProps) {
  const { showToast } = useToast()
  const { user } = useAuth()
  const [type, setType] = useState<RecordType>(defaultType)
  const [glucoseValue, setGlucoseValue] = useState('')
  const [timing, setTiming] = useState('Lúc đói')
  const [systolic, setSystolic] = useState('')
  const [diastolic, setDiastolic] = useState('')
  const [weight, setWeight] = useState('')
  const [activityType, setActivityType] = useState<string>()
  const [durationMinutes, setDurationMinutes] = useState('')
  const [note, setNote] = useState('')
  const [loading, setLoading] = useState(false)
  const [unusualWarning, setUnusualWarning] = useState<string | null>(null)
  const [dangerousGlucose, setDangerousGlucose] = useState<number | null>(null)
  const [recordDate, setRecordDate] = useState(() => localDateValue(new Date()))
  const [recordTime, setRecordTime] = useState(() => localTimeValue(new Date()))

  useEffect(() => {
    if (open) {
      const now = new Date()
      setType(defaultType)
      setRecordDate(localDateValue(now))
      setRecordTime(localTimeValue(now))
      setUnusualWarning(null)
      setDangerousGlucose(null)
    }
  }, [defaultType, open])

  if (!open) return null

  const handleSave = async (confirmedUnusualValue = false) => {
    if (!user) return
    const now = new Date()
    const today = localDateValue(now)
    const measuredAtValue = `${recordDate}T${recordTime}:00`
    const measuredAt = new Date(measuredAtValue)
    if (
      recordDate !== today ||
      !/^([01]\d|2[0-3]):[0-5]\d$/.test(recordTime) ||
      !Number.isFinite(measuredAt.getTime()) ||
      measuredAt.getTime() > now.getTime()
    ) {
      showToast('Thời gian ghi nhận phải hợp lệ, trong hôm nay và không vượt quá giờ hiện tại.', 'error')
      return
    }
    if (type !== 'glucose' && type !== 'bp' && type !== 'activity') {
      showToast('Backend hiện chưa hỗ trợ loại chỉ số này.', 'error')
      return
    }
    const value = Number(glucoseValue)
    const systolicValue = Number(systolic)
    const diastolicValue = Number(diastolic)
    const durationValue = Number(durationMinutes)
    if (type === 'glucose' && (!Number.isFinite(value) || value <= 0 || value > 1000)) {
      showToast('Vui lòng nhập giá trị đường huyết hợp lệ.', 'error')
      return
    }
    if (type === 'bp' && (!Number.isFinite(systolicValue) || systolicValue <= 0 || systolicValue > 300)) {
      showToast('Huyết áp tâm thu phải lớn hơn 0 và không vượt quá 300 mmHg.', 'error')
      return
    }
    if (type === 'bp' && (!Number.isFinite(diastolicValue) || diastolicValue <= 0 || diastolicValue > 200)) {
      showToast('Huyết áp tâm trương phải lớn hơn 0 và không vượt quá 200 mmHg.', 'error')
      return
    }
    if (type === 'bp' && diastolicValue >= systolicValue) {
      showToast('Huyết áp tâm trương phải thấp hơn huyết áp tâm thu.', 'error')
      return
    }
    if (type === 'activity' && !activityType) {
      showToast('Vui lòng chọn loại hoạt động.', 'error')
      return
    }
    if (type === 'activity' && (!Number.isInteger(durationValue) || durationValue <= 0 || durationValue > 1440)) {
      showToast('Thời lượng phải là số phút từ 1 đến 1440.', 'error')
      return
    }
    const warning = getUnusualRecordWarning(type, { glucose: value, systolic: systolicValue, diastolic: diastolicValue, durationMinutes: durationValue })
    if (warning && !confirmedUnusualValue) {
      setUnusualWarning(warning)
      return
    }
    setUnusualWarning(null)
    setLoading(true)
    try {
      if (type === 'glucose') {
        await api.createGlucose({ patient_id: user.id, value, unit: 'mg/dL', measured_at: measuredAt.toISOString(), meal_context: glucoseTimingValue(timing), note: note.trim() || null })
      } else if (type === 'bp') {
        await api.createBloodPressure({ patient_id: user.id, systolic: systolicValue, diastolic: diastolicValue, unit: 'mmHg', measured_at: measuredAt.toISOString(), note: note.trim() || null })
      } else {
        await api.createActivity({ patient_id: user.id, activity_type: activityType!, duration_minutes: durationValue, activity_time: measuredAt.toISOString(), note: note.trim() || null })
      }
      showToast('Đã lưu kết quả thành công!', 'success')
      onSaved?.()
      // Logging glucose/BP auto-completes the matching reminder server-side
      // (see health/service.py's auto_complete_pending) — this tells the
      // already-mounted AppHeader bell badge and any "Lịch hôm nay" widget
      // to refetch so that shows up immediately instead of only after the
      // next full navigation. No matching reminder type exists for
      // activity, so nothing to signal there.
      if (type === 'glucose' || type === 'bp') {
        window.dispatchEvent(new Event('vdiab:notifications-updated'))
      }
      const shouldShowDangerousGlucosePopup = type === 'glucose' && isDangerousGlucose(value)
      setGlucoseValue('')
      setSystolic('')
      setDiastolic('')
      setWeight('')
      setActivityType(undefined)
      setDurationMinutes('')
      setNote('')

      if (shouldShowDangerousGlucosePopup) {
        setDangerousGlucose(value)
      } else {
        onClose()
      }

      if (type === 'glucose') {
        void api.agentSummary(7)
          .then((refreshedSummary) => {
            if (typeof window !== 'undefined') {
              window.dispatchEvent(new CustomEvent('vdiab:agent-summary-updated', { detail: refreshedSummary }))
            }
            showToast('Đã tóm tắt thành công!', 'success')
          })
          .catch(() => {
            showToast('Không thể cập nhật bản tóm tắt.', 'error')
          })
      }
    } catch (error) {
      showToast(error instanceof Error ? error.message : 'Không thể lưu kết quả', 'error')
    } finally {
      setLoading(false)
    }
  }

  return (
    <>
      {dangerousGlucose !== null && (
        <div className="fixed inset-0 z-[70] flex items-center justify-center bg-black/70 p-4" role="dialog" aria-modal="true" aria-labelledby="dangerous-glucose-title">
          <div className="w-full max-w-lg rounded-3xl border-2 border-red-500 bg-red-50 p-6 text-red-950 shadow-2xl sm:p-8">
            <div className="flex items-start gap-4">
              <span className="flex h-14 w-14 shrink-0 items-center justify-center rounded-2xl bg-red-600 text-white">
                <AlertTriangle className="h-8 w-8" aria-hidden="true" />
              </span>
              <div>
                <h2 id="dangerous-glucose-title" className="text-2xl font-bold">Đường huyết nguy hiểm</h2>
                <p className="mt-2 text-lg"><strong>{dangerousGlucose} mg/dL</strong></p>
                <p className="mt-2 text-sm leading-relaxed">Bạn có cần gọi cấp cứu ngay không?</p>
              </div>
            </div>
            <div className="mt-6 grid gap-3 sm:grid-cols-2">
              <a href="tel:115" className="flex items-center justify-center gap-2 rounded-xl bg-red-600 px-4 py-3.5 font-bold text-white hover:bg-red-700">
                <PhoneCall className="h-5 w-5" aria-hidden="true" />
                Gọi 115 ngay
              </a>
              <button type="button" onClick={() => { setDangerousGlucose(null); onClose() }} className="rounded-xl border border-red-300 bg-white px-4 py-3.5 font-semibold text-red-800 hover:bg-red-100">
                Tôi chưa cần
              </button>
            </div>
            <p className="mt-4 text-xs leading-relaxed text-red-700">Hãy gọi ngay nếu bạn lú lẫn, ngất, co giật hoặc khó thở.</p>
          </div>
        </div>
      )}

      {/* Overlay */}
      <div className="fixed inset-0 z-50 bg-black/40" onClick={onClose} />

      {/* Panel – modal on desktop, bottom sheet on mobile */}
      <div className={cn(
        'fixed bottom-0 left-0 right-0 z-50 max-h-[calc(100dvh-1rem)] overflow-y-auto bg-card shadow-xl',
        'rounded-t-3xl',
        'md:bottom-auto md:left-1/2 md:right-auto md:top-1/2 md:w-[calc(100%-2rem)] md:max-w-md',
        'md:-translate-x-1/2 md:-translate-y-1/2 md:rounded-2xl',
      )}>
        {/* Handle on mobile */}
        <div className="md:hidden flex justify-center pt-3 pb-1">
          <div className="w-10 h-1 bg-border rounded-full" />
        </div>

        <div className="p-5">
          <div className="flex items-center justify-between mb-4">
            <h2 className="text-lg font-semibold text-foreground">Ghi chỉ số sức khỏe</h2>
            <button onClick={onClose} className="p-1.5 rounded-lg hover:bg-muted text-muted-foreground">
              <X className="w-4 h-4" />
            </button>
          </div>

          {/* Type selector */}
          <div className="grid grid-cols-3 gap-2 mb-5">
            {recordTypes.map((rt) => (
              <button
                key={rt.id}
                onClick={() => { setType(rt.id); setUnusualWarning(null) }}
                className={cn(
                  'flex min-w-0 items-center justify-center gap-1.5 px-2 py-2 rounded-xl text-sm font-medium transition-colors',
                  type === rt.id ? 'bg-primary text-primary-foreground' : 'bg-muted text-muted-foreground hover:text-foreground',
                )}
              >
                <rt.icon className="w-3.5 h-3.5" />
                {rt.label}
              </button>
            ))}
          </div>

          <div className="space-y-4">
            {type === 'glucose' && (
              <>
                <div>
                  <label className="block text-sm font-medium text-foreground mb-1.5">Giá trị đường huyết</label>
                  <div className="flex gap-2">
                    <input
                      type="number"
                      placeholder="VD: 126"
                      value={glucoseValue}
                      onChange={(e) => { setGlucoseValue(e.target.value); setUnusualWarning(null) }}
                      className="flex-1 px-4 py-3 rounded-xl border border-border bg-background text-foreground text-sm outline-none focus:border-primary focus:ring-2 focus:ring-primary/20"
                    />
                    <div className="px-4 py-3 rounded-xl bg-muted text-sm font-medium text-muted-foreground">mg/dL</div>
                  </div>
                </div>
                <div>
                  <label className="block text-sm font-medium text-foreground mb-1.5">Thời điểm đo</label>
                  <div className="flex flex-wrap gap-2">
                    {timingOptions.map((t) => (
                      <button
                        key={t}
                        onClick={() => setTiming(t)}
                        className={cn(
                          'px-3 py-1.5 rounded-xl text-xs font-medium transition-colors border',
                          timing === t
                            ? 'bg-primary text-primary-foreground border-primary'
                            : 'bg-background text-muted-foreground border-border hover:border-primary hover:text-primary',
                        )}
                      >
                        {t}
                      </button>
                    ))}
                  </div>
                </div>
              </>
            )}

            {type === 'bp' && (
              <div className="space-y-3">
                <div>
                  <label className="mb-1.5 block text-sm font-medium text-foreground">
                    Tâm thu <span className="text-muted-foreground">(mmHg)</span>
                  </label>
                  <input
                    type="number"
                    min="1"
                    max="300"
                    placeholder="VD: 125"
                    value={systolic}
                    onChange={(e) => { setSystolic(e.target.value); setUnusualWarning(null) }}
                    className="w-full rounded-xl border border-border bg-background px-4 py-3 text-sm outline-none focus:border-primary focus:ring-2 focus:ring-primary/20"
                  />
                </div>
                <div>
                  <label className="mb-1.5 block text-sm font-medium text-foreground">
                    Tâm trương <span className="text-muted-foreground">(mmHg)</span>
                  </label>
                  <input
                    type="number"
                    min="1"
                    max="200"
                    placeholder="VD: 80"
                    value={diastolic}
                    onChange={(e) => { setDiastolic(e.target.value); setUnusualWarning(null) }}
                    className="w-full rounded-xl border border-border bg-background px-4 py-3 text-sm outline-none focus:border-primary focus:ring-2 focus:ring-primary/20"
                  />
                </div>
              </div>
            )}

            {type === 'weight' && (
              <div>
                <label className="block text-sm font-medium text-foreground mb-1.5">Cân nặng</label>
                <div className="flex gap-2">
                  <input type="number" step="0.1" placeholder="VD: 68.5" value={weight} onChange={(e) => setWeight(e.target.value)} className="flex-1 px-4 py-3 rounded-xl border border-border bg-background text-sm outline-none focus:border-primary focus:ring-2 focus:ring-primary/20" />
                  <div className="px-4 py-3 rounded-xl bg-muted text-sm font-medium text-muted-foreground">kg</div>
                </div>
              </div>
            )}

            {type === 'activity' && (
              <div className='space-y-3'>
                <div>
                  <label className='mb-1.5 block text-sm font-medium text-foreground'>Loại hoạt động</label>
                  <Select className='w-full' size='large' showSearch value={activityType} onChange={setActivityType} options={activityOptions} placeholder='Chọn hoạt động' optionFilterProp='label' />
                </div>
                <div>
                  <label className='mb-1.5 block text-sm font-medium text-foreground'>Thời lượng</label>
                  <div className='flex gap-2'>
                    <input type='number' min='1' max='1440' step='1' placeholder='VD: 30' value={durationMinutes} onChange={(event) => { setDurationMinutes(event.target.value); setUnusualWarning(null) }} className='flex-1 rounded-xl border border-border bg-background px-4 py-3 text-sm outline-none focus:border-primary focus:ring-2 focus:ring-primary/20' />
                    <div className='rounded-xl bg-muted px-4 py-3 text-sm font-medium text-muted-foreground'>phút</div>
                  </div>
                </div>
              </div>
            )}

            {/* Date/time row */}
            <div className="grid grid-cols-2 gap-3">
              <div>
                <label className="block text-xs font-medium text-muted-foreground mb-1">Ngày</label>
                <DatePicker value={recordDate && dayjs(recordDate).isValid() ? dayjs(recordDate) : null} onChange={(value) => setRecordDate(value?.format('YYYY-MM-DD') ?? '')} format='DD/MM/YYYY' allowClear={false} size='large' className='w-full' disabledDate={(value) => !value.isSame(dayjs(), 'day')} />
              </div>
              <div>
                <label className="block text-xs font-medium text-muted-foreground mb-1">Giờ</label>
                <TimePicker value={recordTime && dayjs(`2000-01-01T${recordTime}:00`).isValid() ? dayjs(`2000-01-01T${recordTime}:00`) : null} onChange={(value) => setRecordTime(value?.format('HH:mm') ?? '')} format='HH:mm' minuteStep={1} allowClear={false} size='large' className='w-full' placeholder='Chọn giờ' />
              </div>
            </div>

            {unusualWarning && <div role={'alert'} className={'rounded-xl border border-amber-300 bg-amber-50 p-4 text-amber-950'}>
              <p className={'text-sm font-semibold'}>Số liệu này có đúng không?</p>
              <p className={'mt-1 text-sm'}>{unusualWarning}</p>
              <div className={'mt-3 flex gap-2'}>
                <button type={'button'} onClick={() => setUnusualWarning(null)} className={'flex-1 rounded-lg border p-2 text-sm'}>Kiểm tra lại</button>
                <button type={'button'} onClick={() => void handleSave(true)} className={'flex-1 rounded-lg bg-amber-700 p-2 text-sm text-white'}>Đúng, vẫn lưu</button>
              </div>
            </div>}

            {/* Note */}
            <div>
              <label className="block text-sm font-medium text-foreground mb-1.5">Ghi chú (không bắt buộc)</label>
              <textarea
                rows={2}
                placeholder="Triệu chứng bất thường, ghi chú thêm..."
                value={note}
                onChange={(e) => setNote(e.target.value)}
                className="w-full px-4 py-3 rounded-xl border border-border bg-background text-sm outline-none focus:border-primary focus:ring-2 focus:ring-primary/20 resize-none"
              />
            </div>
          </div>

          <div className="flex gap-3 mt-5">
            <button onClick={onClose} className="flex-1 py-3 rounded-xl border border-border text-sm font-medium text-muted-foreground hover:bg-muted transition-colors">Hủy</button>
            <button
              onClick={() => void handleSave()}
              disabled={loading}
              className="flex-1 py-3 rounded-xl bg-primary text-primary-foreground text-sm font-semibold hover:bg-[#2D9D97] transition-colors disabled:opacity-70"
            >
              {loading ? 'Đang lưu...' : 'Lưu kết quả'}
            </button>
          </div>
        </div>
      </div>
    </>
  )
}
