'use client'

import { cn } from '@/lib/utils'

type StatusType = 'normal' | 'high' | 'low' | 'critical' | 'good' | 'caution' | 'active' | 'locked' | 'warning' | 'info'

const statusConfig: Record<StatusType, { label: string; className: string }> = {
  normal:   { label: 'Bình thường', className: 'bg-[#DFF7F3] text-[#2D9D97]' },
  good:     { label: 'Tốt',         className: 'bg-[#DFF7F3] text-[#2D9D97]' },
  high:     { label: 'Cao',         className: 'bg-orange-50 text-orange-600' },
  low:      { label: 'Thấp',        className: 'bg-blue-50 text-blue-600' },
  critical: { label: 'Nguy hiểm',   className: 'bg-red-50 text-red-600' },
  caution:  { label: 'Chú ý',       className: 'bg-yellow-50 text-yellow-700' },
  active:   { label: 'Hoạt động',   className: 'bg-[#DFF7F3] text-[#2D9D97]' },
  locked:   { label: 'Đã khóa',     className: 'bg-gray-100 text-gray-500' },
  warning:  { label: 'Cảnh báo',    className: 'bg-orange-50 text-orange-600' },
  info:     { label: 'Thông tin',   className: 'bg-blue-50 text-blue-600' },
}

interface StatusBadgeProps {
  status: StatusType
  label?: string
  className?: string
}

export function StatusBadge({ status, label, className }: StatusBadgeProps) {
  const config = statusConfig[status] ?? statusConfig.normal
  return (
    <span className={cn('inline-flex items-center px-2.5 py-0.5 rounded-full text-xs font-medium', config.className, className)}>
      {label ?? config.label}
    </span>
  )
}
