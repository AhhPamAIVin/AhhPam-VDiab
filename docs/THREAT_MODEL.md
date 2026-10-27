# Threat model

## Dữ liệu và trust boundary

Browser/mobile → public reverse proxy (ngoài repo) → Next.js/FastAPI → PostgreSQL/Qdrant; agent gọi LLM API tương thích OpenAI (câu hỏi, dữ liệu sức khỏe cần cho câu trả lời) và có thể gửi trace sang Langfuse tự host (production: nội dung bị che); ảnh bữa ăn (Epic 1) chỉ được gửi sang LLM API khi bệnh nhân bật `meal_photo_ai_consent`, không ghi ra đĩa/DB, không log và không gửi trace (`get_llm(trace=False)`) — chỉ phần chữ bệnh nhân xác nhận được lưu; embedding chạy local bằng BAAI/bge-m3 nên văn bản tài liệu và insight bệnh nhân không rời hệ thống để embed (trước Epic 5.8 insight được gửi sang OpenAI embeddings); Qdrant ở môi trường không phải local phải bật `QDRANT_API_KEY` vì collection `patient_insights` chứa dữ liệu bệnh nhân; Zalo là kênh ngoài tùy cấu hình. Dữ liệu nhạy cảm gồm account, assignment, health records, medication, clinical notes, chat/summary và notification.

| Threat | Control hiện có | Evidence | Residual risk |
|---|---|---|---|
| Patient A đọc dữ liệu B / doctor chưa assign | JWT RBAC + `ensure_patient_access` | auth/assignment/route tests | Low–Medium; cần pentest runtime |
| Token theft/stale token | expiry, token version khi đổi/reset password; đăng nhập thành công/thất bại vào audit log | auth tests, `test_audit_log.py` | Medium; chưa có refresh/revocation |
| Brute-force login/AI abuse | rate limit theo IP cho `/auth/login`, theo user cho `/agent/*` (`security/rate_limit.py`); đăng nhập thất bại vào audit log | `test_rate_limit.py`, `test_audit_log.py` | Low–Medium; mỗi worker đếm riêng |
| SQL injection | SQLModel parameterization + SQL injection guard | implementation; evidence test chưa map đầy đủ | Medium |
| Prompt injection/đổi liều | deterministic input guardrail, output/citation checks | guardrail/citation tests và eval | Medium; LLM eval còn regression |
| PHI exfiltration qua LLM/tracing/log | Langfuse che nội dung mặc định ở production; backend không khởi động nếu bật LangSmith ở production; rà log + sửa 5 điểm rò rỉ; `hide_parameters` cho lỗi SQL; embedding chạy local | `test_phi_controls.py`, [DATA_FLOWS.md](DATA_FLOWS.md) (checklist rà log) | Medium; LLM API vẫn nhận PHI theo thiết kế — chờ DPA với nhà cung cấp, checklist chờ ký |
| Audit tampering/không truy vết | `audit_events` append-only bằng trigger DB (chặn UPDATE/DELETE/TRUNCATE); ghi đăng nhập, truy cập/bị từ chối dữ liệu bệnh nhân, tạo/sửa/xóa bản ghi sức khỏe, đổi care plan, sinh AI summary, thao tác admin, đổi consent, xuất/xóa dữ liệu; trang admin chỉ đọc | `test_audit_log.py` (SQLite), `check_schema_bootstrap.py` (Postgres, CI) | Low–Medium; role owner của bảng vẫn gỡ được trigger — cần tách role ([BACKUP_RESTORE.md](BACKUP_RESTORE.md#nhật-ký-truy-vết)) |
| Mất database | sao lưu mã hóa mỗi giờ + kho ngoài (`scripts/backup_db.sh`), khôi phục một transaction (`scripts/restore_db.sh`) | diễn tập 2026-10-27: khôi phục 4 giây, mọi bảng khớp số dòng ([BACKUP_RESTORE.md](BACKUP_RESTORE.md)) | Low–Medium; chưa diễn tập trên hạ tầng production, cron/giám sát chưa cài trên máy chủ |
| Secret leak | `.env` ignored, GitHub Secrets; gitleaks trên mọi push/PR (có rule `sk-...`); không log URL chứa token | job `security` trong CI ([SECURITY_CI.md](SECURITY_CI.md)) | Low–Medium; chưa quét toàn bộ lịch sử bằng bộ rule mặc định |
| Transport/at-rest encryption gap | reverse proxy/VPS target | không có config/scan evidence | High/Unverified |
| Lỗ hổng dependency | pip-audit + `npm audit` trong CI | job `security`, job `frontend` | Low; danh sách bỏ qua có lý do ([SECURITY_CI.md](SECURITY_CI.md)) |

## Ma trận quyền rút gọn

| Hành động | Patient | Doctor | Admin |
|---|:---:|:---:|:---:|
| Xem/sửa dữ liệu sức khỏe của mình | Có | — | theo quyền admin implementation |
| Xem patient được assign | — | Có | Có |
| Xem patient không được assign | Không | Không | Có |
| Quản lý user/assignment | Không | Không | Có |
| Xác nhận care plan/clinical review | Không | Có trong phạm vi assign | cần xác minh route |
| Xuất dữ liệu / yêu cầu xóa tài khoản của mình | Có | — | — |
| Duyệt/từ chối xóa tài khoản, xem nhật ký truy vết | Không | Không | Có |

## Retention, deletion và incident

Retention, xuất dữ liệu và xóa tài khoản (bệnh nhân yêu cầu, admin duyệt sau khi kiểm tra legal hold) theo [DATA_RETENTION.md](DATA_RETENTION.md); job dọn chat/snapshot AI hết hạn chạy hằng ngày; test `test_privacy.py`. Còn mở: đội lâm sàng/pháp lý chốt thời hạn và nghĩa vụ lưu hồ sơ bệnh án, owner quy trình duyệt xóa. Báo sự cố qua kênh nội bộ hạn chế quyền (**TODO địa chỉ/owner**); không đăng PHI vào GitHub issue. SEV-1 cô lập access/token/integration, bảo toàn log, xác định phạm vi và đánh giá nghĩa vụ thông báo.
