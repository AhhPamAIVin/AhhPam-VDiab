'use client'

import Image from 'next/image'
import { useAuth } from '@/lib/auth-context'
import { BadgeCheck, CalendarDays, KeyRound, LogOut, Mail, ShieldCheck, Stethoscope, User } from 'lucide-react'

const roleDetails = {
  doctor: { label: 'Bác sĩ', description: 'Tài khoản chuyên môn', icon: Stethoscope },
  admin: { label: 'Quản trị viên', description: 'Tài khoản quản trị hệ thống', icon: ShieldCheck },
} as const

export function AccountPage() {
  const { user, logout } = useAuth()
  if (!user || user.role === 'patient') return null

  const role = roleDetails[user.role]
  const RoleIcon = role.icon
  const avatarSrc = user.role === 'doctor' ? '/doctor.png' : '/administrator.png'

  return (
    <div className="mx-auto max-w-4xl space-y-5">
      <section className="overflow-hidden rounded-2xl border border-border bg-card">
        <div className="h-28 bg-gradient-to-r from-primary via-[#55c4bd] to-[#8eddd8]" />
        <div className="px-5 pb-6 sm:px-7">
          <div className="flex flex-col gap-4 pt-5 sm:flex-row sm:items-end sm:justify-between">
            <div className="flex items-end gap-4">
              <div className="relative -mt-14 h-20 w-20 overflow-hidden rounded-2xl border-4 border-card bg-accent shadow-sm">
                <Image src={avatarSrc} alt={role.label} fill sizes="80px" className="object-cover" priority />
              </div>
              <div className="min-w-0 pb-1">
                <h2 className="truncate text-xl font-bold text-foreground">{user.name}</h2>
                <p className="mt-1 text-sm text-muted-foreground">{role.description}</p>
              </div>
            </div>
            <span className="inline-flex w-fit items-center gap-1.5 rounded-full bg-accent px-3 py-1.5 text-xs font-semibold text-accent-foreground">
              <BadgeCheck className="h-4 w-4" /> Đang hoạt động
            </span>
          </div>
        </div>
      </section>

      <div className="grid gap-5 lg:grid-cols-[1.45fr_1fr]">
        <section className="rounded-2xl border border-border bg-card">
          <div className="border-b border-border px-5 py-4">
            <h3 className="font-semibold text-foreground">Thông tin tài khoản</h3>
            <p className="mt-1 text-xs text-muted-foreground">Thông tin định danh và quyền truy cập của bạn</p>
          </div>
          <div className="divide-y divide-border">
            <AccountRow icon={User} label="Họ và tên" value={user.name} />
            <AccountRow icon={Mail} label="Email đăng nhập" value={user.email} />
            <AccountRow icon={RoleIcon} label="Vai trò" value={role.label} />
          </div>
        </section>

        <div className="space-y-5">
          <section className="rounded-2xl border border-border bg-card p-5">
            <div className="mb-4 flex h-10 w-10 items-center justify-center rounded-xl bg-accent"><KeyRound className="h-5 w-5 text-primary" /></div>
            <h3 className="font-semibold text-foreground">Bảo mật</h3>
            <p className="mt-1 text-sm leading-5 text-muted-foreground">Mật khẩu giúp bảo vệ dữ liệu và quyền truy cập tài khoản.</p>
            <button type="button" className="mt-4 w-full rounded-xl border border-border px-4 py-2.5 text-sm font-medium text-foreground transition-colors hover:bg-muted">Đổi mật khẩu</button>
          </section>

          <section className="rounded-2xl border border-border bg-card p-5">
            <div className="flex items-start gap-3">
              <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded-xl bg-green-50 text-green-600"><CalendarDays className="h-4 w-4" /></div>
              <div><h3 className="text-sm font-semibold text-foreground">Phiên đăng nhập hiện tại</h3><p className="mt-1 text-xs leading-5 text-muted-foreground">Tài khoản đang được xác thực và hoạt động bình thường.</p></div>
            </div>
          </section>

          <button type="button" onClick={logout} className="flex w-full items-center justify-center gap-2 rounded-xl border border-red-200 bg-card px-4 py-3 text-sm font-medium text-red-600 transition-colors hover:bg-red-50">
            <LogOut className="h-4 w-4" /> Đăng xuất khỏi tài khoản
          </button>
        </div>
      </div>
    </div>
  )
}

function AccountRow({ icon: Icon, label, value, mono = false }: { icon: typeof User; label: string; value: string; mono?: boolean }) {
  return (
    <div className="flex items-center gap-4 px-5 py-4">
      <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded-xl bg-accent"><Icon className="h-4 w-4 text-primary" /></div>
      <div className="min-w-0 flex-1"><p className="text-xs text-muted-foreground">{label}</p><p className={`mt-1 truncate text-sm font-medium text-foreground ${mono ? 'font-mono text-xs' : ''}`}>{value}</p></div>
    </div>
  )
}
