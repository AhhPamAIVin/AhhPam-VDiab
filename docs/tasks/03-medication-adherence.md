# Epic 3 — Tuân thủ thuốc & nhắc nhở thông minh

**Ưu tiên:** P2 · **Độ lớn:** M · **Phụ thuộc:** — · **Trạng thái:** Xong phần code (3.1–3.5); định nghĩa chờ bác sĩ chốt; chưa đánh dấu ngày quên trên biểu đồ glucose

## Mục tiêu

1. Đo được **mức tuân thủ dùng thuốc** từ dữ liệu sẵn có và mô tả mối liên quan với đường huyết.
2. **Cá nhân hóa giờ nhắc** theo thói quen thật của bệnh nhân (PRD §5 — mục *Should* chưa làm).

## Hiện trạng (đã kiểm tra trong code)

- `Medication`, `MedicationNote` với `MedicationNoteType.MISSED_DOSE` (`codebase/backend/medications/models.py`).
- `Notification` loại `MEDICATION_REMINDER` có `status` `pending|completed|skipped|snoozed`,
  `scheduled_for`, `responded_at`, `nudge_count` (`codebase/backend/notifications/models.py`).
- `NotificationPreference.time_of_day` cố định; scheduler interval trong `notifications/scheduler.py`.
- **Phát hiện khi làm (2026-10-27):** nhắc uống thuốc **không theo từng thuốc** — mỗi nhắc đo đường huyết đang
  bật (mặc định 6/ngày) kéo theo một `MEDICATION_REMINDER` liệt kê mọi thuốc. Một ngày vì vậy có thể nhiều nhắc
  hơn số liều thật, và bỏ qua nhắc "không đúng giờ uống" sẽ kéo tỷ lệ theo nhắc xuống oan.

## Phạm vi

