# Operations runbook

| Severity | Ví dụ | Phản hồi mục tiêu | Owner |
|---|---|---|---|
| SEV-1 | lộ dữ liệu, sai quyền, mất dữ liệu, cảnh báo nguy hiểm sai diện rộng | acknowledge 15 phút; cô lập ngay | **TODO on-call/security owner** |
| SEV-2 | app down, DB unavailable, login/AI toàn hệ thống lỗi | 30 phút | **TODO technical owner** |
| SEV-3 | một integration/scheduler lỗi, workaround có sẵn | 4 giờ làm việc | **TODO service owner** |

## App không healthy

Xác nhận public URL và `/health`; xem workflow run; trên VPS kiểm tra `docker compose ps` và log backend/frontend; kiểm tra disk/memory; không restart DB tùy tiện. Nếu deploy mới gây lỗi, dùng workflow rollback/previous image và ghi incident timeline.

## Database gần hết dung lượng

Dừng tác vụ import/backfill không thiết yếu; đo filesystem và volume; không xóa volume/log bằng lệnh rộng; tạo backup đã xác minh nếu còn khả năng; mở rộng dung lượng hoặc retention theo change approval. Xác minh record count và health sau xử lý.

## LLM timeout/rate limit

Xác nhận provider status, latency và error class; giữ rule-based alerts hoạt động; thông báo AI tạm không khả dụng; không retry vô hạn. Kiểm tra token/cost và Langfuse nếu đã bật. Không làm yếu guardrail để khôi phục nhanh.

Log `agent_node_timing node=… duration_ms=… intent=…` (logger `codebase.models.agents.timing`, không chứa nội dung câu hỏi/trả lời) cho biết node nào chậm. Mỗi lượt QA bình thường gọi LLM 2 lần nối tiếp (`plan_response` → `synthesize_dispatch`); proxy hiện tại tốn ~1.1–1.4 s mỗi lần gọi kể cả request rỗng, nên latency tăng đồng loạt ở các node LLM thường là do provider. `LLM_NODE_MODELS` cho phép đổi model riêng một node, nhưng đo 2026-10-27 qua proxy hiện tại model nhỏ hơn (`gpt-4.1-nano`) không nhanh hơn `gpt-4o-mini`.

## Vector store (Qdrant) / embedding

- Qdrant là service riêng (`docker compose ps qdrant`), healthcheck gọi `/readyz`; backend chỉ khởi động khi Qdrant healthy. Dữ liệu ở volume `diab_qdrant` — không xóa volume để "sửa" lỗi; collection dựng lại được bằng `make ingest` (corpus) và `make reindex-insights` (insight từ Postgres, nguồn sự thật).
- Môi trường không phải local: đặt cùng một khóa cho Qdrant server (`QDRANT__SERVICE__API_KEY`, qua docker-compose override hoặc biến môi trường của service) và cho backend (`QDRANT_API_KEY`). Backend với `APP_ENV=production` từ chối khởi động nếu thiếu `QDRANT_API_KEY`, vì `patient_insights__bge-m3` chứa dữ liệu bệnh nhân. Không publish cổng 6333/6334 ra internet.
- Embedding `BAAI/bge-m3` chạy trong tiến trình backend: cần ~2.3 GB đĩa cho cache `HF_HOME` và ~3 GB RAM cho backend lúc chạy (xem `Dockerfile`); model nạp ~12 s lúc startup (warm-up chạy nền). Log lỗi "embedding model warm-up failed" → kiểm tra cache/mạng tải model; khi chưa nạp xong, request RAG đầu tiên sẽ chậm chứ không sai.
- Lỗi "embedding model/dimension mismatch" nghĩa là collection được tạo bằng model khác `EMBEDDING_MODEL` hiện tại: chạy lại ingest + reindex cho model mới, không sửa metadata bằng tay.

## Scheduler không chạy

Kiểm tra startup log, timezone `Asia/Ho_Chi_Minh`, process backend và test scheduler. Khôi phục rồi chạy backfill/dry-run theo script tương ứng; tránh gửi reminder trùng và xác nhận idempotency.

## Zalo lỗi

Kiểm tra feature flag, timeout, token expiry và provider response đã redacted. In-app notification là fallback; không log token/PHI. Xác minh bằng tài khoản test trước khi bật lại.

## Deploy lỗi và rollback

Trước khi deploy thủ công, tag image đang chạy thành `:rollback`; build/up backend+frontend, poll health, nếu failure thì retag `:rollback` → `:latest` và restart. Kiểm tra log rollback và public health; nếu không có image cũ, escalate thay vì xóa/recreate DB. Mọi rollback drill phải ghi SHA trước/sau và downtime.

## Sau sự cố

Lưu timeline, impact, data affected, detection, corrective action và owner; đánh giá nghĩa vụ thông báo. Không đưa PHI vào issue công khai.
