'use client'

import { useEffect, useState } from 'react'
import { Moon, Sun } from 'lucide-react'

const STORAGE_KEY = 'vdiab-patient-theme'
type Theme = 'light' | 'dark'

function preferredTheme(): Theme {
  const saved = window.localStorage.getItem(STORAGE_KEY)
  if (saved === 'light' || saved === 'dark') return saved
  return window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light'
}

function applyTheme(theme: Theme) {
  document.documentElement.classList.toggle('dark', theme === 'dark')
}

export function PatientThemeToggle() {
  const [theme, setTheme] = useState<Theme>('light')
  const [mounted, setMounted] = useState(false)

  useEffect(() => {
    const initialTheme = preferredTheme()
    setTheme(initialTheme)
    applyTheme(initialTheme)
    setMounted(true)
    return () => document.documentElement.classList.remove('dark')
  }, [])

  const toggleTheme = () => {
    const nextTheme = theme === 'dark' ? 'light' : 'dark'
    setTheme(nextTheme)
    applyTheme(nextTheme)
    window.localStorage.setItem(STORAGE_KEY, nextTheme)
  }

  const isDark = mounted && theme === 'dark'
  const label = isDark
    ? 'Chuy\u1ec3n sang giao di\u1ec7n s\u00e1ng'
    : 'Chuy\u1ec3n sang giao di\u1ec7n t\u1ed1i'

  return (
    <button
      type="button"
      onClick={toggleTheme}
      className="relative rounded-xl p-2 text-muted-foreground transition-colors hover:bg-muted hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
      aria-label={label}
      title={label}
    >
      {isDark ? <Sun className="h-5 w-5" /> : <Moon className="h-5 w-5" />}
    </button>
  )
}