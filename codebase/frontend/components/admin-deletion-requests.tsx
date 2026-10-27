"use client";

import { useCallback, useEffect, useState } from "react";
import { Loader2, Trash2, UserX } from "lucide-react";
import { api, AdminDeletionRequest } from "@/lib/api";
import { useToast } from "@/components/ui/toast";
import { parseUtcTimestamp } from "@/lib/utils";

// Task 6.5: a patient's erasure request waits here until an admin decides.
// Approving is irreversible — the confirm step makes the admin type the
// patient's email.
export function AdminDeletionRequests() {
  const { showToast } = useToast();
  const [requests, setRequests] = useState<AdminDeletionRequest[]>([]);
  const [loading, setLoading] = useState(true);
  const [deciding, setDeciding] = useState<{ request: AdminDeletionRequest; mode: "approve" | "reject" } | null>(null);
  const [confirmEmail, setConfirmEmail] = useState("");
  const [note, setNote] = useState("");
  const [saving, setSaving] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      setRequests(await api.deletionRequests("pending"));
    } catch (error) {
      showToast(error instanceof Error ? error.message : "Không thể tải yêu cầu xóa tài khoản", "error");
    } finally {
      setLoading(false);
    }
  }, [showToast]);

  useEffect(() => {
    load();
  }, [load]);

  const close = () => {
    setDeciding(null);
    setConfirmEmail("");
    setNote("");
  };

  const submit = async () => {
    if (!deciding) return;
    setSaving(true);
    try {
      if (deciding.mode === "approve") {
        const result = await api.approveDeletionRequest(deciding.request.id, note);
        showToast(`Đã xóa tài khoản và ${result.deleted_rows ?? 0} bản ghi liên quan.`, "success");
      } else {
        await api.rejectDeletionRequest(deciding.request.id, note);
        showToast("Đã từ chối yêu cầu.", "success");
      }
      close();
      await load();
    } catch (error) {
      showToast(error instanceof Error ? error.message : "Không thể xử lý yêu cầu", "error");
    } finally {
      setSaving(false);
    }
  };

  if (!loading && requests.length === 0) return null;

  const approveBlocked =
    deciding?.mode === "approve" &&
    confirmEmail.trim().toLowerCase() !== (deciding.request.patient_email ?? "").toLowerCase();

  return (
    <section className="rounded-2xl border border-red-200 bg-card">
      <div className="border-b border-border p-5">
        <h2 className="flex items-center gap-2 font-semibold">
          <UserX className="h-4 w-4 text-red-600" />
          Yêu cầu xóa tài khoản chờ duyệt
        </h2>
        <p className="mt-1 text-xs text-muted-foreground">
          Trước khi duyệt, kiểm tra nghĩa vụ lưu trữ hồ sơ bệnh án và tranh chấp đang mở (docs/DATA_RETENTION.md).
          Duyệt sẽ xóa vĩnh viễn tài khoản và toàn bộ dữ liệu của bệnh nhân; nhật ký truy vết được giữ lại.
        </p>
      </div>
      {loading ? (
        <div className="flex justify-center p-6 text-muted-foreground">
          <Loader2 className="h-5 w-5 animate-spin" />
        </div>
      ) : (
        <div className="divide-y divide-border">
          {requests.map((request) => (
            <div key={request.id} className="flex flex-wrap items-center gap-3 p-4">
              <div className="min-w-0 flex-1">
                <p className="text-sm font-medium">{request.patient_name ?? "Tài khoản đã xóa"}</p>
                <p className="text-xs text-muted-foreground">
                  {request.patient_email} · gửi lúc {parseUtcTimestamp(request.requested_at).toLocaleString("vi-VN")}
                </p>
              </div>
              <button
                onClick={() => setDeciding({ request, mode: "reject" })}
                className="rounded-xl border border-border px-3 py-2 text-sm hover:bg-muted"
              >
                Từ chối
              </button>
              <button
                onClick={() => setDeciding({ request, mode: "approve" })}
                className="flex items-center gap-1.5 rounded-xl bg-red-600 px-3 py-2 text-sm font-medium text-white hover:bg-red-700"
              >
                <Trash2 className="h-4 w-4" />
                Duyệt xóa
              </button>
            </div>
          ))}
        </div>
      )}

      {deciding && (
        <div className="fixed inset-0 z-50 flex items-center justify-center p-4">
          <button aria-label="Đóng" className="absolute inset-0 bg-slate-950/45" onClick={close} />
          <section className="relative w-full max-w-md rounded-2xl border border-border bg-card p-6 shadow-2xl">
            <h3 className="text-lg font-bold">
              {deciding.mode === "approve" ? "Xóa vĩnh viễn tài khoản?" : "Từ chối yêu cầu xóa"}
            </h3>
            {deciding.mode === "approve" ? (
              <>
                <p className="mt-2 text-sm text-muted-foreground">
                  Không thể hoàn tác. Gõ email của bệnh nhân để xác nhận:{" "}
                  <b className="text-foreground">{deciding.request.patient_email}</b>
                </p>
                <input
                  value={confirmEmail}
                  onChange={(e) => setConfirmEmail(e.target.value)}
                  className="mt-3 w-full rounded-xl border border-border px-3 py-2.5 text-sm"
                  placeholder="email bệnh nhân"
                  autoFocus
                />
              </>
            ) : (
              <p className="mt-2 text-sm text-muted-foreground">
                Bệnh nhân sẽ thấy lý do bạn ghi dưới đây. Không ghi dữ liệu sức khỏe.
              </p>
            )}
            <textarea
              value={note}
              onChange={(e) => setNote(e.target.value)}
              maxLength={500}
              rows={3}
              className="mt-3 w-full rounded-xl border border-border px-3 py-2.5 text-sm"
              placeholder={deciding.mode === "approve" ? "Ghi chú nội bộ (không bắt buộc)" : "Lý do từ chối"}
            />
            <div className="mt-4 flex justify-end gap-2">
              <button onClick={close} className="rounded-xl border border-border px-4 py-2 text-sm hover:bg-muted">
                Hủy
              </button>
              <button
                onClick={submit}
                disabled={saving || approveBlocked}
                className="flex items-center gap-1.5 rounded-xl bg-red-600 px-4 py-2 text-sm font-medium text-white hover:bg-red-700 disabled:opacity-50"
              >
                {saving && <Loader2 className="h-4 w-4 animate-spin" />}
                {deciding.mode === "approve" ? "Xóa vĩnh viễn" : "Từ chối"}
              </button>
            </div>
          </section>
        </div>
      )}
    </section>
  );
}
