"use client";

import { useCallback, useEffect, useState } from "react";
import { CheckCircle2, Loader2, RefreshCw, ShieldAlert, XCircle } from "lucide-react";
import { ConfigProvider, Select } from "antd";
import { api, AuditEvent } from "@/lib/api";
import { useToast } from "@/components/ui/toast";
import { Pagination } from "@/components/ui/pagination";
import { cn, parseUtcTimestamp } from "@/lib/utils";

const PAGE_SIZE = 25;

// Backend action names (codebase/backend/audit/service.py) -> Vietnamese.
// A filter value ending in "." matches every action with that prefix.
const ACTION_LABELS: Record<string, string> = {
  "auth.login": "Đăng nhập",
  "patient_data.access": "Truy cập dữ liệu bệnh nhân",
  "patient_data.export": "Bệnh nhân xuất dữ liệu",
  "consent.update": "Đổi đồng ý xử lý dữ liệu",
  "ai_summary.generate": "Sinh tóm tắt AI",
  "glucose_record.create": "Thêm chỉ số đường huyết",
  "glucose_record.update": "Sửa chỉ số đường huyết",
  "glucose_record.delete": "Xóa chỉ số đường huyết",
  "blood_pressure_record.create": "Thêm chỉ số huyết áp",
  "blood_pressure_record.update": "Sửa chỉ số huyết áp",
  "blood_pressure_record.delete": "Xóa chỉ số huyết áp",
  "meal_record.create": "Thêm bữa ăn",
  "meal_record.update": "Sửa bữa ăn",
  "meal_record.delete": "Xóa bữa ăn",
  "activity_record.create": "Thêm hoạt động",
  "activity_record.update": "Sửa hoạt động",
  "activity_record.delete": "Xóa hoạt động",
  "medication.create": "Thêm thuốc",
  "medication.update": "Sửa/ngừng thuốc",
  "medication.delete": "Xóa thuốc",
  "medication_note.create": "Thêm ghi chú thuốc",
  "medication_note.update": "Sửa ghi chú thuốc",
  "medication_note.delete": "Xóa ghi chú thuốc",
  "clinical_note.create": "Thêm ghi chú lâm sàng",
  "clinical_note.update": "Sửa ghi chú lâm sàng",
  "clinical_note.delete": "Xóa ghi chú lâm sàng",
  "care_plan_target.create": "Đổi mục tiêu điều trị",
  "care_plan_target.update": "Sửa mục tiêu điều trị",
  "care_plan_target.delete": "Xóa mục tiêu điều trị",
  "user.create": "Tạo tài khoản",
  "user.update": "Sửa tài khoản",
  "user.delete": "Xóa tài khoản",
  "user.password_change": "Đổi mật khẩu",
  "user.password_reset": "Đặt lại mật khẩu",
  "patient_assignment.create": "Gán bệnh nhân",
  "patient_assignment.update": "Sửa phân công",
  "patient_assignment.delete": "Bỏ gán bệnh nhân",
  "account_deletion.request": "Yêu cầu xóa tài khoản",
  "account_deletion.cancel": "Hủy yêu cầu xóa tài khoản",
  "account_deletion.approve": "Duyệt xóa tài khoản",
  "account_deletion.reject": "Từ chối xóa tài khoản",
  "retention.purge": "Dọn dữ liệu hết hạn lưu trữ",
};

const ACTION_FILTERS = [
  { value: "", label: "Tất cả hành động" },
  { value: "auth.login", label: "Đăng nhập" },
  { value: "patient_data.", label: "Truy cập / xuất dữ liệu bệnh nhân" },
  { value: "consent.update", label: "Đổi đồng ý" },
  { value: "ai_summary.generate", label: "Sinh tóm tắt AI" },
  { value: "clinical_note.", label: "Ghi chú lâm sàng" },
  { value: "care_plan_target.", label: "Mục tiêu điều trị" },
  { value: "medication.", label: "Thuốc" },
  { value: "glucose_record.", label: "Đường huyết" },
  { value: "user.", label: "Tài khoản (quản trị)" },
  { value: "patient_assignment.", label: "Phân công" },
  { value: "account_deletion.", label: "Xóa tài khoản" },
];

const OUTCOME_FILTERS = [
  { value: "", label: "Mọi kết quả" },
  { value: "success", label: "Thành công" },
  { value: "failure", label: "Thất bại" },
  { value: "denied", label: "Bị từ chối" },
];

const ROLE_LABELS: Record<string, string> = {
  admin: "Quản trị",
  doctor: "Bác sĩ",
  patient: "Bệnh nhân",
  system: "Hệ thống",
  anonymous: "Không xác định",
};

const SELECT_THEME = {
  token: { borderRadius: 12, controlHeightLG: 42, fontSize: 14 },
  components: {
    Select: {
      optionSelectedFontWeight: 500,
      optionSelectedBg: "#39ada7",
      optionActiveBg: "#d9f0ef",
      activeBorderColor: "#39ada7",
    },
  },
};