**In:** chỉ số tuân thủ, tương quan mô tả với glucose, đề xuất giờ nhắc mới (bệnh nhân xác nhận).
**Out:** tự đổi giờ nhắc không hỏi, gợi ý đổi thuốc/liều (PRD Won't), tích hợp hộp thuốc thông minh.

## Task

### 3.1 Chỉ số tuân thủ
- [x] `codebase/backend/medications/adherence.py`: theo thuốc × ngày, từ reminder `completed` /
      `skipped` / `pending` quá hạn + `MISSED_DOSE` note. Định nghĩa (chốt với bác sĩ):
      `adherence = completed / (completed + skipped + missed + expired_pending)`.
      *Vì nhắc không theo từng thuốc (xem trên), có hai số: **theo ngày** (chỉ số chính — ngày "đủ thuốc" khi có
      nhắc thuốc hoàn thành và không có bỏ qua/ghi nhận quên; `day_rate_pct` = đủ / (đủ + có quên)) và **theo
      nhắc** đúng công thức trên (`dose_rate_pct`, số phụ). "Quên" theo từng thuốc chỉ có từ ghi chú `MISSED_DOSE`.
      Cần bác sĩ chốt định nghĩa.*
- [x] Xử lý trùng: một liều vừa `skipped` vừa có `MISSED_DOSE` note chỉ tính một lần.
      *Mỗi ngày: số lần lỡ = max(bỏ qua + quá hạn, số ghi chú quên), chặn bởi số cơ hội.*
- [x] Endpoint `GET /api/v1/medications/adherence?days=` (bệnh nhân / bác sĩ có quyền).
      *`days` 7–90, mặc định 30; qua `ensure_patient_access`.*
- **AC:** test với dữ liệu cố định cho 4 kịch bản (đều đặn, quên rải rác, bỏ cả tuần, không có reminder).
  *`tests/test_backend/test_medication_adherence.py` (4 kịch bản + trùng, chưa trả lời, thuốc khi cần, tương quan,
  quyền, agent, tóm tắt bác sĩ).*

### 3.2 Tương quan tuân thủ ↔ đường huyết
- [x] So sánh glucose trung bình ngày đủ thuốc vs ngày quên (chỉ khi mỗi nhóm có ≥3 ngày).
      *Trung bình của trung bình từng ngày, theo ngày giờ VN (`glucose_by_adherence`).*
- [x] Tool agent `get_medication_adherence(patient_id, days)`; thêm vào `fetch_data` và
      `synthesize_summary` (nhánh bác sĩ).
      *Tool trong `tools/agent_tools.py` (agent chat) + `tools/meal_tools.py::get_medication_adherence`; `fetch_data`:
      SUMMARY luôn có, QA khi câu hỏi có từ khóa thuốc; tóm tắt bác sĩ có dòng tuân thủ, thuốc hay quên, đường huyết
      ngày đủ vs ngày quên (ghi rõ "không phải nguyên nhân").*
- [x] Ngôn ngữ mô tả, không nhân quả; guardrail chặn "bạn nên tăng/giảm liều", "có thể bỏ thuốc".
      *Lỗi mới `dose_change_advice` (tăng/giảm/chỉnh/đổi liều kèm lời khuyên, kể cả không có số; "uống gấp đôi / uống
      bù" tự nó là chỉ dẫn liều); `medication_stop_advice` nhận thêm "có thể". Câu chuyển quyết định cho bác sĩ ("bác
      sĩ có thể chỉnh liều") và câu phủ định ("không uống bù") được cho qua.*
- **AC:** guardrail case mới 100% bị chặn.
  *`eval/data/guardrail_medication_cases.json`: 8 case phải chặn + 5 case an toàn, 13/13 trong
  `tests/test_backend/test_meal_guardrails.py`; cũng nạp vào `eval/respond_agent/guardrail_judge.py`.*

### 3.3 Nhắc nhở thông minh
- [x] Phân tích 14 ngày gần nhất: độ trễ `responded_at - scheduled_for` theo từng `NotificationPreference`.
      *`notifications/smart_reminders.py`. Tính cả nhắc thuốc đi kèm; mỗi ngày lấy lần phản hồi sớm nhất; phản hồi
      trễ hơn 4 giờ không tính là trả lời nhắc đó (`auto_complete_pending` có thể hoàn thành nhắc sáng bằng số đo buổi
      tối).*
- [x] Nếu bệnh nhân **thường xuyên** phản hồi trễ > 30 phút (≥ 5/7 lần), tạo đề xuất giờ mới = trung vị
      giờ phản hồi; bệnh nhân bấm "Đổi" hoặc "Giữ nguyên". Không đề xuất lại trong 14 ngày nếu từ chối.
      *Bảng `reminder_time_suggestions`; làm tròn 5 phút; bỏ qua nếu lệch < 15 phút; không có hai đề xuất chờ cùng
      lúc; 14 ngày chờ áp dụng cho cả đồng ý và từ chối. `GET /notifications/reminder-suggestions` (tính khi đọc, không
      gọi LLM), `POST .../{id}/accept|decline`, chỉ bệnh nhân.*
- [x] Áp dụng cho cả nhắc đo đường huyết (cùng cơ chế preference).
      *Áp dụng cho nhắc đường huyết và huyết áp; nhắc thuốc đi theo giờ nhắc đường huyết nên tự dời theo.*
- **AC:** không bao giờ tự đổi `time_of_day` khi chưa có xác nhận; test kịch bản từ chối.
  *`tests/test_backend/test_smart_reminders.py`. Đồng ý đi qua `update_preference` nên nhắc hôm nay được tạo lại
  đúng giờ mới như khi bệnh nhân tự sửa.*

### 3.4 Giao diện
- [x] `medication-page.tsx`: widget tuân thủ 7/30 ngày theo từng thuốc.
      *`components/medication-adherence-panel.tsx`: % ngày đủ thuốc, dải từng ngày (mỗi trạng thái có icon + chữ),
      thuốc tự ghi nhận quên, đường huyết ngày đủ vs ngày quên.*
- [ ] `patient-detail-page.tsx` (bác sĩ): bảng tuân thủ + ngày quên thuốc trên biểu đồ glucose.
      *Đã có panel tuân thủ (kèm danh sách ngày quên, tỷ lệ theo nhắc) ở tab Thuốc. **Chưa** đánh dấu ngày quên trên
      `GlucoseTrendChart`.*
- [x] Card đề xuất giờ nhắc ở trang thông báo.
      *`components/reminder-suggestions-card.tsx`.*

### 3.5 Test
- [x] `tests/test_backend/test_medication_adherence.py`, `test_smart_reminders.py`.

## Definition of Done
- Chỉ số tuân thủ có ở API, summary bác sĩ và UI; đề xuất giờ nhắc chạy được; test xanh.

## Câu hỏi mở
- Định nghĩa "quên thuốc" khi bệnh nhân không bấm gì (pending hết ngày) — tính là quên hay không rõ?
  *Tạm: "không xác nhận" — không tính là ngày quên, nhưng vẫn vào mẫu số của tỷ lệ theo nhắc (theo công thức).*
- Thuốc dùng "khi cần" (PRN) có loại khỏi chỉ số không? (`frequency` hiện là text tự do)
  *Tạm: nhận diện từ `frequency` ("khi cần", "PRN", "khi đau/sốt"…) và loại ghi chú quên của thuốc đó.*
- Mới: nhắc thuốc nên theo từng thuốc và giờ dùng thật (từ `frequency`) thay vì đi kèm mọi nhắc đường huyết?
  Khi đó tỷ lệ theo liều mới có nghĩa đầy đủ.
