"use client";

import { useState } from "react";
import { useAuth } from "@/lib/auth-context";
import { VDiabLogo } from "@/components/vdiab-logo";
import { Eye, EyeOff, Lock, Mail, ChevronRight } from "lucide-react";
import { cn } from "@/lib/utils";

// Sourced from NEXT_PUBLIC_DEMO_*_EMAIL/PASSWORD (already provisioned as
// "Sensitive" env vars in Vercel — CLI can't read their values back, only
// the deploy build can) with verified-working seeded NHANES accounts
// (scripts/migrate_nhanes_demo_data.py) as the local-dev fallback when those
// aren't set. Patient fallback has real active medications + glucose/meal
// history so AI Consultation/Summary have something to answer from, not an
// empty record.
const DEMO_ACCOUNTS = {
  patient: {
    email:
      process.env.NEXT_PUBLIC_DEMO_PATIENT_EMAIL || "viethh_0461@gmail.com",
    password:
      process.env.NEXT_PUBLIC_DEMO_PATIENT_PASSWORD || "viethh_0461@gmail.com",
    label: "Bệnh nhân demo",
  },
  doctor: {
    email:
      process.env.NEXT_PUBLIC_DEMO_DOCTOR_EMAIL || "tampn_0101@vdiab.com.vn",
    password:
      process.env.NEXT_PUBLIC_DEMO_DOCTOR_PASSWORD || "tampn_0101@vdiab.com.vn",
    label: "Bác sĩ demo",
  },
} as const;

