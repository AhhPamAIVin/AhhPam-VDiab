'use client'

import { useCallback, useEffect, useRef, useState } from 'react'
import { DatePicker, TimePicker } from 'antd'
import dayjs from 'dayjs'
import { Camera, ChevronDown, ChevronUp, Clock, ImagePlus, Loader2, Plus, Upload, X } from 'lucide-react'
import { api, type MealRecord, type MealType } from '@/lib/api'
import { useAuth } from '@/lib/auth-context'
import { useToast } from '@/components/ui/toast'
import { Pagination } from '@/components/ui/pagination'

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
  const fileInput = useRef<HTMLInputElement>(null)
  const cameraInput = useRef<HTMLInputElement>(null)
  const [records, setRecords] = useState<MealRecord[]>([])
  const [mealType, setMealType] = useState<MealType>('lunch')
  const [description, setDescription] = useState('')
  const [portion, setPortion] = useState('')
  const [note, setNote] = useState('')
  const [date, setDate] = useState(() => localDate(new Date()))
  const [time, setTime] = useState(() => localTime(new Date()))
  const [imageFile, setImageFile] = useState<File | null>(null)
  const [imagePreview, setImagePreview] = useState<string | null>(null)
  const [temporaryImages, setTemporaryImages] = useState<Record<string, string>>({})
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

  const selectImage = (file?: File) => {
    if (!file) return
    if (!file.type.startsWith('image/')) {
      showToast('Vui lòng chọn tệp hình ảnh.', 'error')
      return
    }
    if (file.size > 10 * 1024 * 1024) {
      showToast('Ảnh không được vượt quá 10MB.', 'error')
      return
    }
    if (imagePreview) URL.revokeObjectURL(imagePreview)
    setImageFile(file)
    setImagePreview(URL.createObjectURL(file))
  }

  const removeImage = () => {
    if (imagePreview) URL.revokeObjectURL(imagePreview)
    setImageFile(null)
    setImagePreview(null)
    if (fileInput.current) fileInput.current.value = ''
    if (cameraInput.current) cameraInput.current.value = ''
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
      const created = await api.createMeal({
        patient_id: user.id,
        meal_type: mealType,
        meal_time: mealTime.toISOString(),
        description: text,
        note: [portion.trim() && `Khẩu phần: ${portion.trim()}`, note.trim()].filter(Boolean).join(' · ') || null,
      })
      if (imageFile && imagePreview) {
        setTemporaryImages((current) => ({ ...current, [created.id]: imagePreview }))
        setImageFile(null)
        setImagePreview(null)
      }
      setRecords((current) => [created, ...current])
      setDescription('')
      setPortion('')
      setNote('')
      const now = new Date()
      setDate(localDate(now))
      setTime(localTime(now))
      showToast(imageFile ? 'Đã lưu bữa ăn. Ảnh đang được giữ tạm trên thiết bị này.' : 'Đã lưu bữa ăn thành công!', 'success')
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

        <input ref={fileInput} className='hidden' type='file' accept='image/jpeg,image/png,image/webp' onChange={(event) => selectImage(event.target.files?.[0])} />
        <input ref={cameraInput} className='hidden' type='file' accept='image/*' capture='environment' onChange={(event) => selectImage(event.target.files?.[0])} />
        {imagePreview ? (
          <div className='relative h-48 overflow-hidden rounded-2xl border border-border bg-muted'>
            <img src={imagePreview} alt='Ảnh bữa ăn xem trước' className='h-full w-full object-cover' />
            <button type='button' onClick={removeImage} className='absolute right-2 top-2 rounded-full bg-black/60 p-2 text-white'><X className='h-4 w-4' /></button>
            <span className='absolute bottom-2 left-2 rounded-lg bg-black/60 px-2 py-1 text-xs text-white'>Ảnh lưu tạm trên frontend</span>
          </div>
        ) : (
          <div className='rounded-2xl border-2 border-dashed border-border bg-muted/30 p-5 text-center'>
            <ImagePlus className='mx-auto h-7 w-7 text-muted-foreground' />
            <p className='mt-2 text-sm font-medium'>Thêm ảnh bữa ăn</p>
            <p className='mt-1 text-xs text-muted-foreground'>JPG, PNG hoặc WebP · tối đa 10MB</p>
            <button type='button' onClick={() => fileInput.current?.click()} className='mx-auto mt-3 hidden items-center gap-2 rounded-xl bg-primary px-4 py-2 text-sm font-medium text-primary-foreground md:flex'><Upload className='h-4 w-4' /> Chọn ảnh từ máy</button>
            <button type='button' onClick={() => cameraInput.current?.click()} className='mx-auto mt-3 flex items-center gap-2 rounded-xl bg-primary px-4 py-2 text-sm font-medium text-primary-foreground md:hidden'><Camera className='h-4 w-4' /> Chụp bằng camera sau</button>
          </div>
        )}

        <div className='grid gap-3 sm:grid-cols-2'>
          <div><label className='mb-1.5 block text-sm font-medium'>Món ăn</label><input value={description} onChange={(event) => setDescription(event.target.value)} placeholder='VD: Cơm gạo lứt, cá hồi, rau luộc' maxLength={1000} className='w-full rounded-xl border border-border bg-background px-4 py-3 text-sm outline-none focus:border-primary' /></div>
          <div><label className='mb-1.5 block text-sm font-medium'>Loại bữa ăn</label><select value={mealType} onChange={(event) => setMealType(event.target.value as MealType)} className='w-full rounded-xl border border-border bg-background px-4 py-3 text-sm outline-none focus:border-primary'>{mealTypes.map((item) => <option key={item.value} value={item.value}>{item.label}</option>)}</select></div>
        </div>
        <div className='grid grid-cols-2 gap-3'><div><label className='mb-1 block text-xs font-medium text-muted-foreground'>Ngày</label><DatePicker value={date && dayjs(date).isValid() ? dayjs(date) : null} onChange={(value) => setDate(value?.format('YYYY-MM-DD') ?? '')} format='DD/MM/YYYY' allowClear={false} size='large' className='w-full' placeholder='Chọn ngày' /></div><div><label className='mb-1 block text-xs font-medium text-muted-foreground'>Giờ</label><TimePicker value={time && dayjs(`2000-01-01T${time}:00`).isValid() ? dayjs(`2000-01-01T${time}:00`) : null} onChange={(value) => setTime(value?.format('HH:mm') ?? '')} format='HH:mm' allowClear={false} minuteStep={1} size='large' className='w-full' placeholder='Chọn giờ' /></div></div>
        <div className='grid gap-3 sm:grid-cols-2'><div><label className='mb-1.5 block text-sm font-medium'>Khẩu phần</label><input value={portion} onChange={(event) => setPortion(event.target.value)} placeholder='VD: 1 bát, 200g' maxLength={150} className='w-full rounded-xl border border-border bg-background px-4 py-3 text-sm' /></div><div><label className='mb-1.5 block text-sm font-medium'>Ghi chú</label><input value={note} onChange={(event) => setNote(event.target.value)} placeholder='Ghi chú thêm...' maxLength={300} className='w-full rounded-xl border border-border bg-background px-4 py-3 text-sm' /></div></div>
        <button type='button' onClick={handleSave} disabled={saving} className='flex w-full items-center justify-center gap-2 rounded-xl bg-primary py-3 text-sm font-semibold text-primary-foreground disabled:opacity-60'>{saving ? <Loader2 className='h-4 w-4 animate-spin' /> : <Plus className='h-4 w-4' />}{saving ? 'Đang lưu...' : 'Lưu bữa ăn'}</button>
      </div>

      <div><h3 className='mb-3 font-semibold'>Lịch sử bữa ăn</h3>{loading ? <div className='flex justify-center rounded-2xl border border-border bg-card p-10'><Loader2 className='h-5 w-5 animate-spin text-primary' /></div> : <div className='space-y-3'>{paginatedMeals.map((meal) => <div key={meal.id} className='overflow-hidden rounded-2xl border border-border bg-card'><button type='button' onClick={() => setExpanded(expanded === meal.id ? null : meal.id)} className='flex w-full items-start gap-3 p-4 text-left'>{temporaryImages[meal.id] ? <img src={temporaryImages[meal.id]} alt='' className='h-12 w-12 rounded-xl object-cover' /> : <span className='flex h-12 w-12 items-center justify-center rounded-xl bg-accent'><Clock className='h-4 w-4 text-[#43B5AE]' /></span>}<div className='min-w-0 flex-1'><div className='flex items-center gap-2 text-xs'><span className='font-medium text-[#2D9D97]'>{mealLabels[meal.meal_type]}</span><span className='text-muted-foreground'>{new Date(meal.meal_time).toLocaleString('vi-VN')}</span></div><p className='mt-1 truncate text-sm font-semibold'>{meal.description}</p></div>{expanded === meal.id ? <ChevronUp className='h-4 w-4 text-muted-foreground' /> : <ChevronDown className='h-4 w-4 text-muted-foreground' />}</button>{expanded === meal.id && <div className='border-t border-border px-4 py-3 text-sm text-muted-foreground'>{meal.note || 'Không có ghi chú'}{temporaryImages[meal.id] && <p className='mt-2 text-xs text-orange-600'>Ảnh này chỉ được giữ tạm trong phiên hiện tại.</p>}</div>}</div>)}{records.length > 0 && <Pagination page={currentMealPage} totalItems={records.length} onPageChange={setMealPage} className='rounded-2xl border border-border bg-card' />}{records.length === 0 && <div className='rounded-2xl border border-border bg-card p-10 text-center text-sm text-muted-foreground'>Chưa có bữa ăn nào được ghi nhận.</div>}</div>}</div>
    </div>
  )
}
