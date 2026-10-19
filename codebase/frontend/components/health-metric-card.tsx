'use client'

import { cn } from '@/lib/utils'
import { TrendingUp, TrendingDown, Minus } from 'lucide-react'

interface HealthMetricCardProps {
  title: string
  value: string | number
  unit: string
  updatedAt: string
  status: 'normal' | 'high' | 'low' | 'critical'
  statusLabel?: string
  trend?: 'up' | 'down' | 'stable'
  trendValue?: string
  icon: React.ReactNode
  iconBg?: string
  onClick?: () => void
  featured?: boolean
  featuredStats?: Array<{ label: string; value: string | number; tone: 'high' | 'low' }>
  extra?: React.ReactNode
  compactMobile?: boolean
}

const statusStyles = {
  normal:   { border: 'border-transparent', value: 'text-foreground' },
  high:     { border: 'border-orange-200', value: 'text-orange-600' },
  low:      { border: 'border-blue-200',   value: 'text-blue-600' },
  critical: { border: 'border-red-200',    value: 'text-red-600' },
}

const featuredStatusStyles = {
  normal:   { card: 'border-[#43B5AE]/40 bg-[#EAF8F6] dark:border-teal-700 dark:bg-teal-950/45', value: 'text-[#238D87] dark:text-teal-300', trend: 'text-[#238D87] dark:text-teal-300' },
  high:     { card: 'border-amber-300 bg-amber-50 dark:border-amber-700 dark:bg-amber-950/45', value: 'text-amber-700 dark:text-amber-300', trend: 'text-amber-700 dark:text-amber-300' },
  low:      { card: 'border-amber-300 bg-amber-50 dark:border-amber-700 dark:bg-amber-950/45', value: 'text-amber-700 dark:text-amber-300', trend: 'text-amber-700 dark:text-amber-300' },
  critical: { card: 'border-red-300 bg-red-50 dark:border-red-800 dark:bg-red-950/45', value: 'text-red-700 dark:text-red-300', trend: 'text-red-700 dark:text-red-300' },
}

const statusBadgeStyles = {
  normal: 'bg-[#D7F2EE] text-[#238D87] dark:bg-teal-900/70 dark:text-teal-200',
  high: 'bg-amber-100 text-amber-700 dark:bg-amber-900/70 dark:text-amber-200',
  low: 'bg-blue-100 text-blue-700 dark:bg-blue-900/70 dark:text-blue-200',
  critical: 'bg-red-100 text-red-700 dark:bg-red-900/70 dark:text-red-200',
}

