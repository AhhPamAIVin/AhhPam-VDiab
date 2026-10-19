# Epic 5 — Hiệu năng & chất lượng AI

**Ưu tiên:** P1 (làm song song, không chặn feature) · **Độ lớn:** M · **Phụ thuộc:** — · **Trạng thái:** Chưa bắt đầu

## Mục tiêu

- p95 phản hồi AI ≤ 10 giây (PRD §7) — hiện **13.6–16.1s** (`eval/results/report.md`, 2026-08-27).
- Cải thiện các chỉ số eval đang thấp và làm cho eval phản ánh đúng code thật.

## Hiện trạng (đã kiểm tra trong code)

- Graph chat chạy tuần tự (`codebase/models/agents/graph.py`):
  `resolve_context → classify_intent → plan_response → retrieve_knowledge → fetch_data →
  check_data_quality → summarize_history → synthesize → verify_citations → guardrail_check → respond`
  — nhiều node gọi LLM; mọi node dùng chung `settings.model_name = "gpt-4o-mini"`.
- `POST /api/v1/agent/chat` trả kết quả một lần, **không streaming**.
- Eval: golden scenarios — intent 0.767, **citation 0.467**, key-facts 0.686; intent deterministic
  30/37; agent E2E 25/38 (13 fail do citation lệch nguồn).
- **Eval F2 rule engine báo 0/15 "NOT_IMPLEMENTED"** do `eval/harness/deterministic.py:26` hard-code,
  trong khi backend đã có `health/threshold_service.py` + `test_glucose_alert.py`.
- Số liệu baseline lệch nhau: README ghi personal-data faithfulness **3/4 (regression)**,
  `eval/results/report.md` ghi **4/4 (đã fix)**.

## Phạm vi

**In:** đo đạc, tối ưu latency, streaming, cải thiện citation/intent, sửa eval harness.
**Out:** đổi framework agent, fine-tune model.

## Task

### 5.1 Đo đạc trước khi tối ưu
- [ ] Timing từng node (decorator/callback LangGraph), log `node, duration_ms, intent` — **không log
      nội dung câu hỏi/câu trả lời**.
- [ ] Chạy `eval/run_agent_e2e.py` ≥ 2 lần, ghi p50/p95 theo intent vào `docs/PERFORMANCE_REPORT.md`.
- **AC:** biết rõ 3 node tốn thời gian nhất trước khi làm 5.2–5.4.

### 5.2 Chạy song song
- [ ] `retrieve_knowledge` và `fetch_data` độc lập → fan-out/fan-in trong LangGraph.
- [ ] Bỏ qua `retrieve_knowledge` khi intent chỉ cần dữ liệu cá nhân (và ngược lại) nếu chưa có.
- **AC:** test graph hiện có (`tests/test_models/`) vẫn xanh; latency giảm đo được ở 5.1.

### 5.3 Streaming
- [ ] Endpoint `POST /api/v1/agent/chat/stream` (SSE). **Ràng buộc an toàn:** guardrail chạy trên bản
      nháp đầy đủ, nên **không stream token trước guardrail**. Đề xuất: stream *sự kiện tiến trình*
      ("đang tra dữ liệu…", "đang tra tài liệu…") trong lúc chạy, rồi stream câu trả lời đã qua guardrail.
- [ ] Frontend `ai-assistant-page.tsx` / `agent-assistant-page.tsx` hiển thị tiến trình + câu trả lời.
- **AC:** thời gian tới phản hồi đầu tiên ≤ 1s; câu trả lời bị guardrail chặn không bao giờ lộ ra client.

### 5.4 Giảm số lần gọi LLM
- [ ] Fast-path rule-based cho `classify_intent` với câu hỏi rõ ràng (chào hỏi, hỏi số đo hôm nay…).
- [ ] `verify_citations` bỏ qua khi không có nguồn truy hồi.
- [ ] Cho phép cấu hình model riêng theo node (`settings`) — node nhẹ dùng model nhỏ hơn.
- **AC:** eval golden không giảm so với baseline.

### 5.5 Chất lượng
- [ ] Citation 0.467: phân tích 13 case E2E fail (đã biết là "citation lệch nguồn", §2.2 finding #3 trong
      `docs/AI_Eval_Metrics_Plan.md`) → sửa chunking/metadata nguồn hoặc logic chọn nguồn.
- [ ] Intent: xử lý 7 case lệch golden label (sửa code hoặc sửa label có lý do ghi lại).
- **AC:** citation ≥ 0.7, intent ≥ 34/37 (mục tiêu đề xuất — chốt lại).

### 5.6 Sửa eval harness
- [ ] `evaluate_rule_engine` gọi thật `threshold_service` / luồng glucose alert với 15 case trong
      `eval/data/rule_engine_cases.json` thay vì hard-code `NOT_IMPLEMENTED`.
- [ ] Runner cho AI Summary golden (`eval/data/ai_summary_cases.json`, 8 case — hiện chỉ có schema).
- [ ] Đồng bộ bảng baseline trong README với `eval/results/report.md`.
- **AC:** `make eval` phản ánh đúng trạng thái code; README và report khớp số.

## Definition of Done
- p95 ≤ 10s trên benchmark E2E; streaming tiến trình hoạt động; eval F2 chạy code thật; baseline đồng bộ.
