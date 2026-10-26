'use client'

import { useState } from 'react'
import { AlertTriangle, ArrowDown, ArrowUp, Check, HelpCircle, Loader2, Minus, Pencil, Plus, RefreshCw, Trash2 } from 'lucide-react'
import { api, type CarbLoad, type MealAnalysis, type MealItemEdit } from '@/lib/api'
import { useToast } from '@/components/ui/toast'

// Never colour alone (PRD NFR): every level has a text label and its own icon.
export const CARB_LOAD_DISPLAY: Record<CarbLoad, { label: string; icon: typeof ArrowUp; className: string }> = {
  low: { label: 'Carb thấp', icon: ArrowDown, className: 'bg-[#DFF7F3] text-[#1F7F79]' },
  medium: { label: 'Carb vừa', icon: Minus, className: 'bg-yellow-50 text-yellow-800' },
  high: { label: 'Carb cao', icon: ArrowUp, className: 'bg-orange-50 text-orange-800' },
  unknown: { label: 'Chưa rõ carb', icon: HelpCircle, className: 'bg-muted text-muted-foreground' },
}

const GI_LABEL: Record<string, string> = { low: 'GI thấp', medium: 'GI trung bình', high: 'GI cao' }
const STATUS_LABEL: Record<MealAnalysis['status'], string> = {
  draft: 'Bản nháp — chưa xác nhận',
  edited: 'Bạn đã sửa — chưa xác nhận',
  confirmed: 'Đã xác nhận',
}

const formatNumber = (value: number) => String(Math.round(value * 10) / 10).replace('.', ',')

export function CarbLoadBadge({ load }: { load: CarbLoad }) {
  const display = CARB_LOAD_DISPLAY[load]
  const Icon = display.icon
  return (
    <span className={`inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-xs font-medium ${display.className}`}>
      <Icon className='h-3 w-3' aria-hidden /> {display.label}
    </span>
  )
}

export function carbTotalText(analysis: MealAnalysis): string {
  if (analysis.total_carbs_g === null) return 'Chưa ước tính được carb'
  const total = `${formatNumber(analysis.total_carbs_g)} g carb`
  return analysis.has_unmatched ? `ít nhất ${total} (có món chưa tra được)` : `khoảng ${total}`
}

