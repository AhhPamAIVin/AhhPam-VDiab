"use client";

import { useEffect, useState } from "react";
import { DatePicker, Select } from "antd";
import dayjs from "dayjs";
import { doctorPatients, mockDoctor } from "@/lib/mock-data";
import {
  ArrowLeft,
  Droplets,
  Activity,
  Pill,
  UtensilsCrossed,
  MessageSquare,
  AlertTriangle,
  TrendingUp,
  TrendingDown,
  User,
  Phone,
  ChevronRight,
  FileText,
  CalendarDays,
  Bot,
  Calendar,
  Download,
  Plus,
  Pencil,
  Trash2,
  X,
  Send,
  Sparkles,
  Info,
  Lightbulb,
} from "lucide-react";
import { cn, parseUtcTimestamp } from "@/lib/utils";
import { ClinicalNotesPanel } from "@/components/clinical-notes-panel";
import { PatientInsightsPanel } from "@/components/patient-insights-panel";
import { PatientMealsPanel } from "@/components/patient-meals-panel";
import { MedicationAdherencePanel } from "@/components/medication-adherence-panel";
import { AgentSummaryCard } from "@/components/agent-summary-card";
import { PrevisitBriefCard } from "@/components/previsit-brief-card";
import { Pagination } from "@/components/ui/pagination";
import { ChatAnswerCards } from "@/components/chat-answer-cards";
import { AgentChatSidebar } from "@/components/agent-chat-sidebar";
import { TtsPlayButton } from "@/components/tts-play-button";
import { TtsAutoReadToggle } from "@/components/tts-auto-read-toggle";
import { useAgentChatSessions } from "@/hooks/use-agent-chat-sessions";
import { useTtsPlayback } from "@/hooks/use-tts-playback";
import { useTtsAutoRead } from "@/hooks/use-tts-auto-read";
import {
  GlucoseTrendChart,
  glucoseRecordsInRange,
  RANGE_DAYS,
  type GlucoseRange,
} from "@/components/glucose-trend-chart";
import {
  api,
  type AgentSourceCitation,
  type Appointment,
  type BloodPressureRecord,
  type ChatMessage,
  type ClinicalNote,
  type GlucoseRecord,
  type MealGlucoseResponse,
  type MedicationNote,
  type MedicationRecord,
} from "@/lib/api";

type Tab =
  | "overview"
  | "glucose"
  | "medication"
  | "meals"
  | "appointments"
  | "ai"
  | "insights"
  | "notes";

interface PatientDetailPageProps {
  patientId: string;
  onBack: () => void;
}

type Medication = {
  id: string;
  name: string;
  dose: string;
  frequency: string;
  startDate: string;
  status: "active" | "inactive";
  instruction: string;
};

type MedicationForm = Pick<
  Medication,
  "name" | "dose" | "frequency" | "startDate" | "status" | "instruction"
>;

const emptyMedicationForm: MedicationForm = {
  name: "",
  dose: "",
  frequency: "",
  startDate: "",
  status: "active",
  instruction: "",
};

const toMedication = (record: MedicationRecord): Medication => ({
  id: record.id,
  name: record.drug_name,
  dose: record.dose,
  frequency: record.frequency,
  startDate: record.start_date.slice(0, 10),
  status: record.status === "active" ? "active" : "inactive",
  instruction: record.note || record.discontinued_reason || "",
});

type AppointmentForm = {
  scheduledAt: string;
  reason: string;
  status: Appointment["status"];
  notes: string;
};

const emptyAppointmentForm: AppointmentForm = {
  scheduledAt: "",
  reason: "",
  status: "scheduled",
  notes: "",
};

const toDateTimeLocal = (value: string) => {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "";
  const offset = date.getTimezoneOffset() * 60_000;
  return new Date(date.getTime() - offset).toISOString().slice(0, 16);
};

type PatientChatMessage = {
  id: string;
  role: "doctor" | "assistant";
  text: string;
  disclaimer?: string;
  limitations?: string[];
  sources?: AgentSourceCitation[];
  handoff?: boolean;
};

const chatWelcomeMessage: PatientChatMessage = {
  id: "welcome",
  role: "assistant",
  text: "Tôi có thể hỗ trợ phân tích và tóm tắt dữ liệu của bệnh nhân này.",
};

// Restored history has no per-message timestamp to show, and the doctor's
// side of the conversation is labeled "doctor" here vs. "user" over the API.
const mapRestoredChatMessage = (
  message: ChatMessage,
  index: number,
): PatientChatMessage => ({
  id: `restored-${index}`,
  role: message.role === "user" ? "doctor" : "assistant",
  text: message.content,
});

const statusConfig = {
  normal: {
    label: "Bình thường",
    color: "text-green-600",
    bg: "bg-green-50",
    border: "border-green-200",
  },
  high: {
    label: "Cao",
    color: "text-yellow-600",
    bg: "bg-yellow-50",
    border: "border-yellow-200",
  },
  critical: {
    label: "Nguy hiểm",
    color: "text-red-600",
    bg: "bg-red-50",
    border: "border-red-200",
  },
  low: {
    label: "Thấp",
    color: "text-blue-600",
    bg: "bg-blue-50",
    border: "border-blue-200",
  },
};

const escapeReportText = (value: unknown) =>
  String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");

