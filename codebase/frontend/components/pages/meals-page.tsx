'use client'

import { useCallback, useEffect, useState } from 'react'
import { DatePicker, TimePicker } from 'antd'
import dayjs from 'dayjs'
import { Camera, ChevronDown, ChevronUp, Clock, Loader2, Plus } from 'lucide-react'
import { api, type MealInputSource, type MealPhotoAnalysis, type MealRecord, type MealType } from '@/lib/api'
import { useAuth } from '@/lib/auth-context'
import { useToast } from '@/components/ui/toast'
import { Pagination } from '@/components/ui/pagination'
import { MealPhotoPrefill } from '@/components/meal-photo-prefill'

const mealTypes: { value: MealType; label: string }[] = [
  { value: 'breakfast', label: 'Bữa sáng' },
  { value: 'lunch', label: 'Bữa trưa' },
  { value: 'dinner', label: 'Bữa tối' },
  { value: 'snack', label: 'Bữa phụ' },
]

const mealLabels = Object.fromEntries(mealTypes.map((item) => [item.value, item.label])) as Record<MealType, string>

const localDate = (date: Date) => {
  const year = date.getFullYear()
  const month = String(date.getMonth() + 1).padStart(2, '0')
  const day = String(date.getDate()).padStart(2, '0')
  return `${year}-${month}-${day}`
}

const localTime = (date: Date) => `${String(date.getHours()).padStart(2, '0')}:${String(date.getMinutes()).padStart(2, '0')}`

