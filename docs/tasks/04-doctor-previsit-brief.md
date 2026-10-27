# Epic 4 — Brief tái khám cho bác sĩ

**Ưu tiên:** P1 · **Độ lớn:** M · **Phụ thuộc:** Epic 1 (mục bữa ăn), Epic 3 (mục thuốc) · **Trạng thái:** Xong code (2026-10-27); còn đo thời gian xác định bệnh nhân ưu tiên với bác sĩ (4.3) và thu phản hồi insight thật (4.4)

## Mục tiêu

Trước mỗi lịch tái khám, bác sĩ có sẵn bản tóm tắt giai đoạn từ lần khám trước — **không phải bấm tạo
và chờ** — kèm bữa ăn, tuân thủ thuốc và insight; bác sĩ phản hồi được insight AI để đo chất lượng
(PRD F4/F5: xác định bệnh nhân ưu tiên ≤ 2 phút, ≥ 90% nội dung được bác sĩ đánh giá phù hợp).

## Hiện trạng trước khi làm (đã kiểm tra trong code)

- Tóm tắt bác sĩ **theo yêu cầu**: `POST /api/v1/agent/summary` (`SummaryRole.DOCTOR`), cache snapshot.
- **Đã có in/lưu PDF** qua trình duyệt trong `patient-detail-page.tsx` (~dòng 800–1050).
- Insight `APPOINTMENT` (từ ClinicalNote, doctor-facing) — `insights/models.py`.
- ARCHITECTURE "Data Flow #4" mô tả Appointment Service kích hoạt tóm tắt trước lịch hẹn, nhưng
  `appointments/` **chưa có trigger nào** gọi summary/reflect.
- `InsightStatus` chỉ có `active|superseded` — bác sĩ không có cách xác nhận/bác bỏ insight.

## Phạm vi

**In:** tự sinh brief trước lịch hẹn, thêm mục bữa ăn/thuốc, phản hồi insight, hàng đợi lịch khám sắp tới.
**Out:** sinh PDF phía server (giữ in qua trình duyệt), ghi chú khám tự động (AI scribe).

## Task

### 4.1 Tự sinh brief trước lịch hẹn
- [x] Job mỗi giờ (phút :05, thêm vào scheduler sẵn có `insights/scheduler.py`) → `appointments/briefs.py::run_previsit_brief_tick`.
      Lấy lịch `scheduled` trong **48 giờ tới** (`PREVISIT_BRIEF_LEAD_HOURS`), không chỉ 24–48h, để lịch tạo
      sát giờ (< 24h) vẫn có brief. Cửa sổ dữ liệu: từ lần khám trước (lịch `completed` hoặc ghi chú khám
      gần nhất trước giờ khám) đến lúc tạo, kẹp 7–90 ngày; chưa có lần khám nào thì 30 ngày
      (`PREVISIT_BRIEF_DEFAULT_DAYS`). Gọi thẳng `generate_and_store_snapshot(role=doctor, period_start, period_end)`
      thay vì `ensure_snapshot` (cần cửa sổ chính xác); `fetch_data`/`get_health_records` cho phép tóm tắt
      bác sĩ có cửa sổ tường minh tới 90 ngày, chat và tóm tắt bệnh nhân vẫn giới hạn 30 ngày.
- [x] Bảng `previsit_briefs` (một dòng mỗi `appointment_id`, unique) chép nội dung snapshot (snapshot bị
      upsert theo ngày nên không tham chiếu sống). Sinh lại (cùng dòng) khi: đổi giờ khám
      (`appointment_scheduled_at` ≠ giờ hiện tại), lần trước lỗi, hoặc có bản ghi mới
      (đường huyết/huyết áp/bữa ăn/vận động/ghi chú thuốc, theo `created_at`) và brief đã ≥ 6 giờ
      (`PREVISIT_BRIEF_REFRESH_HOURS` — tránh gọi LLM mỗi giờ khi bệnh nhân ghi liên tục).
      Sinh lại lỗi thì brief cũ vẫn đọc được. Xóa lịch hẹn thì xóa brief.
- [x] Thông báo in-app cho bác sĩ: bác sĩ **chưa có hộp thông báo riêng** (`notifications` gắn với bệnh nhân),
      nên dùng `previsit_briefs.seen_at` — mỗi lần (tái) sinh xóa `seen_at`; Dashboard hiện "N brief tái khám mới"
      trên banner và nhãn "Brief mới" trên từng lịch; mở brief ở trang bệnh nhân thì đánh dấu đã xem.
      Lịch mà bác sĩ không còn phụ trách bệnh nhân (`patient_assignments`) thì bỏ qua.
- **AC:** lịch hẹn bị hủy/đổi giờ → không sinh/sinh lại đúng; test idempotent. ✔ (`test_previsit_brief.py`)

### 4.2 Mở rộng nội dung brief
- [x] `synthesize_summary.py` nhánh bác sĩ trả thêm `summary_sections` (lưu `agent_summary_snapshots.sections`,
      trả trong `AgentSummaryResponse.sections`): `changes` — "Thay đổi kể từ lần khám trước", so với cửa sổ
      dài bằng ngay trước đó (`fetch_data` → `tool_results.previous_period`; với brief, cửa sổ đó kết thúc ở
      lần khám trước): ĐH trung bình, % lúc đói trong mục tiêu, % sau ăn > 180, số lần < 70 / vượt ngưỡng
      nguy hiểm, huyết áp TB, số ngày có đo — chỉ so khi mỗi bên ≥ 3 lần đo; `meals` (Epic 1.4) và
      `medication_adherence` (Epic 3.2). Mọi con số do code tính.
