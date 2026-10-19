'use client'

import { ChevronLeft, ChevronRight } from 'lucide-react'
import { cn } from '@/lib/utils'

interface PaginationProps {
  page: number
  totalItems: number
  pageSize?: number
  onPageChange: (page: number) => void
  className?: string
}

export function Pagination({ page, totalItems, pageSize = 10, onPageChange, className }: PaginationProps) {
  const totalPages = Math.max(1, Math.ceil(totalItems / pageSize))
  if (totalItems <= pageSize) return null

  return (
    <nav className={cn('flex items-center justify-between gap-3 border-t border-border px-4 py-3', className)} aria-label={'Ph\u00e2n trang'}>
      <p className="text-xs text-muted-foreground">
        {`${(page - 1) * pageSize + 1}\u2013${Math.min(page * pageSize, totalItems)} / ${totalItems}`}
      </p>
      <div className="flex items-center gap-1">
        <button type="button" onClick={() => onPageChange(page - 1)} disabled={page <= 1} className="rounded-lg border border-border p-2 text-muted-foreground transition-colors hover:bg-muted hover:text-foreground disabled:pointer-events-none disabled:opacity-40" aria-label={'Trang tr\u01b0\u1edbc'}>
          <ChevronLeft className="h-4 w-4" />
        </button>
        <span className="min-w-20 px-2 text-center text-xs font-medium text-foreground">{`Trang ${page}/${totalPages}`}</span>
        <button type="button" onClick={() => onPageChange(page + 1)} disabled={page >= totalPages} className="rounded-lg border border-border p-2 text-muted-foreground transition-colors hover:bg-muted hover:text-foreground disabled:pointer-events-none disabled:opacity-40" aria-label={'Trang sau'}>
          <ChevronRight className="h-4 w-4" />
        </button>
      </div>
    </nav>
  )
}