export function LoginPage() {
  const { login } = useAuth();
  const [showPass, setShowPass] = useState(false);
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [remember, setRemember] = useState(false);
  const [loading, setLoading] = useState(false);
  const [demoLoading, setDemoLoading] = useState<
    keyof typeof DEMO_ACCOUNTS | null
  >(null);
  const [errors, setErrors] = useState<{
    email?: string;
    password?: string;
    form?: string;
  }>({});

  const handleDemoLogin = async (role: keyof typeof DEMO_ACCOUNTS) => {
    const account = DEMO_ACCOUNTS[role];
    setErrors({});
    setDemoLoading(role);
    try {
      await login(account.email, account.password, remember);
    } catch (error) {
      setErrors({
        form: error instanceof Error ? error.message : "Không thể đăng nhập",
      });
    } finally {
      setDemoLoading(null);
    }
  };

  const validate = () => {
    const e: typeof errors = {};
    if (!email) e.email = "Vui lòng nhập email hoặc số điện thoại";
    if (!password) e.password = "Vui lòng nhập mật khẩu";
    else if (password.length < 6)
      e.password = "Mật khẩu phải có ít nhất 6 ký tự";
    setErrors(e);
    return Object.keys(e).length === 0;
  };

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!validate()) return;
    setLoading(true);
    try {
      await login(email, password, remember);
    } catch (error) {
      setErrors((current) => ({
        ...current,
        form: error instanceof Error ? error.message : "Không thể đăng nhập",
      }));
      setLoading(false);
    }
  };

  return (
    <div className="relative flex min-h-dvh flex-col overflow-x-hidden bg-white lg:min-h-screen lg:flex-row lg:bg-background">
      {/* Mobile brand hero */}
      <div className="relative h-[clamp(11rem,29dvh,17rem)] shrink-0 overflow-visible bg-gradient-to-br from-[#087f78] via-[#20a69f] to-[#7edbd3] lg:hidden">
        <div
          aria-hidden="true"
          className="pointer-events-none absolute inset-0 overflow-hidden"
        >
          <div className="login-orb login-orb-one absolute -right-20 -top-16 h-64 w-64 rounded-full bg-white/20 blur-2xl" />
          <div className="login-orb login-orb-two absolute -bottom-28 -left-24 h-72 w-72 rounded-full bg-[#bff8ed]/30 blur-3xl" />
          <div className="login-medical-cross absolute left-[8%] top-[18%] text-5xl font-light text-white/15">
            +
          </div>
          <div className="login-medical-cross login-medical-cross-delayed absolute bottom-[18%] right-[9%] text-7xl font-light text-white/10">
            +
          </div>
          <div className="absolute inset-0 bg-[radial-gradient(circle_at_center,transparent_0,rgba(0,80,76,0.16)_100%)]" />
        </div>
        <div className="relative z-10 flex h-full items-center justify-center pb-10 max-[380px]:pb-8">
          <VDiabLogo className="gap-3.5 [&>div:first-child]:h-16 [&>div:first-child]:w-16 [&>div:first-child]:rounded-[1.25rem] [&>div:first-child]:bg-white/20 [&_svg]:h-10 [&_svg]:w-10 [&_span]:text-[1.75rem] [&_span]:text-white sm:gap-4 sm:[&>div:first-child]:h-[72px] sm:[&>div:first-child]:w-[72px] sm:[&_svg]:h-12 sm:[&_svg]:w-12 sm:[&_span]:text-3xl" />
        </div>
        <svg
          aria-hidden="true"
          viewBox="0 0 1440 180"
          preserveAspectRatio="none"
          className="pointer-events-none absolute -bottom-1 left-0 block h-24 w-full"
        >
          <path
            d="M0 76C130 34 290 48 430 88C570 128 690 86 820 52C980 10 1100 70 1220 92C1320 110 1385 80 1440 62V190H0Z"
            fill="#75d8d0"
            fillOpacity="0.55"
          />
          <path
            d="M0 108C150 126 280 82 410 68C550 52 660 126 820 118C980 110 1090 48 1240 58C1350 66 1390 104 1440 112V190H0Z"
            fill="#b9eee8"
            fillOpacity="0.9"
          />
          <path
            d="M0 140C150 112 300 124 445 146C590 168 735 142 875 126C1020 108 1130 138 1260 144C1350 148 1400 132 1440 124V190H0Z"
            fill="white"
          />
        </svg>
      </div>
      {/* Left decorative panel – hidden on mobile */}
      <div className="hidden lg:flex lg:w-[480px] xl:w-[540px] bg-primary flex-col items-center justify-center p-12 relative overflow-hidden flex-shrink-0">
        {/* Decorative circles */}
        <div className="absolute -top-24 -left-24 w-64 h-64 rounded-full bg-white/10" />
        <div className="absolute -bottom-16 -right-16 w-80 h-80 rounded-full bg-white/10" />
        <div className="absolute top-1/2 left-1/2 -translate-x-1/2 -translate-y-1/2 w-[400px] h-[400px] rounded-full bg-white/5" />

        <div className="relative z-10 text-white text-center max-w-xs">
          <div className="flex justify-center mb-8">
            <div className="w-20 h-20 rounded-3xl bg-white/20 flex items-center justify-center backdrop-blur-sm">
              <svg viewBox="0 0 56 56" fill="none" className="w-12 h-12">
                <path
                  d="M12 16L28 40L44 16"
                  stroke="white"
                  strokeWidth="4.5"
                  strokeLinecap="round"
                  strokeLinejoin="round"
                />
                <path
                  d="M20 28H26L29 22L33 34L37 28H44"
                  stroke="white"
                  strokeWidth="3"
                  strokeLinecap="round"
                  strokeLinejoin="round"
                  opacity="0.8"
                />
              </svg>
            </div>
          </div>
          <h1 className="text-4xl font-bold mb-3 tracking-tight">VDiab</h1>
          <p className="text-lg text-white/80 leading-relaxed font-medium">
            Quản lý sức khỏe chủ động mỗi ngày
          </p>
          <div className="mt-10 space-y-4 text-sm text-white/70">
            {[
              "Theo dõi đường huyết và huyết áp",
              "Nhắc nhở uống thuốc mỗi ngày",
              "Trợ lý AI hỗ trợ 24/7",
            ].map((txt) => (
              <div key={txt} className="flex items-center gap-3">
                <div className="w-5 h-5 rounded-full bg-white/20 flex items-center justify-center flex-shrink-0">
                  <ChevronRight className="w-3 h-3" />
                </div>
                {txt}
              </div>
            ))}
          </div>
        </div>
      </div>

      {/* Right login form */}
      <div className="relative z-10 flex flex-1 items-start justify-center px-4 pb-[max(1rem,env(safe-area-inset-bottom))] pt-2 sm:px-6 sm:pb-[max(1.25rem,env(safe-area-inset-bottom))] sm:pt-3 lg:items-center lg:py-6">
        <div className="w-full max-w-[27rem] bg-white py-2 sm:max-w-md sm:py-3 lg:bg-transparent lg:p-0">
          <div className="mb-3.5 text-center sm:mb-4 lg:mb-8 lg:text-left">
            <h2 className="text-[1.75rem] font-bold leading-tight text-foreground sm:text-3xl">Đăng nhập</h2>
            <p className="mt-1.5 text-base text-muted-foreground">
              Chào mừng trở lại VDiab
            </p>
          </div>

          <form onSubmit={handleSubmit} className="space-y-3 sm:space-y-3.5" noValidate>
            {errors.form && (
              <div className="rounded-xl border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">
                {errors.form}
              </div>
            )}
            <div>
              <label className="mb-1.5 block text-base font-medium text-foreground">
                Email hoặc số điện thoại
              </label>
              <div className="relative">
                <Mail className="absolute left-3.5 top-1/2 h-5 w-5 -translate-y-1/2 text-muted-foreground" />
                <input
                  type="text"
                  value={email}
                  onChange={(e) => {
                    setEmail(e.target.value);
                    setErrors((p) => ({ ...p, email: undefined }));
                  }}
                  placeholder="Nhập email hoặc số điện thoại"
                  className={cn(
                    "w-full rounded-xl border bg-card py-3.5 pl-11 pr-4 text-base text-foreground outline-none transition-all focus:ring-2 focus:ring-primary/30",
                    errors.email
                      ? "border-red-400"
                      : "border-border focus:border-primary",
                  )}
                />
              </div>
              {errors.email && (
                <p className="text-red-500 text-xs mt-1">{errors.email}</p>
              )}
            </div>

            <div>
              <label className="mb-1.5 block text-base font-medium text-foreground">
                Mật khẩu
              </label>
              <div className="relative">
                <Lock className="absolute left-3.5 top-1/2 h-5 w-5 -translate-y-1/2 text-muted-foreground" />
                <input
                  type={showPass ? "text" : "password"}
                  value={password}
                  onChange={(e) => {
                    setPassword(e.target.value);
                    setErrors((p) => ({ ...p, password: undefined }));
                  }}
                  placeholder="Nhập mật khẩu"
                  className={cn(
                    "w-full rounded-xl border bg-card py-3.5 pl-11 pr-11 text-base text-foreground outline-none transition-all focus:ring-2 focus:ring-primary/30",
                    errors.password
                      ? "border-red-400"
                      : "border-border focus:border-primary",
                  )}
                />
                <button
                  type="button"
                  onClick={() => setShowPass(!showPass)}
                  aria-label={showPass ? "Ẩn mật khẩu" : "Hiện mật khẩu"}
                  className="absolute right-1.5 top-1/2 flex h-11 w-11 -translate-y-1/2 items-center justify-center text-muted-foreground hover:text-foreground"
                >
                  {showPass ? (
                    <EyeOff className="h-5 w-5" />
                  ) : (
                    <Eye className="h-5 w-5" />
                  )}
                </button>
              </div>
              {errors.password && (
                <p className="text-red-500 text-xs mt-1">{errors.password}</p>
              )}
            </div>

            <div className="flex items-center justify-between">
              <label className="flex items-center gap-2 cursor-pointer">
                <input
                  type="checkbox"
                  checked={remember}
                  onChange={(e) => setRemember(e.target.checked)}
                  className="h-5 w-5 rounded border-border accent-primary"
                />
                <span className="text-sm text-muted-foreground">
                  Ghi nhớ đăng nhập
                </span>
              </label>
              <button
                type="button"
                disabled
                aria-disabled="true"
                className="cursor-not-allowed text-sm font-medium text-muted-foreground opacity-60"
              >
                Quên mật khẩu?
              </button>
            </div>

            <button
              type="submit"
              disabled={loading}
              className="mt-1 w-full rounded-xl bg-primary py-3.5 text-base font-semibold text-primary-foreground transition-colors hover:bg-[#2D9D97] disabled:opacity-70"
            >
              {loading ? "Đang đăng nhập..." : "Đăng nhập"}
            </button>
          </form>

          <div className="mt-3.5 sm:mt-5">
            <div className="relative">
              <div className="absolute inset-0 flex items-center">
                <div className="w-full border-t border-border" />
              </div>
              <div className="relative flex justify-center text-xs">
                <span className="bg-white px-3 text-muted-foreground lg:bg-background">
                  Hoặc dùng tài khoản demo
                </span>
              </div>
            </div>
            <div className="mt-3 grid grid-cols-2 gap-2">
              {(["patient", "doctor"] as const).map((role) => (
                <button
                  key={role}
                  type="button"
                  onClick={() => handleDemoLogin(role)}
                  disabled={demoLoading !== null || loading}
                  className="min-h-11 rounded-xl border border-border bg-card px-2 py-2.5 text-sm font-medium text-foreground transition-colors hover:border-primary hover:bg-primary/5 disabled:cursor-not-allowed disabled:opacity-60"
                >
                  {demoLoading === role
                    ? "Đang đăng nhập..."
                    : DEMO_ACCOUNTS[role].label}
                </button>
              ))}
            </div>
          </div>

          <p className="mt-3.5 text-center text-xs leading-5 text-muted-foreground sm:mt-6">
            VDiab chỉ cung cấp thông tin hỗ trợ và không thay thế tư vấn y tế
            chuyên nghiệp.
          </p>
        </div>
      </div>

      <svg
        aria-hidden="true"
        viewBox="0 0 1440 100"
        preserveAspectRatio="none"
        className="pointer-events-none absolute inset-x-0 -bottom-1 z-0 block h-24 w-full lg:hidden"
      >
        <path
          d="M0 48C210 18 430 32 650 58C870 84 1100 30 1240 38C1330 42 1390 54 1440 60V110H0Z"
          fill="#dff7f4"
          fillOpacity="0.7"
        />
        <path
          d="M0 70C230 48 470 58 700 76C930 94 1160 54 1300 62C1370 66 1410 74 1440 78V110H0Z"
          fill="#b9eee8"
          fillOpacity="0.55"
        />
      </svg>
    </div>
  );
}
