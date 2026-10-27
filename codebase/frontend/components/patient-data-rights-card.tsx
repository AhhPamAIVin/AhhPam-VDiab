"use client";

import { useEffect, useState } from "react";
import { Download, FileArchive, Loader2, Trash2 } from "lucide-react";
import { api, type DeletionRequest } from "@/lib/api";
import { useToast } from "@/components/ui/toast";
import { parseUtcTimestamp } from "@/lib/utils";

function saveBlob(blob: Blob, filename: string) {
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  document.body.appendChild(link);
  link.click();
  link.remove();
  URL.revokeObjectURL(url);
}

// Task 6.5: the patient downloads everything the system holds about them,
// or asks for their account to be erased (an admin approves it).
export function PatientDataRightsCard() {
  const { showToast } = useToast();
  const [exporting, setExporting] = useState<"json" | "csv" | null>(null);
  const [request, setRequest] = useState<DeletionRequest | null>(null);
  const [loadingRequest, setLoadingRequest] = useState(true);
  const [confirming, setConfirming] = useState(false);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    api
      .myDeletionRequest()
      .then(setRequest)
      .catch(() => setRequest(null))
      .finally(() => setLoadingRequest(false));
  }, []);

  const exportData = async (format: "json" | "csv") => {
    setExporting(format);
    try {
      const { blob, filename } = await api.exportMyData(format);
      saveBlob(blob, filename ?? `vdiab-du-lieu.${format === "csv" ? "zip" : "json"}`);
    } catch (error) {
      showToast(error instanceof Error ? error.message : "Không thể tải dữ liệu", "error");
    } finally {
      setExporting(null);
    }
  };

  const requestDeletion = async () => {
    setSaving(true);
    try {
      setRequest(await api.requestAccountDeletion());
      setConfirming(false);
      showToast("Đã gửi yêu cầu. Quản trị viên sẽ xem xét và phản hồi.", "success");
    } catch (error) {
      showToast(error instanceof Error ? error.message : "Không thể gửi yêu cầu", "error");
    } finally {
      setSaving(false);
    }
  };

  const cancelDeletion = async () => {
    setSaving(true);
    try {
      setRequest(await api.cancelAccountDeletion());
      showToast("Đã hủy yêu cầu xóa tài khoản.", "success");
    } catch (error) {
      showToast(error instanceof Error ? error.message : "Không thể hủy yêu cầu", "error");
    } finally {
      setSaving(false);
    }
  };

  const pending = request?.status === "pending";

  return (
    <section className="overflow-hidden rounded-2xl border border-border bg-card">
      <div className="border-b border-border px-5 py-4">
        <div className="flex items-center gap-3">
          <div className="flex h-10 w-10 items-center justify-center rounded-xl bg-accent">
            <FileArchive className="h-5 w-5 text-primary" />
          </div>
          <div>
            <h3 className="font-semibold text-foreground">Dữ liệu của bạn</h3>
            <p className="mt-0.5 text-xs text-muted-foreground">
              Tải về toàn bộ dữ liệu VDiab đang lưu về bạn, hoặc yêu cầu xóa tài khoản.
            </p>
          </div>
        </div>
      </div>

      <div className="space-y-5 p-5">
        <div>
          <p className="text-sm font-medium text-foreground">Tải dữ liệu</p>
          <p className="mt-0.5 text-xs text-muted-foreground">
            Gồm chỉ số sức khỏe, bữa ăn, thuốc, ghi chú khám, báo cáo, tóm tắt AI và các cuộc trò chuyện với trợ lý.
          </p>
          <div className="mt-3 flex flex-wrap gap-2">
            {(["json", "csv"] as const).map((format) => (
              <button
                key={format}
                type="button"
                onClick={() => exportData(format)}
                disabled={exporting !== null}
                className="flex items-center gap-2 rounded-xl border border-border px-4 py-2.5 text-sm font-medium hover:bg-muted disabled:opacity-50"
              >
                {exporting === format ? <Loader2 className="h-4 w-4 animate-spin" /> : <Download className="h-4 w-4" />}
                {format === "json" ? "Tải JSON" : "Tải CSV (Excel)"}
              </button>
            ))}
          </div>
        </div>

        <div className="border-t border-border pt-5">
          <p className="text-sm font-medium text-foreground">Xóa tài khoản</p>
          {loadingRequest ? (
            <Loader2 className="mt-2 h-4 w-4 animate-spin text-muted-foreground" />
          ) : pending ? (
            <div className="mt-2 rounded-xl bg-orange-50 p-4 text-sm text-orange-800">
              Yêu cầu gửi lúc {parseUtcTimestamp(request.requested_at).toLocaleString("vi-VN")} đang chờ quản trị viên
              duyệt. Bạn vẫn dùng tài khoản bình thường cho tới khi yêu cầu được duyệt.
              <button
                type="button"
                onClick={cancelDeletion}
                disabled={saving}
                className="mt-3 block rounded-xl border border-orange-300 bg-white px-4 py-2 font-medium hover:bg-orange-100 disabled:opacity-50"
              >
                Hủy yêu cầu
              </button>
            </div>
          ) : (
            <>
              {request?.status === "rejected" && (
                <p className="mt-2 rounded-xl bg-muted p-3 text-xs text-muted-foreground">
                  Yêu cầu trước đã bị từ chối{request.decision_note ? `: ${request.decision_note}` : "."}
                </p>
              )}
              <p className="mt-1 text-xs text-muted-foreground">
                Sau khi quản trị viên duyệt, tài khoản và toàn bộ dữ liệu sẽ bị xóa vĩnh viễn, không khôi phục được.
                Hãy tải dữ liệu về trước nếu cần.
              </p>
              {confirming ? (
                <div className="mt-3 flex flex-wrap gap-2">
                  <button
                    type="button"
                    onClick={requestDeletion}
                    disabled={saving}
                    className="flex items-center gap-2 rounded-xl bg-red-600 px-4 py-2.5 text-sm font-medium text-white hover:bg-red-700 disabled:opacity-50"
                  >
                    {saving && <Loader2 className="h-4 w-4 animate-spin" />}
                    Xác nhận gửi yêu cầu
                  </button>
                  <button
                    type="button"
                    onClick={() => setConfirming(false)}
                    className="rounded-xl border border-border px-4 py-2.5 text-sm hover:bg-muted"
                  >
                    Thôi
                  </button>
                </div>
              ) : (
                <button
                  type="button"
                  onClick={() => setConfirming(true)}
                  className="mt-3 flex items-center gap-2 rounded-xl border border-red-200 px-4 py-2.5 text-sm font-medium text-red-600 hover:bg-red-50"
                >
                  <Trash2 className="h-4 w-4" />
                  Yêu cầu xóa tài khoản
                </button>
              )}
            </>
          )}
        </div>
      </div>
    </section>
  );
}
