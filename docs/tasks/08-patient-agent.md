# Epic 8 — Chatbot bệnh nhân thành agent đa chức năng

**Ưu tiên:** P1 · **Độ lớn:** L · **Phụ thuộc:** 1 (tool bữa ăn), 5 (đo latency) · **Trạng thái:** Đang làm

## Mục tiêu

Chat bệnh nhân hiện là một **đồ thị cố định**: `plan_response` (một lời gọi LLM trả JSON) bật/tắt hai nhánh
thu thập có sẵn (`fetch_data`, `retrieve_knowledge`), rồi `synthesize_qa` viết câu trả lời. Mỗi chức năng mới
(bữa ăn, lịch hẹn, thuốc…) phải nối tay vào `fetch_data`/`plan_response`, và trợ lý không thể tự quyết định
"cần thêm dữ liệu gì" sau khi đã thấy kết quả đầu tiên.

Mục tiêu: một **agent có tool calling** cho chat bệnh nhân — LLM tự chọn tool (có thể nhiều vòng), mỗi chức
năng mới chỉ là **thêm một tool vào registry** — trong khi giữ nguyên mọi cơ chế an toàn đang có.

## Nguyên tắc (không đổi)

- Cổng an toàn tất định `classify_intent` (jailbreak, đổi liều/chẩn đoán) vẫn chạy **trước** agent.
- Câu trả lời cuối vẫn do `synthesize_qa` viết từ dữ liệu tool trả về → `verify_citations` → `guardrail_check`.
  Agent chỉ **thu thập**; không có đường nào để văn bản tự do của agent đi thẳng tới người dùng ngoài
  `answer_directly` (vẫn qua guardrail, như `direct_answer` của planner).
- `patient_id` lấy từ phiên đăng nhập, **không bao giờ** là tham số tool do LLM điền.
- Ghi dữ liệu chỉ qua bản nháp → bệnh nhân xác nhận (`record_candidates` + `PendingRecordAction` có sẵn).
- Bật/tắt bằng config; lỗi LLM → quay về luồng planner cũ (gather-everything), không từ chối oan.

## Task

### 8.1 Tool registry
- [ ] `codebase/models/agents/tools/agent_tools.py`: `AgentTool(name, description, args_model, handler,
      terminal)`; handler nhận `(state, session, args)` và trả `(state_update, observation)`. Thêm chức năng
      mới = thêm một `AgentTool`.
- [ ] Mỗi tool tự giới hạn phạm vi (khoảng ngày ≤ 30, k tài liệu cố định), output gửi lại LLM là bản rút gọn.
- **AC:** test từng tool trên DB test; tool không nhận `patient_id` trong args.

### 8.2 Node `agent_plan` + định tuyến
- [ ] Vòng tool calling (`bind_tools`), tối đa N vòng (config), tool song song trong một vòng.
- [ ] Thay `plan_response` cho **chat của bệnh nhân** khi `PATIENT_AGENT_ENABLED`; bác sĩ và tóm tắt giữ luồng cũ.
- [ ] Kết quả gom vào đúng các key state hiện có (`tool_results`, `retrieved_chunks`, `date_range`) để
      `check_data_quality`/`synthesize_qa`/`guardrail` dùng lại; `fetch_data`/`retrieve_knowledge` no-op khi agent
      đã thu thập.
- [ ] Mọi key theo lượt được đặt lại tường minh (state sống qua checkpointer giữa các lượt).
- **AC:** test đồ thị với LLM giả: chào hỏi → câu soạn sẵn; câu hỏi dữ liệu → tool → câu trả lời có số liệu từ
  tool; LLM lỗi → luồng cũ.

### 8.3 Tool v1
- [ ] `get_health_records(days | start_date/end_date)`, `get_medications`, `get_my_doctor`,
      `get_appointments` (mới — trước đây trợ lý không đọc được lịch khám), `get_meal_glucose_response`,
      `search_knowledge(query)`.
- [ ] Tool điều khiển: `reply_social(kind)`, `request_new_appointment`, `out_of_scope`, `answer_directly(answer)`
      — dùng lại câu soạn sẵn của `plan_response`.

### 8.4 Streaming
- [ ] SSE: sự kiện tiến trình theo tool agent vừa gọi ("đang xem lịch khám…").

### 8.5 Đánh giá & bật mặc định
- [ ] Chạy thật vài câu đại diện qua agent và so với luồng cũ (đúng tool, latency).
- [ ] Golden scenarios / E2E chạy lại với agent bật — **lịch 2026-10-29** cùng các eval khác.
- [ ] Quyết định giá trị mặc định của `PATIENT_AGENT_ENABLED` dựa trên số đo.

### 8.6 Tool tiếp theo (sau v1)
- [ ] Gửi ảnh bữa ăn ngay trong chat (`recognize_from_photo` đã là hàm độc lập).
- [ ] Tuân thủ thuốc: đánh dấu đã uống / nhắc (Epic 3).
- [ ] Soạn yêu cầu đặt lịch gửi bác sĩ (hiện chỉ có câu soạn sẵn).
- [ ] Insight dài hạn (đã có đồng ý) trong hỏi đáp, không chỉ trong tóm tắt.
- [ ] Báo cáo tuần (Epic 2) như một tool.

## Definition of Done
- 8.1–8.4 xong, pytest xanh; 8.5 có số đo latency/đúng tool; README/ARCHITECTURE mô tả agent.
