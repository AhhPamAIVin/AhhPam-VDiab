'use client'

import { useEffect, useState } from 'react'
import Link from 'next/link'
import { ChevronRight, FileBarChart } from 'lucide-react'
import { api, type WeeklyReportListItem } from '@/lib/api'

// "Báo cáo mới" card on the patient dashboard (Epic 2.5): shown only while
// the latest weekly report is unread.
export function WeeklyReportBadge() {
  const [unread, setUnread] = useState<WeeklyReportListItem | null>(null)

  useEffect(() => {
    let cancelled = false
    api.weeklyReports()
      .then((list) => { if (!cancelled) setUnread(list[0] && !list[0].read_at ? list[0] : null) })
      .catch(() => {})
    return () => { cancelled = true }
  }, [])

  if (!unread) return null
  const [, m1, d1] = unread.week_start.split('-')
  const [, m2, d2] = unread.week_end.split('-')
  return (
    <Link href='/reports' className='flex items-center gap-3 rounded-2xl border border-primary/30 bg-accent/50 p-4 text-sm'>
      <FileBarChart className='h-5 w-5 shrink-0 text-primary' aria-hidden />
      <span className='flex-1'><strong>Báo cáo tuần mới</strong> · tuần {d1}/{m1} – {d2}/{m2} đã sẵn sàng</span>
      <ChevronRight className='h-4 w-4 text-muted-foreground' aria-hidden />
    </Link>
  )
}
