export type UserRole = "patient" | "doctor" | "admin";

export interface ApiUser {
  id: string;
  email: string;
  full_name: string;
  role: UserRole;
  is_active: boolean;
  must_change_password: boolean;
  ai_memory_consent: boolean;
  meal_photo_ai_consent: boolean;
  weekly_report_enabled: boolean;
  created_at: string;
}

export interface AuditEvent {
  id: string;
  ts: string;
  actor_id: string | null;
  actor_role: string;
  actor_name: string | null;
  action: string;
  resource_type: string | null;
  resource_id: string | null;
  patient_id: string | null;
  patient_name: string | null;
  request_id: string | null;
  ip: string | null;
  outcome: "success" | "failure" | "denied" | string;
  detail: string | null;
}

export interface AuditEventPage {
  items: AuditEvent[];
  total: number;
}

export interface AuditEventFilters {
  action?: string;
  actor_id?: string;
  patient_id?: string;
  outcome?: string;
  since?: string;
  until?: string;
  limit?: number;
  offset?: number;
}

export type DeletionRequestStatus = "pending" | "approved" | "rejected" | "cancelled";

export interface DeletionRequest {
  id: string;
  patient_id: string;
  status: DeletionRequestStatus;
  requested_at: string;
  decided_at: string | null;
  decision_note: string | null;
  deleted_rows: number | null;
}

export interface AdminDeletionRequest extends DeletionRequest {
  patient_name: string | null;
  patient_email: string | null;
}

export interface GlucoseRecord {
  id: string;
  patient_id: string;
  value: number;
  unit: string;
  measured_at: string;
  note: string | null;
  meal_context: string | null;
  created_by: string;
  created_at: string;
  // Personalized-threshold evaluation output (backend health/threshold_service.py).
  // Null when the record predates this feature or the patient has no
  // doctor-approved CarePlanTarget yet — a real "not evaluable" state, not "normal".
  predicted_min_mg_dl: number | null;
  predicted_max_mg_dl: number | null;
  threshold_status: "DANGER" | "WATCH" | "NORMAL" | "UNAVAILABLE" | null;
}

// Mirrors codebase/backend/health/models.py's MealContext enum. EVENT_RECOVERY
// has no UI button (simulator-only — a post-hypo/hyper recheck reading) but
// still needs a label for display.
export const GLUCOSE_TIMING_OPTIONS: { label: string; value: string }[] = [
  { label: "Lúc đói", value: "FASTING" },
  { label: "Trước ăn", value: "BEFORE_MEAL" },
  { label: "Sau ăn", value: "AFTER_MEAL" },
  { label: "Trước khi ngủ", value: "BEFORE_SLEEP" },
  { label: "Khác", value: "RANDOM" },
];

const GLUCOSE_TIMING_LABEL_BY_VALUE: Record<string, string> = {
  ...Object.fromEntries(GLUCOSE_TIMING_OPTIONS.map((o) => [o.value, o.label])),
  EVENT_RECOVERY: "Theo dõi lại",
};

export function glucoseTimingLabel(
  mealContext: string | null | undefined,
): string {
  return (mealContext && GLUCOSE_TIMING_LABEL_BY_VALUE[mealContext]) || "Khác";
}

export function glucoseTimingValue(label: string): string {
  return (
    GLUCOSE_TIMING_OPTIONS.find((o) => o.label === label)?.value ?? "RANDOM"
  );
}

// scripts/migrate_nhanes_demo_data.py tags every simulator-seeded record's
// note with "sim:<measurement_id>" purely for tracing a demo reading back to
// its source row — an internal provenance tag, never a real patient/doctor
// note, so it shouldn't render as if it were one.
export function glucoseDisplayNote(note: string | null | undefined): string {
  return note && !note.startsWith("sim:") ? note : "";
}

export interface BloodPressureRecord {
  id: string;
  patient_id: string;
  systolic: number;
  diastolic: number;
  unit: string;
  measured_at: string;
  note: string | null;
  created_by: string;
  created_at: string;
}

export interface ActivityRecord {
  id: string;
  patient_id: string;
  activity_type: string;
  duration_minutes: number;
  activity_time: string;
  note: string | null;
  created_by: string;
  created_at: string;
}

export type MealType = "breakfast" | "lunch" | "dinner" | "snack";

export interface MealRecord {
  id: string;
  patient_id: string;
  meal_type: MealType;
  meal_time: string;
  description: string;
  note: string | null;
  // Older meals keep their portion inside `note` ("Khẩu phần: ... · ...").
  portion: string | null;
  input_source: MealInputSource | null;
  created_by: string;
  created_at: string;
  // Filled in by a background job after saving; null until it has run.
  analysis: MealAnalysis | null;
}

export type CarbLoad = "low" | "medium" | "high" | "unknown";
export type MealAnalysisStatus = "draft" | "confirmed" | "edited";

export interface MealAnalysisItem {
  name: string;
  matched_food_id: string | null;
  matched_name: string | null;
  quantity: number | null;
  unit: string | null;
  grams: number | null;
  carbs_g: number | null;
  kcal: number | null;
  gi_level: "low" | "medium" | "high" | null;
  match_confidence: number | null;
  portion_estimated: boolean;
}

