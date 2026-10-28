# Epic 8 — Chatbot bệnh nhân thành agent đa chức năng

**Ưu tiên:** P1 · **Độ lớn:** L · **Phụ thuộc:** 1 (tool bữa ăn), 5 (đo latency) · **Trạng thái:** xong code 8.1–8.6 (phạm vi mới: hỏi đáp + ghi số liệu, không lời khuyên), bật mặc định; golden scenarios lịch 2026-10-29

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

### 8.6 Phạm vi mới: chỉ hỏi đáp + ghi số liệu, không lời khuyên (quyết định 2026-10-27)

Trợ lý bệnh nhân chỉ làm hai việc: **trả lời câu hỏi** (về số liệu của chính bệnh nhân, kiến thức chung có
nguồn) và **ghi số liệu** bệnh nhân kể. **Nghiêm cấm lời khuyên**; được đưa nhận định về số liệu nhưng phải tách
riêng và ghi rõ do AI tạo. Bỏ "soạn yêu cầu đặt lịch gửi bác sĩ" khỏi phạm vi.

- [x] **Ghi số liệu bằng LLM thay cho parser regex.** Tool `draft_records`: LLM đọc câu chữ (không dấu, viết tắt,
      "7 phẩy 2", "130 trên 85", nhiều chỉ số một câu, phân biệt câu hỏi/giả định/kế hoạch/số của người khác);
      code kiểm từng giá trị với đoạn trích nguyên văn (`record_actions.draft_from_agent_proposal`), rồi bệnh nhân
      xác nhận trong thẻ như cũ. Những gì đọc được tất định thì code tự đọc, không phụ thuộc LLM: hoàn cảnh đo,
      loại bữa, giờ ("vừa", "30 phút trước", "7h sáng hôm qua"; "lúc 9h" không rõ sáng/tối → hỏi), đơn vị theo độ
      hợp lý (dưới 20 → mmol/L, từ 40 → mg/dL, 20–40 → hỏi). Chặn tất định: kế hoạch/giả định ("định", "nếu", "sẽ"),
      số của người khác ("chồng tôi", "mẹ tôi"). Trên đường agent, parser regex không còn được dùng; nó chỉ chạy khi
      agent lỗi (fallback).
      *Eval `make eval-record-extraction` (63 câu tiếng Việt, LLM thật, 2026-10-27) — xem bảng dưới.*
- [x] **Không lời khuyên + nhãn AI.** Khối quy tắc trong prompt (chỉ cho bệnh nhân), trường `assessment` riêng →
      `AgentChatResponse.ai_assessment`, giao diện hiển thị trong khung *"Nhận định do AI tạo — không phải lời khuyên
      y khoa"* (lưu cả trong lịch sử chat). Guardrail `has_personal_advice` chặn câu khuyên ("bạn nên/hãy/cần…",
      mệnh lệnh, gạch đầu dòng), vẫn cho phép chuyển bác sĩ/115, hướng dẫn dùng app và kiến thức chung có nguồn;
      quét cả khung nhận định. Agent không còn nhận mục "việc nên làm" của báo cáo tuần.
- [x] **Chỉ số nguy hiểm** (đường huyết < 54 hoặc > 250 mg/dL — đã quy đổi mmol/L; huyết áp ≥ 180/120): câu chuyển
      tuyến cố định (115 / báo bác sĩ), không có văn bản do LLM viết, vẫn có thẻ để lưu chỉ số. Lịch sử chat lưu
      đúng câu chuyển tuyến này.
- [x] **Thống kê do code tính** — `get_health_stats`: số lần đo, trung bình/thấp/cao, theo hoàn cảnh đo, số lần
      trên/dưới một mức, so với khoảng liền trước, tới 90 ngày.
- [x] **Sửa/xóa bản ghi nhập nhầm** — `propose_record_deletion` (+ `draft_records` cho giá trị đúng): chỉ bản ghi
      bệnh nhân tự nhập trong 7 ngày, không phải bản ghi bác sĩ nhập hay đã được bác sĩ đánh giá; kiểm lại khi xác
      nhận; xóa kèm phân tích bữa ăn; vào audit log.
- [x] **Đã uống thuốc** — `mark_medication_taken`: hoàn thành lời nhắc thuốc đang mở hôm nay sau khi bệnh nhân xác
      nhận. Ở mức lời nhắc, không theo từng thuốc (lời nhắc Epic 3 gộp mọi thuốc). Quên thuốc vẫn qua `draft_records`.