export function PatientDetailPage({
  patientId,
  onBack,
}: PatientDetailPageProps) {
  // Temporary mock fallback: the API exposes assigned-patient lists, not a patient-detail endpoint.
  const fallbackPatient =
    doctorPatients.find((p) => p.id === patientId) ?? doctorPatients[0];
  const [patient, setPatient] = useState(fallbackPatient);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [accessDenied, setAccessDenied] = useState(false);
  const [activeTab, setActiveTab] = useState<Tab>("overview");
  const [glucoseRange, setGlucoseRange] = useState<GlucoseRange>("7d");
  const [glucoseHistoryPage, setGlucoseHistoryPage] = useState(1);
  const [glucoseRecords, setGlucoseRecords] = useState<GlucoseRecord[]>([]);
  const [bloodPressureRecords, setBloodPressureRecords] = useState<
    BloodPressureRecord[]
  >([]);
  const [clinicalNotes, setClinicalNotes] = useState<ClinicalNote[]>([]);
  const [medicationList, setMedicationList] = useState<Medication[]>([]);
  const [medicationNotes, setMedicationNotes] = useState<MedicationNote[]>([]);
  // Meal <-> post-meal glucose groups (Epic 1.4), for the printed report.
  const [mealResponse, setMealResponse] = useState<MealGlucoseResponse | null>(null);
  useEffect(() => {
    let cancelled = false;
    api
      .mealGlucoseResponse(patientId)
      .then((result) => {
        if (!cancelled) setMealResponse(result);
      })
      .catch(() => {
        if (!cancelled) setMealResponse(null);
      });
    return () => {
      cancelled = true;
    };
  }, [patientId]);
  const [medicationSaving, setMedicationSaving] = useState(false);
  const [medicationError, setMedicationError] = useState<string | null>(null);
  const [isMedicationManagerOpen, setIsMedicationManagerOpen] = useState(false);
  const [editingMedicationId, setEditingMedicationId] = useState<string | null>(
    null,
  );
  const [medicationForm, setMedicationForm] =
    useState<MedicationForm>(emptyMedicationForm);
  const [appointmentList, setAppointmentList] = useState<Appointment[]>([]);
  const [appointmentsLoading, setAppointmentsLoading] = useState(false);
  const [appointmentsError, setAppointmentsError] = useState<string | null>(
    null,
  );
  const [isAppointmentManagerOpen, setIsAppointmentManagerOpen] =
    useState(false);
  const [editingAppointmentId, setEditingAppointmentId] = useState<
    string | null
  >(null);
  const [appointmentForm, setAppointmentForm] =
    useState<AppointmentForm>(emptyAppointmentForm);
  const [appointmentSaving, setAppointmentSaving] = useState(false);
  const [question, setQuestion] = useState("");
  const [chatMessages, setChatMessages] = useState<PatientChatMessage[]>([
    chatWelcomeMessage,
  ]);
  const [chatLoading, setChatLoading] = useState(false);
  const [chatError, setChatError] = useState<string | null>(null);
  const tts = useTtsPlayback();
  const autoRead = useTtsAutoRead();
  const {
    sessions: chatSessions,
    activeSessionId: activeChatSessionId,
    restoring: chatRestoring,
    restoreSession: restoreChatSession,
    startNewChat: startNewChatSession,
    registerSessionId: registerChatSessionId,
  } = useAgentChatSessions<PatientChatMessage>({
    patientId,
    mapMessage: mapRestoredChatMessage,
    welcomeMessage: chatWelcomeMessage,
  });
  useEffect(() => {
    let cancelled = false;
    const loadPatient = async () => {
      setLoading(true);
      setError(null);
      setAccessDenied(false);
      setGlucoseRecords([]);
      setBloodPressureRecords([]);
      setClinicalNotes([]);
      setMedicationList([]);
      setMedicationNotes([]);
      setMedicationError(null);
      // This page's component instance is reused by Next.js when navigating
      // between /doctor/patients/[id] routes (no remount on param change),
      // so chat state left over from the previous patient must be cleared
      // here too — otherwise it visibly leaks into this patient's widget
      // even though the backend already scopes every ChatSession to a
      // specific (patient, doctor) pair (see useAgentChatSessions above,
      // which separately reloads the session list on patientId change).
      setChatMessages([chatWelcomeMessage]);
      setQuestion("");
      setChatError(null);
      try {
        const [
          patientResult,
          glucoseResult,
          bloodPressureResult,
          clinicalNotesResult,
          medicationResult,
          medicationNoteResult,
        ] = await Promise.allSettled([
          api.patients(),
          api.glucose(patientId),
          api.bloodPressure(patientId),
          api.clinicalNotes(patientId),
          api.activeMedications(patientId),
          api.medicationNotes(patientId),
        ]);
        if (patientResult.status === "fulfilled") {
          const result = patientResult.value.find(
            (item) => item.id === patientId,
          );
          if (result && !cancelled) {
            setPatient((current) => ({
              ...current,
              id: result.id,
              name: result.full_name,
            }));
          } else if (!cancelled) {
            // GET /patients is server-scoped to this doctor's OWN assigned
            // patients (patients/router.py::list_my_patients), the same
            // assignment check every other patient-scoped call below
            // (glucose/blood-pressure/notes/medications) 403s on — so
            // patientId missing from this list is an authoritative "not
            // your patient", not a transient fetch failure. Real bug
            // (golden eval TC-SUM-007): without this check, `patient`
            // silently stayed at its mock fallback (doctorPatients[0])
            // forever, so an unassigned doctor saw a fabricated-looking
            // "real" patient header (name/age/alert message) sitting right
            // above the one panel (AgentSummaryCard) that already correctly
            // reported access denied.
            setAccessDenied(true);
          }
        } else if (!cancelled) {
          setError(
            "Không thể tải thông tin bệnh nhân. Đang hiển thị dữ liệu tạm thời.",
          );
        }
        if (glucoseResult.status === "fulfilled" && !cancelled) {
          setGlucoseRecords(glucoseResult.value);
        }
        if (bloodPressureResult.status === "fulfilled" && !cancelled) {
          setBloodPressureRecords(bloodPressureResult.value);
        }
        if (clinicalNotesResult.status === "fulfilled" && !cancelled) {
          setClinicalNotes(clinicalNotesResult.value);
        }
        if (
          glucoseResult.status === "fulfilled" &&
          glucoseResult.value.length > 0 &&
          !cancelled
        ) {
          const latest = glucoseResult.value.reduce((current, record) =>
            new Date(record.measured_at).getTime() >
            new Date(current.measured_at).getTime()
              ? record
              : current,
          );
          // alertLevel/alertMessage otherwise stay stuck on fallbackPatient's
          // mock value forever (this component only ever patches specific
          // fields onto the mock seed, never replaces it) — derive them from
          // the real threshold_status the same way doctor-dashboard.tsx's
          // patient list already does, so the header badge/alert banner
          // here agrees with that list instead of showing unrelated mock
          // text next to otherwise-real data.
          const level =
            latest.threshold_status === "DANGER"
              ? "critical"
              : latest.threshold_status === "WATCH"
                ? "warning"
                : latest.threshold_status === "NORMAL"
                  ? "normal"
                  : latest.value > 180
                    ? "critical"
                    : latest.value > 140
                      ? "warning"
                      : "normal";
          setPatient((current) => ({
            ...current,
            lastGlucose: latest.value,
            lastGlucoseTime: new Date(latest.measured_at).toLocaleString(
              "vi-VN",
            ),
            alertLevel: level as "critical" | "warning" | "normal",
            alertMessage:
              level === "critical"
                ? "Chỉ số đường huyết gần nhất ở mức nguy hiểm."
                : level === "warning"
                  ? "Chỉ số đường huyết gần nhất cần theo dõi (AI dự đoán)."
                  : "",
          }));
        }
        if (!cancelled) {
          if (medicationResult.status === "fulfilled") {
            setMedicationList(medicationResult.value.map(toMedication));
            setMedicationError(null);
          } else {
            setMedicationList([]);
            setMedicationError("Không thể tải phác đồ thuốc của bệnh nhân.");
          }
          setMedicationNotes(
            medicationNoteResult.status === "fulfilled"
              ? medicationNoteResult.value
              : [],
          );
        }
      } catch {
        if (!cancelled)
          setError(
            "Không thể tải dữ liệu bệnh nhân. Đang hiển thị dữ liệu tạm thời.",
          );
      } finally {
        if (!cancelled) setLoading(false);
      }
    };
    loadPatient();
    return () => {
      cancelled = true;
    };
  }, [patientId]);

  useEffect(() => {
    let cancelled = false;
    const loadAppointments = async () => {
      setAppointmentsLoading(true);
      setAppointmentsError(null);
      try {
        const result = await api.appointments(patientId);
        if (!cancelled) setAppointmentList(result);
      } catch (loadError) {
        if (!cancelled)
          setAppointmentsError(
            loadError instanceof Error
              ? loadError.message
              : "Không thể tải lịch khám.",
          );
      } finally {
        if (!cancelled) setAppointmentsLoading(false);
      }
    };
    loadAppointments();
    return () => {
      cancelled = true;
    };
  }, [patientId]);
  const submitQuestion = async (questionOverride?: string) => {
    const trimmedQuestion = (questionOverride ?? question).trim();
    if (!trimmedQuestion || chatLoading) return;
    const now = Date.now();
    setChatMessages((items) => [
      ...items,
      { id: `q-${now}`, role: "doctor", text: trimmedQuestion },
    ]);
    setQuestion("");
    setChatLoading(true);
    setChatError(null);
    try {
      const result = await api.agentChat(
        trimmedQuestion,
        patientId,
        activeChatSessionId ?? undefined,
      );
      registerChatSessionId(result.session_id);
      const assistantMessage: PatientChatMessage = {
        id: `a-${Date.now()}`,
        role: "assistant",
        text: result.response || "AI chưa trả về nội dung.",
        disclaimer: result.disclaimer,
        limitations: result.limitations,
        sources: result.sources,
        handoff: result.handoff,
      };
      setChatMessages((items) => [...items, assistantMessage]);
      if (autoRead.enabled) void tts.play(assistantMessage.id, assistantMessage.text);
    } catch (chatRequestError) {
      setChatError(
        chatRequestError instanceof Error
          ? chatRequestError.message
          : "Không thể kết nối trợ lý AI.",
      );
    } finally {
      setChatLoading(false);
    }
  };

  const selectChatSession = async (sessionId: string) => {
    if (sessionId === activeChatSessionId || chatLoading) return;
    setChatError(null);
    setChatMessages(await restoreChatSession(sessionId));
  };

  const startNewChat = () => {
    if (chatLoading) return;
    setChatError(null);
    setChatMessages(startNewChatSession());
  };

  const startAddingMedication = () => {
    setEditingMedicationId(null);
    setMedicationForm(emptyMedicationForm);
  };

  const startEditingMedication = (medication: Medication) => {
    setEditingMedicationId(medication.id);
    setMedicationForm({
      name: medication.name,
      dose: medication.dose,
      frequency: medication.frequency,
      startDate: medication.startDate,
      status: medication.status,
      instruction: medication.instruction,
    });
  };

  const reloadMedications = async () => {
    const [active, notes] = await Promise.all([
      api.activeMedications(patientId),
      api.medicationNotes(patientId),
    ]);
    setMedicationList(active.map(toMedication));
    setMedicationNotes(notes);
  };

  const saveMedication = async (event: React.FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (
      !medicationForm.name.trim() ||
      !medicationForm.dose.trim() ||
      !medicationForm.frequency.trim() ||
      !medicationForm.startDate
    )
      return;
    setMedicationSaving(true);
    setMedicationError(null);
    try {
      if (editingMedicationId && medicationForm.status === "inactive") {
        await api.discontinueMedication(editingMedicationId, {
          end_date: new Date().toISOString(),
          note: medicationForm.instruction.trim() || "Bác sĩ ngừng thuốc",
        });
      } else {
        const payload = {
          drug_name: medicationForm.name.trim(),
          dose: medicationForm.dose.trim(),
          frequency: medicationForm.frequency.trim(),
          start_date: `${medicationForm.startDate}T00:00:00`,
          note: medicationForm.instruction.trim() || null,
        };
        if (editingMedicationId)
          await api.changeMedication(editingMedicationId, payload);
        else await api.createMedication({ patient_id: patientId, ...payload });
      }
      await reloadMedications();
      startAddingMedication();
    } catch (cause) {
      setMedicationError(
        cause instanceof Error
          ? cause.message
          : "Không thể lưu thay đổi thuốc.",
      );
    } finally {
      setMedicationSaving(false);
    }
  };

  const deleteMedication = async (medication: Medication) => {
    if (
      !window.confirm(
        `Ngừng thuốc “${medication.name}”? Lịch sử phác đồ vẫn được lưu.`,
      )
    )
      return;
    setMedicationSaving(true);
    setMedicationError(null);
    try {
      await api.discontinueMedication(medication.id, {
        note: "Bác sĩ ngừng thuốc",
      });
      await reloadMedications();
      if (editingMedicationId === medication.id) startAddingMedication();
    } catch (cause) {
      setMedicationError(
        cause instanceof Error ? cause.message : "Không thể ngừng thuốc.",
      );
    } finally {
      setMedicationSaving(false);
    }
  };

  const startAddingAppointment = () => {
    setEditingAppointmentId(null);
    setAppointmentForm(emptyAppointmentForm);
    setAppointmentsError(null);
  };

  const startEditingAppointment = (appointment: Appointment) => {
    setEditingAppointmentId(appointment.id);
    setAppointmentForm({
      scheduledAt: toDateTimeLocal(appointment.scheduled_at),
      reason: appointment.reason ?? "",
      status: appointment.status,
      notes: appointment.notes ?? "",
    });
    setAppointmentsError(null);
  };

  const saveAppointment = async (event: React.FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (!appointmentForm.scheduledAt) return;
    setAppointmentSaving(true);
    setAppointmentsError(null);
    const payload = {
      scheduled_at: `${appointmentForm.scheduledAt}:00`,
      reason: appointmentForm.reason.trim() || null,
      status: appointmentForm.status,
      notes: appointmentForm.notes.trim() || null,
    };
    try {
      if (editingAppointmentId) {
        const updated = await api.updateAppointment(
          editingAppointmentId,
          payload,
        );
        setAppointmentList((current) =>
          current
            .map((appointment) =>
              appointment.id === editingAppointmentId ? updated : appointment,
            )
            .sort(
              (first, second) =>
                new Date(first.scheduled_at).getTime() -
                new Date(second.scheduled_at).getTime(),
            ),
        );
      } else {
        const created = await api.createAppointment({
          patient_id: patientId,
          ...payload,
        });
        setAppointmentList((current) =>
          [...current, created].sort(
            (first, second) =>
              new Date(first.scheduled_at).getTime() -
              new Date(second.scheduled_at).getTime(),
          ),
        );
      }
      startAddingAppointment();
    } catch (saveError) {
      setAppointmentsError(
        saveError instanceof Error
          ? saveError.message
          : "Không thể lưu lịch khám.",
      );
    } finally {
      setAppointmentSaving(false);
    }
  };

  const deleteAppointment = async (appointment: Appointment) => {
    const dateTime = new Date(appointment.scheduled_at).toLocaleString("vi-VN");
    if (!window.confirm(`Xóa lịch khám lúc ${dateTime}?`)) return;
    setAppointmentsError(null);
    try {
      await api.deleteAppointment(appointment.id);
      setAppointmentList((current) =>
        current.filter((item) => item.id !== appointment.id),
      );
      if (editingAppointmentId === appointment.id) startAddingAppointment();
    } catch (deleteError) {
      setAppointmentsError(
        deleteError instanceof Error
          ? deleteError.message
          : "Không thể xóa lịch khám.",
      );
    }
  };

  const tabs: { id: Tab; label: string; icon: React.ReactNode }[] = [
    {
      id: "overview",
      label: "Tổng quan",
      icon: <Activity className="w-3.5 h-3.5" />,
    },
    {
      id: "glucose",
      label: "Đường huyết",
      icon: <Droplets className="w-3.5 h-3.5" />,
    },
    {
      id: "medication",
      label: "Thuốc",
      icon: <Pill className="w-3.5 h-3.5" />,
    },
    {
      id: "meals",
      label: "Bữa ăn",
      icon: <UtensilsCrossed className="w-3.5 h-3.5" />,
    },
    {
      id: "appointments",
      label: "Lịch khám",
      icon: <Calendar className="w-3.5 h-3.5" />,
    },
    { id: "ai", label: "AI hỗ trợ", icon: <Bot className="w-3.5 h-3.5" /> },
    {
      id: "insights",
      label: "Nhận định AI",
      icon: <Lightbulb className="w-3.5 h-3.5" />,
    },
    {
      id: "notes",
      label: "Ghi chú",
      icon: <FileText className="w-3.5 h-3.5" />,
    },
  ];

  const glucoseReportRecords = glucoseRecordsInRange(
    glucoseRecords,
    glucoseRange,
  );
  const glucoseValues = glucoseReportRecords.map((record) => record.value);
  const glucoseAverage = glucoseValues.length
    ? Math.round(
        glucoseValues.reduce((sum, value) => sum + value, 0) /
          glucoseValues.length,
      )
    : 0;
  // Prefer the backend's personalized threshold_status over a fixed 70-140
  // cutoff — see health-monitoring.tsx's identical fix for why (that fixed
  // range ignores the doctor-approved CarePlanTarget/AI-predicted band).
  const glucoseInTarget = glucoseReportRecords.filter((record) =>
    record.threshold_status === "NORMAL" ||
    (record.threshold_status == null || record.threshold_status === "UNAVAILABLE"
      ? record.value >= 70 && record.value <= 140
      : false),
  ).length;
  const glucoseTargetPercent = glucoseValues.length
    ? Math.round((glucoseInTarget / glucoseValues.length) * 100)
    : 0;
  const glucoseReportHistory = [...glucoseReportRecords].reverse();
  const glucoseHistoryPageCount = Math.max(1, Math.ceil(glucoseReportHistory.length / 10));
  const currentGlucoseHistoryPage = Math.min(glucoseHistoryPage, glucoseHistoryPageCount);
  const paginatedGlucoseHistory = glucoseReportHistory.slice((currentGlucoseHistoryPage - 1) * 10, currentGlucoseHistoryPage * 10);

  const handleGlucoseRangeChange = (range: GlucoseRange) => {
    setGlucoseRange(range);
    setGlucoseHistoryPage(1);
  };

  const glucoseStatus = (record: GlucoseRecord) =>
    record.threshold_status === "DANGER"
      ? "critical"
      : record.threshold_status === "WATCH"
        ? record.predicted_min_mg_dl != null && record.value < record.predicted_min_mg_dl
          ? "low"
          : "high"
        : record.threshold_status === "NORMAL"
          ? "normal"
          : record.value < 70
            ? "low"
            : record.value > 180
              ? "critical"
              : record.value > 140
                ? "high"
                : "normal";

  const latestGlucoseRecord = [...glucoseRecords]
    .filter(
      (record) =>
        Number.isFinite(record.value) &&
        Number.isFinite(new Date(record.measured_at).getTime()),
    )
    .sort(
      (a, b) =>
        new Date(b.measured_at).getTime() - new Date(a.measured_at).getTime(),
    )[0];
  const latestBloodPressureRecord = [...bloodPressureRecords]
    .filter(
      (record) =>
        Number.isFinite(record.systolic) &&
        Number.isFinite(record.diastolic) &&
        Number.isFinite(new Date(record.measured_at).getTime()),
    )
    .sort(
      (a, b) =>
        new Date(b.measured_at).getTime() - new Date(a.measured_at).getTime(),
    )[0];
  const latestWeightNote = [...clinicalNotes]
    .filter(
      (note) =>
        note.weight_kg !== null &&
        Number.isFinite(note.weight_kg) &&
        Number.isFinite(new Date(note.updated_at).getTime()),
    )
    .sort(
      (a, b) =>
        new Date(b.updated_at).getTime() - new Date(a.updated_at).getTime(),
    )[0];

  const handleExportReport = () => {
    const reportWindow = window.open("", "_blank", "width=1000,height=800");
    if (!reportWindow) {
      window.alert("Trình duyệt đang chặn cửa sổ báo cáo. Vui lòng cho phép cửa sổ bật lên và thử lại.");
      return;
    }
    reportWindow.opener = null;

    const generatedAt = new Intl.DateTimeFormat("vi-VN", { dateStyle: "full", timeStyle: "short" }).format(new Date());
    const rangeLabel = glucoseRange === "24h" ? "24 giờ" : `${RANGE_DAYS[glucoseRange]} ngày`;
    const glucoseRows = glucoseReportHistory.length
      ? glucoseReportHistory.map((record) => `<tr><td>${escapeReportText(new Date(record.measured_at).toLocaleString("vi-VN"))}</td><td><strong>${escapeReportText(record.value)} mg/dL</strong></td><td>${escapeReportText(statusConfig[glucoseStatus(record)].label)}</td><td>${escapeReportText(record.meal_context || "—")}</td><td>${escapeReportText(record.note || "—")}</td></tr>`).join("")
      : '<tr><td colspan="5" class="empty">Chưa có dữ liệu đường huyết trong khoảng đã chọn.</td></tr>';
    const medicationRows = medicationList.length
      ? medicationList.map((item) => `<tr><td><strong>${escapeReportText(item.name)}</strong></td><td>${escapeReportText(item.dose)}</td><td>${escapeReportText(item.frequency)}</td><td>${escapeReportText(item.status === "active" ? "Đang dùng" : "Đã ngừng")}</td><td>${escapeReportText(item.instruction || "—")}</td></tr>`).join("")
      : '<tr><td colspan="5" class="empty">Chưa có thông tin thuốc.</td></tr>';
    const mealResponseRows = mealResponse && mealResponse.by_food.length
      ? mealResponse.by_food.map((group) => `<tr><td><strong>${escapeReportText(group.name)}</strong></td><td>${escapeReportText(group.n)}</td><td>${escapeReportText(Math.round(group.avg_post_mg_dl))} mg/dL</td><td>${group.avg_delta_mg_dl === null ? "—" : `${escapeReportText(Math.round(group.avg_delta_mg_dl))} mg/dL`}</td><td>${escapeReportText(Math.round(group.pct_over_target))}%</td></tr>`).join("")
      : `<tr><td colspan="5" class="empty">Chưa đủ dữ liệu (cần từ ${escapeReportText(mealResponse?.min_n ?? 3)} bữa có đo đường huyết sau ăn cho mỗi món).</td></tr>`;
    const appointmentRows = appointmentList.length
      ? appointmentList.map((item) => `<tr><td>${escapeReportText(new Date(item.scheduled_at).toLocaleString("vi-VN"))}</td><td>${escapeReportText(item.reason || "Tái khám")}</td><td>${escapeReportText(item.status === "scheduled" ? "Đã lên lịch" : item.status === "completed" ? "Đã hoàn thành" : "Đã hủy")}</td><td>${escapeReportText(item.notes || "—")}</td></tr>`).join("")
      : '<tr><td colspan="4" class="empty">Chưa có lịch khám.</td></tr>';

    reportWindow.document.write(`<!doctype html><html lang="vi"><head><meta charset="utf-8"><title>Báo cáo sức khỏe - ${escapeReportText(patient.name)}</title><style>
      @page{size:A4;margin:14mm}*{box-sizing:border-box}body{margin:0;color:#173b3a;font-family:Arial,sans-serif;font-size:12px;line-height:1.45}.header{display:flex;justify-content:space-between;gap:24px;border-bottom:3px solid #43b5ae;padding-bottom:14px}.brand{font-size:22px;font-weight:800;color:#238d87}.muted{color:#64748b}.patient{margin:18px 0;padding:14px 16px;border:1px solid #cde8e5;border-radius:12px;background:#f4fbfa}.patient h1{margin:0 0 5px;font-size:20px}.metrics{display:grid;grid-template-columns:repeat(4,1fr);gap:10px;margin:14px 0}.metric{padding:12px;border:1px solid #dbe5e4;border-radius:10px}.metric span{display:block;color:#64748b;font-size:10px;text-transform:uppercase}.metric strong{display:block;margin-top:4px;font-size:17px;color:#238d87}h2{margin:20px 0 8px;font-size:14px}table{width:100%;border-collapse:collapse;page-break-inside:auto}thead{display:table-header-group}tr{page-break-inside:avoid}th,td{padding:7px 8px;border:1px solid #dbe5e4;text-align:left;vertical-align:top}th{background:#eaf8f6;color:#235f5c;font-size:10px;text-transform:uppercase}.empty{text-align:center;color:#64748b;padding:16px}.footer{margin-top:20px;padding-top:10px;border-top:1px solid #dbe5e4;color:#64748b;font-size:10px}.actions{position:fixed;right:20px;bottom:20px}.actions button{border:0;border-radius:9px;background:#238d87;color:white;padding:10px 16px;font-weight:700;cursor:pointer}@media print{.actions{display:none}}@media(max-width:700px){.metrics{grid-template-columns:repeat(2,1fr)}}
    </style></head><body><header class="header"><div><div class="brand">VDiab</div><div class="muted">Báo cáo theo dõi sức khỏe bệnh nhân</div></div><div class="muted">Ngày xuất: ${escapeReportText(generatedAt)}<br>Khoảng dữ liệu: ${escapeReportText(rangeLabel)}</div></header>
    <section class="patient"><h1>${escapeReportText(patient.name)}</h1><div>${escapeReportText(patient.age > 0 ? `${patient.age} tuổi` : "Chưa rõ tuổi")} · ${escapeReportText(patient.gender)} · ${escapeReportText(patient.disease)}</div></section>
    <div class="metrics"><div class="metric"><span>Đường huyết gần nhất</span><strong>${latestGlucoseRecord ? `${escapeReportText(latestGlucoseRecord.value)} mg/dL` : "Chưa có"}</strong></div><div class="metric"><span>Trung bình</span><strong>${glucoseValues.length ? `${glucoseAverage} mg/dL` : "Chưa có"}</strong></div><div class="metric"><span>Trong mục tiêu</span><strong>${glucoseValues.length ? `${glucoseTargetPercent}%` : "Chưa có"}</strong></div><div class="metric"><span>Huyết áp gần nhất</span><strong>${latestBloodPressureRecord ? `${escapeReportText(latestBloodPressureRecord.systolic)}/${escapeReportText(latestBloodPressureRecord.diastolic)}` : "Chưa có"}</strong></div></div>
    <h2>Lịch sử đường huyết (${escapeReportText(rangeLabel)})</h2><table><thead><tr><th>Thời gian</th><th>Chỉ số</th><th>Trạng thái</th><th>Thời điểm đo</th><th>Ghi chú</th></tr></thead><tbody>${glucoseRows}</tbody></table>
    <h2>Bữa ăn liên quan tăng đường huyết (${escapeReportText(mealResponse?.days ?? 30)} ngày)</h2><table><thead><tr><th>Món</th><th>Số bữa</th><th>ĐH sau ăn TB</th><th>Tăng so với trước ăn</th><th>% bữa &gt; ${escapeReportText(mealResponse?.post_prandial_target_mg_dl ?? 180)} mg/dL</th></tr></thead><tbody>${mealResponseRows}</tbody></table><p class="muted">Liên quan thống kê từ nhật ký tự ghi, không phải nguyên nhân. Carb ước tính từ bảng tra.</p>
    <h2>Phác đồ thuốc</h2><table><thead><tr><th>Thuốc</th><th>Liều dùng</th><th>Tần suất</th><th>Trạng thái</th><th>Hướng dẫn</th></tr></thead><tbody>${medicationRows}</tbody></table>
    <h2>Lịch khám</h2><table><thead><tr><th>Thời gian</th><th>Lý do</th><th>Trạng thái</th><th>Ghi chú</th></tr></thead><tbody>${appointmentRows}</tbody></table>
    <p class="footer">Báo cáo được tạo từ dữ liệu đang có trong hệ thống VDiab và chỉ dùng để hỗ trợ theo dõi. Cần đối chiếu hồ sơ lâm sàng trước khi đưa ra quyết định điều trị.</p><div class="actions"><button onclick="window.print()">In / Lưu PDF</button></div></body></html>`);
    reportWindow.document.close();
    reportWindow.focus();
  };
  const formatMeasuredAt = (value?: string, utc = false) =>
    value
      ? new Intl.DateTimeFormat("vi-VN", {
          hour: "2-digit",
          minute: "2-digit",
          day: "2-digit",
          month: "2-digit",
          year: "numeric",
        }).format(utc ? parseUtcTimestamp(value) : new Date(value))
      : "Chưa có dữ liệu";

  if (accessDenied) {
    return (
      <div className="mx-auto max-w-6xl space-y-5">
        <button
          onClick={onBack}
          className="flex items-center gap-2 text-sm text-muted-foreground hover:text-foreground mb-4 transition-colors"
        >
          <ArrowLeft className="w-4 h-4" /> Quay lại danh sách
        </button>
        <div
          role="alert"
          className="flex items-start gap-3 rounded-2xl border border-red-200 bg-red-50 p-5"
        >
          <AlertTriangle className="mt-0.5 h-5 w-5 flex-shrink-0 text-red-500" />
          <div>
            <p className="font-semibold text-red-700">Không có quyền truy cập</p>
            <p className="mt-1 text-sm leading-relaxed text-red-700">
              Bạn không có quyền truy cập dữ liệu của bệnh nhân này. Bệnh nhân
              này chưa được phân công cho bạn.
            </p>
          </div>
        </div>
      </div>
    );
  }

  return (
    <div className="mx-auto max-w-6xl space-y-5">
      {loading && (
        <div className="rounded-xl border border-border bg-card p-4 text-sm text-muted-foreground">
          Đang tải thông tin bệnh nhân...
        </div>
      )}
      {error && (
        <div
          role="status"
          className="rounded-xl border border-yellow-200 bg-yellow-50 px-4 py-3 text-sm text-yellow-800"
        >
          {error}
        </div>
      )}
      {/* Back + patient header */}
      <div>
        <button
          onClick={onBack}
          className="flex items-center gap-2 text-sm text-muted-foreground hover:text-foreground mb-4 transition-colors"
        >
          <ArrowLeft className="w-4 h-4" /> Quay lại danh sách
        </button>

        <div className="bg-card rounded-2xl p-5 border border-border">
          <div className="flex items-start gap-4">
            <div className="w-14 h-14 rounded-2xl bg-accent flex items-center justify-center flex-shrink-0">
              <User className="w-7 h-7 text-muted-foreground" />
            </div>
            <div className="flex-1 min-w-0">
              <h2 className="text-lg font-bold text-foreground">
                {patient.name}
              </h2>
              <p className="text-sm text-muted-foreground">
                {patient.age} tuổi · {patient.gender} · {patient.disease}
              </p>
              <div className="flex flex-wrap gap-2 mt-3">
                <div
                  className={cn(
                    "flex items-center gap-1.5 px-3 py-1.5 rounded-xl text-xs font-medium border",
                    patient.alertLevel === "critical"
                      ? "bg-red-50 text-red-700 border-red-200"
                      : patient.alertLevel === "warning"
                        ? "bg-yellow-50 text-yellow-700 border-yellow-200"
                        : "bg-green-50 text-green-700 border-green-200",
                  )}
                >
                  <span
                    className={cn(
                      "w-1.5 h-1.5 rounded-full",
                      patient.alertLevel === "critical"
                        ? "bg-red-500"
                        : patient.alertLevel === "warning"
                          ? "bg-yellow-500"
                          : "bg-green-500",
                    )}
                  />
                  {patient.alertLevel === "critical"
                    ? "Nguy hiểm"
                    : patient.alertLevel === "warning"
                      ? "Cần theo dõi"
                      : "Bình thường"}
                </div>
                <div className="flex items-center gap-1.5 px-3 py-1.5 rounded-xl text-xs font-medium bg-accent text-primary border border-primary/20">
                  <Droplets className="w-3 h-3" />{" "}
                  {latestGlucoseRecord
                    ? `${latestGlucoseRecord.value} mg/dL`
                    : "Chưa có đường huyết"}
                </div>
              </div>
            </div>
            <div className="flex gap-2">
              <button className="p-2.5 rounded-xl border border-border bg-card hover:bg-muted transition-colors">
                <Phone className="w-4 h-4 text-muted-foreground" />
              </button>
              <button className="p-2.5 rounded-xl border border-border bg-card hover:bg-muted transition-colors">
                <MessageSquare className="w-4 h-4 text-muted-foreground" />
              </button>
            </div>
          </div>

          {patient.alertMessage && (
            <div className="mt-4 flex gap-2 p-3 bg-red-50 rounded-xl border border-red-200">
              <AlertTriangle className="w-4 h-4 text-red-500 flex-shrink-0 mt-0.5" />
              <p className="text-xs text-red-700 leading-relaxed">
                {patient.alertMessage}
              </p>
            </div>
          )}
        </div>
      </div>

      {/* Tabs */}
      <div className="flex gap-1 bg-muted rounded-xl p-1 overflow-x-auto">
        {tabs.map((t) => (
          <button
            key={t.id}
            onClick={() => setActiveTab(t.id)}
            className={cn(
              "flex-1 flex items-center justify-center gap-1.5 py-2 rounded-lg text-xs font-medium transition-colors whitespace-nowrap px-2",
              activeTab === t.id
                ? "bg-card text-foreground shadow-sm"
                : "text-muted-foreground hover:text-foreground",
            )}
          >
            {t.icon} {t.label}
          </button>
        ))}
      </div>

      {/* Tab: Overview */}
      {activeTab === "overview" && (
        <div className="space-y-4">
          <div className="grid grid-cols-2 gap-3">
            {[
              {
                label: "Đường huyết cuối",
                value: latestGlucoseRecord
                  ? `${latestGlucoseRecord.value} mg/dL`
                  : "—",
                sub: formatMeasuredAt(latestGlucoseRecord?.measured_at),
                icon: Droplets,
                color: "text-[#43B5AE] bg-accent",
              },
              {
                label: "Huyết áp",
                value: latestBloodPressureRecord
                  ? `${latestBloodPressureRecord.systolic}/${latestBloodPressureRecord.diastolic} mmHg`
                  : "—",
                sub: formatMeasuredAt(latestBloodPressureRecord?.measured_at),
                icon: Activity,
                color: "text-red-500 bg-red-50",
              },
              {
                label: "Cân nặng",
                value:
                  latestWeightNote?.weight_kg !== null &&
                  latestWeightNote?.weight_kg !== undefined
                    ? `${latestWeightNote.weight_kg} kg`
                    : "—",
                sub: latestWeightNote
                  ? `Cập nhật ${formatMeasuredAt(latestWeightNote.updated_at, true)}`
                  : "Chưa có dữ liệu khám",
                icon: TrendingDown,
                color: "text-blue-500 bg-blue-50",
              },
              {
                label: "Thuốc đang dùng",
                value: `${medicationList.filter((item) => item.status === "active").length}`,
                sub: medicationList.length
                  ? "Theo phác đồ hiện tại"
                  : "Chưa có thuốc đang dùng",
                icon: Pill,
                color: "text-purple-500 bg-purple-50",
              },
            ].map(({ label, value, sub, icon: Icon, color }) => (
              <div
                key={label}
                className="bg-card rounded-2xl p-4 border border-border"
              >
                <div
                  className={cn(
                    "w-8 h-8 rounded-xl flex items-center justify-center mb-3",
                    color.split(" ")[1],
                  )}
                >
                  <Icon className={cn("w-4 h-4", color.split(" ")[0])} />
                </div>
                <p className="text-lg font-bold text-foreground">{value}</p>
                <p className="text-xs text-muted-foreground mt-0.5">{label}</p>
                <p className="text-xs text-muted-foreground">{sub}</p>
              </div>
            ))}
          </div>

          <PrevisitBriefCard patientId={patientId} patientName={patient.name} />
          <AgentSummaryCard patientId={patientId} days={RANGE_DAYS[glucoseRange]} />
          {/* Trend chart */}
          <div className="bg-card rounded-2xl p-5 border border-border">
            <GlucoseTrendChart
              records={glucoseRecords}
              range={glucoseRange}
              onRangeChange={handleGlucoseRangeChange}
            />
          </div>

          {/* Quick actions */}
          <button
            onClick={handleExportReport}
            type="button"
            className="w-full flex items-center gap-3 p-4 bg-card rounded-2xl border border-border hover:bg-accent/50 transition-colors"
          >
            <div className="w-9 h-9 rounded-xl bg-accent flex items-center justify-center">
              <Download className="w-4 h-4 text-primary" />
            </div>

            <div className="text-left">
              <p className="text-sm font-medium text-foreground">
                Xuất báo cáo
              </p>
              <p className="text-xs text-muted-foreground">
                Xem trước, in hoặc lưu dưới dạng PDF
              </p>
            </div>
          </button>
        </div>
      )}

      {/* Tab: Glucose */}
      {activeTab === "glucose" && (
        <div className="space-y-4">
          <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
            {[
              { label: "Trung bình", value: glucoseAverage, sub: "mg/dL" },
              {
                label: "Cao nhất",
                value: glucoseValues.length ? Math.max(...glucoseValues) : 0,
                sub: "mg/dL",
              },
              {
                label: "Thấp nhất",
                value: glucoseValues.length ? Math.min(...glucoseValues) : 0,
                sub: "mg/dL",
              },
              {
                label: "Trong mục tiêu",
                value: `${glucoseTargetPercent}%`,
                sub: `${glucoseInTarget}/${glucoseValues.length} lần đo`,
              },
            ].map((item) => (
              <div
                key={item.label}
                className="rounded-2xl border border-border bg-card p-4"
              >
                <p className="text-xs text-muted-foreground">{item.label}</p>
                <p className="mt-1 text-2xl font-bold text-foreground">
                  {item.value}
                </p>
                <p className="text-xs text-muted-foreground">{item.sub}</p>
              </div>
            ))}
          </div>

          <div className="rounded-2xl border border-border bg-card p-5">
            <GlucoseTrendChart
              records={glucoseRecords}
              range={glucoseRange}
              onRangeChange={handleGlucoseRangeChange}
            />
          </div>

          <div className="bg-card rounded-2xl p-5 border border-border">
            <div className="mb-4 flex items-center justify-between gap-3">
              <h3 className="font-semibold text-foreground">
                Các lần đo trong khoảng
              </h3>
              <span className="text-xs text-muted-foreground">
                {glucoseReportHistory.length} bản ghi
              </span>
            </div>
            <div className="space-y-2">
              {glucoseReportHistory.length === 0 && (
                <div className="rounded-xl border border-dashed border-border p-8 text-center text-sm text-muted-foreground">
                  Chưa có dữ liệu đường huyết trong khoảng thời gian này.
                </div>
              )}
              {paginatedGlucoseHistory.map((record) => {
                const cfg = statusConfig[glucoseStatus(record)];
                const measuredAt = new Date(record.measured_at);
                const [timing, ...noteParts] = (record.note || "").split(" - ");
                const note = noteParts.join(" - ");
                return (
                  <div
                    key={record.id}
                    className="flex items-center gap-3 p-3 rounded-xl bg-muted"
                  >
                    <div
                      className={cn(
                        "w-8 h-8 rounded-xl flex items-center justify-center flex-shrink-0",
                        cfg.bg,
                      )}
                    >
                      <Droplets className={cn("w-4 h-4", cfg.color)} />
                    </div>
                    <div className="flex-1 min-w-0">
                      <p className="text-sm font-medium text-foreground">
                        {record.value}{" "}
                        <span className="text-xs font-normal text-muted-foreground">
                          mg/dL
                        </span>
                      </p>
                      <p className="text-xs text-muted-foreground">
                        {timing || "Không ghi thời điểm"} ·{" "}
                        {measuredAt.toLocaleTimeString("vi-VN", {
                          hour: "2-digit",
                          minute: "2-digit",
                        })}
                      </p>
                      {note && (
                        <p
                          className="mt-0.5 truncate text-xs text-muted-foreground"
                          title={note}
                        >
                          {note}
                        </p>
                      )}
                    </div>
                    <div className="text-right">
                      <span
                        className={cn(
                          "text-xs px-2 py-0.5 rounded-full font-medium border",
                          cfg.bg,
                          cfg.color,
                          cfg.border,
                        )}
                      >
                        {cfg.label}
                      </span>
                      <p className="text-xs text-muted-foreground mt-1">
                        {measuredAt.toLocaleDateString("vi-VN")}
                      </p>
                    </div>
                  </div>
                );
              })}
            </div>
            <Pagination page={currentGlucoseHistoryPage} totalItems={glucoseReportHistory.length} onPageChange={setGlucoseHistoryPage} className="-mx-5 -mb-5 mt-4" />
          </div>
        </div>
      )}

      {/* Tab: Medication */}
      {activeTab === "medication" && (
        <div className="space-y-3">
          <MedicationAdherencePanel patientId={patientId} audience="doctor" />
          <div className="flex items-center justify-between">
            <div>
              <h3 className="font-semibold text-foreground">Danh sách thuốc</h3>
              <p className="text-xs text-muted-foreground">
                {medicationList.length} thuốc trong phác đồ
              </p>
            </div>
            <button
              type="button"
              onClick={() => {
                startAddingMedication();
                setIsMedicationManagerOpen(true);
              }}
              className="flex items-center gap-2 rounded-xl bg-primary px-3 py-2 text-sm font-medium text-primary-foreground transition-colors hover:bg-[#2D9D97]"
            >
              <Pencil className="h-4 w-4" />
              Chỉnh sửa
            </button>
          </div>

          {medicationError && (
            <div
              role="alert"
              className="rounded-xl border border-red-200 bg-red-50 p-3 text-sm text-red-700"
            >
              {medicationError}
            </div>
          )}

          {medicationList.length === 0 && (
            <div className="rounded-2xl border border-dashed border-border bg-card p-8 text-center">
              <Pill className="mx-auto mb-2 h-8 w-8 text-muted-foreground" />
              <p className="text-sm text-muted-foreground">
                Bệnh nhân chưa có thuốc trong phác đồ.
              </p>
            </div>
          )}

          {medicationList.map((med) => (
            <div
              key={med.id}
              className="bg-card rounded-2xl p-5 border border-border"
            >
              <div className="flex items-start gap-3">
                <div className="w-10 h-10 rounded-xl bg-purple-50 flex items-center justify-center flex-shrink-0">
                  <Pill className="w-5 h-5 text-purple-500" />
                </div>
                <div className="flex-1">
                  <div className="flex items-center gap-2">
                    <h4 className="font-semibold text-foreground">
                      {med.name}
                    </h4>
                    <span
                      className={cn(
                        "text-xs px-2 py-0.5 rounded-full font-medium",
                        med.status === "active"
                          ? "bg-green-100 text-green-700"
                          : "bg-muted text-muted-foreground",
                      )}
                    >
                      {med.status === "active" ? "Đang dùng" : "Dừng"}
                    </span>
                  </div>
                  <p className="text-sm text-muted-foreground mt-0.5">
                    {med.dose} · {med.frequency}
                  </p>
                  <p className="text-xs text-muted-foreground mt-1">
                    Bắt đầu: {med.startDate}
                  </p>
                  <p className="text-xs text-foreground/70 mt-2 leading-relaxed">
                    {med.instruction}
                  </p>
                </div>
              </div>
            </div>
          ))}

          {medicationNotes.length > 0 && (
            <section className="rounded-2xl border border-orange-200 bg-orange-50 p-4">
              <h4 className="font-semibold text-orange-800">
                Báo cáo từ bệnh nhân
              </h4>
              <div className="mt-3 space-y-2">
                {[...medicationNotes].reverse().map((note) => {
                  const medication = medicationList.find(
                    (item) => item.id === note.medication_id,
                  );
                  return (
                    <div
                      key={note.id}
                      className="rounded-xl bg-white/70 p-3 text-sm"
                    >
                      <div className="flex flex-wrap items-center gap-2">
                        <span className="font-medium text-orange-800">
                          {note.note_type === "missed_dose"
                            ? "Quên liều"
                            : note.note_type === "adverse_reaction"
                              ? "Phản ứng bất lợi"
                              : "Ghi chú"}
                        </span>
                        {medication && (
                          <span className="text-xs text-orange-700">
                            · {medication.name}
                          </span>
                        )}
                        <time className="ml-auto text-xs text-orange-700">
                          {parseUtcTimestamp(note.occurred_at).toLocaleString("vi-VN")}
                        </time>
                      </div>
                      {note.text && (
                        <p className="mt-1 text-orange-900/80">{note.text}</p>
                      )}
                    </div>
                  );
                })}
              </div>
            </section>
          )}
        </div>
      )}

      {/* Tab: Appointments */}
      {activeTab === "appointments" && (
        <div className="space-y-3">
          <div className="flex items-center justify-between">
            <div>
              <h3 className="font-semibold text-foreground">
                Lịch khám của bệnh nhân
              </h3>
              <p className="text-xs text-muted-foreground">
                {appointmentList.length} lịch hẹn
              </p>
            </div>
            <button
              type="button"
              onClick={() => {
                startAddingAppointment();
                setIsAppointmentManagerOpen(true);
              }}
              className="flex items-center gap-2 rounded-xl bg-primary px-3 py-2 text-sm font-medium text-primary-foreground transition-colors hover:bg-[#2D9D97]"
            >
              <Plus className="h-4 w-4" />
              Thêm lịch
            </button>
          </div>

          {appointmentsError && !isAppointmentManagerOpen && (
            <div
              role="alert"
              className="rounded-xl border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700"
            >
              {appointmentsError}
            </div>
          )}

          {appointmentsLoading && (
            <div className="rounded-2xl border border-border bg-card p-5 text-sm text-muted-foreground">
              Đang tải lịch khám...
            </div>
          )}

          {!appointmentsLoading && appointmentList.length === 0 && (
            <div className="rounded-2xl border border-dashed border-border bg-card p-8 text-center">
              <CalendarDays className="mx-auto mb-2 h-8 w-8 text-muted-foreground" />
              <p className="text-sm text-muted-foreground">
                Bệnh nhân chưa có lịch khám.
              </p>
            </div>
          )}

          {!appointmentsLoading &&
            appointmentList.map((appointment) => {
              const scheduledAt = new Date(appointment.scheduled_at);
              return (
                <div
                  key={appointment.id}
                  className="rounded-2xl border border-border bg-card p-5"
                >
                  <div className="flex items-start gap-3">
                    <div className="flex h-11 w-11 flex-shrink-0 items-center justify-center rounded-xl bg-accent">
                      <Calendar className="h-5 w-5 text-primary" />
                    </div>
                    <div className="min-w-0 flex-1">
                      <div className="flex flex-wrap items-center gap-2">
                        <h4 className="font-semibold text-foreground">
                          {scheduledAt.toLocaleDateString("vi-VN")}
                        </h4>
                        <span className="text-sm font-medium text-primary">
                          {scheduledAt.toLocaleTimeString("vi-VN", {
                            hour: "2-digit",
                            minute: "2-digit",
                          })}
                        </span>
                        <span
                          className={cn(
                            "rounded-full px-2 py-0.5 text-xs font-medium",
                            appointment.status === "completed"
                              ? "bg-green-100 text-green-700"
                              : appointment.status === "cancelled"
                                ? "bg-red-100 text-red-700"
                                : "bg-blue-100 text-blue-700",
                          )}
                        >
                          {appointment.status === "completed"
                            ? "Đã hoàn thành"
                            : appointment.status === "cancelled"
                              ? "Đã hủy"
                              : "Đã lên lịch"}
                        </span>
                      </div>
                      <p className="mt-1 text-sm text-foreground">
                        {appointment.reason || "Khám định kỳ"}
                      </p>
                      {appointment.notes && (
                        <p className="mt-2 text-xs leading-relaxed text-muted-foreground">
                          {appointment.notes}
                        </p>
                      )}
                    </div>
                    <button
                      type="button"
                      aria-label="Chỉnh sửa lịch khám"
                      onClick={() => {
                        startEditingAppointment(appointment);
                        setIsAppointmentManagerOpen(true);
                      }}
                      className="rounded-lg p-2 text-primary hover:bg-accent"
                    >
                      <Pencil className="h-4 w-4" />
                    </button>
                  </div>
                </div>
              );
            })}
        </div>
      )}

      {isAppointmentManagerOpen && (
        <div
          role="dialog"
          aria-modal="true"
          aria-labelledby="appointment-manager-title"
          className="fixed inset-0 z-50 flex items-center justify-center bg-black/45 p-4"
          onMouseDown={(event) => {
            if (event.target === event.currentTarget)
              setIsAppointmentManagerOpen(false);
          }}
        >
          <div className="max-h-[90vh] w-full max-w-2xl overflow-y-auto rounded-2xl border border-border bg-card shadow-xl">
            <div className="sticky top-0 z-10 flex items-center justify-between border-b border-border bg-card px-5 py-4">
              <div>
                <h3
                  id="appointment-manager-title"
                  className="font-semibold text-foreground"
                >
                  Quản lý lịch khám
                </h3>
                <p className="text-xs text-muted-foreground">
                  Lịch khám của {patient.name}
                </p>
              </div>
              <button
                type="button"
                aria-label="Đóng"
                onClick={() => setIsAppointmentManagerOpen(false)}
                className="rounded-lg p-2 text-muted-foreground hover:bg-muted hover:text-foreground"
              >
                <X className="h-5 w-5" />
              </button>
            </div>

            <div className="grid gap-5 p-5 md:grid-cols-[1fr_1.1fr]">
              <form
                onSubmit={saveAppointment}
                className="space-y-3 rounded-xl bg-muted p-4"
              >
                <div className="flex items-center justify-between">
                  <h4 className="text-sm font-semibold text-foreground">
                    {editingAppointmentId ? "Sửa lịch khám" : "Thêm lịch khám"}
                  </h4>
                  {editingAppointmentId && (
                    <button
                      type="button"
                      onClick={startAddingAppointment}
                      className="text-xs font-medium text-primary hover:underline"
                    >
                      Thêm mới
                    </button>
                  )}
                </div>

                {appointmentsError && (
                  <div
                    role="alert"
                    className="rounded-xl border border-red-200 bg-red-50 p-3 text-xs text-red-700"
                  >
                    {appointmentsError}
                  </div>
                )}

                <label className="block text-xs font-medium text-foreground">
                  Ngày giờ khám *
                  <DatePicker
                    showTime={{ format: "HH:mm", minuteStep: 5 }}
                    value={
                      appointmentForm.scheduledAt &&
                      dayjs(appointmentForm.scheduledAt).isValid()
                        ? dayjs(appointmentForm.scheduledAt)
                        : null
                    }
                    onChange={(value) =>
                      setAppointmentForm((current) => ({
                        ...current,
                        scheduledAt: value?.format("YYYY-MM-DDTHH:mm") ?? "",
                      }))
                    }
                    format="DD/MM/YYYY HH:mm"
                    size="large"
                    className="mt-1 w-full"
                    placeholder="Chọn ngày giờ khám"
                  />
                </label>

                <label className="block text-xs font-medium text-foreground">
                  Lý do khám
                  <input
                    value={appointmentForm.reason}
                    onChange={(event) =>
                      setAppointmentForm((current) => ({
                        ...current,
                        reason: event.target.value,
                      }))
                    }
                    placeholder="Ví dụ: Tái khám định kỳ"
                    maxLength={500}
                    className="mt-1 w-full rounded-xl border border-border bg-card px-3 py-2.5 text-sm outline-none focus:border-primary focus:ring-2 focus:ring-primary/20"
                  />
                </label>

                <label className="block text-xs font-medium text-foreground">
                  Trạng thái
                  <Select
                    value={appointmentForm.status}
                    onChange={(value) =>
                      setAppointmentForm((current) => ({
                        ...current,
                        status: value,
                      }))
                    }
                    options={[
                      { value: "scheduled", label: "Đã lên lịch" },
                      { value: "completed", label: "Đã hoàn thành" },
                      { value: "cancelled", label: "Đã hủy" },
                    ]}
                    size="large"
                    className="mt-1 w-full"
                  />
                </label>

                <label className="block text-xs font-medium text-foreground">
                  Ghi chú
                  <textarea
                    rows={3}
                    value={appointmentForm.notes}
                    onChange={(event) =>
                      setAppointmentForm((current) => ({
                        ...current,
                        notes: event.target.value,
                      }))
                    }
                    placeholder="Nội dung cần lưu ý..."
                    maxLength={2000}
                    className="mt-1 w-full resize-none rounded-xl border border-border bg-card px-3 py-2.5 text-sm outline-none focus:border-primary focus:ring-2 focus:ring-primary/20"
                  />
                </label>

                <button
                  type="submit"
                  disabled={appointmentSaving}
                  className="flex w-full items-center justify-center gap-2 rounded-xl bg-primary py-2.5 text-sm font-medium text-primary-foreground hover:bg-[#2D9D97] disabled:cursor-not-allowed disabled:opacity-50"
                >
                  {editingAppointmentId ? (
                    <Pencil className="h-4 w-4" />
                  ) : (
                    <Plus className="h-4 w-4" />
                  )}
                  {appointmentSaving
                    ? "Đang lưu..."
                    : editingAppointmentId
                      ? "Lưu thay đổi"
                      : "Thêm lịch"}
                </button>
              </form>

              <div>
                <h4 className="mb-3 text-sm font-semibold text-foreground">
                  Lịch hiện tại
                </h4>
                <div className="space-y-2">
                  {appointmentList.length === 0 && (
                    <p className="rounded-xl border border-dashed border-border p-5 text-center text-xs text-muted-foreground">
                      Chưa có lịch khám nào.
                    </p>
                  )}
                  {appointmentList.map((appointment) => (
                    <div
                      key={appointment.id}
                      className="flex items-start gap-2 rounded-xl border border-border p-3"
                    >
                      <div className="min-w-0 flex-1">
                        <p className="text-sm font-semibold text-foreground">
                          {new Date(appointment.scheduled_at).toLocaleString(
                            "vi-VN",
                            {
                              hour: "2-digit",
                              minute: "2-digit",
                              day: "2-digit",
                              month: "2-digit",
                              year: "numeric",
                            },
                          )}
                        </p>
                        <p className="truncate text-xs text-muted-foreground">
                          {appointment.reason || "Khám định kỳ"}
                        </p>
                      </div>
                      <button
                        type="button"
                        aria-label="Sửa lịch khám"
                        onClick={() => startEditingAppointment(appointment)}
                        className="rounded-lg p-2 text-primary hover:bg-accent"
                      >
                        <Pencil className="h-4 w-4" />
                      </button>
                      <button
                        type="button"
                        aria-label="Xóa lịch khám"
                        onClick={() => deleteAppointment(appointment)}
                        className="rounded-lg p-2 text-red-500 hover:bg-red-50"
                      >
                        <Trash2 className="h-4 w-4" />
                      </button>
                    </div>
                  ))}
                </div>
              </div>
            </div>
          </div>
        </div>
      )}

      {isMedicationManagerOpen && (
        <div
          role="dialog"
          aria-modal="true"
          aria-labelledby="medication-manager-title"
          className="fixed inset-0 z-50 flex items-center justify-center bg-black/45 p-4"
          onMouseDown={(event) => {
            if (event.target === event.currentTarget)
              setIsMedicationManagerOpen(false);
          }}
        >
          <div className="max-h-[90vh] w-full max-w-2xl overflow-y-auto rounded-2xl border border-border bg-card shadow-xl">
            <div className="sticky top-0 z-10 flex items-center justify-between border-b border-border bg-card px-5 py-4">
              <div>
                <h3
                  id="medication-manager-title"
                  className="font-semibold text-foreground"
                >
                  Quản lý thuốc
                </h3>
                <p className="text-xs text-muted-foreground">
                  Thêm, cập nhật hoặc xóa thuốc của bệnh nhân
                </p>
              </div>
              <button
                type="button"
                aria-label="Đóng"
                onClick={() => setIsMedicationManagerOpen(false)}
                className="rounded-lg p-2 text-muted-foreground hover:bg-muted hover:text-foreground"
              >
                <X className="h-5 w-5" />
              </button>
            </div>

            <div className="grid gap-5 p-5 md:grid-cols-[1fr_1.1fr]">
              <form
                onSubmit={saveMedication}
                className="space-y-3 rounded-xl bg-muted p-4"
              >
                <div className="flex items-center justify-between">
                  <h4 className="text-sm font-semibold text-foreground">
                    {editingMedicationId ? "Sửa thuốc" : "Thêm thuốc"}
                  </h4>
                  {editingMedicationId && (
                    <button
                      type="button"
                      onClick={startAddingMedication}
                      className="text-xs font-medium text-primary hover:underline"
                    >
                      Thêm mới
                    </button>
                  )}
                </div>

                <label className="block text-xs font-medium text-foreground">
                  Tên thuốc *
                  <input
                    required
                    value={medicationForm.name}
                    onChange={(event) =>
                      setMedicationForm((current) => ({
                        ...current,
                        name: event.target.value,
                      }))
                    }
                    placeholder="Ví dụ: Metformin"
                    className="mt-1 w-full rounded-xl border border-border bg-card px-3 py-2.5 text-sm outline-none focus:border-primary focus:ring-2 focus:ring-primary/20"
                  />
                </label>

                <div className="grid grid-cols-2 gap-3">
                  <label className="block text-xs font-medium text-foreground">
                    Liều dùng *
                    <input
                      required
                      value={medicationForm.dose}
                      onChange={(event) =>
                        setMedicationForm((current) => ({
                          ...current,
                          dose: event.target.value,
                        }))
                      }
                      placeholder="500 mg"
                      className="mt-1 w-full rounded-xl border border-border bg-card px-3 py-2.5 text-sm outline-none focus:border-primary focus:ring-2 focus:ring-primary/20"
                    />
                  </label>
                  <label className="block text-xs font-medium text-foreground">
                    Tần suất *
                    <input
                      required
                      value={medicationForm.frequency}
                      onChange={(event) =>
                        setMedicationForm((current) => ({
                          ...current,
                          frequency: event.target.value,
                        }))
                      }
                      placeholder="2 lần/ngày"
                      className="mt-1 w-full rounded-xl border border-border bg-card px-3 py-2.5 text-sm outline-none focus:border-primary focus:ring-2 focus:ring-primary/20"
                    />
                  </label>
                </div>

                <div className="grid grid-cols-2 gap-3">
                  <label className="block text-xs font-medium text-foreground">
                    Ngày bắt đầu *
                    <DatePicker
                      value={
                        medicationForm.startDate &&
                        dayjs(medicationForm.startDate).isValid()
                          ? dayjs(medicationForm.startDate)
                          : null
                      }
                      onChange={(value) =>
                        setMedicationForm((current) => ({
                          ...current,
                          startDate: value?.format("YYYY-MM-DD") ?? "",
                        }))
                      }
                      format="DD/MM/YYYY"
                      size="large"
                      className="mt-1 w-full"
                      placeholder="Chọn ngày bắt đầu"
                    />
                  </label>
                  <label className="block text-xs font-medium text-foreground">
                    Trạng thái
                    <Select
                      value={medicationForm.status}
                      onChange={(value) =>
                        setMedicationForm((current) => ({
                          ...current,
                          status: value,
                        }))
                      }
                      options={[
                        { value: "active", label: "Đang dùng" },
                        { value: "inactive", label: "Dừng" },
                      ]}
                      size="large"
                      className="mt-1 w-full"
                    />
                  </label>
                </div>

                <label className="block text-xs font-medium text-foreground">
                  Hướng dẫn
                  <textarea
                    rows={3}
                    value={medicationForm.instruction}
                    onChange={(event) =>
                      setMedicationForm((current) => ({
                        ...current,
                        instruction: event.target.value,
                      }))
                    }
                    placeholder="Cách dùng và lưu ý..."
                    className="mt-1 w-full resize-none rounded-xl border border-border bg-card px-3 py-2.5 text-sm outline-none focus:border-primary focus:ring-2 focus:ring-primary/20"
                  />
                </label>

                <button
                  type="submit"
                  disabled={medicationSaving}
                  className="flex w-full items-center justify-center gap-2 rounded-xl bg-primary py-2.5 text-sm font-medium text-primary-foreground hover:bg-[#2D9D97]"
                >
                  {editingMedicationId ? (
                    <Pencil className="h-4 w-4" />
                  ) : (
                    <Plus className="h-4 w-4" />
                  )}
                  {medicationSaving
                    ? "Đang lưu..."
                    : editingMedicationId
                      ? "Lưu thay đổi"
                      : "Thêm thuốc"}
                </button>
              </form>

              <div>
                <h4 className="mb-3 text-sm font-semibold text-foreground">
                  Thuốc hiện tại
                </h4>
                <div className="space-y-2">
                  {medicationList.length === 0 && (
                    <p className="rounded-xl border border-dashed border-border p-5 text-center text-xs text-muted-foreground">
                      Chưa có thuốc nào.
                    </p>
                  )}
                  {medicationList.map((medication) => (
                    <div
                      key={medication.id}
                      className="flex items-start gap-3 rounded-xl border border-border p-3"
                    >
                      <div className="min-w-0 flex-1">
                        <p className="truncate text-sm font-semibold text-foreground">
                          {medication.name}
                        </p>
                        <p className="text-xs text-muted-foreground">
                          {medication.dose}
                          {medication.frequency
                            ? ` · ${medication.frequency}`
                            : ""}
                        </p>
                      </div>
                      <button
                        type="button"
                        aria-label={`Sửa ${medication.name}`}
                        onClick={() => startEditingMedication(medication)}
                        className="rounded-lg p-2 text-primary hover:bg-accent"
                      >
                        <Pencil className="h-4 w-4" />
                      </button>
                      <button
                        type="button"
                        aria-label={`Ngừng ${medication.name}`}
                        onClick={() => deleteMedication(medication)}
                        className="rounded-lg p-2 text-red-500 hover:bg-red-50"
                      >
                        <Trash2 className="h-4 w-4" />
                      </button>
                    </div>
                  ))}
                </div>
              </div>
            </div>
          </div>
        </div>
      )}

      {/* Tab: AI */}
      {activeTab === "ai" && (
        <div className="flex min-h-[540px] overflow-hidden rounded-2xl border border-border bg-card">
          <AgentChatSidebar
            sessions={chatSessions}
            activeSessionId={activeChatSessionId}
            onSelect={(id) => void selectChatSession(id)}
            onNewChat={startNewChat}
            disabled={chatLoading || chatRestoring}
            className="border-r border-border p-3"
          />
          <div className="flex flex-1 flex-col overflow-hidden">
            <div className="flex items-center gap-3 border-b border-border px-5 py-4">
              <div className="flex h-10 w-10 items-center justify-center rounded-xl bg-primary/10">
                <Bot className="h-5 w-5 text-primary" />
              </div>
              <div className="min-w-0 flex-1">
                <h3 className="font-semibold text-foreground">
                  Trợ lý AI cho bác sĩ
                </h3>
                <p className="truncate text-xs text-muted-foreground">
                  Đang hỗ trợ hồ sơ của {patient.name}
                </p>
              </div>
              <TtsAutoReadToggle />
              <span className="flex items-center gap-1.5 text-xs text-green-600">
                <span className="h-2 w-2 rounded-full bg-green-500" />
                Sẵn sàng
              </span>
            </div>

            <div className="m-4 flex items-start gap-2 rounded-xl border border-yellow-200 bg-yellow-50 p-3">
              <Info className="mt-0.5 h-4 w-4 flex-shrink-0 text-yellow-600" />
              <p className="text-xs leading-relaxed text-yellow-700">
                AI chỉ cung cấp thông tin hỗ trợ lâm sàng, không thay thế đánh giá
                và quyết định chuyên môn của bác sĩ.
              </p>
            </div>

            <div className="flex-1 space-y-4 overflow-y-auto px-4 pb-4">
              {chatMessages.map((message) => (
                <div
                  key={message.id}
                  className={cn(
                    "flex items-end gap-2",
                    message.role === "doctor" ? "justify-end" : "justify-start",
                  )}
                >
                  {message.role === "assistant" && (
                    <div className="flex h-8 w-8 flex-shrink-0 items-center justify-center rounded-full bg-primary/10">
                      <Bot className="h-4 w-4 text-primary" />
                    </div>
                  )}
                  <div
                    className={cn(
                      "max-w-[82%] rounded-2xl px-4 py-3 text-sm leading-relaxed",
                      message.role === "doctor"
                        ? "whitespace-pre-wrap rounded-br-sm bg-primary text-primary-foreground"
                        : "rounded-bl-sm border border-border bg-muted text-foreground",
                    )}
                  >
                    {message.role === "assistant" ? (
                      <ChatAnswerCards content={message.text} />
                    ) : (
                      <p className="whitespace-pre-wrap">{message.text}</p>
                    )}
                    {message.handoff && (
                      <p className="mt-2 rounded-lg bg-orange-50 px-2 py-1.5 text-xs text-orange-700">
                        Nội dung này cần trao đổi trực tiếp với bác sĩ.
                      </p>
                    )}
                    {message.limitations && message.limitations.length > 0 && (
                      <p className="mt-2 text-xs opacity-80">
                        Giới hạn: {message.limitations.join(" · ")}
                      </p>
                    )}
                    {message.sources && message.sources.length > 0 && (
                      <div className="mt-2 text-xs opacity-80">
                        Nguồn:{" "}
                        {message.sources
                          .map((source) => source.title)
                          .join(" · ")}
                      </div>
                    )}
                    {message.disclaimer && (
                      <p className="mt-2 text-[11px] opacity-70">
                        {message.disclaimer}
                      </p>
                    )}
                    {message.role === "assistant" && (
                      <div className="mt-1 flex justify-end">
                        <TtsPlayButton
                          state={tts.playingId === message.id ? tts.state : "idle"}
                          onClick={() =>
                            tts.playingId === message.id && tts.state !== "idle"
                              ? tts.stop()
                              : tts.play(message.id, message.text)
                          }
                        />
                      </div>
                    )}
                  </div>
                  {message.role === "doctor" && (
                    <div className="flex h-8 w-8 flex-shrink-0 items-center justify-center rounded-full bg-accent">
                      <User className="h-4 w-4 text-primary" />
                    </div>
                  )}
                </div>
              ))}

              {chatLoading && (
                <div className="flex items-end gap-2">
                  <div className="flex h-8 w-8 items-center justify-center rounded-full bg-primary/10">
                    <Bot className="h-4 w-4 text-primary" />
                  </div>
                  <div className="flex gap-1 rounded-2xl rounded-bl-sm border border-border bg-muted px-4 py-3">
                    {[0, 1, 2].map((index) => (
                      <span
                        key={index}
                        className="h-1.5 w-1.5 animate-bounce rounded-full bg-muted-foreground"
                        style={{ animationDelay: `${index * 150}ms` }}
                      />
                    ))}
                  </div>
                </div>
              )}

              {chatError && (
                <div
                  role="alert"
                  className="rounded-xl border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700"
                >
                  {chatError}
                </div>
              )}
            </div>

            <div className="px-4 pb-3">
              <p className="mb-2 flex items-center gap-1.5 text-xs font-medium text-muted-foreground">
                <Sparkles className="h-3.5 w-3.5" />
                Câu hỏi gợi ý
              </p>
              <div className="flex flex-wrap gap-2">
                {[
                  "Tóm tắt tình trạng bệnh nhân",
                  "Phân tích xu hướng đường huyết",
                  "Cần lưu ý gì khi tái khám?",
                ].map((suggestion) => (
                  <button
                    key={suggestion}
                    type="button"
                    onClick={() => void submitQuestion(suggestion)}
                    disabled={chatLoading}
                    className="rounded-full border border-border bg-muted px-3 py-1.5 text-xs text-foreground transition-colors hover:border-primary/40 hover:bg-accent disabled:cursor-not-allowed disabled:opacity-60"
                  >
                    {suggestion}
                  </button>
                ))}
              </div>
            </div>

            <form
              onSubmit={(event) => {
                event.preventDefault();
                void submitQuestion();
              }}
              className="flex items-end gap-2 border-t border-border p-4"
            >
              <textarea
                value={question}
                onChange={(event) => setQuestion(event.target.value)}
                onKeyDown={(event) => {
                  if (
                    event.key === "Enter" &&
                    !event.shiftKey &&
                    !event.nativeEvent.isComposing
                  ) {
                    event.preventDefault();
                    void submitQuestion();
                  }
                }}
                rows={1}
                placeholder="Hỏi AI về dữ liệu của bệnh nhân..."
                disabled={chatLoading}
                className="max-h-28 min-h-10 flex-1 resize-none rounded-xl border border-border bg-muted px-4 py-2.5 text-sm outline-none placeholder:text-muted-foreground focus:border-primary focus:ring-2 focus:ring-primary/20 disabled:opacity-60"
              />
              <button
                type="submit"
                disabled={!question.trim() || chatLoading}
                aria-label="Gửi câu hỏi"
                className="flex h-10 w-10 flex-shrink-0 items-center justify-center rounded-xl bg-primary text-primary-foreground transition-colors hover:bg-[#2D9D97] disabled:cursor-not-allowed disabled:opacity-40"
              >
                <Send className="h-4 w-4" />
              </button>
            </form>
          </div>
        </div>
      )}

      {/* Tab: Notes */}
      {activeTab === "meals" && <PatientMealsPanel patientId={patientId} />}

      {activeTab === "insights" && <PatientInsightsPanel patientId={patientId} />}

      {activeTab === "notes" && <ClinicalNotesPanel patientId={patientId} />}
    </div>
  );
}
