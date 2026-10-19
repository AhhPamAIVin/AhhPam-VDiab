'use client'

import { cn } from '@/lib/utils'
import { LayoutDashboard, Activity, Plus, Bot, User } from 'lucide-react'
import { useRouter, usePathname } from 'next/navigation'
import { RecordModal } from '@/components/record-modal'
import { useState } from 'react'

const tabs = [
  { icon: LayoutDashboard, label: 'Trang chủ', href: '/dashboard' },
  { icon: Activity,        label: 'Theo dõi',  href: '/health' },
  { icon: null,            label: 'Ghi chỉ số', href: null },
  { icon: Bot,             label: 'AI',        href: '/ai' },
  { icon: User,            label: 'Tài khoản', href: '/profile' },
]

export function MobileBottomNav() {
  const router = useRouter()
  const pathname = usePathname()
  const [recordOpen, setRecordOpen] = useState(false)

  return (
    <>
      <nav className="fixed bottom-0 left-0 right-0 z-40 lg:hidden bg-card border-t border-border">
        <div className="flex items-end h-16">
          {tabs.map((tab, i) => {
            if (tab.href === null) {
              return (
                <div key="fab" className="flex-1 flex justify-center -translate-y-3">
                  <button
                    onClick={() => setRecordOpen(true)}
                    className="w-14 h-14 rounded-full bg-primary text-primary-foreground flex items-center justify-center shadow-lg shadow-primary/30 hover:bg-[#2D9D97] transition-colors active:scale-95"
                    aria-label="Ghi chỉ số"
                  >
                    <Plus className="w-6 h-6" />
                  </button>
                </div>
              )
            }

            const isActive = pathname === tab.href
            const Icon = tab.icon!

            return (
              <button
                key={tab.href}
                onClick={() => router.push(tab.href!)}
                className={cn(
                  'flex-1 flex flex-col items-center justify-center gap-0.5 h-full text-xs font-medium transition-colors',
                  isActive ? 'text-primary' : 'text-muted-foreground',
                )}
              >
                <Icon className="w-5 h-5" />
                <span>{tab.label}</span>
              </button>
            )
          })}
        </div>
      </nav>
      <RecordModal open={recordOpen} onClose={() => setRecordOpen(false)} />
    </>
  )
}
