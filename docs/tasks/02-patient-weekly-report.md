# Epic 2 — Báo cáo tuần cho bệnh nhân

**Ưu tiên:** P1 · **Độ lớn:** M · **Phụ thuộc:** Epic 1 (mục bữa ăn), Epic 3 (mục thuốc) — các mục
này bật dần khi epic tương ứng xong · **Trạng thái:** Xong phần code (2.1–2.5); còn mục thuốc (chờ Epic 3.1) và vòng review bác sĩ (2.6)

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
- [x] Bảng `patient_weekly_reports`: `patient_id`, `week_start` (unique cùng patient), `snapshot_id`
      (FK `agent_summary_snapshots`), `stats` (JSON — số liệu deterministic), `sections_enabled`,
      `status` (`generated|delivered|failed`), `delivered_at`, `read_at`.
      *Module mới `codebase/backend/reports/`. Thêm `summary` (chép từ snapshot — snapshot upsert theo ngày nên lần
      refresh sau không được đổi báo cáo đã gửi), `tip`, `week_end`, `period_start/end` (UTC). Tuần = Chủ nhật–thứ
      Bảy theo giờ VN.*
- **AC:** không tạo trùng báo cáo cho cùng tuần khi cron chạy lại.

### 2.2 Số liệu deterministic
- [x] `codebase/backend/summaries/weekly_stats.py`: % số đo trong mục tiêu (theo `CarePlanTarget` đang
      hiệu lực), trung bình/min/max theo `meal_context`, chênh lệch so tuần trước, số ngày có ghi nhật ký.
      *Ở `reports/stats.py`. "Trong mục tiêu" theo `threshold_status` của từng lần đo (care plan lúc đo); lần đo
      chưa có care plan không tính vào % (có `evaluated_count`). Thêm số lần < 70 mg/dL, huyết áp, vận động.*
- [x] Mục bữa ăn (khi Epic 1.4 xong): top món đi kèm glucose sau ăn cao (n ≥ 3).
      *Từ `meal_glucose_response` 30 ngày, món có ≥ 50% bữa vượt 180 mg/dL, tối đa 3.*
- [ ] Mục thuốc (khi Epic 3.1 xong): % tuân thủ tuần.
- **AC:** unit test trên dữ liệu cố định; không gọi LLM.
  *"Việc nên làm" cũng tất định: `pick_tip` chọn theo thứ tự ưu tiên (an toàn trước) từ câu đã soạn sẵn, mọi
  câu đều qua guardrail trong test — LLM không viết lời khuyên.*

### 2.3 Job sinh báo cáo
- [x] Thêm bước vào cron tuần trong `insights/scheduler.py` (chạy **sau** weekly reflection để dùng
      insight mới): với mỗi bệnh nhân active có dữ liệu trong tuần → tính stats → `ensure_snapshot(
      role=patient, period_days=7)` → ghi `patient_weekly_reports`.
      *Job riêng `weekly_report_tick`, mặc định **8h sáng Chủ nhật** (`WEEKLY_REPORT_DAY_OF_WEEK`/`_HOUR`). Không phụ
      thuộc weekly reflection (tóm tắt không đọc insight). Dùng `generate_and_store_snapshot` với đúng khung tuần
      thay vì `ensure_snapshot` (khung "7 ngày tới hiện tại"). Thêm `POST /reports/weekly/generate` cho bệnh nhân tự
      tạo tuần trước nếu job chưa chạy.*
- [x] Prompt nhánh bệnh nhân: câu ngắn, không thuật ngữ, ≤ 1 "việc nên làm" lấy từ nguồn đã duyệt; qua guardrail.
      *Đoạn tóm tắt dùng prompt bệnh nhân hiện có của AI Summary (đã qua guardrail); "việc nên làm" do code chọn.*
- [x] Bệnh nhân không có dữ liệu tuần → báo cáo dạng "tuần này chưa có số đo" + nhắc nhẹ, không gọi LLM.
- **AC:** lỗi LLM một bệnh nhân không làm dừng cả batch; có log số lượng thành công/thất bại (không PHI).
  *Báo cáo lỗi lưu `failed` và được tạo lại ở lần chạy sau.*

### 2.4 Gửi báo cáo
- [x] `NotificationType.WEEKLY_REPORT` + tạo `Notification` in-app.
      *Mở báo cáo → `read_at` + thông báo chuyển `completed`.*
- [x] Zalo: tin ngắn (2–3 dòng tóm tắt + link mở app), **không** gửi chi tiết số liệu sức khỏe qua Zalo
      nếu chưa có đồng ý riêng.
      *Chỉ gồm tiêu đề tuần + "mở ứng dụng, mục Báo cáo tuần" — không có số liệu, chưa có link vì config không có
      URL công khai của frontend.*
- [x] Bệnh nhân bật/tắt nhận báo cáo trong `NotificationPreference`.
      *Đổi thành `User.weekly_report_enabled` (`PATCH /auth/me`, công tắc trên trang báo cáo): `NotificationPreference`
      là kế hoạch nhắc **hằng ngày** mà cron nhắc nhở quét mỗi phút. Tắt nhận vẫn tạo báo cáo để xem trong app.*
- **AC:** tắt nhận → không tạo notification/Zalo.

### 2.5 Giao diện
- [x] Route `codebase/frontend/app/(app)/reports/` — danh sách tuần + chi tiết; biểu đồ so sánh tuần.
      *`components/pages/weekly-reports-page.tsx`; so sánh tuần bằng thanh ngang có ghi số; mục "Báo cáo tuần" trong
      sidebar, thông báo `weekly_report` mở trang này.*
- [x] Đánh dấu `read_at` khi mở; badge "báo cáo mới" trên dashboard.
- [x] Nút "Đọc to" dùng TTS service sẵn có (`NEXT_PUBLIC_TTS_API_URL`).
      *`TtsPlayButton` đọc tóm tắt + việc nên làm; tự ẩn khi chưa cấu hình TTS.*
- **AC:** hiển thị tốt ở 360px, cỡ chữ lớn, không chỉ dùng màu.
  *Chữ thân `text-base`, thay đổi tuần ghi bằng chữ + mũi tên, layout `flex-wrap`/`grid sm:`. **Chưa kiểm trên
  trình duyệt thật.***

### 2.6 Test & eval
- [x] `tests/test_backend/test_weekly_reports.py`: idempotent, opt-out, không dữ liệu, lỗi LLM.
      *28 case, gồm ranh giới tuần theo giờ VN, Zalo không chứa số liệu, quyền truy cập, tool agent.*
- [ ] 5–10 báo cáo mẫu đưa bác sĩ đánh giá độ dễ hiểu/an toàn (dùng `docs/evaluation/human_review.csv`).

## Definition of Done
- Cron sinh và gửi báo cáo tự động; bệnh nhân xem được lịch sử; test xanh; ≥1 vòng review bác sĩ.

## Câu hỏi mở
- Gửi vào thứ/giờ nào? (đề xuất sáng Chủ nhật, theo `TIMEZONE` của hệ thống)
  *Tạm chốt 2026-10-27: 8h sáng Chủ nhật giờ VN, đổi được bằng config.*
- Báo cáo có chia sẻ cho bác sĩ phụ trách không, hay chỉ bệnh nhân?
  *Tạm chốt: chỉ bệnh nhân (API chỉ cho bệnh nhân; bác sĩ có brief tái khám — Epic 4).*
