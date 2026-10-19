import { cn } from '@/lib/utils'

interface VDiabLogoProps {
  small?: boolean
  className?: string
}

export function VDiabLogo({ small, className }: VDiabLogoProps) {
  return (
    <div className={cn('flex items-center gap-2', className)}>
      <div className={cn(
        'relative flex items-center justify-center rounded-xl bg-primary text-primary-foreground font-bold flex-shrink-0',
        small ? 'w-7 h-7 text-xs' : 'w-9 h-9 text-sm',
      )}>
        {/* Stylized V with health line */}
        <svg viewBox="0 0 28 28" fill="none" className={cn(small ? 'w-5 h-5' : 'w-7 h-7')}>
          <path d="M6 8L14 20L22 8" stroke="white" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round"/>
          <path d="M10 14H13L15 11L17 17L19 14H22" stroke="white" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" opacity="0.8"/>
        </svg>
      </div>
      {!small && (
        <div>
          <span className="text-lg font-bold text-foreground tracking-tight">VDiab</span>
        </div>
      )}
    </div>
  )
}
