"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { weightData7Days } from "@/lib/mock-data";
import { StatusBadge, type StatusType } from "@/components/ui/status-badge";
import { Pagination } from "@/components/ui/pagination";
import {
  Plus,
  Search,
  TrendingUp,
  TrendingDown,
  Target,
  Clock3,
  Footprints,
} from "lucide-react";
import { cn } from "@/lib/utils";
import {
  LineChart,
  Line,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  ResponsiveContainer,
  BarChart,
  Bar,
} from "recharts";
import { RecordModal, type RecordType } from "@/components/record-modal";
import {
  api,
  glucoseDisplayNote,
  glucoseTimingLabel,
  type ActivityRecord,
  type BloodPressureRecord,
  type GlucoseRecord,
} from "@/lib/api";
import { useToast } from "@/components/ui/toast";
import { useAuth } from "@/lib/auth-context";
import { PatientInsightsPanel } from "@/components/patient-insights-panel";
import {
  GlucoseTrendChart,
  glucoseRecordsInRange,
  RANGE_LABELS,
  type GlucoseRange,
} from "@/components/glucose-trend-chart";

type HistoryRange = GlucoseRange | "all";

const HISTORY_RANGE_LABELS: Record<HistoryRange, string> = {
  all: "Mọi thời gian",
  ...RANGE_LABELS,
};

type Tab = "glucose" | "bp" | "weight" | "activity" | "insights";

const tabs: { id: Tab; label: string }[] = [
  { id: "glucose", label: "Đường huyết" },
  { id: "bp", label: "Huyết áp" },
  { id: "weight", label: "Cân nặng" },
  { id: "activity", label: "Hoạt động" },
  { id: "insights", label: "Nhận định AI" },
];

const activityLabels: Record<string, string> = {
  walking: "Đi bộ",
  brisk_walking: "Đi bộ nhanh",
  cycling: "Đạp xe",
  swimming: "Bơi lội",
  yoga: "Yoga",
  tai_chi: "Thái cực quyền",
  strength_training: "Tập sức mạnh nhẹ",
  aerobics: "Thể dục nhịp điệu",
  stretching: "Giãn cơ",
  housework: "Làm việc nhà",
};

function StatCard({
  label,
  value,
  sub,
  icon,
  color,
}: {
  label: string;
  value: string;
  sub: string;
  icon: React.ReactNode;
  color: string;
}) {
  return (
    <div className={cn("rounded-2xl p-4 flex flex-col gap-2", color)}>
      <div className="flex items-center justify-between">
        <span className="text-xs font-medium opacity-70">{label}</span>
        {icon}
      </div>
      <p className="text-2xl font-bold">{value}</p>
      <p className="text-xs opacity-60">{sub}</p>
    </div>
  );
}

