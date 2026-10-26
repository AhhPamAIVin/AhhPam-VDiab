'use client'

import { useEffect, useRef, useState } from 'react'
import { Camera, ImagePlus, Loader2, Sparkles, Upload, X } from 'lucide-react'
import { api, type MealPhotoAnalysis } from '@/lib/api'
import { useToast } from '@/components/ui/toast'

const MAX_PHOTO_BYTES = 10 * 1024 * 1024
const ACCEPTED_TYPES = ['image/jpeg', 'image/png', 'image/webp']

const formatQuantity = (quantity: number) => String(quantity).replace('.', ',')

// Photo -> draft meal (POST /health/meals/photo-analysis). The photo is shown
// from a local object URL and sent once to the vision model; it is never
// stored. The draft goes to the parent form, which the patient checks before
// saving. Asks for the patient's consent the first time.
export function MealPhotoPrefill({ hint, onDraft, onClear }: {
  hint: string
  onDraft: (analysis: MealPhotoAnalysis) => void
  onClear: () => void
}) {
  const { showToast } = useToast()
  const fileInput = useRef<HTMLInputElement>(null)
  const cameraInput = useRef<HTMLInputElement>(null)
  const [file, setFile] = useState<File | null>(null)
  const [preview, setPreview] = useState<string | null>(null)
  const [consent, setConsent] = useState<boolean | null>(null)
  const [askingConsent, setAskingConsent] = useState(false)
  const [analyzing, setAnalyzing] = useState(false)
  const [analysis, setAnalysis] = useState<MealPhotoAnalysis | null>(null)

  useEffect(() => {
    api.me().then((me) => setConsent(me.meal_photo_ai_consent)).catch(() => setConsent(false))
  }, [])

  useEffect(() => () => {
    if (preview) URL.revokeObjectURL(preview)
  }, [preview])

  const clear = () => {
    setFile(null)
    setPreview(null)
    setAnalysis(null)
    setAskingConsent(false)
    if (fileInput.current) fileInput.current.value = ''
    if (cameraInput.current) cameraInput.current.value = ''
    onClear()
  }

  const analyze = async (photo: File) => {
    setAnalyzing(true)
    try {
      const result = await api.analyzeMealPhoto(photo, hint.trim() || undefined)
      setAnalysis(result)
      if (result.recognized) onDraft(result)
      else showToast('Không nhận diện được món ăn trong ảnh. Bạn hãy nhập món bằng tay.', 'warning')
    } catch (error) {
      showToast(error instanceof Error ? error.message : 'Không phân tích được ảnh', 'error')
    } finally {
      setAnalyzing(false)
    }
  }

  const select = (picked?: File) => {
    if (!picked) return
    if (!ACCEPTED_TYPES.includes(picked.type)) {
      showToast('Chỉ nhận ảnh JPG, PNG hoặc WebP.', 'error')
      return
    }
    if (picked.size > MAX_PHOTO_BYTES) {
      showToast('Ảnh không được vượt quá 10MB.', 'error')
      return
    }
    setFile(picked)
    setPreview(URL.createObjectURL(picked))
    setAnalysis(null)
    if (consent) void analyze(picked)
    else setAskingConsent(true)
  }

  const setPhotoConsent = async (value: boolean) => {
    try {
      const me = await api.updateMe({ meal_photo_ai_consent: value })
      setConsent(me.meal_photo_ai_consent)
      return true
    } catch (error) {
      showToast(error instanceof Error ? error.message : 'Không lưu được lựa chọn', 'error')
      return false
    }
  }

  const acceptConsent = async () => {
    if (!file || !(await setPhotoConsent(true))) return
    setAskingConsent(false)
    void analyze(file)
  }

  const revokeConsent = async () => {
    if (await setPhotoConsent(false)) {
      showToast('Đã tắt phân tích ảnh bằng AI.', 'success')
      clear()
    }
  }

  return (
    <div className='space-y-3'>
      <input ref={fileInput} className='hidden' type='file' accept={ACCEPTED_TYPES.join(',')} onChange={(event) => select(event.target.files?.[0])} />
      <input ref={cameraInput} className='hidden' type='file' accept={ACCEPTED_TYPES.join(',')} capture='environment' onChange={(event) => select(event.target.files?.[0])} />
      {preview ? (
        <div className='relative h-48 overflow-hidden rounded-2xl border border-border bg-muted'>
          {/* eslint-disable-next-line @next/next/no-img-element -- local object URL, nothing for next/image to optimize */}
          <img src={preview} alt='Ảnh bữa ăn xem trước' className='h-full w-full object-cover' />
          <button type='button' onClick={clear} aria-label='Bỏ ảnh' className='absolute right-2 top-2 rounded-full bg-black/60 p-2 text-white'><X className='h-4 w-4' /></button>
          {analyzing && <span className='absolute inset-x-0 bottom-0 flex items-center gap-2 bg-black/60 px-3 py-2 text-xs text-white'><Loader2 className='h-3.5 w-3.5 animate-spin' /> Đang nhận diện món ăn...</span>}
        </div>
      ) : (
        <div className='rounded-2xl border-2 border-dashed border-border bg-muted/30 p-5 text-center'>
          <ImagePlus className='mx-auto h-7 w-7 text-muted-foreground' />
          <p className='mt-2 text-sm font-medium'>Chụp ảnh để AI điền giúp món ăn</p>
          <p className='mt-1 text-xs text-muted-foreground'>JPG, PNG hoặc WebP · tối đa 10MB · ảnh không được lưu lại</p>
          <button type='button' onClick={() => fileInput.current?.click()} className='mx-auto mt-3 hidden items-center gap-2 rounded-xl bg-primary px-4 py-2 text-sm font-medium text-primary-foreground md:flex'><Upload className='h-4 w-4' /> Chọn ảnh từ máy</button>
          <button type='button' onClick={() => cameraInput.current?.click()} className='mx-auto mt-3 flex items-center gap-2 rounded-xl bg-primary px-4 py-2 text-sm font-medium text-primary-foreground md:hidden'><Camera className='h-4 w-4' /> Chụp bằng camera sau</button>
        </div>
      )}

      {askingConsent && (
        <div className='space-y-3 rounded-2xl border border-border bg-accent/40 p-4 text-sm'>
          <p className='font-medium'>Gửi ảnh cho AI nhận diện món ăn?</p>
          <p className='text-xs text-muted-foreground'>Ảnh sẽ được gửi tới nhà cung cấp mô hình AI (OpenAI) để nhận diện món và khẩu phần, rồi bị bỏ đi — hệ thống không lưu ảnh. Chỉ phần chữ bạn xác nhận mới được lưu vào nhật ký. Bạn có thể tắt lại bất cứ lúc nào.</p>
          <div className='flex flex-wrap gap-2'>
            <button type='button' onClick={acceptConsent} className='rounded-xl bg-primary px-4 py-2 text-sm font-medium text-primary-foreground'>Đồng ý và phân tích</button>
            <button type='button' onClick={clear} className='rounded-xl border border-border px-4 py-2 text-sm'>Không, tôi tự nhập</button>
          </div>
        </div>
      )}

      {analysis?.recognized && (
        <div className='rounded-2xl border border-border p-4 text-sm'>
          <p className='flex items-center gap-2 font-medium'><Sparkles className='h-4 w-4 text-primary' /> AI nhận diện (bản nháp)</p>
          <ul className='mt-2 list-disc space-y-0.5 pl-5'>
            {analysis.items.map((item, index) => (
              <li key={`${item.name}-${index}`}>{item.name}{item.quantity !== null && ` — ${formatQuantity(item.quantity)}${item.unit ? ` ${item.unit}` : ''}`}</li>
            ))}
          </ul>
          <p className='mt-2 text-xs text-muted-foreground'>Đã điền vào ô bên dưới. Hãy kiểm tra và sửa nếu chưa đúng trước khi lưu. Đây là ước tính, không thay thế chuyên gia dinh dưỡng.</p>
        </div>
      )}

      {consent && !askingConsent && (
        <button type='button' onClick={revokeConsent} className='text-xs text-muted-foreground underline'>Tắt phân tích ảnh bằng AI</button>
      )}
    </div>
  )
}
