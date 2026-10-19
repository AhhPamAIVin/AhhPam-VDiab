"use client";

import { useEffect, useState } from "react";
import { Lightbulb, Loader2, Sparkles, Stethoscope } from "lucide-react";
import { api, type PatientInsight } from "@/lib/api";
import { cn, parseUtcTimestamp } from "@/lib/utils";

const confidenceConfig: Record<PatientInsight["confidence"], { label: string; className: string }> = {
  high: { label: "Độ tin cậy cao", className: "bg-[#DFF7F3] text-[#2D9D97]" },
  medium: { label: "Độ tin cậy trung bình", className: "bg-yellow-50 text-yellow-700" },
  low: { label: "Độ tin cậy thấp", className: "bg-orange-50 text-orange-600" },
};

// category is an open string on the backend (e.g. "glucose_pattern",
// "hypoglycemia_risk"), not a closed enum — prettify generically rather
// than hardcode a label per value.
function categoryLabel(category: string): string {
  return category
    .split("_")
    .filter(Boolean)
    .map((word) => word[0].toUpperCase() + word.slice(1))
    .join(" ");
}

export function PatientInsightsPanel({
  patientId,
  audience = "doctor",
}: {
  patientId: string;
  // Backend endpoint allows patient self-access too (ensure_patient_access),
  // so this panel is shared between the doctor's patient-detail page and
  // the patient's own health-monitoring page — only the disclaimer copy
  // needs to address a different reader.
  audience?: "doctor" | "patient";
}) {
  const [insights, setInsights] = useState<PatientInsight[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);
    api
      .patientInsights(patientId)
      .then((items) => {
        if (!cancelled) {
          setInsights(
            [...items].sort(
              (a, b) =>
                new Date(b.last_confirmed_at).getTime() -
                new Date(a.last_confirmed_at).getTime(),
            ),
          );
        }
      })
      .catch((reason) => {
        if (!cancelled)
          setError(
            reason instanceof Error ? reason.message : "Không thể tải nhận định AI.",
          );
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [patientId]);

  return (
    <div className="space-y-5">
      <div className="rounded-2xl border border-yellow-200 bg-yellow-50 p-4 text-xs leading-relaxed text-yellow-700">
        {audience === "patient"
          ? "Các nhận định dưới đây do AI phát hiện tự động từ dữ liệu sức khỏe của bạn (đường huyết, bữa ăn, ghi chú lâm sàng) — không phải chẩn đoán. Hãy trao đổi với bác sĩ điều trị trước khi tự áp dụng bất kỳ thay đổi nào."
          : "Các nhận định dưới đây do AI phát hiện tự động từ dữ liệu sức khỏe của bệnh nhân (đường huyết, bữa ăn, ghi chú lâm sàng) — không phải chẩn đoán, chỉ là gợi ý để bác sĩ tham khảo và xác minh."}
      </div>

      <section className="space-y-3">
        <div className="flex items-center justify-between">
          <h3 className="font-semibold text-foreground">Nhận định AI</h3>
          <span className="text-xs text-muted-foreground">{insights.length} nhận định</span>
        </div>

        {loading && (
          <div className="flex items-center justify-center gap-2 rounded-2xl border border-border bg-card p-8 text-sm text-muted-foreground">
            <Loader2 className="h-4 w-4 animate-spin" />
            Đang tải nhận định...
          </div>
        )}

        {error && !loading && (
          <p role="alert" className="rounded-xl border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">
            {error}
          </p>
        )}

        {!loading && !error && insights.length === 0 && (
          <div className="rounded-2xl border border-dashed border-border bg-card p-8 text-center">
            <Lightbulb className="mx-auto h-8 w-8 text-muted-foreground/50" />
            <p className="mt-2 text-sm text-muted-foreground">Chưa có nhận định AI nào cho bệnh nhân này.</p>
          </div>
        )}

        {!loading &&
          !error &&
          insights.map((insight) => {
            const confidence = confidenceConfig[insight.confidence] ?? confidenceConfig.low;
            const firstObserved = parseUtcTimestamp(insight.first_observed_at);
            const lastConfirmed = parseUtcTimestamp(insight.last_confirmed_at);
            const sameDay = firstObserved.toDateString() === lastConfirmed.toDateString();
            return (
              <article key={insight.id} className="rounded-2xl border border-border bg-card p-5">
                <div className="flex items-start gap-3">
                  <div
                    className={cn(
                      "flex h-9 w-9 shrink-0 items-center justify-center rounded-xl",
                      insight.reviewed ? "bg-accent" : "bg-[#DFF7F3]",
                    )}
                  >
                    {insight.reviewed ? (
                      <Stethoscope className="h-4 w-4 text-primary" />
                    ) : (
                      <Sparkles className="h-4 w-4 text-[#2D9D97]" />
                    )}
                  </div>
                  <div className="min-w-0 flex-1">
                    <div className="flex flex-wrap items-center gap-2">
                      <h4 className="font-semibold text-foreground">{categoryLabel(insight.category)}</h4>
                      <span
                        className={cn(
                          "rounded-full px-2.5 py-0.5 text-[11px] font-medium",
                          insight.reviewed
                            ? "bg-accent text-accent-foreground"
                            : "bg-blue-50 text-blue-600",
                        )}
                      >
                        {insight.reviewed ? "Từ ghi chú bác sĩ" : "AI tự động phát hiện"}
                      </span>
                      <span className={cn("rounded-full px-2.5 py-0.5 text-[11px] font-medium", confidence.className)}>
                        {confidence.label}
                      </span>
                      {insight.version > 1 && (
                        <span className="rounded-full bg-muted px-2.5 py-0.5 text-[11px] font-medium text-muted-foreground">
                          Đã cập nhật {insight.version} lần
                        </span>
                      )}
                    </div>
                    <p className="mt-2 whitespace-pre-wrap text-sm leading-relaxed text-foreground">
                      {insight.text}
                    </p>
                    <p className="mt-3 text-xs text-muted-foreground">
                      {sameDay
                        ? `Ghi nhận ${lastConfirmed.toLocaleDateString("vi-VN")}`
                        : `Ghi nhận ${firstObserved.toLocaleDateString("vi-VN")} · Xác nhận gần nhất ${lastConfirmed.toLocaleDateString("vi-VN")}`}
                    </p>
                  </div>
                </div>
              </article>
            );
          })}
      </section>
    </div>
  );
}