// Estimate from the nutrition table. With has_unmatched the totals cover
// only the matched items (a lower bound).
export interface MealAnalysis {
  id: string;
  meal_id: string;
  items: MealAnalysisItem[];
  total_carbs_g: number | null;
  total_kcal: number | null;
  carb_load: CarbLoad;
  has_unmatched: boolean;
  source: MealInputSource;
  status: MealAnalysisStatus;
  model_version: string | null;
  nutrition_db_version: string | null;
  updated_at: string;
}

export interface MealGroupResponse {
  n: number;
  avg_post_mg_dl: number;
  avg_delta_mg_dl: number | null;
  n_with_delta: number;
  pct_over_target: number;
}

// Association between meals and post-meal glucose, not cause. Groups with
// fewer than min_n paired meals are left out by the server.
export interface MealGlucoseResponse {
  start: string;
  end: string;
  days: number;
  meals_paired: number;
  min_n: number;
  post_prandial_target_mg_dl: number;
  by_food: (MealGroupResponse & { food_id: string; name: string })[];
  by_carb_load: (MealGroupResponse & { carb_load: CarbLoad })[];
}

export type MealItemEdit = { name: string; quantity: number | null; unit: string | null };

export type AdherenceDayStatus = "taken" | "missed" | "unconfirmed" | "no_data";

// GET /medications/adherence (backend medications/adherence.py). day_rate_pct
// is the headline: share of days with medication taken among days with an
// answer; "unconfirmed" = reminders left unanswered, not counted as forgotten.
export interface MedicationAdherence {
  start: string;
  end: string;
  days: number;
  day_rate_pct: number | null;
  days_taken: number;
  days_missed: number;
  days_unconfirmed: number;
  days_no_data: number;
  dose_rate_pct: number | null;
  doses_considered: number;
  medications: { medication_id: string; drug_name: string; as_needed: boolean; missed_notes: number }[];
  missed_dates: string[];
  daily: { date: string; status: AdherenceDayStatus; completed: number; skipped: number; expired: number; missed_notes: number }[];
  glucose_by_adherence: { taken_days: number; taken_avg_mg_dl: number; missed_days: number; missed_avg_mg_dl: number } | null;
}

export interface ReminderSuggestion {
  id: string;
  preference_id: string;
  label: string;
  previous_time: string; // HH:MM:SS
  suggested_time: string;
  late_count: number;
  sample_count: number;
  status: "pending" | "accepted" | "declined";
}

type StatSummary = { count: number; avg: number | null; min: number | null; max: number | null };

// Deterministic figures computed by the backend (reports/stats.py).
export interface WeeklyReportStats {
  glucose: StatSummary & {
    low_count: number;
    evaluated_count: number;
    in_target_pct: number | null;
    by_context: Record<string, StatSummary>;
  };
  previous_week: { count: number; avg: number | null; in_target_pct: number | null };
  change: { avg_delta: number | null; in_target_pct_delta: number | null };
  blood_pressure: { count: number; systolic_avg: number | null; diastolic_avg: number | null };
  meal_count: number;
  activity_count: number;
  activity_minutes: number;
  days_logged: number;
  meals: {
    lookback_days: number;
    meals_paired: number;
    min_n: number;
    top_foods: { name: string; n: number; avg_post_mg_dl: number; pct_over_target: number }[];
  };
  // Absent on reports generated before Epic 3.
  medications?: Pick<
    MedicationAdherence,
    "day_rate_pct" | "days_taken" | "days_missed" | "days_unconfirmed" | "days_no_data" | "dose_rate_pct" | "medications" | "daily"
  >;
}

export interface WeeklyReportListItem {
  id: string;
  week_start: string; // YYYY-MM-DD, local Sunday
  week_end: string; // inclusive Saturday
  status: "generated" | "delivered" | "failed";
  tip: string | null;
  read_at: string | null;
  created_at: string;
}

export interface WeeklyReport extends WeeklyReportListItem {
  summary: string;
  stats: WeeklyReportStats | Record<string, never>;
  sections_enabled: string[];
  delivered_at: string | null;
}

export type MealInputSource = "text" | "photo" | "voice";

// POST /health/meals/photo-analysis — a draft for the meal form. Nothing is
// saved by the call, the photo included.
export interface MealPhotoAnalysis {
  recognized: boolean;
  description: string | null;
  portion: string | null;
  items: { name: string; quantity: number | null; unit: string | null }[];
}

export type NotificationType =
  | "glucose_reminder"
  | "glucose_alert"
  | "blood_pressure_reminder"
  | "medication_reminder"
  | "custom"
  | "weekly_report";
export type NotificationStatus =
  | "pending"
  | "completed"
  | "skipped"
  | "snoozed";

export interface NotificationRecord {
  id: string;
  patient_id: string;
  notification_type: NotificationType;
  title: string;
  message: string;
  slot_date: string;
  scheduled_for: string;
  status: NotificationStatus;
  snoozed_until: string | null;
  responded_at: string | null;
  last_notified_at: string;
  nudge_count: number;
  created_at: string;
}

export interface NotificationPreference {
  id: string;
  patient_id: string;
  reminder_kind: string;
  notification_type: NotificationType;
  meal_type: MealType | null;
  meal_context: string | null;
  label: string;
  time_of_day: string;
  is_enabled: boolean;
  is_custom: boolean;
  updated_at: string;
}

