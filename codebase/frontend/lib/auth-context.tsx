'use client'

import React, { createContext, useContext, useState, useCallback, useEffect } from 'react'
import { useRouter } from 'next/navigation'
import { api, ApiUser, clearToken, getToken, loginRequest, storeToken } from '@/lib/api'

export type UserRole = 'patient' | 'doctor' | 'admin'

export interface AuthUser {
  id: string
  name: string
  email: string
  role: UserRole
  avatar?: string | null
  mustChangePassword?: boolean
}

interface AuthContextValue {
  user: AuthUser | null
  loading: boolean
  login: (email: string, password: string, remember?: boolean) => Promise<AuthUser>
  loginDemo: (role: UserRole) => void
  logout: () => void
}

const DEMO_USERS: Record<UserRole, AuthUser> = {
  patient: { id: 'p1', name: 'Nguyễn Văn An',  email: 'an.nguyen@email.com',    role: 'patient', avatar: null },
  doctor:  { id: 'd1', name: 'BS. Trần Minh',   email: 'minh.tran@hospital.com', role: 'doctor',  avatar: null },
  admin:   { id: 'a1', name: 'Admin Hệ Thống',  email: 'admin@vdiab.vn',         role: 'admin',   avatar: null },
}

const HOME_BY_ROLE: Record<UserRole, string> = {
  patient: '/dashboard',
  doctor:  '/doctor',
  admin:   '/admin',
}

const SESSION_KEY = 'vdiab_user'

function toAuthUser(user: ApiUser): AuthUser {
  return { id: user.id, name: user.full_name, email: user.email, role: user.role, avatar: null, mustChangePassword: user.must_change_password }
}

const AuthContext = createContext<AuthContextValue | null>(null)

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const router = useRouter()
  const [user, setUser] = useState<AuthUser | null>(null)
  const [loading, setLoading] = useState(true)

  // Rehydrate from sessionStorage on mount
  useEffect(() => {
    try {
      const stored = localStorage.getItem(SESSION_KEY) || sessionStorage.getItem(SESSION_KEY)
      if (stored && getToken()) setUser(JSON.parse(stored))
    } catch {}
    if (getToken()) api.me().then((me) => setUser(toAuthUser(me))).catch(() => { clearToken(); setUser(null) }).finally(() => setLoading(false))
    else setLoading(false)
  }, [])

  const login = useCallback(async (email: string, password: string, remember = false) => {
    const token = await loginRequest(email, password)
    storeToken(token.access_token, remember)
    const me = await api.me()
    const u = toAuthUser(me)
    setUser(u)
    const storage = remember ? localStorage : sessionStorage
    storage.setItem(SESSION_KEY, JSON.stringify(u))
    router.push(HOME_BY_ROLE[u.role])
    return u
  }, [router])

  const loginDemo = useCallback((role: UserRole) => {
    const u = DEMO_USERS[role]
    setUser(u)
    try { sessionStorage.setItem(SESSION_KEY, JSON.stringify(u)) } catch {}
    router.push(HOME_BY_ROLE[role])
  }, [router])

  const logout = useCallback(() => {
    setUser(null)
    clearToken()
    try { sessionStorage.removeItem(SESSION_KEY); localStorage.removeItem(SESSION_KEY) } catch {}
    router.push('/login')
  }, [router])

  useEffect(() => {
    const unauthorized = () => logout()
    window.addEventListener('vdiab:unauthorized', unauthorized)
    return () => window.removeEventListener('vdiab:unauthorized', unauthorized)
  }, [logout])

  return (
    <AuthContext.Provider value={{ user, loading, login, loginDemo, logout }}>
      {children}
    </AuthContext.Provider>
  )
}

export function useAuth() {
  const ctx = useContext(AuthContext)
  if (!ctx) throw new Error('useAuth must be used inside AuthProvider')
  return ctx
}
