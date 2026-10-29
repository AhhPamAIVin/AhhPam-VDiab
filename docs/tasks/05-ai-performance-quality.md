# Epic 5 — Hiệu năng & chất lượng AI

**Ưu tiên:** P1 (làm song song, không chặn feature) · **Độ lớn:** M · **Phụ thuộc:** — · **Trạng thái:** Gần xong — mọi task đã làm; còn p95 10.3–11.8s (mục tiêu ≤ 10s, xem 5.1) — đo lại trên luồng agent chưa làm được 29/10 (thiếu tài khoản đăng nhập trên DB local) và một số FAIL thật của eval AI Summary (xem 5.6)

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

**In:** đo đạc, tối ưu latency, streaming, cải thiện citation/intent, sửa eval harness, chuyển vector store sang Qdrant, thay embedding sang BGE-M3 local.
**Out:** đổi framework agent, fine-tune model.

## Task

### 5.1 Đo đạc trước khi tối ưu
- [x] Timing từng node (decorator/callback LangGraph), log `node, duration_ms, intent` — **không log
      nội dung câu hỏi/câu trả lời**.
      *`codebase/models/agents/timing.py`: `timed_node()` bọc mọi node trong `graph.py`, log
      `agent_node_timing node=… duration_ms=… intent=…`; `collect_node_timings()` cho benchmark in-process.*
- [x] Chạy `eval/run_agent_e2e.py` ≥ 2 lần, ghi p50/p95 theo intent vào `docs/PERFORMANCE_REPORT.md`.
      *2 lần trước + 2 lần sau tối ưu (2026-10-27). 3 node chậm nhất trước tối ưu: `synthesize` p50 3.6s,
      `verify_citations` 2.7s, `plan_response` 2.5s — đều là lời gọi LLM; proxy tốn ~1.1–1.4s/lời gọi kể cả
      request rỗng. Sau Epic 5: p95 **14.1/17.2s → 10.3/11.8s**, p50 8.1/8.4s → 5.9/6.7s. Chưa đạt ≤ 10s:
      còn 2 lời gọi LLM nối tiếp; model nhỏ hơn qua proxy này không nhanh hơn (đo trong PERFORMANCE_REPORT).*
      *2026-10-28: số trên đo trên luồng `plan_response` → `synthesize` (trước Epic 8). Chat bệnh nhân giờ chạy
      `agent_plan` — mặc định vẫn một lời gọi LLM ở bước lập kế hoạch (dừng sau vòng tool thành công đầu tiên) — nên
      p95 phải đo lại trên luồng agent trước khi tối ưu tiếp; gộp vào lần chạy E2E/golden với agent bật lịch 2026-10-29
      (8.5). Không tối ưu thêm khi chưa có số đo mới: phần còn lại chủ yếu là độ trễ nền của provider (~1.1–1.4 s/lời gọi).*
- **AC:** biết rõ 3 node tốn thời gian nhất trước khi làm 5.2–5.4.

### 5.2 Chạy song song
- [x] `retrieve_knowledge` và `fetch_data` độc lập → fan-out/fan-in trong LangGraph.
      *`retrieve_knowledge`, `fetch_data→check_data_quality`, `summarize_history` chạy song song, join ở
      `synthesize_dispatch`. Tiết kiệm đo được nhỏ (~0.2s) vì hai node này vốn nhanh — xem 5.1.*
- [x] Bỏ qua `retrieve_knowledge` khi intent chỉ cần dữ liệu cá nhân (và ngược lại) nếu chưa có.
      *Đã có từ trước: `plan_response` đặt `skip_knowledge_retrieval` / `needs_health_data`.*
- **AC:** test graph hiện có (`tests/test_models/`) vẫn xanh; latency giảm đo được ở 5.1.

### 5.3 Streaming
- [x] Endpoint `POST /api/v1/agent/chat/stream` (SSE). **Ràng buộc an toàn:** guardrail chạy trên bản
      nháp đầy đủ, nên **không stream token trước guardrail**. Đề xuất: stream *sự kiện tiến trình*
      ("đang tra dữ liệu…", "đang tra tài liệu…") trong lúc chạy, rồi stream câu trả lời đã qua guardrail.
      *Sự kiện `progress` → đúng một `final` (cùng `AgentChatResponse` như `/chat`) hoặc `error`. Đo trên server
      thật: sự kiện đầu tiên sau 0.14–0.22s. Test: `tests/test_backend/test_agent_chat_stream.py` (kể cả bản nháp
      bị guardrail chặn không xuất hiện trong bất kỳ sự kiện nào).*