// The analysis of one meal: items, estimated carbs, load band. Patients can
// edit / confirm / rerun; readOnly is the doctor's view (and shows kcal).
export function MealAnalysisCard({ mealId, analysis, onChange, readOnly = false, showKcal = false }: {
  mealId: string
  analysis: MealAnalysis | null
  onChange?: (analysis: MealAnalysis) => void
  readOnly?: boolean
  showKcal?: boolean
}) {
  const { showToast } = useToast()
  const [busy, setBusy] = useState<'rerun' | 'save' | 'confirm' | null>(null)
  const [editing, setEditing] = useState<MealItemEdit[] | null>(null)

  const run = async (kind: 'rerun' | 'save' | 'confirm', call: () => Promise<MealAnalysis>, done: string) => {
    setBusy(kind)
    try {
      onChange?.(await call())
      setEditing(null)
      showToast(done, 'success')
    } catch (error) {
      showToast(error instanceof Error ? error.message : 'Không thực hiện được', 'error')
    } finally {
      setBusy(null)
    }
  }

  if (!analysis) {
    return (
      <div className='rounded-xl border border-dashed border-border p-3 text-xs text-muted-foreground'>
        <p>Chưa có phân tích dinh dưỡng cho bữa này (đang xử lý hoặc chưa chạy).</p>
        {!readOnly && (
          <button type='button' disabled={busy !== null} onClick={() => run('rerun', () => api.rerunMealAnalysis(mealId), 'Đã phân tích bữa ăn.')} className='mt-2 inline-flex items-center gap-1.5 rounded-lg border border-border px-3 py-1.5 font-medium text-foreground disabled:opacity-60'>
            {busy === 'rerun' ? <Loader2 className='h-3.5 w-3.5 animate-spin' /> : <RefreshCw className='h-3.5 w-3.5' />} Phân tích ngay
          </button>
        )}
      </div>
    )
  }

  const updateRow = (index: number, patch: Partial<MealItemEdit>) =>
    setEditing((rows) => rows && rows.map((row, i) => (i === index ? { ...row, ...patch } : row)))

  const saveEdit = () => {
    const rows = (editing ?? [])
      .map((row) => ({ ...row, name: row.name.trim(), unit: row.unit?.trim() || null }))
      .filter((row) => row.name)
    if (!rows.length) {
      showToast('Cần ít nhất một món.', 'warning')
      return
    }
    void run('save', () => api.editMealAnalysis(mealId, rows), 'Đã cập nhật phân tích.')
  }

  return (
    <div className='space-y-3 rounded-xl border border-border p-3 text-sm'>
      <div className='flex flex-wrap items-center justify-between gap-2'>
        <p className='font-medium text-foreground'>{carbTotalText(analysis)}{showKcal && analysis.total_kcal !== null && ` · ${Math.round(analysis.total_kcal)} kcal`}</p>
        <CarbLoadBadge load={analysis.carb_load} />
      </div>

      {editing ? (
        <div className='space-y-2'>
          {editing.map((row, index) => (
            <div key={index} className='flex flex-wrap items-center gap-2'>
              <input aria-label='Số lượng' type='number' min={0} step={0.5} value={row.quantity ?? ''} onChange={(e) => updateRow(index, { quantity: e.target.value ? Number(e.target.value) : null })} className='w-16 rounded-lg border border-border bg-background px-2 py-1.5 text-sm' />
              <input aria-label='Đơn vị' value={row.unit ?? ''} onChange={(e) => updateRow(index, { unit: e.target.value })} placeholder='bát' maxLength={20} className='w-20 rounded-lg border border-border bg-background px-2 py-1.5 text-sm' />
              <input aria-label='Tên món' value={row.name} onChange={(e) => updateRow(index, { name: e.target.value })} placeholder='Tên món' maxLength={100} className='min-w-0 flex-1 rounded-lg border border-border bg-background px-2 py-1.5 text-sm' />
              <button type='button' aria-label='Xóa món' onClick={() => setEditing((rows) => rows && rows.filter((_, i) => i !== index))} className='rounded-lg p-1.5 text-muted-foreground'><Trash2 className='h-4 w-4' /></button>
            </div>
          ))}
          <div className='flex flex-wrap gap-2'>
            {editing.length < 10 && <button type='button' onClick={() => setEditing((rows) => [...(rows ?? []), { name: '', quantity: 1, unit: null }])} className='inline-flex items-center gap-1 rounded-lg border border-border px-3 py-1.5 text-xs'><Plus className='h-3.5 w-3.5' /> Thêm món</button>}
            <button type='button' disabled={busy !== null} onClick={saveEdit} className='inline-flex items-center gap-1 rounded-lg bg-primary px-3 py-1.5 text-xs font-medium text-primary-foreground disabled:opacity-60'>{busy === 'save' && <Loader2 className='h-3.5 w-3.5 animate-spin' />} Lưu</button>
            <button type='button' onClick={() => setEditing(null)} className='rounded-lg border border-border px-3 py-1.5 text-xs'>Hủy</button>
          </div>
        </div>
      ) : (
        <ul className='space-y-1.5'>
          {analysis.items.map((item, index) => (
            <li key={`${item.name}-${index}`} className='flex flex-wrap items-baseline justify-between gap-x-3 gap-y-0.5'>
              <span className='min-w-0'>
                {item.quantity !== null && `${formatNumber(item.quantity)} ${item.unit ?? ''} `}{item.matched_name ?? item.name}
                {item.matched_name && item.matched_name !== item.name && <span className='text-xs text-muted-foreground'> (bạn ghi: {item.name})</span>}
                {item.portion_estimated && <span className='text-xs text-muted-foreground'> · khẩu phần theo mặc định</span>}
              </span>
              <span className='text-xs text-muted-foreground'>
                {item.carbs_g === null
                  ? <span className='inline-flex items-center gap-1'><AlertTriangle className='h-3 w-3' aria-hidden /> chưa có trong bảng tra</span>
                  : <>{formatNumber(item.carbs_g)} g carb{item.gi_level && ` · ${GI_LABEL[item.gi_level]}`}{showKcal && item.kcal !== null && ` · ${Math.round(item.kcal)} kcal`}</>}
              </span>
            </li>
          ))}
        </ul>
      )}

      <p className='text-xs text-muted-foreground'>{STATUS_LABEL[analysis.status]}. Số liệu là ước tính từ bảng tra dinh dưỡng, không thay thế chuyên gia dinh dưỡng.</p>

      {!readOnly && !editing && (
        <div className='flex flex-wrap gap-2'>
          {analysis.status !== 'confirmed' && (
            <button type='button' disabled={busy !== null} onClick={() => run('confirm', () => api.confirmMealAnalysis(mealId), 'Đã xác nhận.')} className='inline-flex items-center gap-1 rounded-lg bg-primary px-3 py-1.5 text-xs font-medium text-primary-foreground disabled:opacity-60'>
              {busy === 'confirm' ? <Loader2 className='h-3.5 w-3.5 animate-spin' /> : <Check className='h-3.5 w-3.5' />} Xác nhận
            </button>
          )}
          <button type='button' onClick={() => setEditing(analysis.items.map((item) => ({ name: item.matched_name ?? item.name, quantity: item.quantity, unit: item.unit })))} className='inline-flex items-center gap-1 rounded-lg border border-border px-3 py-1.5 text-xs'><Pencil className='h-3.5 w-3.5' /> Sửa</button>
          <button type='button' disabled={busy !== null} onClick={() => run('rerun', () => api.rerunMealAnalysis(mealId), 'Đã phân tích lại.')} className='inline-flex items-center gap-1 rounded-lg border border-border px-3 py-1.5 text-xs disabled:opacity-60'>
            {busy === 'rerun' ? <Loader2 className='h-3.5 w-3.5 animate-spin' /> : <RefreshCw className='h-3.5 w-3.5' />} Phân tích lại
          </button>
        </div>
      )}
    </div>
  )
}
