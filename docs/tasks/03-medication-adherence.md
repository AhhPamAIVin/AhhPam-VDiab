# Epic 3 — Tuân thủ thuốc & nhắc nhở thông minh

**Ưu tiên:** P2 · **Độ lớn:** M · **Phụ thuộc:** — · **Trạng thái:** Chưa bắt đầu

## Mục tiêu

1. Đo được **mức tuân thủ dùng thuốc** từ dữ liệu sẵn có và mô tả mối liên quan với đường huyết.
2. **Cá nhân hóa giờ nhắc** theo thói quen thật của bệnh nhân (PRD §5 — mục *Should* chưa làm).

## Hiện trạng (đã kiểm tra trong code)

- `Medication`, `MedicationNote` với `MedicationNoteType.MISSED_DOSE` (`codebase/backend/medications/models.py`).
- `Notification` loại `MEDICATION_REMINDER` có `status` `pending|completed|skipped|snoozed`,
  `scheduled_for`, `responded_at`, `nudge_count` (`codebase/backend/notifications/models.py`).
- `NotificationPreference.time_of_day` cố định; scheduler interval trong `notifications/scheduler.py`.
- **Thiếu:** không có chỉ số tuân thủ; agent/summary không biết bệnh nhân hay quên thuốc; giờ nhắc
  không bao giờ tự điều chỉnh.

## Phạm vi

**In:** chỉ số tuân thủ, tương quan mô tả với glucose, đề xuất giờ nhắc mới (bệnh nhân xác nhận).
**Out:** tự đổi giờ nhắc không hỏi, gợi ý đổi thuốc/liều (PRD Won't), tích hợp hộp thuốc thông minh.

## Task

### 3.1 Chỉ số tuân thủ
- [ ] `codebase/backend/medications/adherence.py`: theo thuốc × ngày, từ reminder `completed` /
      `skipped` / `pending` quá hạn + `MISSED_DOSE` note. Định nghĩa (chốt với bác sĩ):
      `adherence = completed / (completed + skipped + missed + expired_pending)`.
- [ ] Xử lý trùng: một liều vừa `skipped` vừa có `MISSED_DOSE` note chỉ tính một lần.
- [ ] Endpoint `GET /api/v1/medications/adherence?days=` (bệnh nhân / bác sĩ có quyền).
- **AC:** test với dữ liệu cố định cho 4 kịch bản (đều đặn, quên rải rác, bỏ cả tuần, không có reminder).

### 3.2 Tương quan tuân thủ ↔ đường huyết
- [ ] So sánh glucose trung bình ngày đủ thuốc vs ngày quên (chỉ khi mỗi nhóm có ≥3 ngày).
- [ ] Tool agent `get_medication_adherence(patient_id, days)`; thêm vào `fetch_data` và
      `synthesize_summary` (nhánh bác sĩ).
- [ ] Ngôn ngữ mô tả, không nhân quả; guardrail chặn "bạn nên tăng/giảm liều", "có thể bỏ thuốc".
- **AC:** guardrail case mới 100% bị chặn.

### 3.3 Nhắc nhở thông minh
- [ ] Phân tích 14 ngày gần nhất: độ trễ `responded_at - scheduled_for` theo từng `NotificationPreference`.
- [ ] Nếu bệnh nhân **thường xuyên** phản hồi trễ > 30 phút (≥ 5/7 lần), tạo đề xuất giờ mới = trung vị
      giờ phản hồi; bệnh nhân bấm "Đổi" hoặc "Giữ nguyên". Không đề xuất lại trong 14 ngày nếu từ chối.
- [ ] Áp dụng cho cả nhắc đo đường huyết (cùng cơ chế preference).
- **AC:** không bao giờ tự đổi `time_of_day` khi chưa có xác nhận; test kịch bản từ chối.

### 3.4 Giao diện
- [ ] `medication-page.tsx`: widget tuân thủ 7/30 ngày theo từng thuốc.
- [ ] `patient-detail-page.tsx` (bác sĩ): bảng tuân thủ + ngày quên thuốc trên biểu đồ glucose.
- [ ] Card đề xuất giờ nhắc ở trang thông báo.

### 3.5 Test
- [ ] `tests/test_backend/test_medication_adherence.py`, `test_smart_reminders.py`.

## Definition of Done
- Chỉ số tuân thủ có ở API, summary bác sĩ và UI; đề xuất giờ nhắc chạy được; test xanh.

## Câu hỏi mở
- Định nghĩa "quên thuốc" khi bệnh nhân không bấm gì (pending hết ngày) — tính là quên hay không rõ?
- Thuốc dùng "khi cần" (PRN) có loại khỏi chỉ số không? (`frequency` hiện là text tự do)
