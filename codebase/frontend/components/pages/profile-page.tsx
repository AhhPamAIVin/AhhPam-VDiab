"use client";

import Image from "next/image";
import { useEffect, useState, type FormEvent } from "react";
import { useAuth } from "@/lib/auth-context";
import {
  api,
  replaceToken,
  type BloodPressureRecord,
  type ClinicalNote,
  type GlucoseRecord,
  type MedicationRecord,
} from "@/lib/api";
import {
  User,
  Mail,
  Heart,
  Stethoscope,
  ShieldCheck,
  Eye,
  EyeOff,
  Lock,
  LogOut,
  Info,
  CalendarDays,
  Droplets,
  Pill,
  Loader2,
  Type,
} from "lucide-react";
import { cn } from "@/lib/utils";
import { PatientDataRightsCard } from "@/components/patient-data-rights-card";
import {
  getPatientFontSize,
  savePatientFontSize,
  type PatientFontSize,
} from "@/lib/patient-font-size";

type Tab = "info" | "health" | "settings";

const tabs: { id: Tab; label: string }[] = [
  { id: "info", label: "Thông tin" },
  { id: "health", label: "Sức khỏe" },
  { id: "settings", label: "Cài đặt" },
];

export function ProfilePage() {
  const { user, logout } = useAuth();
  const [activeTab, setActiveTab] = useState<Tab>("info");
  const [currentPassword, setCurrentPassword] = useState("");
  const [newPassword, setNewPassword] = useState("");
  const [confirmPassword, setConfirmPassword] = useState("");
  const [showPasswords, setShowPasswords] = useState(false);
  const [passwordLoading, setPasswordLoading] = useState(false);
  const [passwordError, setPasswordError] = useState<string | null>(null);
  const [passwordSuccess, setPasswordSuccess] = useState<string | null>(null);
  const [healthLoading, setHealthLoading] = useState(true);
  const [glucoseRecords, setGlucoseRecords] = useState<GlucoseRecord[]>([]);
  const [bloodPressureRecords, setBloodPressureRecords] = useState<BloodPressureRecord[]>([]);
  const [medications, setMedications] = useState<MedicationRecord[]>([]);
  const [clinicalNotes, setClinicalNotes] = useState<ClinicalNote[]>([]);
  const [fontSize, setFontSize] = useState<PatientFontSize>("standard");

  useEffect(() => {
    setFontSize(getPatientFontSize());
  }, []);

  const changeFontSize = (size: PatientFontSize) => {
    setFontSize(size);
    savePatientFontSize(size);
  };

  useEffect(() => {
    if (!user?.id) return;
    let active = true;
    setHealthLoading(true);
    Promise.allSettled([
      api.glucose(),
      api.bloodPressure(),
      api.activeMedications(),
      api.clinicalNotes(user.id),
    ]).then(([glucose, bloodPressure, medication, notes]) => {
      if (!active) return;
      setGlucoseRecords(glucose.status === "fulfilled" ? glucose.value : []);
      setBloodPressureRecords(bloodPressure.status === "fulfilled" ? bloodPressure.value : []);
      setMedications(medication.status === "fulfilled" ? medication.value : []);
      setClinicalNotes(notes.status === "fulfilled" ? notes.value : []);
      setHealthLoading(false);
    });
    return () => { active = false; };
  }, [user?.id]);

  const latestGlucose = [...glucoseRecords].sort(
    (a, b) => new Date(b.measured_at).getTime() - new Date(a.measured_at).getTime(),
  )[0];
  const latestBloodPressure = [...bloodPressureRecords].sort(
    (a, b) => new Date(b.measured_at).getTime() - new Date(a.measured_at).getTime(),
  )[0];
  const latestClinicalNote = [...clinicalNotes].sort(
    (a, b) => new Date(b.updated_at).getTime() - new Date(a.updated_at).getTime(),
  )[0];

  const handlePasswordChange = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    setPasswordError(null);
    setPasswordSuccess(null);

    if (newPassword.length < 8) {
      setPasswordError("Mật khẩu mới phải có ít nhất 8 ký tự.");
      return;
    }
    if (newPassword !== confirmPassword) {
      setPasswordError("Mật khẩu xác nhận không khớp.");
      return;
    }

    setPasswordLoading(true);
    try {
      const result = await api.changePassword(currentPassword, newPassword);
      replaceToken(result.access_token);
      setCurrentPassword("");
      setNewPassword("");
      setConfirmPassword("");
      setPasswordSuccess("Đổi mật khẩu thành công.");
    } catch (error) {
      setPasswordError(
        error instanceof Error ? error.message : "Không thể đổi mật khẩu.",
      );
    } finally {
      setPasswordLoading(false);
    }
  };
  return (
    <div className="max-w-2xl mx-auto space-y-5">
      {/* Avatar header */}
      <div className="bg-card rounded-2xl p-6 border border-border flex items-center gap-5">
        <div className="relative h-20 w-20 flex-shrink-0 overflow-hidden rounded-2xl bg-accent">
          <Image
            src="/patient.png"
            alt="Ảnh đại diện bệnh nhân"
            fill
            sizes="80px"
            className="object-cover"
            priority
          />
        </div>
        <div className="flex-1 min-w-0">
          <h2 className="text-lg font-bold text-foreground truncate">
            {user?.name || "Bệnh nhân"}
          </h2>
          <p className="text-sm text-muted-foreground mt-0.5">{user?.email}</p>
          <span className="inline-flex items-center mt-2 px-2.5 py-1 rounded-full text-xs font-medium bg-accent text-primary">
            Bệnh nhân
          </span>
        </div>
      </div>

      {/* Tabs */}
      <div className="flex gap-1 bg-muted rounded-xl p-1">
        {tabs.map((t) => (
          <button
            key={t.id}
            onClick={() => setActiveTab(t.id)}
            className={cn(
              "flex-1 py-2 rounded-lg text-sm font-medium transition-colors",
              activeTab === t.id
                ? "bg-card text-foreground shadow-sm"
                : "text-muted-foreground hover:text-foreground",
            )}
          >
            {t.label}
          </button>
        ))}
      </div>

      {/* Tab: Info */}
      {activeTab === "info" && (
        <div className="space-y-4">
          <div className="overflow-hidden rounded-2xl border border-border bg-card">
            <div className="border-b border-border px-5 py-4">
              <h3 className="font-semibold text-foreground">Thông tin tài khoản</h3>
              <p className="mt-1 text-xs text-muted-foreground">Thông tin được xác nhận từ tài khoản đăng nhập.</p>
            </div>
            {[
              { icon: User, label: "Họ và tên", value: user?.name || "Chưa cập nhật" },
              { icon: Mail, label: "Email", value: user?.email || "Chưa cập nhật" },
              { icon: ShieldCheck, label: "Loại tài khoản", value: "Bệnh nhân" },
            ].map(({ icon: Icon, label, value }) => (
              <div key={label} className="flex items-center gap-4 border-b border-border px-5 py-4 last:border-b-0">
                <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-xl bg-accent">
                  <Icon className="h-4 w-4 text-primary" />
                </span>
                <div className="min-w-0 flex-1">
                  <p className="text-xs text-muted-foreground">{label}</p>
                  <p className="mt-0.5 truncate text-sm font-medium text-foreground">{value}</p>
                </div>
              </div>
            ))}
          </div>

          <div className="flex items-start gap-3 rounded-2xl border border-blue-200 bg-blue-50 p-4 dark:border-blue-900 dark:bg-blue-950/30">
            <Info className="mt-0.5 h-4 w-4 shrink-0 text-blue-600" />
            <div>
              <p className="text-sm font-medium text-blue-900 dark:text-blue-200">Hồ sơ cá nhân chưa đầy đủ</p>
              <p className="mt-1 text-xs leading-relaxed text-blue-700 dark:text-blue-300">
                Số điện thoại, ngày sinh, giới tính, địa chỉ và liên hệ khẩn cấp chưa được hệ thống thu thập. Những trường này sẽ hiển thị khi tính năng cập nhật hồ sơ được hỗ trợ.
              </p>
            </div>
          </div>
        </div>
      )}

      {/* Tab: Health */}
      {activeTab === "health" && (
        <div className="space-y-4">
          {healthLoading ? (
            <div className="flex items-center justify-center gap-2 rounded-2xl border border-border bg-card py-14 text-sm text-muted-foreground">
              <Loader2 className="h-4 w-4 animate-spin" /> Đang tải hồ sơ sức khỏe...
            </div>
          ) : (
            <>
              <div className="grid grid-cols-2 gap-3">
                <div className="rounded-2xl border border-border bg-card p-4">
                  <span className="flex h-9 w-9 items-center justify-center rounded-xl bg-[#DFF7F3] text-[#238D87]">
                    <Droplets className="h-4 w-4" />
                  </span>
                  <p className="mt-3 text-xs text-muted-foreground">Đường huyết gần nhất</p>
                  <p className="mt-1 text-xl font-bold text-foreground">
                    {latestGlucose?.value ?? "—"} <span className="text-xs font-normal text-muted-foreground">mg/dL</span>
                  </p>
                  <p className="mt-1 text-[11px] text-muted-foreground">
                    {latestGlucose ? new Date(latestGlucose.measured_at).toLocaleString("vi-VN") : "Chưa có dữ liệu"}
                  </p>
                </div>
                <div className="rounded-2xl border border-border bg-card p-4">
                  <span className="flex h-9 w-9 items-center justify-center rounded-xl bg-red-50 text-red-500 dark:bg-red-950/40">
                    <Heart className="h-4 w-4" />
                  </span>
                  <p className="mt-3 text-xs text-muted-foreground">Huyết áp gần nhất</p>
                  <p className="mt-1 text-xl font-bold text-foreground">
                    {latestBloodPressure ? `${latestBloodPressure.systolic}/${latestBloodPressure.diastolic}` : "—"} <span className="text-xs font-normal text-muted-foreground">mmHg</span>
                  </p>
                  <p className="mt-1 text-[11px] text-muted-foreground">
                    {latestBloodPressure ? new Date(latestBloodPressure.measured_at).toLocaleString("vi-VN") : "Chưa có dữ liệu"}
                  </p>
                </div>
              </div>

              <div className="rounded-2xl border border-border bg-card p-5">
                <div className="flex items-center gap-3">
                  <span className="flex h-10 w-10 items-center justify-center rounded-xl bg-purple-50 text-purple-500 dark:bg-purple-950/40">
                    <Pill className="h-5 w-5" />
                  </span>
                  <div>
                    <h3 className="font-semibold text-foreground">Thuốc đang sử dụng</h3>
                    <p className="text-xs text-muted-foreground">{medications.length} thuốc đang hoạt động</p>
                  </div>
                </div>
                <div className="mt-4 space-y-2">
                  {medications.map((medication) => (
                    <div key={medication.id} className="rounded-xl bg-muted px-3 py-2.5">
                      <p className="text-sm font-medium text-foreground">{medication.drug_name} · {medication.dose}</p>
                      <p className="mt-0.5 text-xs text-muted-foreground">{medication.frequency}</p>
                    </div>
                  ))}
                  {medications.length === 0 && <p className="text-sm text-muted-foreground">Chưa có thuốc đang sử dụng.</p>}
                </div>
              </div>

              <div className="rounded-2xl border border-border bg-card p-5">
                <div className="flex items-center gap-3">
                  <span className="flex h-10 w-10 items-center justify-center rounded-xl bg-orange-50 text-orange-500 dark:bg-orange-950/40">
                    <Stethoscope className="h-5 w-5" />
                  </span>
                  <div>
                    <h3 className="font-semibold text-foreground">Hồ sơ khám gần nhất</h3>
                    <p className="text-xs text-muted-foreground">Thông tin do bác sĩ ghi nhận</p>
                  </div>
                </div>
                {latestClinicalNote ? (
                  <div className="mt-4 space-y-3">
                    <div><p className="text-xs text-muted-foreground">Chẩn đoán</p><p className="mt-0.5 text-sm font-medium text-foreground">{latestClinicalNote.diagnosis}</p></div>
                    {latestClinicalNote.hba1c_pct != null && <div><p className="text-xs text-muted-foreground">HbA1c</p><p className="mt-0.5 text-sm font-medium text-foreground">{latestClinicalNote.hba1c_pct}%</p></div>}
                    {latestClinicalNote.follow_up_date && <div className="flex items-center gap-2 rounded-xl bg-accent px-3 py-2 text-sm text-foreground"><CalendarDays className="h-4 w-4 text-primary" /> Tái khám: {new Date(`${latestClinicalNote.follow_up_date}T00:00:00`).toLocaleDateString("vi-VN")}</div>}
                  </div>
                ) : (
                  <p className="mt-4 text-sm text-muted-foreground">Chưa có hồ sơ khám được ghi nhận.</p>
                )}
              </div>

              <div className="flex items-start gap-2 rounded-xl bg-muted p-3">
                <Info className="mt-0.5 h-4 w-4 shrink-0 text-muted-foreground" />
                <p className="text-xs leading-relaxed text-muted-foreground">Chiều cao, BMI và mục tiêu đường huyết chưa có API hồ sơ công khai nên không hiển thị tại đây để tránh nhầm với dữ liệu thật.</p>
              </div>
            </>
          )}
        </div>
      )}

      {/* Tab: Settings */}
      {activeTab === "settings" && (
        <div className="space-y-4">
          <section className="overflow-hidden rounded-2xl border border-border bg-card">
            <div className="border-b border-border px-5 py-4">
              <div className="flex items-center gap-3">
                <div className="flex h-10 w-10 items-center justify-center rounded-xl bg-accent">
                  <Type className="h-5 w-5 text-primary" />
                </div>
                <div>
                  <h3 className="font-semibold text-foreground">Cỡ chữ hiển thị</h3>
                  <p className="mt-0.5 text-xs text-muted-foreground">
                    Tăng cỡ chữ để nội dung dễ đọc và dễ thao tác hơn.
                  </p>
                </div>
              </div>
            </div>

            <div className="p-5">
              <div className="grid grid-cols-3 gap-2" role="radiogroup" aria-label="Chọn cỡ chữ">
                {([
                  { value: "standard", label: "Tiêu chuẩn", sample: "A" },
                  { value: "large", label: "Lớn", sample: "A+" },
                  { value: "extra-large", label: "Rất lớn", sample: "A++" },
                ] as const).map((option) => (
                  <button
                    key={option.value}
                    type="button"
                    role="radio"
                    aria-checked={fontSize === option.value}
                    onClick={() => changeFontSize(option.value)}
                    className={cn(
                      "flex min-h-24 flex-col items-center justify-center rounded-xl border p-3 text-center transition-colors",
                      fontSize === option.value
                        ? "border-primary bg-accent text-primary ring-2 ring-primary/15"
                        : "border-border bg-background text-muted-foreground hover:border-primary/40 hover:bg-muted",
                    )}
                  >
                    <span className={cn(
                      "font-bold",
                      option.value === "standard" ? "text-base" : option.value === "large" ? "text-xl" : "text-2xl",
                    )}>{option.sample}</span>
                    <span className="mt-2 text-xs font-semibold">{option.label}</span>
                  </button>
                ))}
              </div>
              <div className="mt-4 rounded-xl bg-muted p-4">
                <p className="font-semibold text-foreground">Nội dung xem trước</p>
                <p className="mt-1 text-sm leading-relaxed text-muted-foreground">
                  Hãy chọn cỡ chữ mà bạn cảm thấy dễ đọc nhất.
                </p>
              </div>
            </div>
          </section>

          {user?.role === "patient" && <PatientDataRightsCard />}

          <section className="overflow-hidden rounded-2xl border border-border bg-card">
          <div className="border-b border-border px-5 py-4">
            <div className="flex items-center gap-3">
              <div className="flex h-10 w-10 items-center justify-center rounded-xl bg-accent">
                <ShieldCheck className="h-5 w-5 text-primary" />
              </div>
              <div>
                <h3 className="font-semibold text-foreground">Đổi mật khẩu</h3>
                <p className="mt-0.5 text-xs text-muted-foreground">
                  Cập nhật mật khẩu đăng nhập của bạn.
                </p>
              </div>
            </div>
          </div>

          <form onSubmit={handlePasswordChange} className="space-y-4 p-5">
            <label className="block">
              <span className="mb-1.5 block text-sm font-medium text-foreground">
                Mật khẩu hiện tại
              </span>
              <div className="relative">
                <Lock className="absolute left-3.5 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
                <input
                  type={showPasswords ? "text" : "password"}
                  value={currentPassword}
                  onChange={(event) => setCurrentPassword(event.target.value)}
                  autoComplete="current-password"
                  required
                  className="w-full rounded-xl border border-border bg-muted py-3 pl-10 pr-11 text-sm text-foreground outline-none focus:border-primary focus:ring-2 focus:ring-primary/20"
                />
              </div>
            </label>

            <label className="block">
              <span className="mb-1.5 block text-sm font-medium text-foreground">
                Mật khẩu mới
              </span>
              <div className="relative">
                <Lock className="absolute left-3.5 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
                <input
                  type={showPasswords ? "text" : "password"}
                  value={newPassword}
                  onChange={(event) => setNewPassword(event.target.value)}
                  autoComplete="new-password"
                  minLength={8}
                  maxLength={72}
                  required
                  className="w-full rounded-xl border border-border bg-muted py-3 pl-10 pr-11 text-sm text-foreground outline-none focus:border-primary focus:ring-2 focus:ring-primary/20"
                />
              </div>
            </label>

            <label className="block">
              <span className="mb-1.5 block text-sm font-medium text-foreground">
                Xác nhận mật khẩu mới
              </span>
              <div className="relative">
                <Lock className="absolute left-3.5 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
                <input
                  type={showPasswords ? "text" : "password"}
                  value={confirmPassword}
                  onChange={(event) => setConfirmPassword(event.target.value)}
                  autoComplete="new-password"
                  minLength={8}
                  maxLength={72}
                  required
                  className="w-full rounded-xl border border-border bg-muted py-3 pl-10 pr-11 text-sm text-foreground outline-none focus:border-primary focus:ring-2 focus:ring-primary/20"
                />
                <button
                  type="button"
                  onClick={() => setShowPasswords((visible) => !visible)}
                  aria-label={showPasswords ? "Ẩn mật khẩu" : "Hiện mật khẩu"}
                  className="absolute right-3.5 top-1/2 -translate-y-1/2 text-muted-foreground hover:text-foreground"
                >
                  {showPasswords ? (
                    <EyeOff className="h-4 w-4" />
                  ) : (
                    <Eye className="h-4 w-4" />
                  )}
                </button>
              </div>
            </label>

            {passwordError && (
              <p
                role="alert"
                className="rounded-xl border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700"
              >
                {passwordError}
              </p>
            )}
            {passwordSuccess && (
              <p
                role="status"
                className="rounded-xl border border-green-200 bg-green-50 px-4 py-3 text-sm text-green-700"
              >
                {passwordSuccess}
              </p>
            )}

            <button
              type="submit"
              disabled={passwordLoading}
              className="w-full rounded-xl bg-primary px-4 py-3 text-sm font-semibold text-primary-foreground transition-colors hover:bg-[#2D9D97] disabled:cursor-not-allowed disabled:opacity-60"
            >
              {passwordLoading ? "Đang đổi mật khẩu..." : "Đổi mật khẩu"}
            </button>
          </form>

          <div className="border-t border-border p-5">
            <button
              type="button"
              onClick={logout}
              className="flex w-full items-center justify-center gap-2 rounded-xl border border-red-200 bg-red-50 px-4 py-3 text-sm font-semibold text-red-600 transition-colors hover:bg-red-100"
            >
              <LogOut className="h-4 w-4" />
              Đăng xuất
            </button>
          </div>
          </section>
        </div>
      )}
    </div>
  );
}