export interface ZaloLinkStatus {
  linked: boolean;
}

export interface ZaloLinkCode {
  code: string;
  expires_at: string;
  chat_url: string | null;
}

export interface DoctorAlert {
  id: string;
  patient_id: string;
  patient_name: string;
  severity: "critical" | "warning" | "info";
  alert_type: string;
  title: string;
  message: string;
  occurred_at: string;
  action_path: string;
}

export interface Appointment {
  id: string;
  patient_id: string;
  doctor_id: string;
  scheduled_at: string;
  reason: string | null;
  status: "scheduled" | "completed" | "cancelled";
  notes: string | null;
  created_at: string;
  updated_at: string;
}

export interface AppointmentInput {
  scheduled_at: string;
  reason?: string | null;
  status?: Appointment["status"];
  notes?: string | null;
}

// Epic 4 — codebase/backend/appointments/schemas.py. "stale": made before the
// appointment was rescheduled; "failed": last attempt failed (retried hourly).
export type BriefState = "ready" | "stale" | "failed" | "pending";

// "changes" / "clinical" / "meals" / "medication_adherence" — each never empty for a
// doctor summary (a "Chưa đủ dữ liệu" line instead).
export type SummarySections = Partial<Record<"changes" | "clinical" | "meals" | "medication_adherence", string[]>>;

export interface PrevisitBrief {
  id: string;
  appointment_id: string;
  patient_id: string;
  state: BriefState;
  appointment_scheduled_at: string;
  previous_visit_at: string | null;
  period_start: string;
  period_end: string;
  period_days: number;
  summary: string;
  trends: string[];
  attention_points: string[];
  limitations: string[];
  sections: SummarySections;
  data_completeness: Record<string, unknown>;
  generated_at: string;
  seen_at: string | null;
}

export interface UpcomingAppointment {
  id: string;
  patient_id: string;
  patient_name: string;
  scheduled_at: string;
  reason: string | null;
  brief_state: BriefState;
  brief_generated_at: string | null;
  brief_unseen: boolean;
  attention_count: number | null;
}

export interface ClinicalNote {
  id: string;
  patient_id: string;
  doctor_id: string;
  appointment_id: string | null;
  reason_for_visit: string;
  history_of_present_illness: string;
  past_medical_history: string | null;
  weight_kg: number | null;
  blood_pressure_systolic: number | null;
  blood_pressure_diastolic: number | null;
  clinical_exam_findings: string | null;
  data_period_start: string | null;
  data_period_end: string | null;
  hba1c_pct: number | null;
  paraclinical_results: string | null;
  diagnosis: string;
  treatment_plan: string;
  follow_up_date: string | null;
  created_at: string;
  updated_at: string;
}

// Mirrors codebase/backend/insights/schemas.py's PatientInsightPublic —
// doctor-facing view of a PatientInsight row, both source_kind streams
// (appointment / weekly_batch) the backend allowlists for this endpoint.
// source_kind="chat" never reaches here (patient-consent-gated, backend
// filters it out server-side).
export interface PatientInsight {
  id: string;
  category: string;
  text: string;
  confidence: "low" | "medium" | "high";
  version: number;
  source_kind: "appointment" | "weekly_batch";
  reviewed: boolean;
  first_observed_at: string;
  last_confirmed_at: string;
  // Latest doctor verdict (Epic 4.4); always null in a patient's own view.
  verdict: InsightVerdict | null;
  verdict_comment: string | null;
  verdict_version: number | null;
  verdict_at: string | null;
}

export type InsightVerdict = "confirmed" | "rejected";

export interface MedicationRecord {
  id: string;
  patient_id: string;
  drug_name: string;
  dose: string;
  frequency: string;
  start_date: string;
  end_date: string | null;
  status: "active" | "discontinued";
  note: string | null;
  discontinued_reason: string | null;
  replaces_medication_id: string | null;
  created_by: string;
  created_at: string;
  updated_at: string;
}

export interface MedicationNote {
  id: string;
  medication_id: string;
  patient_id: string;
  note_type: "missed_dose" | "adverse_reaction" | "other";
  text: string | null;
  occurred_at: string;
  created_by: string;
  created_at: string;
}

export type MedicationInput = {
  drug_name: string;
  dose: string;
  frequency: string;
  start_date: string;
  note?: string | null;
};

export type ClinicalNoteInput = {
  appointment_id?: string | null;
  reason_for_visit: string;
  history_of_present_illness: string;
  past_medical_history?: string | null;
  weight_kg?: number | null;
  blood_pressure_systolic?: number | null;
  blood_pressure_diastolic?: number | null;
  clinical_exam_findings?: string | null;
  data_period_start?: string | null;
  data_period_end?: string | null;
  hba1c_pct?: number | null;
  paraclinical_results?: string | null;
  diagnosis: string;
  treatment_plan: string;
  follow_up_date?: string | null;
};
export interface AgentSourceCitation {
  title: string;
  section: string | null;
  page_ref: string | null;
  source_url: string | null;
  tier: string | null;
  organization?: string | null;
}

export interface AgentChoiceOption {
  label: string;
  value: string;
}

export interface AgentInteraction {
  type: "single_choice" | "confirm_record" | "record_saved";
  pending_action_id: string;
  field: string | null;
  options: AgentChoiceOption[];
  draft: Record<string, unknown>;
  saved_record_id: string | null;
}

