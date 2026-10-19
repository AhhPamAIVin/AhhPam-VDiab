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

Xác nhận provider status, latency và error class; giữ rule-based alerts hoạt động; thông báo AI tạm không khả dụng; không retry vô hạn. Kiểm tra token/cost và LangSmith nếu đã bật. Không làm yếu guardrail để khôi phục nhanh.

## Scheduler không chạy

Kiểm tra startup log, timezone `Asia/Ho_Chi_Minh`, process backend và test scheduler. Khôi phục rồi chạy backfill/dry-run theo script tương ứng; tránh gửi reminder trùng và xác nhận idempotency.

## Zalo lỗi

Kiểm tra feature flag, timeout, token expiry và provider response đã redacted. In-app notification là fallback; không log token/PHI. Xác minh bằng tài khoản test trước khi bật lại.

## Deploy lỗi và rollback

Trước khi deploy thủ công, tag image đang chạy thành `:rollback`; build/up backend+frontend, poll health, nếu failure thì retag `:rollback` → `:latest` và restart. Kiểm tra log rollback và public health; nếu không có image cũ, escalate thay vì xóa/recreate DB. Mọi rollback drill phải ghi SHA trước/sau và downtime.

## Sau sự cố

Lưu timeline, impact, data affected, detection, corrective action và owner; đánh giá nghĩa vụ thông báo. Không đưa PHI vào issue công khai.