export function MealsPage() {
  const { user } = useAuth()
  const { showToast } = useToast()
  const [records, setRecords] = useState<MealRecord[]>([])
  const [mealType, setMealType] = useState<MealType>('lunch')
  const [description, setDescription] = useState('')
  const [portion, setPortion] = useState('')
  const [note, setNote] = useState('')
  const [date, setDate] = useState(() => localDate(new Date()))
  const [time, setTime] = useState(() => localTime(new Date()))
  const [inputSource, setInputSource] = useState<MealInputSource>('text')
  // Bumped after a save to reset the photo picker.
  const [photoKey, setPhotoKey] = useState(0)
  const [expanded, setExpanded] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [mealPage, setMealPage] = useState(1)

  const mealPageCount = Math.max(1, Math.ceil(records.length / 10))
  const currentMealPage = Math.min(mealPage, mealPageCount)
  const paginatedMeals = records.slice((currentMealPage - 1) * 10, currentMealPage * 10)

  const loadMeals = useCallback(() => {
    setLoading(true)
    api.meals().then(setRecords).catch((error) => {
      showToast(error instanceof Error ? error.message : 'Không thể tải bữa ăn', 'error')
    }).finally(() => setLoading(false))
  }, [showToast])

  useEffect(() => loadMeals(), [loadMeals])

  const applyPhotoDraft = (analysis: MealPhotoAnalysis) => {
    setDescription(analysis.description ?? '')
    setPortion(analysis.portion ?? '')
    setInputSource('photo')
  }

  const handleSave = async () => {
    if (!user || saving) return
    const text = description.trim()
    if (!text) {
      showToast('Vui lòng nhập món ăn.', 'warning')
      return
    }
    const mealTimeValue = `${date}T${time}:00`
    const mealTime = new Date(mealTimeValue)
    if (!date || !time || !Number.isFinite(mealTime.getTime())) {
      showToast('Vui lòng nhập ngày giờ bữa ăn hợp lệ.', 'error')
      return
    }
    setSaving(true)
    try {
      const saved = await api.createMeal({
        patient_id: user.id,
        meal_type: mealType,
        meal_time: mealTime.toISOString(),
        description: text,
        portion: portion.trim() || null,
        note: note.trim() || null,
        input_source: inputSource,
      })
      setRecords((current) => [saved, ...current])
      setInputSource('text')
      setPhotoKey((key) => key + 1)
      setDescription('')
      setPortion('')
      setNote('')
      const now = new Date()
      setDate(localDate(now))
      setTime(localTime(now))
      showToast('Đã lưu bữa ăn thành công!', 'success')
    } catch (error) {
      showToast(error instanceof Error ? error.message : 'Không thể lưu bữa ăn', 'error')
    } finally {
      setSaving(false)
    }
  }

  return (
    <div className='mx-auto max-w-3xl space-y-5'>
      <div className='space-y-4 rounded-2xl border border-border bg-card p-5'>
        <div><h2 className='font-semibold'>Thêm bữa ăn</h2><p className='mt-1 text-xs text-muted-foreground'>Ghi món ăn và thời điểm để theo dõi dinh dưỡng hằng ngày.</p></div>

        <MealPhotoPrefill key={photoKey} hint={description} onDraft={applyPhotoDraft} onClear={() => setInputSource('text')} />

        <div className='grid gap-3 sm:grid-cols-2'>
          <div><label className='mb-1.5 block text-sm font-medium'>Món ăn</label><input value={description} onChange={(event) => setDescription(event.target.value)} placeholder='VD: Cơm gạo lứt, cá hồi, rau luộc' maxLength={1000} className='w-full rounded-xl border border-border bg-background px-4 py-3 text-sm outline-none focus:border-primary' /></div>
          <div><label className='mb-1.5 block text-sm font-medium'>Loại bữa ăn</label><select value={mealType} onChange={(event) => setMealType(event.target.value as MealType)} className='w-full rounded-xl border border-border bg-background px-4 py-3 text-sm outline-none focus:border-primary'>{mealTypes.map((item) => <option key={item.value} value={item.value}>{item.label}</option>)}</select></div>
        </div>
        <div className='grid grid-cols-2 gap-3'><div><label className='mb-1 block text-xs font-medium text-muted-foreground'>Ngày</label><DatePicker value={date && dayjs(date).isValid() ? dayjs(date) : null} onChange={(value) => setDate(value?.format('YYYY-MM-DD') ?? '')} format='DD/MM/YYYY' allowClear={false} size='large' className='w-full' placeholder='Chọn ngày' /></div><div><label className='mb-1 block text-xs font-medium text-muted-foreground'>Giờ</label><TimePicker value={time && dayjs(`2000-01-01T${time}:00`).isValid() ? dayjs(`2000-01-01T${time}:00`) : null} onChange={(value) => setTime(value?.format('HH:mm') ?? '')} format='HH:mm' allowClear={false} minuteStep={1} size='large' className='w-full' placeholder='Chọn giờ' /></div></div>
        <div className='grid gap-3 sm:grid-cols-2'><div><label className='mb-1.5 block text-sm font-medium'>Khẩu phần</label><input value={portion} onChange={(event) => setPortion(event.target.value)} placeholder='VD: 1 bát, 200g' maxLength={100} className='w-full rounded-xl border border-border bg-background px-4 py-3 text-sm' /></div><div><label className='mb-1.5 block text-sm font-medium'>Ghi chú</label><input value={note} onChange={(event) => setNote(event.target.value)} placeholder='Ghi chú thêm...' maxLength={300} className='w-full rounded-xl border border-border bg-background px-4 py-3 text-sm' /></div></div>
        <button type='button' onClick={handleSave} disabled={saving} className='flex w-full items-center justify-center gap-2 rounded-xl bg-primary py-3 text-sm font-semibold text-primary-foreground disabled:opacity-60'>{saving ? <Loader2 className='h-4 w-4 animate-spin' /> : <Plus className='h-4 w-4' />}{saving ? 'Đang lưu...' : 'Lưu bữa ăn'}</button>
      </div>

      <div><h3 className='mb-3 font-semibold'>Lịch sử bữa ăn</h3>{loading ? <div className='flex justify-center rounded-2xl border border-border bg-card p-10'><Loader2 className='h-5 w-5 animate-spin text-primary' /></div> : <div className='space-y-3'>{paginatedMeals.map((meal) => <div key={meal.id} className='overflow-hidden rounded-2xl border border-border bg-card'><button type='button' onClick={() => setExpanded(expanded === meal.id ? null : meal.id)} className='flex w-full items-start gap-3 p-4 text-left'><span className='flex h-12 w-12 shrink-0 items-center justify-center rounded-xl bg-accent'>{meal.input_source === 'photo' ? <Camera className='h-4 w-4 text-[#43B5AE]' aria-label='Ghi từ ảnh' /> : <Clock className='h-4 w-4 text-[#43B5AE]' />}</span><div className='min-w-0 flex-1'><div className='flex items-center gap-2 text-xs'><span className='font-medium text-[#2D9D97]'>{mealLabels[meal.meal_type]}</span><span className='text-muted-foreground'>{new Date(meal.meal_time).toLocaleString('vi-VN')}</span></div><p className='mt-1 truncate text-sm font-semibold'>{meal.description}</p></div>{expanded === meal.id ? <ChevronUp className='h-4 w-4 text-muted-foreground' /> : <ChevronDown className='h-4 w-4 text-muted-foreground' />}</button>{expanded === meal.id && <div className='space-y-2 border-t border-border px-4 py-3 text-sm text-muted-foreground'>{meal.input_source === 'photo' && <p className='text-xs'>Ghi từ ảnh (AI nhận diện, bạn đã xác nhận)</p>}{meal.portion && <p><span className='font-medium text-foreground'>Khẩu phần:</span> {meal.portion}</p>}<p>{meal.note || 'Không có ghi chú'}</p></div>}</div>)}{records.length > 0 && <Pagination page={currentMealPage} totalItems={records.length} onPageChange={setMealPage} className='rounded-2xl border border-border bg-card' />}{records.length === 0 && <div className='rounded-2xl border border-border bg-card p-10 text-center text-sm text-muted-foreground'>Chưa có bữa ăn nào được ghi nhận.</div>}</div>}</div>
    </div>
  )
}
