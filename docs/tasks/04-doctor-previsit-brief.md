# Epic 4 — Brief tái khám cho bác sĩ

**Ưu tiên:** P1 · **Độ lớn:** M · **Phụ thuộc:** Epic 1 (mục bữa ăn), Epic 3 (mục thuốc) · **Trạng thái:** Chưa bắt đầu

## Mục tiêu

Trước mỗi lịch tái khám, bác sĩ có sẵn bản tóm tắt giai đoạn từ lần khám trước — **không phải bấm tạo
và chờ** — kèm bữa ăn, tuân thủ thuốc và insight; bác sĩ phản hồi được insight AI để đo chất lượng
(PRD F4/F5: xác định bệnh nhân ưu tiên ≤ 2 phút, ≥ 90% nội dung được bác sĩ đánh giá phù hợp).

## Hiện trạng (đã kiểm tra trong code)

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
- [ ] Job (thêm vào scheduler hiện có) mỗi giờ: tìm appointment trong 24–48h tới chưa có brief →
      `ensure_snapshot(role=doctor, period_days=<từ lần khám trước, tối đa 90>)`.
- [ ] Bảng/trường đánh dấu `appointment_id → snapshot_id` để không sinh lại; sinh lại nếu có dữ liệu mới
      sau thời điểm sinh.
- [ ] Thông báo in-app cho bác sĩ phụ trách (`doctor_patient_assignments`).
- **AC:** lịch hẹn bị hủy/đổi giờ → không sinh/sinh lại đúng; test idempotent.

### 4.2 Mở rộng nội dung brief
- [ ] `synthesize_summary.py` nhánh bác sĩ: mục "Bữa ăn liên quan tăng đường huyết" (Epic 1.4),
      "Tuân thủ thuốc" (Epic 3.1), "Thay đổi kể từ lần khám trước".
      *Mục bữa ăn đã có (Epic 1.4, `_build_meal_response_lines`); còn "Tuân thủ thuốc" và "Thay đổi kể từ lần khám trước".*
- [ ] Template in trong `patient-detail-page.tsx` thêm các mục tương ứng.
      *Mục "Bữa ăn liên quan tăng đường huyết" đã có trong bản in (Epic 1.5).*
- **AC:** thiếu dữ liệu mục nào thì ghi rõ "chưa đủ dữ liệu", không bỏ trống im lặng.

### 4.3 Dashboard bác sĩ
- [ ] `doctor-dashboard.tsx`: khối "Lịch khám sắp tới" — bệnh nhân, giờ khám, trạng thái brief,
      số điểm cần chú ý; sắp xếp theo mức ưu tiên.
- **AC:** đo thời gian xác định bệnh nhân ưu tiên trong một phiên test với bác sĩ (mục tiêu PRD ≤ 2 phút).

### 4.4 Phản hồi insight của bác sĩ
- [ ] Thêm `InsightStatus.CONFIRMED` / `REJECTED` (hoặc bảng `insight_feedback` riêng: `insight_id`,
      `doctor_id`, `verdict`, `comment`) — ưu tiên bảng riêng để không phá logic superseded của
      `upsert_insight.py`.
- [ ] Insight bị bác bỏ không hiện lại cho bệnh nhân và được `reflect` tránh đề xuất lại.
- [ ] Thống kê tỷ lệ chấp thuận → `docs/evaluation/` (đây là "clinician approval rate" còn thiếu).
- **AC:** test quyền — chỉ bác sĩ phụ trách được phản hồi.

### 4.5 Test
- [ ] `tests/test_backend/test_previsit_brief.py`, `test_insight_feedback.py`.

## Definition of Done
- Brief tự có trước lịch hẹn, có mục bữa ăn/thuốc khi dữ liệu sẵn sàng, bác sĩ phản hồi được insight; test xanh.

## Câu hỏi mở
- Khoảng thời gian brief: từ lần khám trước hay cố định 30/90 ngày?
- Insight `APPOINTMENT` hiện được kích hoạt từ đâu khi tạo ClinicalNote? — kiểm tra lại khi bắt đầu 4.4.
