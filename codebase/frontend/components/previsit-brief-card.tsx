"use client";

import { useEffect, useState } from "react";
import { AlertTriangle, CalendarClock, Loader2, Printer, RefreshCw } from "lucide-react";
import { api, type PrevisitBrief, type UpcomingAppointment } from "@/lib/api";
import { cn, parseUtcTimestamp } from "@/lib/utils";

// Epic 4: the brief the hourly job prepared for this patient's next
// appointment (codebase/backend/appointments/briefs.py). Shown only when the
// patient has a scheduled appointment in the next 30 days.

const SECTIONS: { key: "changes" | "meals" | "medication_adherence"; title: string }[] = [
  { key: "changes", title: "Thay đổi kể từ lần khám trước" },
  { key: "meals", title: "Bữa ăn liên quan tăng đường huyết" },
  { key: "medication_adherence", title: "Tuân thủ thuốc" },
];
const NO_DATA = "Chưa đủ dữ liệu.";

const escapeHtml = (value: unknown) =>
  String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");

const formatDate = (value: string) => parseUtcTimestamp(value).toLocaleDateString("vi-VN");
const formatDateTime = (value: string) =>
  parseUtcTimestamp(value).toLocaleString("vi-VN", { dateStyle: "short", timeStyle: "short" });

function periodLabel(brief: PrevisitBrief): string {
  const range = `${formatDate(brief.period_start)} – ${formatDate(brief.period_end)}`;
  return brief.previous_visit_at
    ? `Từ lần khám trước (${formatDate(brief.previous_visit_at)}): ${range}, ${brief.period_days} ngày`
    : `${brief.period_days} ngày gần nhất (chưa có lần khám trước trong hệ thống): ${range}`;
}

function printBrief(brief: PrevisitBrief, patientName: string) {
  const reportWindow = window.open("", "_blank", "width=900,height=800");
  if (!reportWindow) {
    window.alert("Trình duyệt đang chặn cửa sổ báo cáo. Vui lòng cho phép cửa sổ bật lên và thử lại.");
    return;
  }
  reportWindow.opener = null;
  const list = (items: string[] | undefined) =>
    items && items.length
      ? `<ul>${items.map((item) => `<li>${escapeHtml(item)}</li>`).join("")}</ul>`
      : `<p class="muted">${NO_DATA}</p>`;
  const sections = SECTIONS.map(({ key, title }) => `<h2>${title}</h2>${list(brief.sections[key])}`).join("");
  reportWindow.document.write(`<!doctype html><html lang="vi"><head><meta charset="utf-8"><title>Brief tái khám - ${escapeHtml(patientName)}</title><style>
    @page{size:A4;margin:14mm}body{margin:0;color:#173b3a;font-family:Arial,sans-serif;font-size:12px;line-height:1.5}.header{border-bottom:3px solid #43b5ae;padding-bottom:12px}.brand{font-size:20px;font-weight:800;color:#238d87}.muted{color:#64748b}h1{margin:14px 0 4px;font-size:18px}h2{margin:18px 0 6px;font-size:13px;color:#235f5c}ul{margin:0;padding-left:18px}li{margin:3px 0}.summary{margin-top:12px;padding:12px;border:1px solid #cde8e5;border-radius:10px;background:#f4fbfa;white-space:pre-wrap}.footer{margin-top:20px;padding-top:10px;border-top:1px solid #dbe5e4;color:#64748b;font-size:10px}.actions{position:fixed;right:20px;bottom:20px}.actions button{border:0;border-radius:9px;background:#238d87;color:white;padding:10px 16px;font-weight:700;cursor:pointer}@media print{.actions{display:none}}
  </style></head><body><header class="header"><div class="brand">VDiab</div><div class="muted">Brief tái khám · Giờ khám ${escapeHtml(formatDateTime(brief.appointment_scheduled_at))}</div></header>
  <h1>${escapeHtml(patientName)}</h1><div class="muted">${escapeHtml(periodLabel(brief))} · Tạo lúc ${escapeHtml(formatDateTime(brief.generated_at))}</div>
  <div class="summary">${escapeHtml(brief.summary)}</div>
  <h2>Điểm cần chú ý</h2>${list(brief.attention_points.length ? brief.attention_points : ["Không có điểm bất thường nào được ghi nhận trong giai đoạn."])}
  ${sections}
  <h2>Xu hướng</h2>${list(brief.trends)}
  ${brief.limitations.length ? `<h2>Giới hạn dữ liệu</h2>${list(brief.limitations)}` : ""}
  <p class="footer">Bản tóm tắt do AI tổng hợp từ dữ liệu bệnh nhân tự ghi, các con số do hệ thống tính. Không phải chẩn đoán; cần đối chiếu hồ sơ lâm sàng trước khi ra quyết định điều trị.</p><div class="actions"><button onclick="window.print()">In / Lưu PDF</button></div></body></html>`);
  reportWindow.document.close();
  reportWindow.focus();
}

