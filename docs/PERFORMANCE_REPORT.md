# Performance report

> Trạng thái: AI Consultation đã đo trên benchmark E2E qua HTTP (2026-10-27). Health/auth/CRUD, dashboard và
> AI Summary chưa có benchmark runtime đủ điều kiện. Không suy p95/p99 từ unit test hoặc lý thuyết scalability.

| Workload | Dataset/users | Requests | p50 | p95 | p99 | Error rate | Status |
|---|---:|---:|---:|---:|---:|---:|---|
| Health/auth/CRUD | Chưa đo | — | — | — | — | — | Unverified |
| Doctor dashboard | Chưa đo | — | — | — | — | — | Unverified |
| AI Consultation (`POST /agent/chat`, sau Epic 5) | 38 case golden, 1 bệnh nhân + 1 bác sĩ | 76 (2 lần chạy) | 5.9 / 6.7 s | **10.3 / 11.8 s** | 11.3 / 13.3 s | 0% | Gần đạt, **chưa đạt** p95 ≤ 10 s |
| AI Consultation (trước Epic 5, cùng điều kiện) | như trên | 76 | 8.1 / 8.4 s | 14.1 / 17.2 s | 15.0 / 29.5 s | 0% | Baseline |
| AI Consultation — thời gian tới sự kiện đầu tiên (`/agent/chat/stream`) | 2 câu kiểm tra thủ công | 2 | 0.14–0.22 s | — | — | 0% | Đạt AC ≤ 1 s (mẫu nhỏ) |
| AI Summary | Chưa đo | — | — | — | — | — | Unverified; đọc snapshot đã cache, không gọi LLM theo request |

Mỗi ô "a / b" là lần chạy 1 / lần chạy 2. Error rate = tỷ lệ HTTP khác 200.

## Môi trường đo (2026-10-27)

- Máy: laptop Intel i7-13700H (14 nhân / 20 luồng), RAM 15.8 GB, Windows 11; Python 3.11.9; commit gốc `440f5e8` + thay đổi Epic 5 chưa commit.
- Backend: `uvicorn` 1 worker trên chính máy đó, `RATE_LIMIT_ENABLED=false` chỉ cho tiến trình benchmark (38 case x 2 request/case vượt giới hạn 20/phút).
- Postgres 16 và Qdrant 1.20.0 trong Docker Desktop cùng máy; corpus 1146 chunk (`knowledge_base__bge-m3`).
- Embedding `BAAI/bge-m3` trên CPU, đã warm-up trước khi đo. LLM `gpt-4o-mini` qua proxy OpenAI-compatible (`AI_API_URL`).
- Tải: tuần tự, concurrency 1 (`eval/run_agent_e2e.py`, mỗi case xóa thread trước khi hỏi). Không loại bỏ mẫu chậm.

## Thời gian theo node (log `agent_node_timing`, Epic 5.1)

Tổng hợp từ log backend của 2 lần chạy mỗi phía (`agent_node_timing node=… duration_ms=… intent=…`, không chứa
nội dung câu hỏi/câu trả lời). Ba node tốn thời gian nhất trước khi tối ưu đều là lời gọi LLM:

| Node | Trước: p50 / p95 | Sau: p50 / p95 | Ghi chú |
|---|---:|---:|---|
| `synthesize_dispatch` (LLM) | 3.6 / 6.3 s | 4.5 / 6.1 s | Lời gọi soạn câu trả lời; giờ trả thêm `used_chunk_ids` |
| `verify_citations` (LLM) | **2.7 / 5.0 s** | **0 s** (bỏ qua) | Trích dẫn tự khai báo trong lời gọi soạn + kiểm tra word-overlap (5.4) |
| `plan_response` (LLM) | 2.5 / 5.1 s | 2.9 / 4.1 s | Fast-path cho chào hỏi / tra cứu số liệu rõ ràng bỏ qua lời gọi này (5.4) |
| `retrieve_knowledge` | 0.22 / 0.27 s | 0.20 / 0.25 s | Embed local + Qdrant; giờ chạy song song với `fetch_data` (5.2) |
| các node còn lại | ≈ 0 | ≈ 0 | `classify_intent`, `guardrail_check` là rule-based |

Theo intent, lượt QA (có gọi LLM): p50 10.1 → 7.8 s, p95 16.7 → 11.6 s. HIGH_RISK/JAILBREAK trả lời bằng canned
response tất định trong ~2 ms.

### Vì sao chưa xuống dưới 10 s

- Proxy LLM hiện tại tốn **~1.1–1.4 s cho một request gần như rỗng** (`max_tokens=3`, đo 6 lần); mỗi lượt QA
  còn 2 lời gọi nối tiếp (`plan_response` → `synthesize`), tức ~2.5 s chỉ là độ trễ nền của provider.
- Model nhỏ hơn không giúp qua proxy này: cùng prompt của planner, p50 `gpt-4o-mini` 1.96 s, `gpt-4.1-mini` 2.14 s,
  `gpt-4.1-nano` 2.91 s (5 lần mỗi model). Cấu hình `LLM_NODE_MODELS` vẫn có sẵn khi đổi provider.
- Song song hóa (5.2) chỉ tiết kiệm phần không phải LLM (~0.2 s) vì `fetch_data` và `retrieve_knowledge` vốn nhanh.
- Hướng còn lại: đo lại với endpoint OpenAI trực tiếp/region gần hơn; hoặc gộp quyết định của planner vào lời
  gọi soạn câu trả lời (thay đổi thiết kế lớn — planner hiện là cổng phân loại phạm vi trước khi lấy dữ liệu).
  Streaming (5.3) không rút ngắn tổng thời gian nhưng người dùng thấy phản hồi đầu tiên sau < 0.3 s.

### Embedding local

`embed_query` của bge-m3 trên CPU (đo sơ bộ trước Epic 5.8 hoàn tất): p50 102 ms, p95 141 ms; nạp model 12 s, chạy
nền lúc startup. Toàn bộ `retrieve_knowledge` (embed + Qdrant + BM25) p50 0.20 s trong benchmark trên, nên embedding
local không làm tăng p95 chat.

## Tái tạo

```bash
make db-up && make run                              # backend ở :8100 (đặt RATE_LIMIT_ENABLED=false cho benchmark)
EVAL_PATIENT_TOKEN=... EVAL_DOCTOR_TOKEN=... EVAL_PATIENT_ID=... python eval/run_agent_e2e.py --output <file>.json
# per-node: lọc dòng "agent_node_timing" trong log backend
```

## Protocol tối thiểu

Ghi CPU/RAM/OS, Python, database topology, commit, dataset size, concurrency và warm-up. Chạy workload lặp lại; xuất raw result; đo API thường tách khỏi AI, và AI tách DB/retrieval/provider nếu có trace. Báo cả kết quả xấu, timeout và retry. Capacity chỉ được công bố trong phạm vi workload đã đo.

Target đề xuất để thảo luận, chưa phải SLO đã cam kết: CRUD p95 ≤1s; dashboard p95 ≤2s; AI p95 ≤10s; error rate <1% trong workload pilot. Nếu AI p95 >10s, ghi actual, nguyên nhân dự kiến, hành động và tiêu chí rerun; không loại bỏ sample chậm.