export function HealthMonitoring() {
  const { showToast } = useToast();
  const { user } = useAuth();
  const router = useRouter();
  const pathname = usePathname();
  const searchParams = useSearchParams();
  const requestedRecord = searchParams.get("record");
  const recordType: RecordType =
    requestedRecord === "bp" ||
    requestedRecord === "weight" ||
    requestedRecord === "activity"
      ? requestedRecord
      : "glucose";
  const [tab, setTab] = useState<Tab>(recordType);
  const [records, setRecords] = useState<GlucoseRecord[]>([]);
  const [bloodPressureRecords, setBloodPressureRecords] = useState<BloodPressureRecord[]>([]);
  const [activityRecords, setActivityRecords] = useState<ActivityRecord[]>([]);
  const [recordOpen, setRecordOpen] = useState(Boolean(requestedRecord));
  const [glucoseRange, setGlucoseRange] = useState<GlucoseRange>("7d");
  const [historyPage, setHistoryPage] = useState(1);
  const [historySearch, setHistorySearch] = useState("");
  const [historyStatus, setHistoryStatus] = useState("all");
  const [historyTiming, setHistoryTiming] = useState("all");
  const [historyRange, setHistoryRange] = useState<HistoryRange>("all");

  useEffect(() => {
    if (!requestedRecord) return;

    setTab(recordType);
    setRecordOpen(true);

    const nextSearchParams = new URLSearchParams(searchParams.toString());
    nextSearchParams.delete("record");
    const nextQuery = nextSearchParams.toString();
    router.replace(nextQuery ? `${pathname}?${nextQuery}` : pathname, { scroll: false });
  }, [pathname, recordType, requestedRecord, router, searchParams]);

  const loadGlucose = useCallback(() => {
    api
      .glucose()
      .then(setRecords)
      .catch((error) =>
        showToast(
          error instanceof Error ? error.message : "Không thể tải dữ liệu",
          "error",
        ),
      );
  }, [showToast]);

  const loadBloodPressure = useCallback(() => {
    api
      .bloodPressure()
      .then(setBloodPressureRecords)
      .catch((error) =>
        showToast(
          error instanceof Error ? error.message : "Không thể tải dữ liệu huyết áp",
          "error",
        ),
      );
  }, [showToast]);

  const loadHealthRecords = useCallback(() => {
    loadGlucose();
    loadBloodPressure();
    api.activities().then(setActivityRecords).catch((error) =>
      showToast(error instanceof Error ? error.message : "Không thể tải dữ liệu hoạt động", "error"),
    );
  }, [loadBloodPressure, loadGlucose, showToast]);

  useEffect(() => {
    loadHealthRecords();
  }, [loadHealthRecords]);

  const historyRangedRecords = useMemo(
    () => (historyRange === "all" ? records : glucoseRecordsInRange(records, historyRange)),
    [records, historyRange],
  );

  const history = useMemo(
    () =>
      // glucoseRecordsInRange sorts oldest-first (what the trend chart needs
      // to draw a line left-to-right) — a history log reads the other way,
      // most recent entry on top, regardless of which range filter (or
      // none) produced this list.
      [...historyRangedRecords]
        .sort((a, b) => new Date(b.measured_at).getTime() - new Date(a.measured_at).getTime())
        .map((row) => {
          const date = new Date(row.measured_at);
          return {
            id: row.id,
            value: row.value,
            date: date.toLocaleDateString("vi-VN"),
            time: date.toLocaleTimeString("vi-VN", {
              hour: "2-digit",
              minute: "2-digit",
            }),
            timing: glucoseTimingLabel(row.meal_context),
            note: glucoseDisplayNote(row.note),
            // Prefer the backend's personalized threshold_status over a
            // fixed cutoff — see the inRange comment above for why. WATCH
            // has no fixed high/low split of its own (the model flags
            // "outside personal band" either direction), so it's split here
            // by comparing against the record's own predicted band.
            status:
              row.threshold_status === "DANGER"
                ? "critical"
                : row.threshold_status === "WATCH"
                  ? row.predicted_min_mg_dl != null && row.value < row.predicted_min_mg_dl
                    ? "low"
                    : "high"
                  : row.threshold_status === "NORMAL"
                    ? "normal"
                    : row.value < 70
                      ? "low"
                      : row.value > 180
                        ? "critical"
                        : row.value > 140
                          ? "high"
                          : "normal",
          };
        }),
    [historyRangedRecords],
  );

  const historyTimings = useMemo(
    () => [...new Set(history.map((row) => row.timing))],
    [history],
  );
  const filteredHistory = useMemo(() => {
    const query = historySearch.trim().toLocaleLowerCase("vi-VN");
    return history.filter((row) => {
      const matchesSearch =
        !query ||
        [row.value, row.date, row.time, row.timing, row.note]
          .join(" ")
          .toLocaleLowerCase("vi-VN")
          .includes(query);
      const matchesStatus = historyStatus === "all" || row.status === historyStatus;
      const matchesTiming = historyTiming === "all" || row.timing === historyTiming;
      return matchesSearch && matchesStatus && matchesTiming;
    });
  }, [history, historySearch, historyStatus, historyTiming]);

  useEffect(() => {
    setHistoryPage(1);
  }, [historySearch, historyStatus, historyTiming, historyRange]);

  const historyPageCount = Math.max(1, Math.ceil(filteredHistory.length / 10));
  const currentHistoryPage = Math.min(historyPage, historyPageCount);
  const paginatedHistory = filteredHistory.slice((currentHistoryPage - 1) * 10, currentHistoryPage * 10);

  const rangedRecords = useMemo(
    () => glucoseRecordsInRange(records, glucoseRange),
    [records, glucoseRange],
  );

  const avg = rangedRecords.length
    ? Math.round(rangedRecords.reduce((sum, row) => sum + row.value, 0) / rangedRecords.length)
    : 0;
  const max = rangedRecords.length ? Math.max(...rangedRecords.map((row) => row.value)) : 0;
  const min = rangedRecords.length ? Math.min(...rangedRecords.map((row) => row.value)) : 0;
  // Prefer the backend's personalized threshold_status (doctor-approved
  // CarePlanTarget + AI-predicted band, see health/threshold_service.py)
  // over a fixed 70-140 cutoff — that fixed range doesn't reflect this
  // patient's actual target and made "Trong mục tiêu" read 0% for patients
  // whose doctor-approved/personalized range runs higher. Records that
  // predate this feature or belong to a patient with no CarePlanTarget yet
  // (threshold_status null/UNAVAILABLE) fall back to the old fixed cutoff
  // rather than being silently excluded from the denominator.
  const inRange = rangedRecords.filter((row) =>
    row.threshold_status === "NORMAL" ||
    (row.threshold_status == null || row.threshold_status === "UNAVAILABLE"
      ? row.value >= 70 && row.value <= 140
      : false),
  ).length;
  const pct = rangedRecords.length ? Math.round((inRange / rangedRecords.length) * 100) : 0;

  const bloodPressureChartData = [...bloodPressureRecords]
    .filter((row) => Number.isFinite(new Date(row.measured_at).getTime()))
    .sort((a, b) => new Date(a.measured_at).getTime() - new Date(b.measured_at).getTime())
    .slice(-7)
    .map((row) => ({
      date: new Date(row.measured_at).toLocaleDateString("vi-VN", { day: "2-digit", month: "2-digit" }),
      systolic: row.systolic,
      diastolic: row.diastolic,
    }));
  return (
    <div className="space-y-5 max-w-5xl mx-auto">
      {/* Tabs */}
      <div className="flex gap-1 bg-card rounded-2xl p-1.5 border border-border overflow-x-auto scrollbar-none">
        {tabs.map((t) => (
          <button
            key={t.id}
            onClick={() => setTab(t.id)}
            className={cn(
              "px-3 py-2 rounded-xl text-sm font-medium transition-colors whitespace-nowrap flex-shrink-0",
              tab === t.id
                ? "bg-primary text-primary-foreground"
                : "text-muted-foreground hover:text-foreground hover:bg-muted",
            )}
          >
            {t.label}
          </button>
        ))}
      </div>

      {tab === 'glucose' && (
        <>
          {/* Stats */}
          <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
            <StatCard
              label="Trung bình"
              value={`${avg}`}
              sub="mg/dL"
              icon={<Target className="w-4 h-4" />}
              color="bg-[#DFF7F3] text-[#2D9D97]"
            />
            <StatCard
              label="Cao nhất"
              value={`${max}`}
              sub="mg/dL"
              icon={<TrendingUp className="w-4 h-4" />}
              color="bg-orange-50 text-orange-600"
            />
            <StatCard
              label="Thấp nhất"
              value={`${min}`}
              sub="mg/dL"
              icon={<TrendingDown className="w-4 h-4" />}
              color="bg-blue-50 text-blue-600"
            />
            <StatCard
              label="Trong mục tiêu"
              value={`${pct}%`}
              sub={`${inRange}/${rangedRecords.length} lần đo`}
              icon={<Target className="w-4 h-4" />}
              color="bg-card border border-border text-foreground"
            />
          </div>

          {/* Chart */}
          <div className="bg-card rounded-2xl p-5 border border-border">
            <div className="flex justify-end mb-2">
              <button
                onClick={() => setRecordOpen(true)}
                className="flex items-center gap-1.5 px-3 py-2 rounded-xl bg-primary text-primary-foreground text-sm font-medium hover:bg-[#2D9D97] transition-colors"
              >
                <Plus className="w-4 h-4" /> Ghi mới
              </button>
            </div>
            <GlucoseTrendChart
              records={records}
              range={glucoseRange}
              onRangeChange={setGlucoseRange}
              availableRanges={["24h", "7d"]}
            />
          </div>

          {/* History – table on desktop, cards on mobile */}
          <div className="bg-card rounded-2xl border border-border overflow-hidden">
            <div className="flex items-center justify-between p-5 pb-4">
              <h3 className="font-semibold text-foreground">
                Lịch sử đo đường huyết
              </h3>
              <span className="text-xs text-muted-foreground">
                {filteredHistory.length === history.length
                  ? `${history.length} bản ghi`
                  : `${filteredHistory.length}/${history.length} bản ghi`}
              </span>
            </div>

            <div className="grid gap-2 border-y border-border bg-muted/20 p-3 sm:grid-cols-[minmax(0,1fr)_auto_auto_auto] sm:px-5">
              <label className="relative min-w-0">
                <span className="sr-only">Tìm trong lịch sử đường huyết</span>
                <Search className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
                <input
                  type="search"
                  value={historySearch}
                  onChange={(event) => setHistorySearch(event.target.value)}
                  placeholder="Tìm theo ngày, giá trị, ghi chú..."
                  className="h-10 w-full rounded-xl border border-border bg-card pl-9 pr-3 text-sm outline-none transition focus:border-primary focus:ring-2 focus:ring-primary/15"
                />
              </label>
              <label>
                <span className="sr-only">Lọc theo khoảng thời gian</span>
                <select
                  value={historyRange}
                  onChange={(event) => setHistoryRange(event.target.value as HistoryRange)}
                  className="h-10 w-full rounded-xl border border-border bg-card px-3 text-sm text-foreground outline-none focus:border-primary sm:w-36"
                >
                  {(Object.keys(HISTORY_RANGE_LABELS) as HistoryRange[]).map((key) => (
                    <option key={key} value={key}>{HISTORY_RANGE_LABELS[key]}</option>
                  ))}
                </select>
              </label>
              <label>
                <span className="sr-only">Lọc theo trạng thái</span>
                <select
                  value={historyStatus}
                  onChange={(event) => setHistoryStatus(event.target.value)}
                  className="h-10 w-full rounded-xl border border-border bg-card px-3 text-sm text-foreground outline-none focus:border-primary sm:w-36"
                >
                  <option value="all">Mọi trạng thái</option>
                  <option value="normal">Bình thường</option>
                  <option value="high">Cao</option>
                  <option value="low">Thấp</option>
                  <option value="critical">Nguy hiểm</option>
                </select>
              </label>
              <label>
                <span className="sr-only">Lọc theo thời điểm đo</span>
                <select
                  value={historyTiming}
                  onChange={(event) => setHistoryTiming(event.target.value)}
                  className="h-10 w-full rounded-xl border border-border bg-card px-3 text-sm text-foreground outline-none focus:border-primary sm:w-40"
                >
                  <option value="all">Mọi thời điểm</option>
                  {historyTimings.map((timing) => (
                    <option key={timing} value={timing}>{timing}</option>
                  ))}
                </select>
              </label>
            </div>

            {/* Desktop table */}
            <div className="hidden md:block overflow-x-auto">
              <table className="w-full text-sm">
                <thead className="bg-muted/50 border-b border-border">
                  <tr>
                    {[
                      "Ngày",
                      "Giờ",
                      "Giá trị",
                      "Thời điểm",
                      "Ghi chú",
                      "Trạng thái",
                    ].map((h) => (
                      <th
                        key={h}
                        className="px-4 py-3 text-left text-xs font-semibold text-muted-foreground"
                      >
                        {h}
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody className="divide-y divide-border">
                  {paginatedHistory.map((row) => (
                    <tr
                      key={row.id}
                      className="hover:bg-muted/30 transition-colors"
                    >
                      <td className="px-4 py-3 text-sm">{row.date}</td>
                      <td className="px-4 py-3 text-sm text-muted-foreground">
                        {row.time}
                      </td>
                      <td className="px-4 py-3 font-semibold text-foreground">
                        {row.value}{" "}
                        <span className="text-xs text-muted-foreground font-normal">
                          mg/dL
                        </span>
                      </td>
                      <td className="px-4 py-3 text-sm text-muted-foreground">
                        {row.timing}
                      </td>
                      <td className="px-4 py-3 text-sm text-muted-foreground">
                        {row.note || "—"}
                      </td>
                      <td className="px-4 py-3">
                        <StatusBadge status={row.status as StatusType} />
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>

            {/* Mobile card list */}
            <div className="md:hidden divide-y divide-border">
              {paginatedHistory.map((row) => (
                <div
                  key={row.id}
                  className="p-4 flex items-start justify-between gap-3"
                >
                  <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-2 mb-1">
                      <span className="text-lg font-bold text-foreground">
                        {row.value}
                      </span>
                      <span className="text-xs text-muted-foreground">
                        mg/dL
                      </span>
                      <StatusBadge
                        status={row.status as StatusType}
                        className="ml-auto"
                      />
                    </div>
                    <p className="text-xs text-muted-foreground">
                      {row.date} {row.time} · {row.timing}
                    </p>
                    {row.note && (
                      <p className="text-xs text-muted-foreground mt-0.5 italic">
                        {row.note}
                      </p>
                    )}
                  </div>
                </div>
              ))}
            </div>
            {filteredHistory.length === 0 && (
              <div className="px-5 py-10 text-center">
                <Search className="mx-auto h-6 w-6 text-muted-foreground/60" />
                <p className="mt-2 text-sm font-medium text-foreground">Không tìm thấy bản ghi</p>
                <p className="mt-1 text-xs text-muted-foreground">Thử thay đổi từ khóa hoặc bộ lọc.</p>
              </div>
            )}
            <Pagination page={currentHistoryPage} totalItems={filteredHistory.length} onPageChange={setHistoryPage} />
          </div>
        </>
      )}

      {tab === "bp" && (
        <div className="bg-card rounded-2xl p-5 border border-border">
          <h3 className="font-semibold text-foreground mb-4">
            Biểu đồ huyết áp 7 ngày
          </h3>
          <ResponsiveContainer width="100%" height={240}>
            <BarChart
              data={bloodPressureChartData}
              margin={{ top: 4, right: 4, left: -20, bottom: 0 }}
            >
              <CartesianGrid strokeDasharray="3 3" stroke="var(--border)" />
              <XAxis dataKey="date" tick={{ fontSize: 11, fill: "var(--muted-foreground)" }} />
              <YAxis tick={{ fontSize: 11, fill: "var(--muted-foreground)" }} domain={[60, 160]} />
              <Tooltip
                contentStyle={{
                  borderRadius: "12px",
                  border: "1px solid var(--border)",
                  backgroundColor: "var(--popover)",
                  color: "var(--popover-foreground)",
                  fontSize: "12px",
                }}
              />
              <Bar
                dataKey="systolic"
                name="Tâm thu"
                fill="var(--primary)"
                radius={[4, 4, 0, 0]}
              />
              <Bar
                dataKey="diastolic"
                name="Tâm trương"
                fill="var(--accent-foreground)"
                radius={[4, 4, 0, 0]}
              />
            </BarChart>
          </ResponsiveContainer>
        </div>
      )}

      {tab === "weight" && (
        <div className="bg-card rounded-2xl p-5 border border-border">
          <h3 className="font-semibold text-foreground mb-4">
            Cân nặng 7 ngày
          </h3>
          <ResponsiveContainer width="100%" height={240}>
            <LineChart
              data={weightData7Days}
              margin={{ top: 4, right: 4, left: -20, bottom: 0 }}
            >
              <CartesianGrid strokeDasharray="3 3" stroke="var(--border)" />
              <XAxis dataKey="date" tick={{ fontSize: 11, fill: "var(--muted-foreground)" }} />
              <YAxis tick={{ fontSize: 11, fill: "var(--muted-foreground)" }} domain={[66, 72]} />
              <Tooltip
                contentStyle={{ borderRadius: "12px", border: "1px solid var(--border)", backgroundColor: "var(--popover)", color: "var(--popover-foreground)", fontSize: "12px" }}
                formatter={(v) => [`${v ?? 0} kg`, "Cân nặng"]}
              />
              <Line
                type="monotone"
                dataKey="value"
                stroke="var(--primary)"
                strokeWidth={2.5}
                dot={{ r: 4, fill: "var(--primary)" }}
                name="Cân nặng"
              />
            </LineChart>
          </ResponsiveContainer>
        </div>
      )}

      {tab === 'activity' && (
        <div className='space-y-4'>
          <div className='grid grid-cols-2 gap-3'>
            <StatCard label='Số buổi' value={`${activityRecords.length}`} sub='đã ghi nhận' icon={<Footprints className='h-4 w-4' />} color='bg-orange-50 text-orange-600' />
            <StatCard label='Tổng thời lượng' value={`${activityRecords.reduce((sum, row) => sum + row.duration_minutes, 0)}`} sub='phút' icon={<Clock3 className='h-4 w-4' />} color='bg-[#DFF7F3] text-[#2D9D97]' />
          </div>
          <div className='overflow-hidden rounded-2xl border border-border bg-card'>
            <div className='flex items-center justify-between border-b border-border p-5'>
              <div><h3 className='font-semibold'>Lịch sử hoạt động</h3><p className='mt-1 text-xs text-muted-foreground'>Vận động đều đặn hỗ trợ kiểm soát đường huyết</p></div>
              <button type='button' onClick={() => setRecordOpen(true)} className='flex items-center gap-1.5 rounded-xl bg-primary px-3 py-2 text-sm font-medium text-primary-foreground'><Plus className='h-4 w-4' /> Ghi hoạt động</button>
            </div>
            <div className='divide-y divide-border'>
              {activityRecords.map((row) => (
                <div key={row.id} className='flex items-start justify-between gap-4 p-4'>
                  <div className='flex gap-3'><span className='rounded-xl bg-orange-50 p-2 text-orange-500'><Footprints className='h-4 w-4' /></span><div><p className='font-medium'>{activityLabels[row.activity_type] ?? row.activity_type}</p><p className='text-xs text-muted-foreground'>{new Date(row.activity_time).toLocaleString('vi-VN')}{row.note ? ` · ${row.note}` : ''}</p></div></div>
                  <strong className='whitespace-nowrap text-sm'>{row.duration_minutes} phút</strong>
                </div>
              ))}
              {activityRecords.length === 0 && <div className='p-8 text-center text-sm text-muted-foreground'>Chưa có hoạt động nào được ghi nhận.</div>}
            </div>
          </div>
        </div>
      )}

      {tab === 'insights' && user && <PatientInsightsPanel patientId={user.id} audience="patient" />}

      <RecordModal
        open={recordOpen}
        onClose={() => setRecordOpen(false)}
        defaultType={tab === 'bp' || tab === 'weight' || tab === 'activity' ? tab : recordType}
        onSaved={loadHealthRecords}
      />
    </div>
  );
}
