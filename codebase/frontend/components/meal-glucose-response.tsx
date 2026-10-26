'use client'

import { useEffect, useState } from 'react'
import { Loader2, UtensilsCrossed } from 'lucide-react'
import { api, type MealGlucoseResponse } from '@/lib/api'
import { CarbLoadBadge } from '@/components/meal-analysis-card'

const formatNumber = (value: number) => String(Math.round(value)).replace('.', ',')

// Which foods / carb bands tend to go with high post-meal glucose (Epic 1.4).
// Association only — the copy says "thường đi kèm", never "gây ra". Shared
// by the patient's meals page and the doctor's patient page.
export function MealGlucoseResponsePanel({ patientId, audience }: { patientId?: string; audience: 'patient' | 'doctor' }) {
  const [data, setData] = useState<MealGlucoseResponse | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    let cancelled = false
    api.mealGlucoseResponse(patientId)
      .then((result) => { if (!cancelled) setData(result) })
      .catch((err) => { if (!cancelled) setError(err instanceof Error ? err.message : 'Không tải được dữ liệu') })
      .finally(() => { if (!cancelled) setLoading(false) })
    return () => { cancelled = true }
  }, [patientId])

  const title = audience === 'patient' ? 'Món ảnh hưởng đường huyết của bạn' : 'Bữa ăn liên quan tăng đường huyết'
  const subject = audience === 'patient' ? 'bạn' : 'bệnh nhân'

  return (
    <section className='space-y-3 rounded-2xl border border-border bg-card p-5'>
      <div>
        <h3 className='flex items-center gap-2 font-semibold'><UtensilsCrossed className='h-4 w-4 text-primary' aria-hidden /> {title}</h3>
        <p className='mt-1 text-xs text-muted-foreground'>
          Ghép mỗi bữa ăn với lần đo đường huyết sau ăn 1–3 giờ{data ? ` trong ${data.days} ngày qua` : ''}. Chỉ hiện món có từ {data?.min_n ?? 3} bữa trở lên. Đây là mối liên quan, không phải nguyên nhân.
        </p>
      </div>

      {loading && <div className='flex justify-center py-4'><Loader2 className='h-5 w-5 animate-spin text-primary' /></div>}
      {error && <p className='text-sm text-muted-foreground'>{error}</p>}

      {data && !loading && (data.by_food.length === 0 && data.by_carb_load.length === 0 ? (
        <p className='text-sm text-muted-foreground'>
          Chưa đủ dữ liệu ({data.meals_paired} bữa có đo đường huyết sau ăn). Ghi bữa ăn và đo đường huyết &quot;Sau ăn&quot; khoảng 2 giờ sau bữa để xem món nào thường đi kèm đường huyết cao.
        </p>
      ) : (
        <div className='space-y-4'>
          {data.by_food.length > 0 && (
            <ul className='space-y-2'>
              {data.by_food.map((group) => (
                <li key={group.food_id} className='rounded-xl border border-border p-3 text-sm'>
                  <div className='flex flex-wrap items-baseline justify-between gap-2'>
                    <span className='font-medium'>{group.name}</span>
                    <span className='text-xs text-muted-foreground'>{group.n} bữa</span>
                  </div>
                  <p className='mt-1 text-xs text-muted-foreground'>
                    Đường huyết sau ăn trung bình <strong className='text-foreground'>{formatNumber(group.avg_post_mg_dl)} mg/dL</strong>
                    {group.avg_delta_mg_dl !== null && <> · tăng trung bình {formatNumber(group.avg_delta_mg_dl)} mg/dL so với trước ăn ({group.n_with_delta} bữa)</>}
                    {' · '}<strong className='text-foreground'>{formatNumber(group.pct_over_target)}%</strong> số bữa vượt {formatNumber(data.post_prandial_target_mg_dl)} mg/dL
                  </p>
                  {group.pct_over_target >= 50 && <p className='mt-1 text-xs'>Món này thường đi kèm đường huyết sau ăn cao ở {subject}.</p>}
                </li>
              ))}
            </ul>
          )}
          {data.by_carb_load.length > 0 && (
            <div className='space-y-2'>
              <p className='text-xs font-medium text-muted-foreground'>Theo mức carb của bữa</p>
              <ul className='space-y-1.5'>
                {data.by_carb_load.map((group) => (
                  <li key={group.carb_load} className='flex flex-wrap items-center gap-2 text-xs'>
                    <CarbLoadBadge load={group.carb_load} />
                    <span>{group.n} bữa · TB {formatNumber(group.avg_post_mg_dl)} mg/dL · {formatNumber(group.pct_over_target)}% vượt mục tiêu</span>
                  </li>
                ))}
              </ul>
            </div>
          )}
          <p className='text-xs text-muted-foreground'>
            {audience === 'patient'
              ? 'Hãy trao đổi với bác sĩ hoặc chuyên gia dinh dưỡng trước khi thay đổi chế độ ăn hay thuốc.'
              : 'Mối liên quan thống kê từ nhật ký tự ghi; carb là ước tính từ bảng tra chưa đối chiếu Bảng thành phần thực phẩm VN.'}
          </p>
        </div>
      ))}
    </section>
  )
}
