# Epic 2 — Báo cáo tuần cho bệnh nhân

**Ưu tiên:** P1 · **Độ lớn:** M · **Phụ thuộc:** Epic 1 (mục bữa ăn), Epic 3 (mục thuốc) — các mục
này bật dần khi epic tương ứng xong · **Trạng thái:** Chưa bắt đầu

## Mục tiêu

Mỗi tuần bệnh nhân **tự động nhận** một báo cáo ngắn, dễ hiểu (hướng tới người cao tuổi): đường huyết
tuần này so với tuần trước, điểm cần chú ý, món ăn liên quan tăng đường, mức tuân thủ thuốc, một việc
nên làm tuần tới. Hỗ trợ mục tiêu F2 trong PRD (bệnh nhân hiểu xu hướng của mình).

## Hiện trạng (đã kiểm tra trong code)

- Đã có tóm tắt **theo yêu cầu**: `POST /api/v1/agent/summary` với `SummaryRole.PATIENT`, cache trong
  `AgentSummarySnapshot` (`codebase/backend/summaries/`), hiển thị qua `AgentSummaryCard` ở dashboard.
- Đã có cron hàng tuần `insights/scheduler.py::run_weekly_reflection_tick` sinh insight
  `WEEKLY_BATCH` (patient-facing).
- Đã có kênh Zalo (`notifications/zalo.py`, `_push_zalo_reminder`) và in-app `Notification`.
- **Thiếu:** không chủ động gửi; không lưu lịch sử báo cáo theo tuần; không so sánh tuần trước;
  không có mục bữa ăn/thuốc.

## Phạm vi

**In:** sinh báo cáo tự động mỗi tuần, lưu lịch sử, gửi in-app + Zalo, trang xem báo cáo.
**Out:** báo cáo tháng/quý, email, xuất PDF cho bệnh nhân (có thể thêm sau).

## Task

### 2.1 Mô hình dữ liệu
- [ ] Bảng `patient_weekly_reports`: `patient_id`, `week_start` (unique cùng patient), `snapshot_id`
      (FK `agent_summary_snapshots`), `stats` (JSON — số liệu deterministic), `sections_enabled`,
      `status` (`generated|delivered|failed`), `delivered_at`, `read_at`.
- **AC:** không tạo trùng báo cáo cho cùng tuần khi cron chạy lại.

### 2.2 Số liệu deterministic
- [ ] `codebase/backend/summaries/weekly_stats.py`: % số đo trong mục tiêu (theo `CarePlanTarget` đang
      hiệu lực), trung bình/min/max theo `meal_context`, chênh lệch so tuần trước, số ngày có ghi nhật ký.
- [ ] Mục bữa ăn (khi Epic 1.4 xong): top món đi kèm glucose sau ăn cao (n ≥ 3).
- [ ] Mục thuốc (khi Epic 3.1 xong): % tuân thủ tuần.
- **AC:** unit test trên dữ liệu cố định; không gọi LLM.

### 2.3 Job sinh báo cáo
- [ ] Thêm bước vào cron tuần trong `insights/scheduler.py` (chạy **sau** weekly reflection để dùng
      insight mới): với mỗi bệnh nhân active có dữ liệu trong tuần → tính stats → `ensure_snapshot(
      role=patient, period_days=7)` → ghi `patient_weekly_reports`.
- [ ] Prompt nhánh bệnh nhân: câu ngắn, không thuật ngữ, ≤ 1 "việc nên làm" lấy từ nguồn đã duyệt; qua guardrail.
- [ ] Bệnh nhân không có dữ liệu tuần → báo cáo dạng "tuần này chưa có số đo" + nhắc nhẹ, không gọi LLM.
- **AC:** lỗi LLM một bệnh nhân không làm dừng cả batch; có log số lượng thành công/thất bại (không PHI).

### 2.4 Gửi báo cáo
- [ ] `NotificationType.WEEKLY_REPORT` + tạo `Notification` in-app.
- [ ] Zalo: tin ngắn (2–3 dòng tóm tắt + link mở app), **không** gửi chi tiết số liệu sức khỏe qua Zalo
      nếu chưa có đồng ý riêng.
- [ ] Bệnh nhân bật/tắt nhận báo cáo trong `NotificationPreference`.
- **AC:** tắt nhận → không tạo notification/Zalo.

### 2.5 Giao diện
- [ ] Route `codebase/frontend/app/(app)/reports/` — danh sách tuần + chi tiết; biểu đồ so sánh tuần.
- [ ] Đánh dấu `read_at` khi mở; badge "báo cáo mới" trên dashboard.
- [ ] Nút "Đọc to" dùng TTS service sẵn có (`NEXT_PUBLIC_TTS_API_URL`).
- **AC:** hiển thị tốt ở 360px, cỡ chữ lớn, không chỉ dùng màu.

### 2.6 Test & eval
- [ ] `tests/test_backend/test_weekly_reports.py`: idempotent, opt-out, không dữ liệu, lỗi LLM.
- [ ] 5–10 báo cáo mẫu đưa bác sĩ đánh giá độ dễ hiểu/an toàn (dùng `docs/evaluation/human_review.csv`).

## Definition of Done
- Cron sinh và gửi báo cáo tự động; bệnh nhân xem được lịch sử; test xanh; ≥1 vòng review bác sĩ.

## Câu hỏi mở
- Gửi vào thứ/giờ nào? (đề xuất sáng Chủ nhật, theo `TIMEZONE` của hệ thống)
- Báo cáo có chia sẻ cho bác sĩ phụ trách không, hay chỉ bệnh nhân?