export function HealthMetricCard({
  title, value, unit, updatedAt, status, statusLabel, trend, trendValue, icon, iconBg = 'bg-accent', onClick, featured = false, featuredStats, extra, compactMobile = false,
}: HealthMetricCardProps) {
  const styles = statusStyles[status]
  const featuredStyles = featuredStatusStyles[status]
  const TrendIcon = trend === 'up' ? TrendingUp : trend === 'down' ? TrendingDown : Minus
  const trendColor = trend === 'up' ? 'text-orange-500' : trend === 'down' ? 'text-[#43B5AE]' : 'text-gray-400'

  return (
    <button
      onClick={onClick}
      className={cn(
        'bg-card rounded-2xl p-4 border text-left transition-all hover:shadow-md hover:-translate-y-0.5 active:translate-y-0 w-full',
        featured && 'p-5 lg:p-6 flex flex-col justify-between',
        compactMobile && 'max-lg:p-3',
        featured ? featuredStyles.card : styles.border,
        !onClick && 'cursor-default hover:shadow-none hover:translate-y-0',
      )}
    >
      <div className={cn('flex items-start justify-between mb-3', compactMobile && 'max-lg:mb-2')}>
        <div className={cn('w-10 h-10 rounded-xl flex items-center justify-center flex-shrink-0', compactMobile && 'max-lg:h-8 max-lg:w-8 max-lg:rounded-lg', iconBg)}>
          {icon}
        </div>
        {trend && (
          <div className={cn('flex items-center gap-1 text-xs font-semibold', featured ? featuredStyles.trend : trendColor)}>
            <TrendIcon className="w-3 h-3" />
            {trendValue}
          </div>
        )}
      </div>
      <div className={cn(featured && featuredStats?.length && 'grid grid-cols-[minmax(0,1fr)_auto] items-start gap-4 lg:block')}>
        <div className="min-w-0">
          <div className={cn(compactMobile && statusLabel && 'max-lg:flex max-lg:items-center max-lg:justify-between max-lg:gap-2')}>
            <div className={cn('text-2xl font-bold mb-0.5', featured && 'text-5xl lg:text-6xl tracking-tight', compactMobile && 'max-lg:text-xl', featured ? featuredStyles.value : styles.value)}>
              {value} <span className={cn('text-sm font-medium text-muted-foreground', featured && 'text-base lg:text-lg')}>{unit}</span>
            </div>
            {compactMobile && statusLabel && (
              <span className={cn('hidden shrink-0 rounded-full px-3.5 py-2 text-sm font-bold shadow-sm ring-1 ring-current/20 max-lg:inline-flex', statusBadgeStyles[status])}>
                {statusLabel}
              </span>
            )}
          </div>
          {statusLabel && (
            <div className={cn('mb-2 mt-2 flex flex-wrap items-center gap-2', compactMobile && 'max-lg:hidden')}>
              <span className="text-xs font-medium text-muted-foreground">Trạng thái</span>
              <span className={cn('rounded-full px-2.5 py-1 text-xs font-semibold', statusBadgeStyles[status])}>
                {statusLabel}
              </span>
            </div>
          )}
          <div className={cn('text-sm font-medium text-foreground mb-1', featured && 'text-base lg:text-lg font-semibold', compactMobile && 'max-lg:mb-0 max-lg:text-xs')}>{title}</div>
          <div className={cn('text-xs text-muted-foreground', compactMobile && 'max-lg:hidden')}>{updatedAt}</div>
        </div>
        {featured && featuredStats && featuredStats.length > 0 && (
          <div className={cn('grid min-w-[7.5rem] gap-2 lg:hidden', compactMobile && 'max-lg:hidden')}>
            {featuredStats.map((stat) => (
              <div
                key={stat.label}
                className={cn(
                  'rounded-xl px-3 py-2.5',
                  stat.tone === 'high' ? 'bg-amber-100/80 text-amber-800 dark:bg-amber-900/60 dark:text-amber-200' : 'bg-sky-100/80 text-sky-800 dark:bg-sky-900/60 dark:text-sky-200',
                )}
              >
                <div className="text-[10px] font-semibold uppercase tracking-wide opacity-75">{stat.label}</div>
                <div className="mt-0.5 text-base font-bold leading-none">
                  {stat.value} <span className="text-[10px] font-semibold">mg/dL</span>
                </div>
              </div>
            ))}
          </div>
        )}
      </div>
      {featured && featuredStats && featuredStats.length > 0 && (
        <div className="mt-4 hidden grid-cols-2 gap-2 border-t border-current/10 pt-4 lg:grid">
          {featuredStats.map((stat) => (
            <div
              key={stat.label}
              className={cn(
                'rounded-xl px-3 py-2.5',
                stat.tone === 'high' ? 'bg-amber-100/80 text-amber-800 dark:bg-amber-900/60 dark:text-amber-200' : 'bg-sky-100/80 text-sky-800 dark:bg-sky-900/60 dark:text-sky-200',
              )}
            >
              <div className="text-[11px] font-semibold uppercase tracking-wide opacity-75">{stat.label}</div>
              <div className="mt-0.5 text-lg font-bold leading-none">
                {stat.value} <span className="text-[11px] font-semibold">mg/dL</span>
              </div>
            </div>
          ))}
        </div>
      )}
      {featured && extra && <div className={cn(compactMobile && 'max-lg:hidden')}>{extra}</div>}
    </button>
  )
}
