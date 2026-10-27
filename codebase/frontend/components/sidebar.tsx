"use client";

import Image from "next/image";
import { cn } from "@/lib/utils";
import { useAuth } from "@/lib/auth-context";
import { useRouter, usePathname, useSearchParams } from "next/navigation";
import { VDiabLogo } from "@/components/vdiab-logo";
import {
  LayoutDashboard,
  Activity,
  Utensils,
  Pill,
  Bot,
  Bell,
  User,
  CalendarClock,
  ShieldCheck,
  Settings,
  BookOpen,
  UserCog,
  X,
  LogOut,
  Link2,
  FileBarChart,
} from "lucide-react";

type NavItem = {
  icon: typeof LayoutDashboard;
  label: string;
  href: string;
  badge?: number;
  section?: string;
};

const patientNav: NavItem[] = [
  { icon: LayoutDashboard, label: "Tổng quan", href: "/dashboard" },
  { icon: Activity, label: "Theo dõi sức khỏe", href: "/health" },
  { icon: Bot, label: "AI Assistant", href: "/ai" },
  { icon: Utensils, label: "Bữa ăn", href: "/meals" },
  { icon: Pill, label: "Thuốc", href: "/medication" },
  { icon: CalendarClock, label: "Lịch khám", href: "/appointments" },
  { icon: FileBarChart, label: "Báo cáo tuần", href: "/reports" },
  { icon: Bell, label: "Thông báo", href: "/notifications" },
  { icon: User, label: "Hồ sơ", href: "/profile" },
];

const doctorNav: NavItem[] = [
  {
    icon: LayoutDashboard,
    label: "Tổng quan",
    href: "/doctor",
    section: "overview",
  },
  {
    icon: CalendarClock,
    label: "Lịch tái khám",
    href: "/doctor?section=appointments",
    section: "appointments",
  },
  { icon: Bell, label: "Cảnh báo", href: "/notifications" },
  { icon: User, label: "Tài khoản", href: "/doctor/account" },
];

const adminNav: NavItem[] = [
  {
    icon: LayoutDashboard,
    label: "Tổng quan",
    href: "/admin",
    section: "overview",
  },
  {
    icon: UserCog,
    label: "Quản lý tài khoản",
    href: "/admin?section=users",
    section: "users",
  },
  {
    icon: Link2,
    label: "Phân công",
    href: "/admin?section=assignments",
    section: "assignments",
  },
  {
    icon: BookOpen,
    label: "Nhật ký hệ thống",
    href: "/admin?section=audit",
    section: "audit",
  },
  {
    icon: Settings,
    label: "Cài đặt hệ thống",
    href: "/admin?section=settings",
    section: "settings",
  },
  { icon: User, label: "Tài khoản", href: "/admin/account" },
];
interface SidebarProps {
  onClose?: () => void;
}

export function Sidebar({ onClose }: SidebarProps) {
  const { user, logout } = useAuth();
  const router = useRouter();
  const pathname = usePathname();
  const searchParams = useSearchParams();
  const avatarSrc =
    user?.role === "doctor"
      ? "/doctor.png"
      : user?.role === "admin"
        ? "/administrator.png"
        : "/patient.png";

  const nav =
    user?.role === "doctor"
      ? doctorNav
      : user?.role === "admin"
        ? adminNav
        : patientNav;

  const handleNav = (href: string) => {
    router.push(href);
    onClose?.();
  };

  return (
    <div className="flex flex-col h-full w-full bg-card border-r border-border">
      {/* Header */}
      <div className="flex items-center justify-between p-5 pb-4">
        <button
          type="button"
          onClick={() =>
            handleNav(
              user?.role === "doctor"
                ? "/doctor"
                : user?.role === "admin"
                  ? "/admin"
                  : "/dashboard",
            )
          }
          className="rounded-lg text-left focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary focus-visible:ring-offset-2"
          aria-label="Về trang chính"
        >
          <VDiabLogo />
        </button>
        {onClose && (
          <button
            onClick={onClose}
            className="p-1.5 rounded-lg hover:bg-muted text-muted-foreground"
          >
            <X className="w-4 h-4" />
          </button>
        )}
      </div>

      {/* User info */}
      {user && (
        <div className="mx-4 mb-4 p-3 bg-accent rounded-xl flex items-center gap-3">
          <div className="relative w-9 h-9 overflow-hidden rounded-full bg-primary/20 flex-shrink-0">
            <Image
              src={avatarSrc}
              alt="Ảnh đại diện"
              fill
              sizes="36px"
              className="object-cover"
            />
          </div>
          <div className="min-w-0">
            <p className="text-sm font-semibold text-foreground truncate">
              {user.name}
            </p>
            <p className="text-xs text-muted-foreground capitalize">
              {user.role === "patient"
                ? "Bệnh nhân"
                : user.role === "doctor"
                  ? "Bác sĩ"
                  : "Quản trị viên"}
            </p>
          </div>
        </div>
      )}

      {/* Nav */}
      <nav className="flex-1 px-3 overflow-y-auto">
        <ul className="space-y-0.5">
          {nav.map((item, i) => {
            const section = searchParams.get("section") || "overview";
            const isActive = item.section
              ? pathname === item.href.split("?")[0] && section === item.section
              : pathname === item.href ||
                (item.href !== "/" &&
                  pathname.startsWith(item.href + "/") &&
                  item.href !== "/doctor");
            return (
              <li key={`${item.href}-${i}`}>
                <button
                  onClick={() => handleNav(item.href)}
                  className={cn(
                    "w-full flex items-center gap-3 px-3 py-2.5 rounded-xl text-sm font-medium transition-colors text-left",
                    isActive
                      ? "bg-primary text-primary-foreground"
                      : "text-muted-foreground hover:bg-muted hover:text-foreground",
                  )}
                >
                  <item.icon className="w-4 h-4 flex-shrink-0" />
                  <span className="flex-1">{item.label}</span>
                  {item.badge && (
                    <span
                      className={cn(
                        "text-xs rounded-full w-5 h-5 flex items-center justify-center font-semibold",
                        isActive
                          ? "bg-white/20 text-white"
                          : "bg-red-100 text-red-600",
                      )}
                    >
                      {item.badge}
                    </span>
                  )}
                </button>
              </li>
            );
          })}
        </ul>
      </nav>

      {/* Footer */}
      <div className="p-4 border-t border-border">
        <button
          onClick={logout}
          className="w-full px-3 py-2.5 rounded-xl text-sm font-medium text-muted-foreground hover:bg-muted hover:text-foreground transition-colors text-left flex items-center gap-3"
        >
          <LogOut className="w-4 h-4" />
          Đăng xuất
        </button>
      </div>
    </div>
  );
}
