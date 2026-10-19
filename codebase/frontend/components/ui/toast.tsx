'use client'

import { useState, useEffect, createContext, useContext, useCallback } from 'react'
import { cn } from '@/lib/utils'
import { CheckCircle2, XCircle, X, AlertCircle, Info } from 'lucide-react'

type ToastType = 'success' | 'error' | 'warning' | 'info'

interface Toast {
  id: string
  message: string
  type: ToastType
}

interface ToastContextValue {
  showToast: (message: string, type?: ToastType) => void
}

const ToastContext = createContext<ToastContextValue | null>(null)

const icons = {
  success: CheckCircle2,
  error:   XCircle,
  warning: AlertCircle,
  info:    Info,
}

const styles = {
  success: 'bg-[#DFF7F3] text-[#2D9D97] border-[#43B5AE]/30',
  error:   'bg-red-50 text-red-700 border-red-200',
  warning: 'bg-orange-50 text-orange-700 border-orange-200',
  info:    'bg-blue-50 text-blue-700 border-blue-200',
}

export function ToastProvider({ children }: { children: React.ReactNode }) {
  const [toasts, setToasts] = useState<Toast[]>([])

  const showToast = useCallback((message: string, type: ToastType = 'success') => {
    const id = Math.random().toString(36).slice(2)
    setToasts((prev) => [...prev, { id, message, type }])
    setTimeout(() => setToasts((prev) => prev.filter((t) => t.id !== id)), 3500)
  }, [])

  return (
    <ToastContext.Provider value={{ showToast }}>
      {children}
      <div className="fixed bottom-20 lg:bottom-6 left-0 right-0 flex flex-col items-center gap-2 z-50 pointer-events-none px-4">
        {toasts.map((t) => {
          const Icon = icons[t.type]
          return (
            <div
              key={t.id}
              className={cn(
                'flex items-center gap-2.5 px-4 py-3 rounded-2xl border shadow-lg pointer-events-auto max-w-sm w-full text-sm font-medium',
                styles[t.type],
              )}
            >
              <Icon className="w-4 h-4 flex-shrink-0" />
              <span className="flex-1">{t.message}</span>
              <button onClick={() => setToasts((p) => p.filter((x) => x.id !== t.id))}>
                <X className="w-3.5 h-3.5 opacity-60 hover:opacity-100" />
              </button>
            </div>
          )
        })}
      </div>
    </ToastContext.Provider>
  )
}

export function useToast() {
  const ctx = useContext(ToastContext)
  if (!ctx) throw new Error('useToast must be inside ToastProvider')
  return ctx
}
