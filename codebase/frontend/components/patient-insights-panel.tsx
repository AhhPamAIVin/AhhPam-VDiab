"use client";

import { useEffect, useState } from "react";
import { Check, Lightbulb, Loader2, Sparkles, Stethoscope, X } from "lucide-react";
import { api, type InsightVerdict, type PatientInsight } from "@/lib/api";
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

function InsightFeedbackControls({
  insight,
  saving,
  rejecting,
  comment,
  onComment,
  onConfirm,
  onStartReject,
  onCancelReject,
  onReject,
}: {
  insight: PatientInsight;
  saving: boolean;
  rejecting: boolean;
  comment: string;
  onComment: (value: string) => void;
  onConfirm: () => void;
  onStartReject: () => void;
  onCancelReject: () => void;
  onReject: () => void;
}) {
  const outdated = insight.verdict_version !== null && insight.verdict_version < insight.version;
  return (
    <div className="mt-3 border-t border-border pt-3">
      {insight.verdict && (
        <p
          className={cn(
            "mb-2 text-xs font-medium",
            insight.verdict === "confirmed" ? "text-[#2D9D97]" : "text-red-600",
          )}
        >
          {insight.verdict === "confirmed"
            ? "Bạn đã xác nhận nhận định này phù hợp."
            : "Bạn đã bác bỏ nhận định này — bệnh nhân không còn thấy và AI không đề xuất lại."}
          {insight.verdict_comment ? ` Lý do: ${insight.verdict_comment}` : ""}
          {outdated ? " (Nội dung đã được AI cập nhật sau khi bạn đánh giá.)" : ""}
        </p>
      )}
      {rejecting ? (
        <div className="space-y-2">
          <textarea
            value={comment}
            onChange={(event) => onComment(event.target.value)}
            maxLength={1000}
            rows={2}
            placeholder="Lý do bác bỏ (không bắt buộc)"
            aria-label="Lý do bác bỏ"
            className="w-full rounded-xl border border-border bg-background px-3 py-2 text-sm outline-none focus:border-primary"
          />
          <div className="flex gap-2">
            <button
              type="button"
              onClick={onReject}
              disabled={saving}
              className="rounded-lg bg-red-600 px-3 py-1.5 text-xs font-medium text-white disabled:opacity-60"
            >
              {saving ? "Đang lưu..." : "Bác bỏ"}
            </button>
            <button
              type="button"
              onClick={onCancelReject}
              className="rounded-lg px-3 py-1.5 text-xs font-medium text-muted-foreground hover:bg-muted"
            >
              Hủy
            </button>
          </div>
        </div>
      ) : (
        <div className="flex flex-wrap gap-2">
          <button
            type="button"
            onClick={onConfirm}
            disabled={saving || (insight.verdict === "confirmed" && !outdated)}
            className="inline-flex items-center gap-1 rounded-lg border border-[#43B5AE]/50 px-3 py-1.5 text-xs font-medium text-[#2D9D97] hover:bg-[#DFF7F3] disabled:opacity-50"
          >
            <Check className="h-3.5 w-3.5" />
            Phù hợp
          </button>
          <button
            type="button"
            onClick={onStartReject}
            disabled={saving || (insight.verdict === "rejected" && !outdated)}
            className="inline-flex items-center gap-1 rounded-lg border border-red-200 px-3 py-1.5 text-xs font-medium text-red-600 hover:bg-red-50 disabled:opacity-50"
          >
            <X className="h-3.5 w-3.5" />
            Không phù hợp
          </button>
        </div>
      )}
    </div>
  );
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
  // Epic 4.4: the doctor's confirm/reject — rejecting asks for an optional reason first.
  const [rejectingId, setRejectingId] = useState<string | null>(null);
  const [comment, setComment] = useState("");
  const [savingId, setSavingId] = useState<string | null>(null);
  const [feedbackError, setFeedbackError] = useState<string | null>(null);

  const sendFeedback = async (insight: PatientInsight, verdict: InsightVerdict, reason?: string) => {
    setSavingId(insight.id);
    setFeedbackError(null);
    try {
      const updated = await api.setInsightFeedback(patientId, insight.id, verdict, reason);
      setInsights((current) => current.map((item) => (item.id === updated.id ? updated : item)));
      setRejectingId(null);
      setComment("");
    } catch (reason) {
      setFeedbackError(reason instanceof Error ? reason.message : "Không lưu được phản hồi.");
    } finally {
      setSavingId(null);
    }
  };

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

        {(error || feedbackError) && !loading && (
          <p role="alert" className="rounded-xl border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">
            {error || feedbackError}
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
                        : `Ghi nhận ${firstObserved.toLocaleDateString("vi-VN")} · AI ghi nhận lại gần nhất ${lastConfirmed.toLocaleDateString("vi-VN")}`}
                    </p>
                    {audience === "doctor" && (
                      <InsightFeedbackControls
                        insight={insight}
                        saving={savingId === insight.id}
                        rejecting={rejectingId === insight.id}
                        comment={comment}
                        onComment={setComment}
                        onConfirm={() => void sendFeedback(insight, "confirmed")}
                        onStartReject={() => {
                          setRejectingId(insight.id);
                          setComment("");
                        }}
                        onCancelReject={() => setRejectingId(null)}
                        onReject={() => void sendFeedback(insight, "rejected", comment)}
                      />
                    )}
                  </div>
                </div>
              </article>
            );
          })}
      </section>
    </div>
  );
}