export function PrevisitBriefCard({ patientId, patientName }: { patientId: string; patientName: string }) {
  const [appointment, setAppointment] = useState<UpcomingAppointment | null>(null);
  const [brief, setBrief] = useState<PrevisitBrief | null>(null);
  const [loading, setLoading] = useState(true);
  const [generating, setGenerating] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);
    (async () => {
      const upcoming = await api.upcomingAppointments(30);
      const next = upcoming.find((item) => item.patient_id === patientId) ?? null;
      if (cancelled) return;
      setAppointment(next);
      if (next && (next.brief_state === "ready" || next.brief_state === "stale")) {
        const loaded = await api.previsitBrief(next.id);
        if (cancelled) return;
        setBrief(loaded);
        if (!loaded.seen_at) void api.markPrevisitBriefSeen(next.id).catch(() => undefined);
      }
    })()
      .catch((reason) => {
        if (!cancelled) setError(reason instanceof Error ? reason.message : "Không thể tải brief tái khám.");
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [patientId]);

  const generate = async () => {
    if (!appointment) return;
    setGenerating(true);
    setError(null);
    try {
      setBrief(await api.generatePrevisitBrief(appointment.id));
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Chưa tạo được brief.");
    } finally {
      setGenerating(false);
    }
  };

  if (loading || !appointment) return null;

  return (
    <section className="rounded-2xl border border-[#43B5AE]/40 bg-card p-5">
      <div className="mb-3 flex flex-wrap items-center gap-3">
        <div className="flex h-10 w-10 items-center justify-center rounded-xl bg-accent">
          <CalendarClock className="h-5 w-5 text-[#43B5AE]" />
        </div>
        <div className="min-w-0 flex-1">
          <h3 className="font-semibold text-foreground">Brief tái khám</h3>
          <p className="text-[11px] text-muted-foreground">
            Lịch khám {formatDateTime(appointment.scheduled_at)}
            {brief ? ` · Tạo lúc ${formatDateTime(brief.generated_at)}` : ""}
          </p>
        </div>
        {brief && (
          <button
            type="button"
            onClick={() => printBrief(brief, patientName)}
            className="inline-flex items-center gap-1.5 rounded-lg border border-border px-3 py-1.5 text-xs font-medium text-foreground hover:bg-muted"
          >
            <Printer className="h-3.5 w-3.5" />
            In / Lưu PDF
          </button>
        )}
        <button
          type="button"
          onClick={() => void generate()}
          disabled={generating}
          className="inline-flex items-center gap-1.5 rounded-lg bg-primary px-3 py-1.5 text-xs font-medium text-primary-foreground disabled:opacity-60"
        >
          {generating ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <RefreshCw className="h-3.5 w-3.5" />}
          {brief ? "Tạo lại" : "Tạo ngay"}
        </button>
      </div>

      {error && (
        <p role="alert" className="mb-3 rounded-xl border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-700">
          {error}
        </p>
      )}

      {!brief ? (
        <p className="rounded-xl bg-muted p-4 text-sm text-muted-foreground">
          {appointment.brief_state === "failed"
            ? "Lần tạo brief gần nhất bị lỗi; hệ thống sẽ thử lại sau mỗi giờ."
            : "Chưa có brief. Hệ thống tự tạo trong 48 giờ trước giờ khám, hoặc bấm “Tạo ngay”."}
        </p>
      ) : (
        <div className="space-y-3 text-sm">
          {brief.state === "stale" && (
            <p className="flex items-start gap-2 rounded-xl bg-amber-50 p-3 text-xs text-amber-800">
              <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0" />
              Lịch khám đã đổi giờ sau khi brief được tạo; hệ thống sẽ tạo lại khi gần giờ khám.
            </p>
          )}
          <p className="text-xs text-muted-foreground">{periodLabel(brief)}</p>
          <p className="whitespace-pre-wrap leading-relaxed text-foreground">{brief.summary}</p>
          <BriefList
            title="Điểm cần chú ý"
            items={brief.attention_points}
            empty="Không có điểm bất thường nào được ghi nhận trong giai đoạn."
            tone="warning"
          />
          {SECTIONS.map(({ key, title }) => (
            <BriefList key={key} title={title} items={brief.sections[key] ?? []} empty={NO_DATA} />
          ))}
          {brief.limitations.length > 0 && (
            <div className="rounded-xl bg-muted p-3 text-xs text-muted-foreground">
              <strong>Giới hạn dữ liệu:</strong> {brief.limitations.join(" · ")}
            </div>
          )}
        </div>
      )}
    </section>
  );
}

function BriefList({
  title,
  items,
  empty,
  tone,
}: {
  title: string;
  items: string[];
  empty: string;
  tone?: "warning";
}) {
  return (
    <div className="border-t border-border pt-3">
      <p
        className={cn(
          "mb-1.5 text-[11px] font-semibold uppercase tracking-wide",
          tone === "warning" ? "text-[#B9790A]" : "text-muted-foreground",
        )}
      >
        {title}
      </p>
      {items.length ? (
        <ul className="list-disc space-y-1 pl-5 text-foreground">
          {items.map((item, index) => (
            <li key={index} className="leading-relaxed">
              {item}
            </li>
          ))}
        </ul>
      ) : (
        <p className="text-muted-foreground">{empty}</p>
      )}
    </div>
  );
}
