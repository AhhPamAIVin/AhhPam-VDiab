"use client";

import { useEffect, useState } from "react";
import { useSearchParams } from "next/navigation";
import { doctorPatients, mockDoctor } from "@/lib/mock-data";
import {
  Users,
  CalendarDays,
  AlertTriangle,
  Search,
  ChevronRight,
  Droplets,
  Activity,
  User,
  Clock,
  ClipboardList,
} from "lucide-react";
import { cn } from "@/lib/utils";
import { api, Appointment } from "@/lib/api";
import { useAuth } from "@/lib/auth-context";
import { Pagination } from "@/components/ui/pagination";
import { Select } from "antd";

const PAGE_SIZE = 8;

interface DoctorDashboardProps {
  onViewPatient: (id: string) => void;
}

type AlertFilter = "all" | "critical" | "warning" | "normal";
type GenderFilter = "all" | "male" | "female" | "unknown";

type DoctorAppointment = {
  id: string;
  patient: string;
  date: string;
  time: string;
  type: string;
  notes: string | null;
  status: Appointment["status"];
  scheduledAt: number;
};

const alertConfig = {
  critical: {
    label: "Nguy hiểm",
    dot: "bg-red-500",
    badge: "bg-red-100 text-red-700 border-red-200",
    cardBorder: "border-red-200",
    cardBg: "bg-red-50/50",
  },
  warning: {
    label: "Cần chú ý",
    dot: "bg-yellow-500",
    badge: "bg-yellow-100 text-yellow-700 border-yellow-200",
    cardBorder: "border-yellow-200",
    cardBg: "bg-yellow-50/30",
  },
  normal: {
    label: "Bình thường",
    dot: "bg-green-500",
    badge: "bg-green-100 text-green-700 border-green-200",
    cardBorder: "border-border",
    cardBg: "bg-card",
  },
};