export interface AgentChatResponse {
  response: string;
  // Patient chat: the AI's evaluation of the data, shown in a labelled box.
  ai_assessment?: string | null;
  data_used: string[];
  sources: AgentSourceCitation[];
  limitations: string[];
  handoff: boolean;
  disclaimer: string;
  session_id: string;
  interaction: AgentInteraction | null;
  interactions: AgentInteraction[];
}

export interface AgentRecordActionResponse {
  response: string;
  interaction: AgentInteraction | null;
}

export interface ChatSession {
  id: string;
  patient_id: string;
  title: string | null;
  created_at: string;
  updated_at: string;
}

export interface ChatMessage {
  role: "user" | "assistant";
  content: string;
  ai_assessment?: string | null;
}

export interface ChatSessionMessagesResponse {
  session_id: string;
  messages: ChatMessage[];
}

export interface AgentSummaryResponse {
  summary: string;
  date_range: [string, string] | null;
  data_used: string[];
  trends: string[];
  attention_points: string[];
  data_completeness: Record<string, unknown>;
  limitations: string[];
  suggested_doctor_questions: string[];
  relevant_insights: string[];
  sections: SummarySections;
  disclaimer: string;
}

export interface KeyMetric {
  label: string;
  value: string;
  unit: string | null;
  tone: "good" | "warning" | "critical";
}

export interface GlucoseTrend {
  direction: "up" | "down" | "stable";
  delta: number;
  first_half_avg: number;
  second_half_avg: number;
}

export interface KeyMetricsResponse {
  key_metrics: KeyMetric[];
  glucose_trend: GlucoseTrend | null;
}

export interface ChatSuggestionsResponse {
  suggestions: string[];
}

export interface PatientAssignment {
  id: string;
  doctor_id: string;
  patient_id: string;
  assigned_by: string;
  created_at: string;
}

const API_BASE = (
  process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000/api/v1"
).replace(/\/$/, "");
const TOKEN_KEY = "vdiab_access_token";

export function getToken() {
  if (typeof window === "undefined") return null;
  return localStorage.getItem(TOKEN_KEY) || sessionStorage.getItem(TOKEN_KEY);
}

export function storeToken(token: string, remember = false) {
  const selected = remember ? localStorage : sessionStorage;
  const other = remember ? sessionStorage : localStorage;
  other.removeItem(TOKEN_KEY);
  selected.setItem(TOKEN_KEY, token);
}

export function clearToken() {
  if (typeof window === "undefined") return;
  localStorage.removeItem(TOKEN_KEY);
  sessionStorage.removeItem(TOKEN_KEY);
}

export function replaceToken(token: string) {
  if (typeof window === "undefined") return;
  storeToken(token, localStorage.getItem(TOKEN_KEY) !== null);
}

function errorMessage(payload: unknown, fallback: string) {
  if (payload && typeof payload === "object" && "detail" in payload) {
    const detail = (payload as { detail: unknown }).detail;
    if (typeof detail === "string") return detail;
  }
  return fallback;
}

async function throwForResponse(response: Response): Promise<never> {
  let payload: unknown;
  try {
    payload = await response.json();
  } catch {
    payload = null;
  }
  if (response.status === 401)
    window.dispatchEvent(new Event("vdiab:unauthorized"));
  throw new Error(
    errorMessage(payload, `Yêu cầu thất bại (${response.status})`),
  );
}

export async function apiRequest<T>(
  path: string,
  init: RequestInit = {},
): Promise<T> {
  const headers = new Headers(init.headers);
  const token = getToken();
  if (token) headers.set("Authorization", `Bearer ${token}`);
  if (
    init.body &&
    !(init.body instanceof FormData) &&
    !headers.has("Content-Type")
  ) {
    headers.set("Content-Type", "application/json");
  }
  const response = await fetch(`${API_BASE}${path}`, { ...init, headers });
  if (!response.ok) await throwForResponse(response);
  if (response.status === 204) return undefined as T;
  return response.json() as Promise<T>;
}

// A file the server sends as an attachment (Content-Disposition), e.g. the
// patient's data export.
export async function apiDownload(
  path: string,
): Promise<{ blob: Blob; filename: string | null }> {
  const headers = new Headers();
  const token = getToken();
  if (token) headers.set("Authorization", `Bearer ${token}`);
  const response = await fetch(`${API_BASE}${path}`, { headers });
  if (!response.ok) await throwForResponse(response);
  const disposition = response.headers.get("Content-Disposition") ?? "";
  const match = /filename="([^"]+)"/.exec(disposition);
  return { blob: await response.blob(), filename: match ? match[1] : null };
}