- [x] Frontend `ai-assistant-page.tsx` / `agent-assistant-page.tsx` hiển thị tiến trình + câu trả lời.
      *`agent-assistant-page.tsx` (trang chat thật) gọi `api.agentChatStream` và hiện dòng tiến trình cạnh hiệu ứng
      chờ; `ai-assistant-page.tsx` là trang demo câu trả lời tĩnh, không nối API nên không đổi.*
- **AC:** thời gian tới phản hồi đầu tiên ≤ 1s; câu trả lời bị guardrail chặn không bao giờ lộ ra client.

### 5.4 Giảm số lần gọi LLM
- [x] Fast-path rule-based cho `classify_intent` với câu hỏi rõ ràng (chào hỏi, hỏi số đo hôm nay…).
      *`classify_intent` vốn không gọi LLM; lời gọi cần bỏ là của `plan_response`. Fast-path ở đầu
      `plan_response`: khớp **toàn câu** (chào/cảm ơn/tạm biệt; "đường huyết hôm nay/tuần này…", "tôi đang dùng
      thuốc gì"). "ok"/"vâng" cố ý không vào fast-path (có thể là "có" cho câu hỏi trước của trợ lý).*
- [x] `verify_citations` bỏ qua khi không có nguồn truy hồi.
      *Đã có từ trước. Thêm: lời gọi soạn câu trả lời tự khai báo `used_chunk_ids`; chỉ chunk vừa được khai báo
      vừa qua kiểm tra word-overlap mới được trích dẫn, và `verify_citations` bỏ qua khi đã có self-report
      (bỏ hẳn một lời gọi LLM ~2.7s/lượt; vẫn chạy khi output không parse được).*
- [x] Cho phép cấu hình model riêng theo node (`settings`) — node nhẹ dùng model nhỏ hơn.
      *`LLM_NODE_MODELS="node=model,..."`, `get_llm(node)`. Mặc định không đổi: qua proxy hiện tại
      `gpt-4.1-nano` chậm hơn `gpt-4o-mini` trên prompt planner.*
- **AC:** eval golden không giảm so với baseline.

### 5.5 Chất lượng
- [x] Citation 0.467: phân tích 13 case E2E fail (đã biết là "citation lệch nguồn", §2.2 finding #3 trong
      `docs/AI_Eval_Metrics_Plan.md`) → sửa chunking/metadata nguồn hoặc logic chọn nguồn.
      *Golden citation 0.467 → 0.667 (sau chuyển bge-m3/Qdrant) → **0.933**. Ba nguyên nhân: (1) bộ chấm chỉ so
      tiêu đề trong khi nhãn ghi đơn vị ban hành (VADE, Bộ Y tế) — nguồn giờ mang `organization` tới API/UI và
      golden/E2E chấm theo "tiêu đề | đơn vị" như eval retrieval; (2) word-overlap trích dẫn tài liệu không liên
      quan (IWGDF chăm sóc bàn chân cho câu hỏi khổ qua) — thay bằng self-report ∩ overlap; (3) model đôi khi
      chép "chunk_id=" hoặc gõ sai tiền tố tier — chuẩn hóa id. Guardrail drift không còn chặn câu trả lời trung
      thực "chưa có bằng chứng, hỏi bác sĩ" khi kho tài liệu không có nội dung (điều kiện hẹp: không khai báo
      chunk, không có con số mới ngoài số người dùng nêu, có chuyển bác sĩ).*
- [x] Intent: xử lý 7 case lệch golden label (sửa code hoặc sửa label có lý do ghi lại).
      *Cả 7 là nhãn `OUT_OF_SCOPE` cho yêu cầu đổi thuốc/kê đơn/chẩn đoán (thiết kế: `HIGH_RISK`) và mạo danh
      bác sĩ (`JAILBREAK`) — sửa nhãn, lý do ở `intent_label_note` từng case. Đồng thời sửa code: 3 câu đổi
      liều (021/023/024) lọt cổng tất định vào LLM — thêm regex liều theo lượng, "nên tiêm bao nhiêu đơn vị",
      ngừng thuốc theo tên thuốc; canned response HIGH_RISK tách theo loại (chỉnh liều / ngừng / kê đơn / chẩn
      đoán). Intent tất định 30/37 → **37/37**, golden intent 0.767 → **1.000**.*
- **AC:** citation ≥ 0.7, intent ≥ 34/37 (mục tiêu đề xuất — chốt lại).

### 5.6 Sửa eval harness
- [x] `evaluate_rule_engine` gọi thật `threshold_service` / luồng glucose alert với 15 case trong
      `eval/data/rule_engine_cases.json` thay vì hard-code `NOT_IMPLEMENTED`.
      *12/15 qua `create_glucose_record`. 3 case cần mục tiêu theo bối cảnh bữa ăn mà care plan chưa có.
      Eval này phát hiện lỗi mmol/L bị so như mg/dL (đã sửa).*
- [x] Runner cho AI Summary golden (`eval/data/ai_summary_cases.json`, 8 case — hiện chỉ có schema).
      *`eval/respond_agent/ai_summary_golden.py` (`make eval-ai-summary-golden`), kết quả
      `eval/results/ai_summary_golden.json`; mỗi kỳ vọng là PASS/FAIL/NOT_SUPPORTED, các FAIL thật ghi ở
      `eval/results/report.md`. TC-SUM-004 SKIPPED: thứ tự ưu tiên nằm ở frontend. Golden scenarios và
      trajectory có thêm `--local` (LangSmith key cũ trả 403).*
- [x] Đồng bộ bảng baseline trong README với `eval/results/report.md`.
- [x] Thêm: eval retrieval tier 2 (`eval/harness/retrieval_tier2.py`, 24 câu, nhãn theo file + cụm bằng chứng),
      cổng regression trong CI (`eval/baseline.json`).
- **AC:** `make eval` phản ánh đúng trạng thái code; README và report khớp số.

### 5.7 Chuyển vector store từ Chroma sang Qdrant
Làm trước 5.8 để chỉ phải re-index một lần (trên Qdrant, bằng model mới).

Hiện trạng: đã có adapter `QdrantChromaClient` / `QdrantCollection` trong `codebase/models/rag/vector_store.py`
(giả lập đúng tập API Chroma mà `ingest.py`, `knowledge_tools.py`, `insight_tools.py`, `upsert_insight.py` dùng),
Qdrant `v1.20.0` đã có trong `docker-compose.yml` (`make db-up`), `.env.example` đặt `VECTOR_STORE=qdrant`.
Nhưng `config.py:53` mặc định vẫn `vector_store = "chroma"`, `tests/conftest.py:10` ép `VECTOR_STORE=chroma`,
test RAG/reflect dùng `chromadb.EphemeralClient()`, và `chromadb` vẫn nằm trong `requirements.txt`.
Có 2 collection: `knowledge_base` (corpus) và `patient_insights` (insight bệnh nhân — PHI).

- [x] `config.py`: mặc định `vector_store = "qdrant"`; bỏ `chroma_persist_dir`, bỏ nhánh Chroma trong
      `vector_store.py::get_vector_client()` (bỏ `Literal["chroma", "qdrant"]` hoặc giữ một giá trị).
- [x] `QdrantChromaClient` nhận thêm `location=":memory:"` để test chạy không cần server.
- [x] Test: `tests/conftest.py` bỏ `VECTOR_STORE=chroma`; thay `chromadb.EphemeralClient()` trong
      `test_rag_ingest.py`, `test_rag_knowledge_tools.py`, `test_reflect.py` bằng `QdrantChromaClient(location=":memory:")`
      (mỗi test một client nên hết vấn đề "EphemeralClient không cô lập" đang được ghi chú trong các test đó);
      giữ `test_vector_store.py`.
- [x] Payload index cho các trường lọc thường dùng (`patient_id`, `status`, … của `patient_insights`) khi tạo collection.
- [x] Đổ dữ liệu: `knowledge_base` → chạy lại ingest (`python -m codebase.models.rag.ingest`);
      `patient_insights` → script backfill embed lại mọi `PatientInsight` ACTIVE từ Postgres (không copy vector
      từ Chroma — số chiều sẽ đổi ở 5.8). Script idempotent, chạy được trong `scripts/bootstrap_db.py`.
      *`knowledge_base__bge-m3` trên Qdrant server: 1146 chunk (manifest 2026-10-26). DB dev có 0 insight ACTIVE
      nên reindex là no-op; đã chạy reflect create → update trên Qdrant server thật (TC-SUM-005/006): lần hai tìm
      được insight cũ và cập nhật v1 → v2, rồi xóa collection test.*
- [x] Gỡ Chroma: `chromadb` khỏi `requirements.txt`; `mkdir -p data/chroma` trong `scripts/setup.sh`;
      ghi chú chown trong `scripts/docker-entrypoint.sh`; `.gitignore` / `.dockerignore`; xóa `data/chroma/`.
      *Đã xóa `data/chroma/` (baseline OpenAI đã ghi: recall 0.679, `eval/results/retrieval_recall_1787794950.json`).*
- [x] Deploy: Qdrant có volume bền (`diab_qdrant`), đặt `QDRANT_API_KEY` ở môi trường không phải local;
      backend chờ Qdrant sẵn sàng (healthcheck) trước khi nhận request.
      *Healthcheck `/readyz` qua bash `/dev/tcp` (image không có curl), backend `depends_on: service_healthy`;
      backend từ chối khởi động ở `APP_ENV=production` khi thiếu `QDRANT_API_KEY`; hướng dẫn ở OPERATIONS_RUNBOOK.*
- [x] Cập nhật chữ "Chroma" trong docstring/comment (`ingest.py` "chroma upsert helpers", `upsert_insight.py`,
      `insight_tools.py`), `Makefile` (mục eval), `eval/respond_agent/*.py`, `README.md`, `ARCHITECTURE.md`,
      `docs/architecture_diagram.md`, `docs/THREAT_MODEL.md`, `docs/AI_Eval_Metrics_Plan.md`,
      `docs/AI_Summary_Respond_System_Design.md`, `docs/MVP_Diabetes_Health_Management_System.md`.
      *`grep -ri chroma codebase tests scripts requirements.txt` rỗng (`QdrantChromaClient` → `QdrantVectorClient`).
      README/ARCHITECTURE/docs mô tả hệ thống hiện tại đã sửa; tài liệu ghi lịch sử (AI_Eval_Metrics_Plan,
      Design doc) giữ nguyên, Design doc có ghi chú cập nhật ở đầu.*
- **AC:** `grep -ri chroma codebase tests scripts requirements.txt` không còn kết quả; toàn bộ test xanh không cần
  Qdrant server; `make db-up` + ingest + backfill → chat trả lời có trích dẫn và reflect tìm được insight cũ;
  `make eval-retrieval-recall` chạy trên Qdrant cho số liệu bằng baseline Chroma (cùng model embedding).

### 5.8 Thay model embedding sang BGE-M3 chạy local
Hiện trạng: `EMBEDDING_MODEL = "text-embedding-3-small"` hard-code ở `codebase/models/rag/chunking.py:32`,
`EMBEDDING_DIMENSIONS = 1536` ở `rag/ingest.py:60`, client cố định `OpenAIEmbeddings` trong
`ingest.py::get_embeddings_client()` — dùng chung cho `knowledge_tools.py`, `insight_tools.py`, `upsert_insight.py`.
Nghĩa là **text insight của bệnh nhân đang được gửi sang OpenAI để embed** (liên quan 6.4).
Đếm token chunk bằng `tiktoken cl100k_base` (tokenizer của OpenAI).

Model đích: **`BAAI/bge-m3`** — đa ngôn ngữ (có tiếng Việt), dense 1024 chiều, nhận tới 8192 token,
so sánh cosine; không cần tiền tố instruction cho query như bge v1.5. Chạy trong tiến trình bằng
`sentence-transformers` (qua `langchain_huggingface.HuggingFaceEmbeddings`, `normalize_embeddings=True`), CPU mặc định,
dùng GPU nếu có.

- [x] Đo baseline trước khi đổi: `make eval-retrieval-recall` (recall@k) và citation của `make eval-golden-scenarios`
      với `text-embedding-3-small` trên Qdrant (sau 5.7). Lưu số vào `docs/PERFORMANCE_REPORT.md`.
      *Baseline thực tế đo trên Chroma + `text-embedding-3-small` (cùng bộ câu hỏi): recall@6 0.679, citation 0.467
      (2026-08-27) — không đo lại OpenAI trên Qdrant vì 5.7/5.8 làm cùng đợt. Sau: recall@6 0.830, citation 0.933.*
- [x] Settings mới (`config.py` + `.env.example`): `EMBEDDING_MODEL=BAAI/bge-m3`, `EMBEDDING_DEVICE=cpu`,
      `EMBEDDING_BATCH_SIZE`, `HF_HOME` (thư mục cache model). Bỏ hằng số hard-code trong `chunking.py` / `ingest.py`;
      số chiều lấy từ model (`get_sentence_embedding_dimension()`), không ghi tay.
- [x] `get_embeddings_client()` trả client BGE, nạp model **một lần** (`lru_cache`) — không nạp lại mỗi request;
      giữ nguyên interface `EmbeddingsClient` (`embed_documents`, `embed_query`) để các nơi gọi không đổi.
      Thêm `sentence-transformers`, `langchain-huggingface` vào `requirements.txt`; `langchain_openai` vẫn cần cho LLM.
      *Dùng thẳng `sentence-transformers` (`codebase/models/rag/embeddings.py`), không cần `langchain-huggingface`.*
- [x] Docker: tải sẵn model khi build image hoặc mount volume cache `HF_HOME` (~2.3 GB) để container không tải
      lại mỗi lần khởi động; ghi rõ RAM tối thiểu. Warm-up model lúc startup để request đầu không chậm.
- [x] Tokenizer chunking: đếm token bằng tokenizer của bge-m3 (XLM-RoBERTa, `AutoTokenizer`) thay cho `cl100k_base`;
      xem lại `TRACK_A_MAX_TOKENS=800`, `TRACK_B_MAX_TOKENS=600` và chạy lại `test_rag_chunking.py`.
      *Đo thực tế trên corpus: bge-m3 ra **ít** token hơn (309k so với 517k của cl100k, ~3,5 ký tự/token), không
      phải nhiều hơn. Chunker viết lại thành v2 (một chunker theo cấu trúc cho mọi file, bỏ Track A/B) với
      ngân sách 384/512 token — xem docstring `chunking.py`.*
- [x] Ghi `embedding_model` + `embedding_dimensions` vào manifest và metadata collection; khi query/upsert mà model đang
      cấu hình khác model của collection → báo lỗi rõ ràng, không trả kết quả sai. Tên collection gắn model
      (vd. `knowledge_base__bge-m3`) để đổi qua lại không đè dữ liệu cũ.
- [x] Re-index cả `knowledge_base` (ingest) và `patient_insights` (script backfill ở 5.7). *Xem 5.7.*
- [x] Rà lại ngưỡng điểm cosine / trọng số hybrid trong `agents/tools/knowledge_tools.py` và ngưỡng "insight trùng"
      trong `insight_tools.py::find_similar_insights` — thang điểm của bge-m3 khác OpenAI.
      *Ngưỡng insight 0.85 vẫn tách đúng fixture `expected_reflections.json` với bge-m3 (UPDATE 0.897, CREATE 0.677).
      `TIER1_TRUST_BOOST` quét 0/0.01/0.03/0.05/0.08 trên 3 eval retrieval: boost cao tăng recall fact card
      (tier 1) nhưng giảm hit@1 tài liệu dài (tier 2) — 0.03 giữ tier2 hit@3 0.917 và source MRR 0.787 cao nhất,
      giữ nguyên.*
- [x] Test: fake embeddings client trong test giữ nguyên (không tải model khi chạy unit test); thêm 1 test
      đánh dấu `slow` chạy model thật trên vài câu tiếng Việt.
- [x] Đo latency `embed_query` trên CPU (p50/p95) và đưa vào 5.1 — embed local phải không làm tăng p95 chat.
      *Đo sơ bộ (laptop, CPU, torch 2.14): p50 102 ms, p95 141 ms; nạp model 12 s (có warm-up lúc startup).
      Trong benchmark E2E cả node `retrieve_knowledge` p50 0.20s — không phải điểm nghẽn.*
- [x] Cập nhật `ARCHITECTURE.md` §Embeddings (bỏ "OpenAI embedding model"), bảng phụ thuộc bên ngoài,
      và tài liệu luồng dữ liệu ở 6.4: insight bệnh nhân không còn gửi ra ngoài để embed.
      *ARCHITECTURE §5/§6, THREAT_MODEL (trust boundary).*
- **AC:** recall@k và citation không thấp hơn baseline đo ở bước đầu; không còn lời gọi OpenAI embeddings nào
  (`grep -r OpenAIEmbeddings codebase` rỗng); chạy được offline sau khi đã cache model; p95 chat không tăng;
  toàn bộ test xanh.

## Definition of Done
- p95 ≤ 10s trên benchmark E2E; streaming tiến trình hoạt động; eval F2 chạy code thật; baseline đồng bộ;
  RAG chạy trên Qdrant với embedding BGE-M3 local, retrieval không kém baseline.