- [x] **Đặt nhắc thuốc qua chat** (2026-10-28) — `set_medication_reminder`: "nhắc tôi uống thuốc lúc 8h tối" → thẻ
      "Nhắc uống thuốc lúc 20:00 hằng ngày" → bệnh nhân xác nhận → lời nhắc riêng loại `MEDICATION_REMINDER`
      (`NotificationPreference` tùy chỉnh, đổi/tắt ở trang Thông báo; tin nhắc liệt kê thuốc đang dùng). Giờ do code đọc
      từ đoạn trích (`record_actions.stated_time_of_day`), không lấy từ model; "lúc 8h" không rõ sáng/tối hoặc không có
      giờ → hỏi lại, không soạn thẻ. Đã có nhắc uống thuốc đúng giờ đó (kể cả nhắc đi kèm nhắc đo đường huyết) → báo
      đã có, không tạo trùng; xác nhận lặp lại cũng không tạo trùng. Tên thuốc có trong câu thì vào nhãn. Đổi/tắt lời
      nhắc có sẵn qua chat: không làm (hướng dẫn vào trang Thông báo).
      *Khi làm phát hiện lỗi bộ đọc giờ khi ghi số liệu: bỏ dấu thì "tôi" = "tối", nên "tôi đo lúc 9h được 120" bị ghi
      21:00 thay vì hỏi lại. Đã sửa: câu có dấu chỉ tính "tối/chiều" có dấu; câu không dấu chỉ tính "toi" ngay sau giờ
      ("9h toi") hoặc "toi qua/toi nay". Test: `test_medication_reminder_chat.py`.*
- [x] **Gửi ảnh bữa ăn trong chat** — nút máy ảnh → `POST /agent/meal-photo` (dùng chung nhận diện và đồng ý với trang
      Bữa ăn, ảnh không lưu) → thẻ "lưu bữa ăn" như ghi bằng chữ. Không qua LLM agent.
- [x] **Insight dài hạn** — `get_my_insights`: `weekly_batch` + `chat` (chỉ khi còn đồng ý `ai_memory_consent`), bỏ
      insight bác sĩ đã bác; chỉ được nêu trong khung nhận định AI.
- [x] Báo cáo tuần (Epic 2) như một tool. *`get_weekly_report` — chỉ số liệu (không còn "việc nên làm").*
- [~] ~~Soạn yêu cầu đặt lịch gửi bác sĩ~~ — bỏ khỏi phạm vi.

**Eval ghi số liệu** (`make eval-record-extraction`; 63 câu: có dấu/không dấu, viết tắt, đơn vị, giờ, huyết áp, bữa
ăn, vận động, thuốc, câu ghép, 12 câu *không được ghi*; LLM thật qua proxy hiện tại, 2026-10-27; chấm trên bản nháp
**sau** lớp kiểm chứng của code, tức đúng những gì sẽ hiện thành thẻ):

| | Agent (LLM + kiểm chứng) | Parser regex cũ |
|---|---|---|
| Câu đúng hoàn toàn | **93,7%** (59/63) | 54,0% |
| Ghi nhầm ở câu không được ghi | **0%** (0/12) | 16,7% |
| Recall bản ghi | 92,7% | 52,7% |
| Độ đúng từng trường | 100% | 96,6% |
| Không dấu / viết tắt | 8/8 | 0/8 |

4 câu còn hỏng đều do mô hình: 2 lượt mô hình không gọi tool nào (proxy chậm 22–28 s — trên hệ thống thật luồng
đó rơi về parser cũ), 1 câu vừa kể vừa hỏi bị coi là câu hỏi, 1 câu "quên uống metformin" mô hình gọi nhầm
`mark_medication_taken` — đã chặn tất định trong tool (quên/chưa/không uống không bao giờ thành "đã uống"). Thời gian
bước agent trung vị 6,9 s (cùng proxy). Báo cáo: `eval/results/record_extraction-epic8.json`. Các lần chạy trước
lộ ra lỗi của lớp kiểm chứng (tên hoạt động bị LLM dịch sang tiếng Anh, giờ không kèm đoạn trích, dấu câu cuối đoạn,
"8 phay 1", kế hoạch "định ăn", "chồng tôi…") — đã sửa và có test hồi quy.

Test: `test_agent_recording.py`, `test_no_advice.py`, `test_agent_tools_v2.py`, `test_meal_photo.py` (ảnh trong chat).

## Definition of Done
- 8.1–8.4 xong, pytest xanh; 8.5 có số đo latency/đúng tool; README/ARCHITECTURE mô tả agent.