// POST /agent/chat/stream (SSE): `progress` events while the agent works, then
// one `final` event with the same AgentChatResponse /agent/chat returns, or an
// `error` event. The answer only arrives after the server's safety check ran.
async function agentChatStream(
  body: { message: string; patient_id: string | null; session_id: string | null },
  onProgress?: (message: string) => void,
): Promise<AgentChatResponse> {
  const headers = new Headers({
    "Content-Type": "application/json",
    Accept: "text/event-stream",
  });
  const token = getToken();
  if (token) headers.set("Authorization", `Bearer ${token}`);
  const response = await fetch(`${API_BASE}/agent/chat/stream`, {
    method: "POST",
    headers,
    body: JSON.stringify(body),
  });
  if (!response.ok) await throwForResponse(response);
  if (!response.body) throw new Error("Trình duyệt không hỗ trợ nhận dữ liệu dạng luồng.");

  const reader = response.body.pipeThrough(new TextDecoderStream()).getReader();
  let buffer = "";
  for (;;) {
    const { value, done } = await reader.read();
    if (value) buffer += value.replace(/\r\n/g, "\n");
    let separator = buffer.indexOf("\n\n");
    while (separator !== -1) {
      const block = buffer.slice(0, separator);
      buffer = buffer.slice(separator + 2);
      separator = buffer.indexOf("\n\n");
      let event = "message";
      let data = "";
      for (const line of block.split("\n")) {
        if (line.startsWith("event:")) event = line.slice(6).trim();
        else if (line.startsWith("data:")) data += line.slice(5).trim();
      }
      if (!data) continue;
      const payload: unknown = JSON.parse(data);
      if (event === "progress") {
        onProgress?.((payload as { message: string }).message);
      } else if (event === "final") {
        return payload as AgentChatResponse;
      } else if (event === "error") {
        if ((payload as { status?: number }).status === 401)
          window.dispatchEvent(new Event("vdiab:unauthorized"));
        throw new Error(errorMessage(payload, "Không thể kết nối trợ lý AI."));
      }
    }
    if (done) break;
  }
  throw new Error("Kết nối tới trợ lý AI bị gián đoạn. Vui lòng thử lại.");
}

export async function loginRequest(email: string, password: string) {
  const form = new URLSearchParams({ username: email, password });
  const response = await fetch(`${API_BASE}/auth/login`, {
    method: "POST",
    headers: { "Content-Type": "application/x-www-form-urlencoded" },
    body: form,
  });
  if (!response.ok) {
    let payload: unknown;
    try {
      payload = await response.json();
    } catch {
      payload = null;
    }
    throw new Error(errorMessage(payload, "Email hoặc mật khẩu không đúng"));
  }
  return response.json() as Promise<{
    access_token: string;
    token_type: string;
  }>;
}

