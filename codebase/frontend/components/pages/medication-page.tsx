"use client";

import { useEffect, useState } from "react";
import { AlertTriangle, Check, Info, Loader2, Pill, RefreshCw, Send } from "lucide-react";
import { api, type MedicationNote, type MedicationRecord } from "@/lib/api";
import { useToast } from "@/components/ui/toast";
import { MedicationAdherencePanel } from "@/components/medication-adherence-panel";

type ReportForm = {
  medicationId: string;
  type: MedicationNote["note_type"];
  text: string;
};

// Local (browser) yyyy-mm-dd, matching the backend's `on_date` filter — see
// patient-dashboard.tsx's identical helper for why this (not a UTC ISO slice)
// is the right notion of "today" here.
function todayDateString(): string {
  const now = new Date();
  return `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, "0")}-${String(now.getDate()).padStart(2, "0")}`;
}

export function MedicationPage() {
  const { showToast } = useToast();
  const [medications, setMedications] = useState<MedicationRecord[]>([]);
  const [notes, setNotes] = useState<MedicationNote[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [report, setReport] = useState<ReportForm | null>(null);
  const [pendingReminders, setPendingReminders] = useState(0);
  const [markingTaken, setMarkingTaken] = useState(false);

  const load = async () => {
    setLoading(true);
    setError(null);
    const [medicationResult, noteResult] = await Promise.allSettled([
      api.activeMedications(),
      api.medicationNotes(),
    ]);
    if (medicationResult.status === "fulfilled") setMedications(medicationResult.value);
    else setError(medicationResult.reason instanceof Error ? medicationResult.reason.message : "Không thể tải danh sách thuốc.");
    if (noteResult.status === "fulfilled") setNotes(noteResult.value);
    setLoading(false);
  };

  const loadPendingReminders = () => {
    api.notifications(undefined, todayDateString())
      .then((items) => setPendingReminders(items.filter((item) => item.notification_type === "medication_reminder" && (item.status === "pending" || item.status === "snoozed")).length))
      .catch(() => setPendingReminders(0));
  };

  useEffect(() => { void load(); }, []);

  useEffect(() => {
    loadPendingReminders();
    window.addEventListener("vdiab:notifications-updated", loadPendingReminders);
    return () => window.removeEventListener("vdiab:notifications-updated", loadPendingReminders);
  }, []);

  // There's no per-drug "dose taken" log in this app — the reminder that
  // fires (see notifications/scheduler.py) already covers every active
  // medication together in one slot, so marking it done is a single
  // page-level action rather than something on each medication card. Reuses
  // the same generic notification-status endpoint the /notifications page's
  // "Hoàn thành tất cả" already goes through, just pre-filtered to today's
  // medication_reminder notifications.
  const markMedicationTaken = async () => {
    setMarkingTaken(true);
    try {
      const items = await api.notifications(undefined, todayDateString());
      const due = items.filter((item) => item.notification_type === "medication_reminder" && (item.status === "pending" || item.status === "snoozed"));
      for (const item of due) {
        await api.updateNotification(item.id, "completed");
      }
      window.dispatchEvent(new Event("vdiab:notifications-updated"));
      showToast(due.length ? "Đã đánh dấu hoàn thành nhắc nhở uống thuốc hôm nay." : "Đã ghi nhận.", "success");
    } catch (cause) {
      showToast(cause instanceof Error ? cause.message : "Không thể cập nhật nhắc nhở.", "error");
    } finally {
      setMarkingTaken(false);
    }
  };

  const submitReport = async (event: React.FormEvent) => {
    event.preventDefault();
    if (!report) return;
    setSaving(true);
    try {
      await api.createMedicationNote(report.medicationId, {
        note_type: report.type,
        text: report.text.trim() || null,
        occurred_at: new Date().toISOString(),
      });
      showToast("Đã gửi báo cáo cho bác sĩ.", "success");
      setReport(null);
      setNotes(await api.medicationNotes());
    } catch (cause) {
      showToast(cause instanceof Error ? cause.message : "Không thể gửi báo cáo.", "error");
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="mx-auto max-w-2xl space-y-4">
      <MedicationAdherencePanel audience="patient" />
      <div className="flex gap-3 rounded-2xl border border-orange-200 bg-orange-50 p-4 dark:border-[#4B3B27] dark:bg-[#211D18]">
        <AlertTriangle className="mt-0.5 h-5 w-5 flex-shrink-0 text-orange-500" />
        <div>
          <p className="text-sm font-semibold text-orange-800 dark:text-[#D6B98A]">Không tự ý thay đổi thuốc</p>
          <p className="mt-1 text-xs leading-relaxed text-orange-700 dark:text-[#BDA98A]">Hãy báo quên liều hoặc phản ứng bất lợi tại đây. Mọi thay đổi phác đồ cần được bác sĩ thực hiện.</p>
        </div>
      </div>

      <div className="flex items-center justify-between">
        <div>
          <h2 className="font-semibold text-foreground">Thuốc đang dùng</h2>
          <p className="text-xs text-muted-foreground">{medications.length} thuốc trong phác đồ hiện tại</p>
        </div>
        <button type="button" onClick={() => void load()} disabled={loading} className="rounded-xl border border-border p-2 text-muted-foreground hover:bg-muted disabled:opacity-50" aria-label="Tải lại">
          <RefreshCw className={`h-4 w-4 ${loading ? "animate-spin" : ""}`} />
        </button>
      </div>

      {!loading && !error && medications.length > 0 && (
        <div className="flex items-center justify-between gap-2 rounded-2xl border border-primary/30 bg-accent p-3 sm:gap-3 sm:p-4">
          <div className="flex min-w-0 items-center gap-2 sm:gap-3">
            <span className="flex h-9 w-9 flex-shrink-0 items-center justify-center rounded-xl bg-primary/10 text-primary sm:h-10 sm:w-10"><Pill className="h-5 w-5" /></span>
            <div className="min-w-0">
              <p className="text-sm font-semibold text-foreground">Đã uống thuốc hôm nay?</p>
              <p className="mt-0.5 hidden text-xs text-muted-foreground sm:block">{pendingReminders > 0 ? `${pendingReminders} nhắc nhở uống thuốc đang chờ` : "Đánh dấu để tắt nhắc nhở uống thuốc hôm nay"}</p>
            </div>
          </div>
          <button type="button" onClick={() => void markMedicationTaken()} disabled={markingTaken} className="flex min-h-12 flex-shrink-0 items-center gap-2 whitespace-nowrap rounded-xl bg-primary px-4 py-3 text-sm font-semibold text-primary-foreground shadow-sm disabled:opacity-50 sm:min-h-0 sm:py-2.5 sm:text-xs sm:font-medium sm:shadow-none">
            {markingTaken ? <Loader2 className="h-4 w-4 animate-spin" /> : <Check className="h-4 w-4" />} Đã uống thuốc
          </button>
        </div>
      )}

      {error && <div role="alert" className="rounded-xl border border-red-200 bg-red-50 p-3 text-sm text-red-700">{error}</div>}
      {!loading && !error && medications.length === 0 && (
        <div className="rounded-2xl border border-dashed border-border bg-card p-8 text-center text-sm text-muted-foreground">Bạn chưa có thuốc đang dùng.</div>
      )}

      {medications.map((medication) => {
        const medicationNotes = notes.filter((note) => note.medication_id === medication.id);
        return (
          <article key={medication.id} className="rounded-2xl border border-border bg-card p-5">
            <div className="flex items-start gap-3">
              <div className="flex h-10 w-10 flex-shrink-0 items-center justify-center rounded-xl bg-purple-50 dark:bg-[#272131]"><Pill className="h-5 w-5 text-purple-500 dark:text-[#B6A3CA]" /></div>
              <div className="min-w-0 flex-1">
                <div className="flex flex-wrap items-center gap-2">
                  <h3 className="font-semibold text-foreground">{medication.drug_name}</h3>
                  <span className="rounded-full bg-green-100 px-2 py-0.5 text-xs font-medium text-green-700 dark:bg-[#1B2C24] dark:text-[#8FBEA3]">Đang dùng</span>
                </div>
                <p className="mt-1 text-sm text-muted-foreground">{medication.dose} · {medication.frequency}</p>
                <p className="mt-1 text-xs text-muted-foreground">Bắt đầu {new Date(medication.start_date).toLocaleDateString("vi-VN")}</p>
                {medication.note && <p className="mt-3 rounded-xl bg-blue-50 p-3 text-xs leading-relaxed text-blue-700 dark:bg-[#18252F] dark:text-[#95AFC4]"><Info className="mr-1 inline h-3.5 w-3.5" />{medication.note}</p>}
              </div>
            </div>
            <div className="mt-4 flex flex-wrap gap-2 border-t border-border pt-4">
              <button type="button" onClick={() => setReport({ medicationId: medication.id, type: "missed_dose", text: "" })} className="rounded-xl border border-border px-3 py-2 text-xs font-medium text-foreground hover:bg-muted">Báo quên liều</button>
              <button type="button" onClick={() => setReport({ medicationId: medication.id, type: "adverse_reaction", text: "" })} className="rounded-xl border border-orange-200 px-3 py-2 text-xs font-medium text-orange-700 hover:bg-orange-50 dark:border-[#4B3B27] dark:text-[#C6A77D] dark:hover:bg-[#292219]">Báo phản ứng bất lợi</button>
              {medicationNotes.length > 0 && <span className="ml-auto self-center text-xs text-muted-foreground">Đã gửi {medicationNotes.length} báo cáo</span>}
            </div>
          </article>
        );
      })}

      {report && (
        <form onSubmit={submitReport} className="rounded-2xl border border-primary/30 bg-accent p-4">
          <h3 className="text-sm font-semibold text-foreground">{report.type === "missed_dose" ? "Báo quên liều" : "Báo phản ứng bất lợi"}</h3>
          <textarea autoFocus required={report.type === "adverse_reaction"} rows={3} value={report.text} onChange={(event) => setReport({ ...report, text: event.target.value })} placeholder={report.type === "missed_dose" ? "Ghi chú thời điểm hoặc lý do (không bắt buộc)" : "Mô tả triệu chứng hoặc phản ứng bạn gặp phải"} className="mt-3 w-full resize-none rounded-xl border border-border bg-card px-3 py-2.5 text-sm outline-none focus:border-primary" />
          <div className="mt-3 flex justify-end gap-2">
            <button type="button" onClick={() => setReport(null)} className="rounded-xl px-4 py-2 text-sm text-muted-foreground hover:bg-muted">Hủy</button>
            <button type="submit" disabled={saving} className="flex items-center gap-2 rounded-xl bg-primary px-4 py-2 text-sm font-medium text-primary-foreground disabled:opacity-50"><Send className="h-4 w-4" />{saving ? "Đang gửi..." : "Gửi bác sĩ"}</button>
          </div>
        </form>
      )}
    </div>
  );
}