function shortId(id: string | null) {
  return id ? id.slice(0, 8) : "";
}

function OutcomeIcon({ outcome }: { outcome: string }) {
  if (outcome === "success")
    return (
      <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-xl bg-emerald-50 text-emerald-600">
        <CheckCircle2 className="h-4 w-4" />
      </span>
    );
  if (outcome === "denied")
    return (
      <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-xl bg-red-50 text-red-600">
        <ShieldAlert className="h-4 w-4" />
      </span>
    );
  return (
    <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-xl bg-orange-50 text-orange-600">
      <XCircle className="h-4 w-4" />
    </span>
  );
}

function EventRow({ event }: { event: AuditEvent }) {
  const actor =
    event.actor_name ??
    (event.actor_id ? `#${shortId(event.actor_id)}` : ROLE_LABELS[event.actor_role] ?? event.actor_role);
  const patient = event.patient_id
    ? event.patient_name ?? `bệnh nhân #${shortId(event.patient_id)} (đã xóa)`
    : null;
  return (
    <div className="flex items-start gap-3 p-4">
      <OutcomeIcon outcome={event.outcome} />
      <div className="min-w-0 flex-1">
        <p className="text-sm font-medium">
          {ACTION_LABELS[event.action] ?? event.action}
          {event.outcome !== "success" && (
            <span
              className={cn(
                "ml-2 rounded-full px-2 py-0.5 text-[11px] font-medium",
                event.outcome === "denied" ? "bg-red-50 text-red-700" : "bg-orange-50 text-orange-700",
              )}
            >
              {event.outcome === "denied" ? "Bị từ chối" : "Thất bại"}
            </span>
          )}
        </p>
        <p className="mt-0.5 break-words text-xs text-muted-foreground">
          {actor} ({ROLE_LABELS[event.actor_role] ?? event.actor_role})
          {patient && <> · {patient}</>}
          {event.detail && <> · {event.detail}</>}
        </p>
        <p className="mt-0.5 text-[11px] text-muted-foreground/80">
          {event.ip && <>IP {event.ip}</>}
          {event.request_id && <> · request {event.request_id.slice(0, 12)}</>}
        </p>
      </div>
      <time className="shrink-0 text-xs text-muted-foreground" dateTime={event.ts}>
        {parseUtcTimestamp(event.ts).toLocaleString("vi-VN")}
      </time>
    </div>
  );
}

export function AdminAuditPanel() {
  const { showToast } = useToast();
  const [action, setAction] = useState("");
  const [outcome, setOutcome] = useState("");
  const [page, setPage] = useState(1);
  const [events, setEvents] = useState<AuditEvent[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(true);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const result = await api.auditEvents({
        action,
        outcome,
        limit: PAGE_SIZE,
        offset: (page - 1) * PAGE_SIZE,
      });
      setEvents(result.items);
      setTotal(result.total);
    } catch (error) {
      showToast(error instanceof Error ? error.message : "Không thể tải nhật ký", "error");
    } finally {
      setLoading(false);
    }
  }, [action, outcome, page, showToast]);

  useEffect(() => {
    load();
  }, [load]);

  return (
    <section className="rounded-2xl border border-border bg-card">
      <div className="flex flex-wrap items-center justify-between gap-3 border-b border-border p-5">
        <div>
          <h2 className="font-semibold">Nhật ký truy vết</h2>
          <p className="mt-1 text-xs text-muted-foreground">
            Chỉ đọc, không sửa hay xóa được. Không chứa nội dung dữ liệu sức khỏe — chỉ ai, làm gì, lúc nào.
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <ConfigProvider theme={SELECT_THEME}>
            <Select
              value={action}
              onChange={(value) => {
                setAction(value);
                setPage(1);
              }}
              className="w-[240px]"
              size="large"
              options={ACTION_FILTERS}
            />
            <Select
              value={outcome}
              onChange={(value) => {
                setOutcome(value);
                setPage(1);
              }}
              className="w-[160px]"
              size="large"
              options={OUTCOME_FILTERS}
            />
          </ConfigProvider>
          <button
            onClick={load}
            className="rounded-xl border border-border p-2.5 text-muted-foreground hover:bg-muted"
            aria-label="Tải lại"
          >
            <RefreshCw className={cn("h-4 w-4", loading && "animate-spin")} />
          </button>
        </div>
      </div>
      {loading && events.length === 0 ? (
        <div className="flex justify-center p-10 text-muted-foreground">
          <Loader2 className="h-5 w-5 animate-spin" />
        </div>
      ) : events.length === 0 ? (
        <p className="p-10 text-center text-sm text-muted-foreground">Không có sự kiện nào khớp bộ lọc.</p>
      ) : (
        <div className="divide-y divide-border">
          {events.map((event) => (
            <EventRow key={event.id} event={event} />
          ))}
        </div>
      )}
      <div className="border-t border-border p-3">
        <Pagination page={page} totalItems={total} pageSize={PAGE_SIZE} onPageChange={setPage} className="px-2" />
      </div>
    </section>
  );
}
