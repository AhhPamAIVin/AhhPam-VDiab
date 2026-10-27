# Epic 8 — Chatbot bệnh nhân thành agent đa chức năng

**Ưu tiên:** P1 · **Độ lớn:** L · **Phụ thuộc:** 1 (tool bữa ăn), 5 (đo latency) · **Trạng thái:** v1 xong (8.1–8.4), bật mặc định; eval đầy đủ lịch 2026-10-29; 8.6 chưa làm

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
- [x] `codebase/models/agents/tools/agent_tools.py`: `AgentTool(name, description, args_model, handler,
      terminal)`; handler nhận `(state, session, args)` và trả `(state_update, observation)`. Thêm chức năng
      mới = thêm một `AgentTool`.
- [x] Mỗi tool tự giới hạn phạm vi (khoảng ngày ≤ 30, k tài liệu cố định), output gửi lại LLM là bản rút gọn.
      *Observation chỉ là số đếm/tiêu đề; dữ liệu đầy đủ đi thẳng vào state cho `synthesize_qa`.*
- **AC:** test từng tool trên DB test; tool không nhận `patient_id` trong args.

### 8.2 Node `agent_plan` + định tuyến
- [x] Vòng tool calling (`bind_tools`), tối đa N vòng (config), tool song song trong một vòng.
      *`nodes/agent_plan.py`. Mặc định **dừng sau vòng đầu nếu mọi tool chạy được** (1 lời gọi LLM/lượt như
      planner — đo ở 8.5: thêm vòng "XONG" làm mỗi lượt chậm gấp đôi); vòng sau chỉ để thử lại tool lỗi/sai
      tham số. `PATIENT_AGENT_REFLECT=true` cho agent xem kết quả rồi gọi thêm (đa bước, +1 lời gọi).*
- [x] Thay `plan_response` cho **chat của bệnh nhân** khi `PATIENT_AGENT_ENABLED`; bác sĩ và tóm tắt giữ luồng cũ.
- [x] Kết quả gom vào đúng các key state hiện có (`tool_results`, `retrieved_chunks`, `date_range`) để
      `check_data_quality`/`synthesize_qa`/`guardrail` dùng lại; `fetch_data`/`retrieve_knowledge` no-op khi agent
      đã thu thập.
- [x] Mọi key theo lượt được đặt lại tường minh (state sống qua checkpointer giữa các lượt).
      *`agent_gathered`/`agent_tool_calls` do cả hai planner đặt lại mỗi lượt.*
- **AC:** test đồ thị với LLM giả: chào hỏi → câu soạn sẵn; câu hỏi dữ liệu → tool → câu trả lời có số liệu từ
  tool; LLM lỗi → luồng cũ.
  *`tests/test_backend/test_patient_agent.py` (21 case: registry, vòng lặp, fallback, `/agent/chat` đầu-cuối,
  bác sĩ và cờ tắt vẫn đi planner). Fast-path chào hỏi của planner vẫn chạy trước agent. Agent không gọi tool nào
  → về mặc định "lấy hết" của planner, không bao giờ trả lời câu hỏi sức khỏe mà không có dữ liệu.*

### 8.3 Tool v1
- [x] `get_health_records(days | start_date/end_date)`, `get_medications`, `get_my_doctor`,
      `get_appointments` (mới — trước đây trợ lý không đọc được lịch khám), `get_meal_glucose_response`,
      `search_knowledge(query)`.
- [x] Tool điều khiển: `reply_social(kind)`, `request_new_appointment`, `out_of_scope`, `answer_directly(answer)`
      — dùng lại câu soạn sẵn của `plan_response`.
      *Ghi chỉ số qua chat không cần tool riêng: `synthesize_qa` đã trả `record_candidates`, router kiểm chứng
      với nguyên văn tin nhắn rồi mới tạo thẻ xác nhận.*

### 8.4 Streaming
- [x] SSE: sự kiện tiến trình theo tool agent vừa gọi ("đang xem lịch khám…").
      *`get_stream_writer()` trong node + `stream_mode="custom"` ở `/agent/chat/stream`.*

### 8.5 Đánh giá & bật mặc định
- [x] Chạy thật vài câu đại diện qua agent và so với luồng cũ (đúng tool, latency).
      *2026-10-27, proxy hiện tại, 8 câu (dữ liệu giả, tìm tài liệu được stub): agent chọn đúng 8/8 — kể cả
      "Món nào làm đường huyết sau ăn của tôi tăng?" (planner cũ chỉ tra tài liệu, bỏ dữ liệu cá nhân) và "Khi nào
      tôi tái khám?" (planner cũ không có dữ liệu lịch khám). Thời gian bước lập kế hoạch: agent 1.2–9.5s
      (trung vị ~2.8s), planner 1.7–7.4s (trung vị ~4.6s); proxy dao động mạnh (một lần đo trước đó planner mất
      20s) nên đây chưa phải số p95.*
- [ ] Golden scenarios / E2E chạy lại với agent bật — **lịch 2026-10-29** cùng các eval khác.
- [x] Quyết định giá trị mặc định của `PATIENT_AGENT_ENABLED` dựa trên số đo.
      *Bật mặc định (8/8 đúng tool, không chậm hơn). Tắt bằng `PATIENT_AGENT_ENABLED=false` nếu eval 29/10 xấu
      đi. Pytest ghim cờ tắt cho các test hợp đồng của planner (`tests/conftest.py`).*

### 8.6 Tool tiếp theo (sau v1)
- [ ] Gửi ảnh bữa ăn ngay trong chat (`recognize_from_photo` đã là hàm độc lập).
- [ ] Tuân thủ thuốc: đánh dấu đã uống / nhắc (Epic 3).
- [ ] Soạn yêu cầu đặt lịch gửi bác sĩ (hiện chỉ có câu soạn sẵn).
- [ ] Insight dài hạn (đã có đồng ý) trong hỏi đáp, không chỉ trong tóm tắt.
- [x] Báo cáo tuần (Epic 2) như một tool.
      *`get_weekly_report` — báo cáo gần nhất (số liệu + việc nên làm).*

## Definition of Done
- 8.1–8.4 xong, pytest xanh; 8.5 có số đo latency/đúng tool; README/ARCHITECTURE mô tả agent.