export function DoctorDashboard({ onViewPatient }: DoctorDashboardProps) {
  const { user } = useAuth();
  const searchParams = useSearchParams();
  const [patients, setPatients] = useState<typeof doctorPatients>([]);
  const [appointments, setAppointments] = useState<DoctorAppointment[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [search, setSearch] = useState("");
  const [filter, setFilter] = useState<AlertFilter>("all");
  const [genderFilter, setGenderFilter] = useState<GenderFilter>("all");
  const [patientPage, setPatientPage] = useState(1);

  useEffect(() => {
    let cancelled = false;

    const load = async () => {
      setLoading(true);
      setError(null);
      const [patientsResult, appointmentsResult] = await Promise.allSettled([
        api.patients(),
        api.appointments(),
      ]);

      if (cancelled) return;

      if (patientsResult.status === "fulfilled") {
        const mapped = await Promise.all(
          patientsResult.value.map(async (item) => {
            const records = await api.glucose(item.id).catch(() => []);
            const latest = records.reduce<(typeof records)[number] | undefined>(
              (current, record) => {
                if (!current) return record;
                return new Date(record.measured_at).getTime() >
                  new Date(current.measured_at).getTime()
                  ? record
                  : current;
              },
              undefined,
            );
            const latestValue = latest?.value ?? 0;
            // Prefer the backend's personalized threshold_status (doctor-
            // approved CarePlanTarget + AI-predicted band, see
            // health/threshold_service.py) over a fixed 140/180 cutoff —
            // that fixed check ignores per-patient DANGER thresholds and the
            // ML-predicted WATCH band entirely. Falls back to the old fixed
            // cutoff only when threshold_status is null/UNAVAILABLE (record
            // predates this feature or patient has no CarePlanTarget yet).
            const level =
              latest?.threshold_status === "DANGER"
                ? "critical"
                : latest?.threshold_status === "WATCH"
                  ? "warning"
                  : latest?.threshold_status === "NORMAL"
                    ? "normal"
                    : latestValue > 180
                      ? "critical"
                      : latestValue > 140
                        ? "warning"
                        : "normal";
            return {
              id: item.id,
              name: item.full_name,
              age: 0,
              gender: "Chưa rõ",
              disease: "Đái tháo đường",
              lastGlucose: latest?.value ?? 0,
              lastGlucoseTime: latest
                ? new Date(latest.measured_at).toLocaleTimeString("vi-VN", {
                    hour: "2-digit",
                    minute: "2-digit",
                  })
                : "Chưa có dữ liệu",
              alertLevel: level as "critical" | "warning" | "normal",
              alertMessage:
                level === "critical"
                  ? "Chỉ số đường huyết gần nhất ở mức nguy hiểm."
                  : level === "warning"
                    ? "Chỉ số đường huyết gần nhất cần theo dõi (AI dự đoán)."
                    : "",
              updatedAt: latest
                ? new Date(latest.measured_at).toLocaleDateString("vi-VN")
                : "—",
            };
          }),
        );
        if (!cancelled) setPatients(mapped);
      } else {
        setPatients([]);
        setError("Không thể tải danh sách bệnh nhân.");
      }

      if (appointmentsResult.status === "fulfilled") {
        setAppointments(
          appointmentsResult.value.map((item) => ({
            id: item.id,
            patient: item.patient_id,
            date: new Date(item.scheduled_at).toLocaleDateString("vi-VN"),
            time: new Date(item.scheduled_at).toLocaleTimeString("vi-VN", {
              hour: "2-digit",
              minute: "2-digit",
            }),
            type: item.reason || "Tái khám",
            notes: item.notes,
            status: item.status,
            scheduledAt: new Date(item.scheduled_at).getTime(),
          })),
        );
      } else if (!error) {
        setError("Không thể tải lịch hẹn.");
      }
      setLoading(false);
    };

    load().catch(() => {
      if (!cancelled) {
        setPatients([]);
        setAppointments([]);
        setError("Không thể tải dữ liệu bác sĩ.");
        setLoading(false);
      }
    });

    return () => {
      cancelled = true;
    };
  }, []);

  const criticalCount = patients.filter(
    (p) => p.alertLevel === "critical",
  ).length;
  const warningCount = patients.filter(
    (p) => p.alertLevel === "warning",
  ).length;
  const normalCount = patients.filter((p) => p.alertLevel === "normal").length;

  const todayLabel = new Date().toLocaleDateString("vi-VN");
  const today = appointments
    .filter((a) => a.date === todayLabel && a.status === "scheduled")
    .sort((a, b) => a.scheduledAt - b.scheduledAt);
  const patientNames = new Map(
    patients.map((patient) => [patient.id, patient.name]),
  );
  const requestedSection = searchParams.get("section") || "overview";
  const section = requestedSection === "patients" ? "overview" : requestedSection;

  const filtered = patients.filter((p) => {
    const matchSearch =
      !search || p.name.toLowerCase().includes(search.toLowerCase());
    const matchFilter = filter === "all" || p.alertLevel === filter;
    const normalizedGender = p.gender.trim().toLowerCase();
    const isUnknownGender = !normalizedGender || normalizedGender === "—" || normalizedGender === "chưa rõ";
    const matchGender =
      genderFilter === "all" ||
      (genderFilter === "male" && normalizedGender === "nam") ||
      (genderFilter === "female" && (normalizedGender === "nữ" || normalizedGender === "nu")) ||
      (genderFilter === "unknown" && isUnknownGender);
    return matchSearch && matchFilter && matchGender;
  });
  const paginatedPatients = filtered.slice(
    (patientPage - 1) * PAGE_SIZE,
    patientPage * PAGE_SIZE,
  );

  useEffect(() => {
    setPatientPage(1);
  }, [search, filter, genderFilter]);

  useEffect(() => {
    setPatientPage((current) =>
      Math.min(current, Math.max(1, Math.ceil(filtered.length / PAGE_SIZE))),
    );
  }, [filtered.length]);

  const prioritizedPatients = [...filtered]
    .sort((a, b) => {
      const priority = { critical: 0, warning: 1, normal: 2 };
      return (
        priority[a.alertLevel as keyof typeof priority] -
          priority[b.alertLevel as keyof typeof priority] ||
        b.lastGlucose - a.lastGlucose
      );
    });
  const overviewPatients = prioritizedPatients.slice(
    (patientPage - 1) * PAGE_SIZE,
    patientPage * PAGE_SIZE,
  );

  const getAlertReason = (patient: (typeof patients)[number]) =>
    patient.alertMessage ||
    (patient.alertLevel === "critical"
      ? "Đường huyết gần nhất vượt ngưỡng nguy hiểm."
      : patient.alertLevel === "warning"
        ? "Đường huyết gần nhất cao hơn ngưỡng cần theo dõi."
        : "Các chỉ số gần nhất đang ở mức bình thường.");

  return (
    <div className="space-y-6 max-w-5xl mx-auto">
      {error && (
        <div
          role="alert"
          className="rounded-xl border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700"
        >
          {error}
        </div>
      )}
      {loading && (
        <div className="rounded-2xl border border-border bg-card p-6 text-center text-sm text-muted-foreground">
          Đang tải dữ liệu...
        </div>
      )}
      {/* Doctor welcome */}
      <div className="bg-primary rounded-2xl p-5 text-white relative overflow-hidden">
        <div className="absolute right-0 top-0 w-32 h-32 rounded-full bg-white/10 translate-x-8 -translate-y-8" />
        <div className="absolute right-12 bottom-0 w-20 h-20 rounded-full bg-white/10 translate-y-6" />
        <div className="relative z-10">
          <p className="text-white/80 text-sm">Xin chào,</p>
          <h2 className="text-xl font-bold mt-0.5">
            {user?.name || mockDoctor.name}
          </h2>
          <p className="text-white/70 text-sm mt-1">{mockDoctor.specialty}</p>
          <div className="mt-3 flex items-center gap-4">
            <div className="flex items-center gap-1.5">
              <Users className="w-4 h-4 text-white/80" />
              <span className="text-sm text-white/90 font-medium">
                {patients.length} bệnh nhân
              </span>
            </div>
            <div className="flex items-center gap-1.5">
              <CalendarDays className="w-4 h-4 text-white/80" />
              <span className="text-sm text-white/90 font-medium">
                {today.length} lịch hôm nay
              </span>
            </div>
          </div>
        </div>
      </div>

      {/* Overview */}
      {section === "overview" && (
        <div className="space-y-6">
          <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
            {[
              { label: "Tổng bệnh nhân", description: "Đang được phân công", count: patients.length, color: "text-[#238D87]", iconBg: "bg-[#D7F2EE]", card: "border-[#43B5AE]/40 bg-[#EAF8F6]/70", icon: Users },
              { label: "Nguy hiểm", description: "Cần xử lý ưu tiên", count: criticalCount, color: "text-red-700", iconBg: "bg-red-100", card: "border-red-200 bg-red-50/70", icon: AlertTriangle },
              { label: "Cần chú ý", description: "Cần theo dõi thêm", count: warningCount, color: "text-amber-700", iconBg: "bg-amber-100", card: "border-amber-200 bg-amber-50/70", icon: Activity },
              { label: "Lịch hôm nay", description: "Lịch tái khám", count: today.length, color: "text-blue-700", iconBg: "bg-blue-100", card: "border-blue-200 bg-blue-50/70", icon: CalendarDays },
            ].map(({ label, description, count, color, iconBg, card, icon: Icon }) => (
              <div key={label} className={cn("relative overflow-hidden rounded-2xl border p-4", card)}>
                <div className={cn("absolute inset-y-0 left-0 w-1", iconBg)} />
                <div className="flex items-center gap-3">
                  <div className={cn("flex h-11 w-11 shrink-0 items-center justify-center rounded-xl", iconBg)}>
                    <Icon className={cn("h-5 w-5", color)} />
                  </div>
                  <div className="min-w-0">
                    <p className={cn("text-2xl font-bold leading-none", color)}>{count}</p>
                    <p className="mt-1.5 text-sm font-semibold leading-tight text-foreground">{label}</p>
                    <p className="mt-0.5 text-[11px] leading-tight text-muted-foreground">{description}</p>
                  </div>
                </div>
              </div>
            ))}
          </div>

          <section className="overflow-hidden rounded-2xl border border-border bg-card">
            <div className="flex items-center gap-2 border-b border-border px-5 py-4">
              <AlertTriangle className="h-4 w-4 text-primary" />
              <div>
                <h3 className="font-semibold text-foreground">Danh sách bệnh nhân</h3>
                <p className="text-xs text-muted-foreground">Sắp xếp theo mức độ: nguy hiểm, cần chú ý, bình thường</p>
              </div>
              <span className="ml-auto rounded-full bg-accent px-2 py-0.5 text-xs font-medium text-primary">
                {filtered.length === patients.length ? patients.length : `${filtered.length}/${patients.length}`}
              </span>
            </div>
            <div className="flex items-center gap-3 overflow-x-auto border-b border-border p-4">
              <div className="relative min-w-[16rem] flex-1">
                <Search className="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
                <input
                  type="search"
                  value={search}
                  onChange={(event) => setSearch(event.target.value)}
                  placeholder="Tìm bệnh nhân theo tên..."
                  aria-label="Tìm bệnh nhân theo tên"
                  className="w-full rounded-xl border border-border bg-background py-2.5 pl-9 pr-4 text-sm outline-none transition-colors placeholder:text-muted-foreground focus:border-primary focus:ring-2 focus:ring-primary/20"
                />
              </div>
              <Select<AlertFilter>
                value={filter}
                onChange={setFilter}
                size="large"
                className="w-44 shrink-0"
                aria-label="Lọc theo trạng thái"
                options={[
                  { value: "all", label: `Tất cả trạng thái (${patients.length})` },
                  { value: "critical", label: `Nguy hiểm (${criticalCount})` },
                  { value: "warning", label: `Cần chú ý (${warningCount})` },
                  { value: "normal", label: `Bình thường (${normalCount})` },
                ]}
              />
              <Select<GenderFilter>
                value={genderFilter}
                onChange={setGenderFilter}
                size="large"
                className="w-40 shrink-0"
                aria-label="Lọc theo giới tính"
                options={[
                  { value: "all", label: "Tất cả giới tính" },
                  { value: "male", label: "Nam" },
                  { value: "female", label: "Nữ" },
                  { value: "unknown", label: "Chưa rõ" },
                ]}
              />
            </div>
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead className="bg-muted/50">
                  <tr className="border-b border-border">
                    {['Bệnh nhân', 'Thông tin', 'Trạng thái', 'Đường huyết gần nhất', 'Lý do cảnh báo'].map((heading) => (
                      <th key={heading} className="px-4 py-3 text-left font-medium text-muted-foreground">{heading}</th>
                    ))}
                    <th className="px-4 py-3 text-right font-medium text-muted-foreground">Thao tác</th>
                  </tr>
                </thead>
                <tbody>
                  {!loading && filtered.length === 0 && (
                    <tr><td colSpan={6} className="px-4 py-10 text-center text-muted-foreground">Không tìm thấy bệnh nhân phù hợp.</td></tr>
                  )}
                  {overviewPatients.map((patient) => {
                    const cfg =
                      alertConfig[
                        patient.alertLevel as keyof typeof alertConfig
                      ];
                    return (
                      <tr key={patient.id} className="border-b border-border last:border-0 hover:bg-muted/40">
                        <td className="whitespace-nowrap px-4 py-3">
                          <div className="flex items-center gap-3">
                            <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded-xl bg-accent">
                              <User className="h-4 w-4 text-primary" />
                            </div>
                            <span className="font-semibold text-foreground">{patient.name}</span>
                          </div>
                        </td>
                        <td className="whitespace-nowrap px-4 py-3">
                          <p className="text-sm text-foreground">{patient.age > 0 ? `${patient.age} tuổi` : "Chưa rõ"} · {patient.gender}</p>
                          <p className="mt-0.5 text-xs text-muted-foreground">{patient.disease}</p>
                        </td>
                        <td className="px-4 py-3">
                          <span className={cn("inline-flex items-center gap-1.5 whitespace-nowrap rounded-full border px-2.5 py-1 text-xs font-medium", cfg.badge)}>
                            <span className={cn("h-1.5 w-1.5 rounded-full", cfg.dot)} />{cfg.label}
                          </span>
                        </td>
                        <td className="whitespace-nowrap px-4 py-3">
                          <Droplets className="mr-1.5 inline h-3.5 w-3.5 text-[#43B5AE]" />
                          <span className="font-semibold text-foreground">{patient.lastGlucose || "—"}</span>
                          {patient.lastGlucose > 0 && <span className="ml-1 text-xs text-muted-foreground">mg/dL</span>}
                          <p className="mt-0.5 text-[11px] text-muted-foreground">{patient.lastGlucoseTime}</p>
                        </td>
                        <td className="min-w-64 px-4 py-3 text-xs leading-relaxed text-muted-foreground">{getAlertReason(patient)}</td>
                        <td className="px-4 py-3 text-right">
                          <button type="button" onClick={() => onViewPatient(patient.id)} className="inline-flex items-center gap-1 whitespace-nowrap rounded-lg px-2.5 py-1.5 text-xs font-medium text-primary transition-colors hover:bg-primary/10">
                            Xem chi tiết<ChevronRight className="h-3.5 w-3.5" />
                          </button>
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
            <Pagination
              page={patientPage}
              totalItems={prioritizedPatients.length}
              pageSize={PAGE_SIZE}
              onPageChange={setPatientPage}
            />
          </section>

          <div>
            <section className="rounded-2xl border border-border bg-card p-5">
              <div className="mb-4 flex items-center gap-2">
                <CalendarDays className="h-4 w-4 text-primary" />
                <div><h3 className="font-semibold text-foreground">Lịch hôm nay</h3><p className="text-xs text-muted-foreground">Các lịch tái khám sắp tới</p></div>
                <span className="ml-auto rounded-full bg-accent px-2 py-0.5 text-xs font-medium text-primary">{today.length}</span>
              </div>
              {today.length === 0 ? (
                <p className="rounded-xl bg-muted p-6 text-center text-sm text-muted-foreground">Không có lịch tái khám nào hôm nay.</p>
              ) : (
                <div className="space-y-2">
                  {today.map((appointment) => (
                    <div key={appointment.id} className="flex items-center gap-3 rounded-xl bg-muted p-3">
                      <div className="flex h-9 w-9 flex-shrink-0 items-center justify-center rounded-xl bg-accent"><Clock className="h-4 w-4 text-primary" /></div>
                      <div className="min-w-0 flex-1">
                        <p className="truncate text-sm font-medium text-foreground">{patientNames.get(appointment.patient) || appointment.patient}</p>
                        <p className="truncate text-xs text-muted-foreground">{appointment.type}</p>
                      </div>
                      <p className="text-sm font-semibold text-foreground">{appointment.time}</p>
                    </div>
                  ))}
                </div>
              )}
            </section>

          </div>

        </div>
      )}

      {/* Appointment management */}
      {section === "appointments" && (
        <section className="rounded-2xl border border-border bg-card p-5">
          <div className="mb-4 flex items-center gap-2">
            <ClipboardList className="h-4 w-4 text-primary" />
            <div>
              <h3 className="font-semibold text-foreground">Lịch tái khám</h3>
              <p className="text-xs text-muted-foreground">
                Các lịch hẹn được phân công cho bạn
              </p>
            </div>
          </div>
          {appointments.length === 0 ? (
            <p className="rounded-xl bg-muted p-6 text-center text-sm text-muted-foreground">
              Chưa có lịch hẹn.
            </p>
          ) : (
            <div className="space-y-2">
              {appointments.map((appointment) => {
                const status = {
                  scheduled: {
                    label: "Đã lên lịch",
                    className: "bg-blue-50 text-blue-700 border-blue-200",
                  },
                  completed: {
                    label: "Hoàn tất",
                    className: "bg-green-50 text-green-700 border-green-200",
                  },
                  cancelled: {
                    label: "Đã hủy",
                    className: "bg-red-50 text-red-700 border-red-200",
                  },
                }[appointment.status];
                return (
                  <article
                    key={appointment.id}
                    className="rounded-xl border border-border p-4"
                  >
                    <div className="flex flex-wrap items-start justify-between gap-3">
                      <div className="min-w-0">
                        <p className="font-medium text-foreground">
                          {patientNames.get(appointment.patient) ||
                            appointment.patient}
                        </p>
                        <p className="mt-1 text-sm text-muted-foreground">
                          {appointment.date} · {appointment.time}
                        </p>
                        <p className="mt-1 text-xs text-muted-foreground">
                          {appointment.type}
                        </p>
                        {appointment.notes && (
                          <p className="mt-2 text-xs leading-relaxed text-foreground/70">
                            {appointment.notes}
                          </p>
                        )}
                      </div>
                      <span
                        className={cn(
                          "rounded-full border px-2.5 py-1 text-xs font-medium",
                          status.className,
                        )}
                      >
                        {status.label}
                      </span>
                    </div>
                  </article>
                );
              })}
            </div>
          )}
        </section>
      )}
      {/* Patient list */}
      {section === "patients" && (
        <section>
          {/* Search */}
          <div className="mb-3 flex gap-2">
            <div className="relative flex-1">
              <Search className="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />

              <input
                type="text"
                value={search}
                onChange={(e) => setSearch(e.target.value)}
                placeholder="Tìm bệnh nhân..."
                className="w-full rounded-xl border border-border bg-card py-2.5 pl-9 pr-4 text-sm transition-colors focus:border-primary focus:outline-none focus:ring-2 focus:ring-primary/30"
              />
            </div>
          </div>

          {/* Filter */}
          <div className="mb-4 flex gap-2 overflow-x-auto pb-1">
            {(["all", "critical", "warning", "normal"] as AlertFilter[]).map(
              (f) => (
                <button
                  key={f}
                  onClick={() => setFilter(f)}
                  className={cn(
                    "flex-shrink-0 rounded-xl border px-3 py-1.5 text-xs font-medium transition-colors",
                    filter === f
                      ? "border-primary bg-primary text-primary-foreground"
                      : "border-border bg-card text-muted-foreground hover:text-foreground",
                  )}
                >
                  {f === "all" ? "Tất cả" : alertConfig[f].label}

                  {f !== "all" && (
                    <span className="ml-1.5">
                      (
                      {f === "critical"
                        ? criticalCount
                        : f === "warning"
                          ? warningCount
                          : normalCount}
                      )
                    </span>
                  )}
                </button>
              ),
            )}
          </div>

          {/* Table */}
          <div className="overflow-hidden rounded-2xl border border-border bg-card">
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead className="bg-muted/50">
                  <tr className="border-b border-border">
                    <th className="px-4 py-3 text-left font-medium text-muted-foreground">
                      Bệnh nhân
                    </th>

                    <th className="px-4 py-3 text-left font-medium text-muted-foreground">
                      Thông tin
                    </th>

                    <th className="px-4 py-3 text-left font-medium text-muted-foreground">
                      Trạng thái
                    </th>

                    <th className="px-4 py-3 text-left font-medium text-muted-foreground">
                      Cảnh báo
                    </th>

                    <th className="px-4 py-3 text-left font-medium text-muted-foreground">
                      Đường huyết
                    </th>

                    <th className="px-4 py-3 text-left font-medium text-muted-foreground">
                      Cập nhật
                    </th>

                    <th className="px-4 py-3 text-right font-medium text-muted-foreground">
                      Thao tác
                    </th>
                  </tr>
                </thead>

                <tbody>
                  {!loading && filtered.length === 0 && (
                    <tr>
                      <td
                        colSpan={7}
                        className="px-4 py-10 text-center text-sm text-muted-foreground"
                      >
                        Không có bệnh nhân phù hợp.
                      </td>
                    </tr>
                  )}

                  {paginatedPatients.map((patient) => {
                    const cfg =
                      alertConfig[
                        patient.alertLevel as keyof typeof alertConfig
                      ];

                    return (
                      <tr
                        key={patient.id}
                        onClick={() => onViewPatient(patient.id)}
                        className="cursor-pointer border-b border-border transition-colors last:border-b-0 hover:bg-muted/40"
                      >
                        {/* Patient */}
                        <td className="px-4 py-3">
                          <div className="flex items-center gap-3">
                            <div className="flex h-9 w-9 flex-shrink-0 items-center justify-center rounded-xl bg-accent">
                              <User className="h-4 w-4 text-muted-foreground" />
                            </div>

                            <span className="whitespace-nowrap font-semibold text-foreground">
                              {patient.name}
                            </span>
                          </div>
                        </td>

                        {/* Info */}
                        <td className="px-4 py-3">
                          <div className="whitespace-nowrap text-sm text-foreground">
                            {patient.age > 0 ? `${patient.age} tuổi` : "Chưa rõ"} · {patient.gender}
                          </div>

                          <div className="mt-0.5 text-xs text-muted-foreground">
                            {patient.disease}
                          </div>
                        </td>

                        {/* Status */}
                        <td className="px-4 py-3">
                          <span
                            className={cn(
                              "inline-flex items-center whitespace-nowrap rounded-full border px-2.5 py-1 text-xs font-medium",
                              cfg.badge,
                            )}
                          >
                            <span
                              className={cn(
                                "mr-1.5 h-1.5 w-1.5 rounded-full",
                                cfg.dot,
                              )}
                            />

                            {cfg.label}
                          </span>
                        </td>

                        {/* Alert */}
                        <td className="max-w-[260px] px-4 py-3">
                          {patient.alertMessage ? (
                            <p
                              className="truncate text-xs text-foreground/70"
                              title={patient.alertMessage}
                            >
                              {patient.alertMessage}
                            </p>
                          ) : (
                            <span className="text-xs text-muted-foreground">
                              —
                            </span>
                          )}
                        </td>

                        {/* Glucose */}
                        <td className="px-4 py-3">
                          <div className="flex items-center gap-1.5 whitespace-nowrap">
                            <Droplets className="h-3.5 w-3.5 text-[#43B5AE]" />

                            <span
                              className={cn(
                                "font-bold",
                                patient.alertLevel === "critical"
                                  ? "text-red-500"
                                  : patient.alertLevel === "warning"
                                    ? "text-yellow-600"
                                    : "text-green-600",
                              )}
                            >
                              {patient.lastGlucose}
                            </span>

                            <span className="text-xs text-muted-foreground">
                              mg/dL
                            </span>
                          </div>

                          <p className="mt-0.5 text-[11px] text-muted-foreground">
                            {patient.lastGlucoseTime}
                          </p>
                        </td>

                        {/* Updated */}
                        <td className="whitespace-nowrap px-4 py-3 text-xs text-muted-foreground">
                          {patient.updatedAt}
                        </td>

                        {/* Action */}
                        <td className="px-4 py-3 text-right">
                          <button
                            type="button"
                            onClick={(e) => {
                              e.stopPropagation();
                              onViewPatient(patient.id);
                            }}
                            className="inline-flex items-center gap-1 whitespace-nowrap rounded-lg px-2.5 py-1.5 text-xs font-medium text-primary transition-colors hover:bg-primary/10"
                          >
                            Xem chi tiết
                            <ChevronRight className="h-3.5 w-3.5" />
                          </button>
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
            <Pagination
              page={patientPage}
              totalItems={filtered.length}
              pageSize={PAGE_SIZE}
              onPageChange={setPatientPage}
            />
          </div>
        </section>
      )}
    </div>
  );
}
