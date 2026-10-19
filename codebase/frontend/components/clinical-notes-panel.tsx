"use client";

import { useEffect, useState } from "react";
import {
  CalendarDays,
  FileText,
  Loader2,
  Pencil,
  Plus,
  Save,
  Trash2,
  X,
} from "lucide-react";
import {
  api,
  type ClinicalNote,
  type ClinicalNoteInput,
} from "@/lib/api";
import { useAuth } from "@/lib/auth-context";
import { parseUtcTimestamp } from "@/lib/utils";

type NoteForm = { text: string };

const emptyForm: NoteForm = { text: "" };

function toPayload(form: NoteForm): ClinicalNoteInput {
  const text = form.text.trim();
  return {
    reason_for_visit: text.slice(0, 500),
    history_of_present_illness: text,
    diagnosis: text.slice(0, 1000),
    treatment_plan: text.slice(0, 2000),
  };
}

function noteToForm(note: ClinicalNote): NoteForm {
  return { text: note.history_of_present_illness };
}

export function ClinicalNotesPanel({ patientId }: { patientId: string }) {
  const { user } = useAuth();
  const [notes, setNotes] = useState<ClinicalNote[]>([]);
  const [form, setForm] = useState<NoteForm>(emptyForm);
  const [editingId, setEditingId] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);
    api.clinicalNotes(patientId)
      .then((noteItems) => {
        if (!cancelled) setNotes(noteItems);
      })
      .catch((reason) => {
        if (!cancelled)
          setError(
            reason instanceof Error ? reason.message : "Không thể tải bệnh án.",
          );
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [patientId]);

  const update = (field: keyof NoteForm, value: string) =>
    setForm((current) => ({ ...current, [field]: value }));

  const resetForm = () => {
    setForm(emptyForm);
    setEditingId(null);
    setError(null);
  };

  const submit = async (event: React.FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (!form.text.trim()) return;
    setSaving(true);
    setError(null);
    try {
      const saved = editingId
        ? await api.updateClinicalNote(patientId, editingId, toPayload(form))
        : await api.createClinicalNote(patientId, toPayload(form));
      setNotes((current) =>
        editingId
          ? current.map((item) => (item.id === editingId ? saved : item))
          : [saved, ...current],
      );
      resetForm();
    } catch (reason) {
      setError(
        reason instanceof Error ? reason.message : "Không thể lưu bệnh án.",
      );
    } finally {
      setSaving(false);
    }
  };

  const edit = (note: ClinicalNote) => {
    setEditingId(note.id);
    setForm(noteToForm(note));
    setError(null);
    window.scrollTo({ top: 0, behavior: "smooth" });
  };

  const remove = async (note: ClinicalNote) => {
    if (!window.confirm("Xóa bệnh án này? Thao tác này không thể hoàn tác."))
      return;
    setError(null);
    try {
      await api.deleteClinicalNote(patientId, note.id);
      setNotes((current) => current.filter((item) => item.id !== note.id));
      if (editingId === note.id) resetForm();
    } catch (reason) {
      setError(
        reason instanceof Error ? reason.message : "Không thể xóa bệnh án.",
      );
    }
  };

  return (
    <div className="space-y-5">
      <form
        onSubmit={submit}
        className="rounded-2xl border border-border bg-card p-5"
      >
        <div className="mb-5 flex items-start justify-between gap-3">
          <div>
            <h3 className="font-semibold text-foreground">
              {editingId ? "Cập nhật bệnh án" : "Thêm bệnh án lâm sàng"}
            </h3>
            <p className="mt-1 text-xs text-muted-foreground">
              Chỉ cần nhập nội dung ghi chú. Các trường bắt buộc của hệ thống sẽ được tự động điền.
            </p>
          </div>
          {editingId && (
            <button
              type="button"
              onClick={resetForm}
              className="rounded-lg p-2 text-muted-foreground hover:bg-muted"
              aria-label="Hủy chỉnh sửa"
            >
              <X className="h-4 w-4" />
            </button>
          )}
        </div>

        <Field label="Nội dung ghi chú *">
          <textarea
            required
            maxLength={4000}
            rows={6}
            value={form.text}
            onChange={(e) => update("text", e.target.value)}
            placeholder="Nhập ghi chú lâm sàng..."
            className={textareaClass}
          />
        </Field>

        {error && (
          <p
            role="alert"
            className="mt-4 rounded-xl border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700"
          >
            {error}
          </p>
        )}
        <button
          type="submit"
          disabled={saving}
          className="mt-4 flex w-full items-center justify-center gap-2 rounded-xl bg-primary py-2.5 text-sm font-medium text-primary-foreground hover:bg-brand-dark disabled:cursor-not-allowed disabled:opacity-50"
        >
          {saving ? (
            <Loader2 className="h-4 w-4 animate-spin" />
          ) : editingId ? (
            <Save className="h-4 w-4" />
          ) : (
            <Plus className="h-4 w-4" />
          )}
          {saving ? "Đang lưu..." : editingId ? "Lưu thay đổi" : "Lưu bệnh án"}
        </button>
      </form>

      <section className="space-y-3">
        <div className="flex items-center justify-between">
          <h3 className="font-semibold text-foreground">Lịch sử bệnh án</h3>
          <span className="text-xs text-muted-foreground">
            {notes.length} bản ghi
          </span>
        </div>
        {loading && (
          <div className="flex items-center justify-center gap-2 rounded-2xl border border-border bg-card p-8 text-sm text-muted-foreground">
            <Loader2 className="h-4 w-4 animate-spin" />
            Đang tải bệnh án...
          </div>
        )}
        {!loading && notes.length === 0 && (
          <div className="rounded-2xl border border-dashed border-border bg-card p-8 text-center">
            <FileText className="mx-auto h-8 w-8 text-muted-foreground/50" />
            <p className="mt-2 text-sm text-muted-foreground">
              Chưa có bệnh án lâm sàng.
            </p>
          </div>
        )}
        {!loading &&
          notes.map((note) => {
            const mine = note.doctor_id === user?.id;
            return (
              <article
                key={note.id}
                className="rounded-2xl border border-border bg-card p-5"
              >
                <div className="flex items-start gap-3">
                  <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded-xl bg-accent">
                    <FileText className="h-4 w-4 text-primary" />
                  </div>
                  <div className="min-w-0 flex-1">
                    <div className="flex flex-wrap items-start justify-between gap-2">
                      <div>
                        <h4 className="font-semibold text-foreground">
                          {note.reason_for_visit}
                        </h4>
                        <p className="mt-1 text-xs text-muted-foreground">
                          {parseUtcTimestamp(note.created_at).toLocaleString("vi-VN")}
                          {note.updated_at !== note.created_at
                            ? " · Đã cập nhật"
                            : ""}
                        </p>
                      </div>
                      {mine && (
                        <div className="flex">
                          <button
                            type="button"
                            onClick={() => edit(note)}
                            className="rounded-lg p-2 text-primary hover:bg-accent"
                            aria-label="Sửa bệnh án"
                          >
                            <Pencil className="h-4 w-4" />
                          </button>
                          <button
                            type="button"
                            onClick={() => void remove(note)}
                            className="rounded-lg p-2 text-red-500 hover:bg-red-50"
                            aria-label="Xóa bệnh án"
                          >
                            <Trash2 className="h-4 w-4" />
                          </button>
                        </div>
                      )}
                    </div>
                    <div className="mt-4 grid gap-3 text-sm md:grid-cols-2">
                      <NoteSection
                        title="Bệnh sử"
                        value={note.history_of_present_illness}
                      />
                      <NoteSection title="Chẩn đoán" value={note.diagnosis} />
                      <NoteSection
                        title="Kế hoạch điều trị"
                        value={note.treatment_plan}
                      />
                      <NoteSection
                        title="Khám và xét nghiệm"
                        value={
                          [
                            note.weight_kg
                              ? `Cân nặng ${note.weight_kg} kg`
                              : "",
                            note.blood_pressure_systolic
                              ? `HA ${note.blood_pressure_systolic}/${note.blood_pressure_diastolic} mmHg`
                              : "",
                            note.hba1c_pct ? `HbA1c ${note.hba1c_pct}%` : "",
                            note.clinical_exam_findings,
                            note.paraclinical_results,
                          ]
                            .filter(Boolean)
                            .join(" · ") || "Chưa ghi nhận"
                        }
                      />
                    </div>
                    {note.follow_up_date && (
                      <p className="mt-4 flex items-center gap-2 rounded-xl bg-accent px-3 py-2 text-xs font-medium text-accent-foreground">
                        <CalendarDays className="h-4 w-4" />
                        Tái khám:{" "}
                        {new Date(
                          note.follow_up_date + "T00:00:00",
                        ).toLocaleDateString("vi-VN")}
                      </p>
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

const inputClass =
  "mt-1 w-full rounded-xl border border-border bg-background px-3 py-2.5 text-sm outline-none focus:border-primary focus:ring-2 focus:ring-primary/20";
const textareaClass = inputClass + " resize-none";

function Field({
  label,
  wide,
  children,
}: {
  label: string;
  wide?: boolean;
  children: React.ReactNode;
}) {
  return (
    <label className={wide ? "block md:col-span-2" : "block"}>
      <span className="text-xs font-medium text-foreground">{label}</span>
      {children}
    </label>
  );
}

function NoteSection({ title, value }: { title: string; value: string }) {
  return (
    <div className="rounded-xl bg-muted p-3">
      <p className="text-xs font-medium text-muted-foreground">{title}</p>
      <p className="mt-1 whitespace-pre-wrap leading-relaxed text-foreground">
        {value}
      </p>
    </div>
  );
}