- [x] Brief có template in riêng (`components/previsit-brief-card.tsx`, "In / Lưu PDF") gồm tóm tắt, điểm cần chú ý,
      ba mục trên, xu hướng, giới hạn dữ liệu; AI Summary của bác sĩ hiện thêm nhóm "Thay đổi so với giai đoạn trước".
      Báo cáo in tổng quát sẵn có trên trang bệnh nhân giữ nguyên.
- **AC:** thiếu dữ liệu mục nào thì ghi rõ "chưa đủ dữ liệu", không bỏ trống im lặng. ✔ — kể cả khi không đủ
  đường huyết để tóm tắt (nhánh không gọi LLM) vẫn có đủ ba mục.

### 4.3 Dashboard bác sĩ
- [x] `doctor-dashboard.tsx`: khối "Lịch khám sắp tới" (7 ngày; thay khối "Lịch hôm nay", ô thống kê
      "Lịch hôm nay" giữ nguyên) — bệnh nhân, giờ khám, trạng thái brief (đã có / brief mới / đổi giờ — chờ tạo lại /
      lỗi — đang thử lại / chưa có), số điểm cần chú ý; xếp theo mức cảnh báo hiện tại → số điểm cần chú ý →
      giờ khám. API `GET /api/v1/appointments/upcoming?days=7`.
- [x] Trang chi tiết bệnh nhân (tab Tổng quan): card "Brief tái khám" cho lịch gần nhất trong 30 ngày, nút "Tạo ngay/Tạo lại"
      (`POST /api/v1/appointments/{id}/brief`, rate limit như agent) cho lịch xa hơn 48h hoặc job chưa chạy tới.
- **AC:** đo thời gian xác định bệnh nhân ưu tiên trong một phiên test với bác sĩ (mục tiêu PRD ≤ 2 phút). ☐ **Chưa đo** — cần phiên test.

### 4.4 Phản hồi insight của bác sĩ
- [x] Bảng riêng `insight_feedback` (`insight_id`, `patient_id`, `doctor_id`, `verdict` confirmed/rejected, `comment`,
      `insight_version`), unique (insight, bác sĩ), sửa tại chỗ khi bác sĩ đổi ý. API
      `PUT /api/v1/doctor/patients/{patient_id}/insights/{insight_id}/feedback`; danh sách insight trả kèm verdict cho bác sĩ.
      Nút "Phù hợp / Không phù hợp" (+ lý do tùy chọn) trong tab Nhận định AI.
- [x] Insight có verdict mới nhất là bác bỏ: không còn trong `relevant_insights` của bệnh nhân và danh sách insight
      bệnh nhân xem; `upsert_insight` gặp ứng viên khớp (≥ ngưỡng tương đồng) insight đã bác bỏ thì bỏ qua — không
      cập nhật, không tạo mới. Insight đã xác nhận vẫn được reflect cập nhật; UI ghi "nội dung đã cập nhật sau khi
      bạn đánh giá" khi phiên bản mới hơn verdict.
- [x] Thống kê tỷ lệ chấp thuận: `python -m scripts.export_insight_feedback_stats` → `docs/evaluation/insight_feedback_stats.md`
      (tổng và theo `source_kind`, chỉ số đếm). Chưa có số thật — cần bác sĩ dùng trong pilot.
- **AC:** test quyền — chỉ bác sĩ phụ trách được phản hồi. ✔ (bác sĩ khác, bệnh nhân, admin → 403; insight của bệnh
  nhân khác hoặc insight `chat` → 404)

### 4.5 Test
- [x] `tests/test_backend/test_previsit_brief.py` (cửa sổ, lần khám trước, job idempotent/hủy/đổi giờ/dữ liệu mới/lỗi,
      các mục "chưa đủ dữ liệu", API, quyền), `test_insight_feedback.py` (quyền, ẩn với bệnh nhân, reflect bỏ qua,
      tỷ lệ chấp thuận, script xuất); `test_tool_data_fetch_correctness.py` thêm cửa sổ 90 ngày + `previous_period`.

## Definition of Done
- Brief tự có trước lịch hẹn, có mục bữa ăn/thuốc khi dữ liệu sẵn sàng, bác sĩ phản hồi được insight; test xanh. ✔ (code)
- Còn lại: phiên đo ≤ 2 phút với bác sĩ (4.3) và số liệu tỷ lệ chấp thuận thật (4.4).

## Câu hỏi mở
- ~~Khoảng thời gian brief: từ lần khám trước hay cố định 30/90 ngày?~~ Đã chọn: từ lần khám trước, kẹp 7–90 ngày,
  mặc định 30 khi chưa có lần khám — **cần bác sĩ xác nhận**.
- ~~Insight `APPOINTMENT` hiện được kích hoạt từ đâu khi tạo ClinicalNote?~~ Đã kiểm tra: **không từ đâu cả** trong
  backend — tạo ClinicalNote không gọi `reflect(source_kind="appointment")`; chỉ job tuần (`weekly_batch`, `chat`)
  gọi reflect. Insight `appointment` hiện chỉ có từ dữ liệu mock/backfill. Nối trigger này là việc riêng, chưa làm.
- Brief chưa có HbA1c, mục tiêu/mức hoàn thành và trích dẫn như `ai_reports` trong ARCHITECTURE mô tả.