export const api = {
  me: () => apiRequest<ApiUser>("/auth/me"),
  changePassword: (currentPassword: string, newPassword: string) =>
    apiRequest<{ access_token: string; token_type: string }>(
      "/auth/change-password",
      {
        method: "POST",
        body: JSON.stringify({
          current_password: currentPassword,
          new_password: newPassword,
        }),
      },
    ),
  users: () => apiRequest<ApiUser[]>("/admin/users"),
  createUser: (data: {
    email: string;
    full_name: string;
    role: UserRole;
    temporary_password: string;
  }) =>
    apiRequest<ApiUser>("/admin/users", {
      method: "POST",
      body: JSON.stringify(data),
    }),
  resetPassword: (userId: string, temporaryPassword?: string) =>
    apiRequest<{ temporary_password: string }>(
      `/admin/users/${encodeURIComponent(userId)}/reset-password`,
      {
        method: "POST",
        body: JSON.stringify({ temporary_password: temporaryPassword || null }),
      },
    ),
  assignments: (doctorId?: string) =>
    apiRequest<PatientAssignment[]>(
      `/admin/patient-assignments${doctorId ? `?doctor_id=${encodeURIComponent(doctorId)}` : ""}`,
    ),
  assignPatient: (doctorId: string, patientId: string) =>
    apiRequest<PatientAssignment>("/admin/patient-assignments", {
      method: "POST",
      body: JSON.stringify({ doctor_id: doctorId, patient_id: patientId }),
    }),
  unassignPatient: (assignmentId: string) =>
    apiRequest<void>(
      `/admin/patient-assignments/${encodeURIComponent(assignmentId)}`,
      { method: "DELETE" },
    ),
  patients: () => apiRequest<ApiUser[]>("/doctor/patients"),
  glucose: (patientId?: string) =>
    apiRequest<GlucoseRecord[]>(
      `/health/glucose${patientId ? `?patient_id=${encodeURIComponent(patientId)}` : ""}`,
    ),
  createGlucose: (data: {
    patient_id: string;
    value: number;
    unit: string;
    measured_at: string;
    note?: string | null;
    meal_context?: string | null;
  }) =>
    apiRequest<GlucoseRecord>("/health/glucose", {
      method: "POST",
      body: JSON.stringify(data),
    }),
  bloodPressure: (patientId?: string) =>
    apiRequest<BloodPressureRecord[]>(
      `/health/blood-pressure${patientId ? `?patient_id=${encodeURIComponent(patientId)}` : ""}`,
    ),
  createBloodPressure: (data: {
    patient_id: string;
    systolic: number;
    diastolic: number;
    unit: string;
    measured_at: string;
    note?: string | null;
  }) =>
    apiRequest<BloodPressureRecord>("/health/blood-pressure", {
      method: "POST",
      body: JSON.stringify(data),
    }),
  activities: (patientId?: string) =>
    apiRequest<ActivityRecord[]>(
      `/health/activities${patientId ? `?patient_id=${encodeURIComponent(patientId)}` : ""}`,
    ),
  createActivity: (data: {
    patient_id: string;
    activity_type: string;
    duration_minutes: number;
    activity_time: string;
    note?: string | null;
  }) =>
    apiRequest<ActivityRecord>("/health/activities", {
      method: "POST",
      body: JSON.stringify(data),
    }),
  meals: (patientId?: string) =>
    apiRequest<MealRecord[]>(
      `/health/meals${patientId ? `?patient_id=${encodeURIComponent(patientId)}` : ""}`,
    ),
  createMeal: (data: {
    patient_id: string;
    meal_type: MealType;
    meal_time: string;
    description: string;
    note?: string | null;
    portion?: string | null;
    input_source?: MealInputSource;
  }) =>
    apiRequest<MealRecord>("/health/meals", {
      method: "POST",
      body: JSON.stringify(data),
    }),
  mealAnalysis: (mealId: string) =>
    apiRequest<MealAnalysis>(`/health/meals/${encodeURIComponent(mealId)}/analysis`),
  rerunMealAnalysis: (mealId: string) =>
    apiRequest<MealAnalysis>(`/health/meals/${encodeURIComponent(mealId)}/analysis/rerun`, { method: "POST" }),
  editMealAnalysis: (mealId: string, items: MealItemEdit[]) =>
    apiRequest<MealAnalysis>(`/health/meals/${encodeURIComponent(mealId)}/analysis`, {
      method: "PATCH",
      body: JSON.stringify({ items }),
    }),
  confirmMealAnalysis: (mealId: string) =>
    apiRequest<MealAnalysis>(`/health/meals/${encodeURIComponent(mealId)}/analysis/confirm`, { method: "POST" }),
  mealGlucoseResponse: (patientId?: string, days = 30) => {
    const params = new URLSearchParams({ days: String(days) });
    if (patientId) params.set("patient_id", patientId);
    return apiRequest<MealGlucoseResponse>(`/health/meals/glucose-response?${params}`);
  },
  analyzeMealPhoto: (file: File, hint?: string) => {
    const form = new FormData();
    form.append("file", file);
    if (hint) form.append("hint", hint);
    return apiRequest<MealPhotoAnalysis>("/health/meals/photo-analysis", {
      method: "POST",
      body: form,
    });
  },
  // A meal photo sent in the chat: recognized, then offered as a meal draft
  // card in that conversation (POST /agent/meal-photo). Never stored.
  agentMealPhoto: (file: File, sessionId?: string) => {
    const form = new FormData();
    form.append("file", file);
    if (sessionId) form.append("session_id", sessionId);
    return apiRequest<AgentChatResponse>("/agent/meal-photo", { method: "POST", body: form });
  },
  weeklyReports: () => apiRequest<WeeklyReportListItem[]>("/reports/weekly"),
  weeklyReport: (reportId: string) =>
    apiRequest<WeeklyReport>(`/reports/weekly/${encodeURIComponent(reportId)}`),
  markWeeklyReportRead: (reportId: string) =>
    apiRequest<WeeklyReport>(`/reports/weekly/${encodeURIComponent(reportId)}/read`, { method: "POST" }),
  generateWeeklyReport: () =>
    apiRequest<WeeklyReport>("/reports/weekly/generate", { method: "POST" }),
  notifications: (status?: NotificationStatus, onDate?: string) => {
    const params = new URLSearchParams();
    if (status) params.set("status", status);
    if (onDate) params.set("on_date", onDate);
    const qs = params.toString();
    return apiRequest<NotificationRecord[]>(
      `/notifications${qs ? `?${qs}` : ""}`,
    );
  },
  // Temporary frontend-only data source until doctor alerts are supported by the backend.
  doctorAlerts: async (): Promise<DoctorAlert[]> => [],
  updateNotification: (
    notificationId: string,
    status: NotificationStatus,
    snoozeMinutes?: number,
  ) =>
    apiRequest<NotificationRecord>(
      `/notifications/${encodeURIComponent(notificationId)}`,
      {
        method: "PATCH",
        body: JSON.stringify({
          status,
          snooze_minutes: status === "snoozed" ? snoozeMinutes : null,
        }),
      },
    ),
  notificationPreferences: () =>
    apiRequest<NotificationPreference[]>("/notifications/preferences"),
  createNotificationPreference: (data: {
    label: string;
    time_of_day: string;
    notification_type?: NotificationType;
  }) =>
    apiRequest<NotificationPreference>("/notifications/preferences", {
      method: "POST",
      body: JSON.stringify(data),
    }),
  updateNotificationPreference: (
    preferenceId: string,
    data: { label?: string; time_of_day?: string; is_enabled?: boolean },
  ) =>
    apiRequest<NotificationPreference>(
      `/notifications/preferences/${encodeURIComponent(preferenceId)}`,
      { method: "PATCH", body: JSON.stringify(data) },
    ),
  deleteNotificationPreference: (preferenceId: string) =>
    apiRequest<void>(
      `/notifications/preferences/${encodeURIComponent(preferenceId)}`,
      { method: "DELETE" },
    ),
  zaloLinkStatus: () =>
    apiRequest<ZaloLinkStatus>("/notifications/zalo/link-status"),
  createZaloLinkCode: () =>
    apiRequest<ZaloLinkCode>("/notifications/zalo/link-code", {
      method: "POST",
    }),
  reminderSuggestions: () =>
    apiRequest<ReminderSuggestion[]>("/notifications/reminder-suggestions"),
  acceptReminderSuggestion: (suggestionId: string) =>
    apiRequest<ReminderSuggestion>(
      `/notifications/reminder-suggestions/${encodeURIComponent(suggestionId)}/accept`,
      { method: "POST" },
    ),
  declineReminderSuggestion: (suggestionId: string) =>
    apiRequest<ReminderSuggestion>(
      `/notifications/reminder-suggestions/${encodeURIComponent(suggestionId)}/decline`,
      { method: "POST" },
    ),
  medicationAdherence: (patientId?: string, days = 30) => {
    const params = new URLSearchParams({ days: String(days) });
    if (patientId) params.set("patient_id", patientId);
    return apiRequest<MedicationAdherence>(`/medications/adherence?${params}`);
  },
  activeMedications: (patientId?: string) =>
    apiRequest<MedicationRecord[]>(
      `/medications/active${patientId ? `?patient_id=${encodeURIComponent(patientId)}` : ""}`,
    ),
  medicationHistory: (patientId?: string) =>
    apiRequest<MedicationRecord[]>(
      `/medications/history${patientId ? `?patient_id=${encodeURIComponent(patientId)}` : ""}`,
    ),
  medicationNotes: (patientId?: string) =>
    apiRequest<MedicationNote[]>(
      `/medications/notes${patientId ? `?patient_id=${encodeURIComponent(patientId)}` : ""}`,
    ),
  createMedication: (data: MedicationInput & { patient_id: string }) =>
    apiRequest<MedicationRecord>("/medications", {
      method: "POST",
      body: JSON.stringify(data),
    }),
  changeMedication: (medicationId: string, data: MedicationInput) =>
    apiRequest<MedicationRecord>(
      `/medications/${encodeURIComponent(medicationId)}/change`,
      {
        method: "POST",
        body: JSON.stringify(data),
      },
    ),
  discontinueMedication: (
    medicationId: string,
    data: { end_date?: string | null; note?: string | null },
  ) =>
    apiRequest<MedicationRecord>(
      `/medications/${encodeURIComponent(medicationId)}/discontinue`,
      {
        method: "POST",
        body: JSON.stringify(data),
      },
    ),
  createMedicationNote: (
    medicationId: string,
    data: {
      note_type: MedicationNote["note_type"];
      text?: string | null;
      occurred_at?: string | null;
    },
  ) =>
    apiRequest<MedicationNote>(
      `/medications/${encodeURIComponent(medicationId)}/notes`,
      {
        method: "POST",
        body: JSON.stringify(data),
      },
    ),
  clinicalNotes: (patientId: string) =>
    apiRequest<ClinicalNote[]>(
      "/doctor/patients/" + encodeURIComponent(patientId) + "/notes",
    ),
  patientInsights: (patientId: string) =>
    apiRequest<PatientInsight[]>(
      "/doctor/patients/" + encodeURIComponent(patientId) + "/insights",
    ),
  setInsightFeedback: (
    patientId: string,
    insightId: string,
    verdict: InsightVerdict,
    comment?: string,
  ) =>
    apiRequest<PatientInsight>(
      `/doctor/patients/${encodeURIComponent(patientId)}/insights/${encodeURIComponent(insightId)}/feedback`,
      { method: "PUT", body: JSON.stringify({ verdict, comment: comment || null }) },
    ),
  createClinicalNote: (patientId: string, data: ClinicalNoteInput) =>
    apiRequest<ClinicalNote>(
      "/doctor/patients/" + encodeURIComponent(patientId) + "/notes",
      {
        method: "POST",
        body: JSON.stringify(data),
      },
    ),
  updateClinicalNote: (
    patientId: string,
    noteId: string,
    data: Partial<ClinicalNoteInput>,
  ) =>
    apiRequest<ClinicalNote>(
      "/doctor/patients/" +
        encodeURIComponent(patientId) +
        "/notes/" +
        encodeURIComponent(noteId),
      {
        method: "PATCH",
        body: JSON.stringify(data),
      },
    ),
  deleteClinicalNote: (patientId: string, noteId: string) =>
    apiRequest<void>(
      "/doctor/patients/" +
        encodeURIComponent(patientId) +
        "/notes/" +
        encodeURIComponent(noteId),
      {
        method: "DELETE",
      },
    ),
  appointments: (patientId?: string) =>
    apiRequest<Appointment[]>(
      `/appointments${patientId ? `?patient_id=${encodeURIComponent(patientId)}` : ""}`,
    ),
  createAppointment: (data: AppointmentInput & { patient_id: string }) =>
    apiRequest<Appointment>("/appointments", {
      method: "POST",
      body: JSON.stringify(data),
    }),
  updateAppointment: (
    appointmentId: string,
    data: Required<Pick<AppointmentInput, "scheduled_at" | "status">> &
      Omit<AppointmentInput, "scheduled_at" | "status">,
  ) =>
    apiRequest<Appointment>(
      `/appointments/${encodeURIComponent(appointmentId)}`,
      {
        method: "PUT",
        body: JSON.stringify(data),
      },
    ),
  deleteAppointment: (appointmentId: string) =>
    apiRequest<void>(`/appointments/${encodeURIComponent(appointmentId)}`, {
      method: "DELETE",
    }),
  upcomingAppointments: (days = 7) =>
    apiRequest<UpcomingAppointment[]>(`/appointments/upcoming?days=${days}`),
  previsitBrief: (appointmentId: string) =>
    apiRequest<PrevisitBrief>(`/appointments/${encodeURIComponent(appointmentId)}/brief`),
  generatePrevisitBrief: (appointmentId: string) =>
    apiRequest<PrevisitBrief>(`/appointments/${encodeURIComponent(appointmentId)}/brief`, {
      method: "POST",
    }),
  markPrevisitBriefSeen: (appointmentId: string) =>
    apiRequest<PrevisitBrief>(`/appointments/${encodeURIComponent(appointmentId)}/brief/seen`, {
      method: "POST",
    }),
  agentChat: (message: string, patientId?: string, sessionId?: string) =>
    apiRequest<AgentChatResponse>("/agent/chat", {
      method: "POST",
      body: JSON.stringify({
        message,
        patient_id: patientId || null,
        session_id: sessionId || null,
      }),
    }),
  agentChatStream: (
    message: string,
    patientId?: string,
    sessionId?: string,
    onProgress?: (message: string) => void,
  ) =>
    agentChatStream(
      { message, patient_id: patientId || null, session_id: sessionId || null },
      onProgress,
    ),
  agentRecordAction: (
    actionId: string,
    data:
      | { action: "provide_field"; field: string; value: string }
      | { action: "confirm" | "cancel" },
  ) =>
    apiRequest<AgentRecordActionResponse>(
      `/agent/record-actions/${encodeURIComponent(actionId)}`,
      {
        method: "POST",
        body: JSON.stringify(data),
      },
    ),
  agentSummary: (days = 7, patientId?: string) =>
    apiRequest<AgentSummaryResponse>("/agent/summary", {
      method: "POST",
      body: JSON.stringify({ days, patient_id: patientId || null }),
    }),
  // Separate, additive endpoint (codebase/backend/agent/summary_metrics.py) —
  // display-only stat tiles layered on top of the AI Summary agent, not part
  // of its own response. Composed with agentSummary() in the UI, not merged
  // server-side, so either call failing degrades independently.
  agentSummaryKeyMetrics: (days = 7, patientId?: string) => {
    const params = new URLSearchParams({ days: String(days) });
    if (patientId) params.set("patient_id", patientId);
    return apiRequest<KeyMetricsResponse>(`/agent/summary/key-metrics?${params.toString()}`);
  },
  // Separate, additive endpoint (codebase/backend/agent/chat_suggestions.py) —
  // proposes follow-up question chips from the last Q&A turn. Never part of
  // the chat graph/response itself; a failure here just keeps the previous
  // suggestion chips shown.
  agentChatSuggestions: (message: string, response: string) =>
    apiRequest<ChatSuggestionsResponse>("/agent/chat/suggestions", {
      method: "POST",
      body: JSON.stringify({ message, response }),
    }),
  listAgentSessions: (patientId?: string) =>
    apiRequest<ChatSession[]>(
      `/agent/sessions${patientId ? `?patient_id=${encodeURIComponent(patientId)}` : ""}`,
    ),
  getAgentSessionMessages: (sessionId: string) =>
    apiRequest<ChatSessionMessagesResponse>(
      `/agent/sessions/${encodeURIComponent(sessionId)}/messages`,
    ),
  updateMe: (data: { ai_memory_consent?: boolean; meal_photo_ai_consent?: boolean; weekly_report_enabled?: boolean }) =>
    apiRequest<ApiUser>("/auth/me", {
      method: "PATCH",
      body: JSON.stringify(data),
    }),
  // Task 6.1 — read-only audit trail (admin).
  auditEvents: (filters: AuditEventFilters = {}) => {
    const params = new URLSearchParams();
    for (const [key, value] of Object.entries(filters)) {
      if (value !== undefined && value !== "") params.set(key, String(value));
    }
    return apiRequest<AuditEventPage>(`/admin/audit-events?${params.toString()}`);
  },
  // Task 6.5 — patient data rights.
  exportMyData: (format: "json" | "csv") =>
    apiDownload(`/privacy/me/export?format=${format}`),
  myDeletionRequest: () =>
    apiRequest<DeletionRequest | null>("/privacy/me/deletion-request"),
  requestAccountDeletion: () =>
    apiRequest<DeletionRequest>("/privacy/me/deletion-request", { method: "POST" }),
  cancelAccountDeletion: () =>
    apiRequest<DeletionRequest>("/privacy/me/deletion-request/cancel", { method: "POST" }),
  deletionRequests: (status?: DeletionRequestStatus) =>
    apiRequest<AdminDeletionRequest[]>(
      `/admin/deletion-requests${status ? `?status=${status}` : ""}`,
    ),
  approveDeletionRequest: (id: string, note?: string) =>
    apiRequest<DeletionRequest>(`/admin/deletion-requests/${encodeURIComponent(id)}/approve`, {
      method: "POST",
      body: JSON.stringify({ note: note || null }),
    }),
  rejectDeletionRequest: (id: string, note?: string) =>
    apiRequest<DeletionRequest>(`/admin/deletion-requests/${encodeURIComponent(id)}/reject`, {
      method: "POST",
      body: JSON.stringify({ note: note || null }),
    }),
};
