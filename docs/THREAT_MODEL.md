# Threat model

## Dữ liệu và trust boundary

Browser/mobile → public reverse proxy (ngoài repo) → Next.js/FastAPI → PostgreSQL/Qdrant; agent gọi LLM API tương thích OpenAI (câu hỏi, dữ liệu sức khỏe cần cho câu trả lời) và có thể gửi trace sang Langfuse tự host; embedding chạy local bằng BAAI/bge-m3 nên văn bản tài liệu và insight bệnh nhân không rời hệ thống để embed (trước Epic 5.8 insight được gửi sang OpenAI embeddings); Qdrant ở môi trường không phải local phải bật `QDRANT_API_KEY` vì collection `patient_insights` chứa dữ liệu bệnh nhân; Zalo là kênh ngoài tùy cấu hình. Dữ liệu nhạy cảm gồm account, assignment, health records, medication, clinical notes, chat/summary và notification.

| Threat | Control hiện có | Evidence | Residual risk |
|---|---|---|---|
| Patient A đọc dữ liệu B / doctor chưa assign | JWT RBAC + `ensure_patient_access` | auth/assignment/route tests | Low–Medium; cần pentest runtime |
| Token theft/stale token | expiry, token version khi đổi/reset password | auth tests | Medium; chưa có refresh/revocation/audit evidence |
| Brute-force login/AI abuse | không có rate limit | Không có | High |
| SQL injection | SQLModel parameterization + SQL injection guard | implementation; evidence test chưa map đầy đủ | Medium |
| Prompt injection/đổi liều | deterministic input guardrail, output/citation checks | guardrail/citation tests và eval | Medium; LLM eval còn regression |
| PHI exfiltration qua LLM/tracing/log | exception handler che internals; config flags | chưa có DLP/log review/vendor evidence | High |
| Audit tampering/không truy vết | audit target trong architecture | chưa có implementation | High |
| Mất database | persistent volume | chưa có backup/restore drill | High |
| Secret leak | `.env` ignored, GitHub Secrets trong workflow | repo/config review; chưa có secret scan report | Medium |
| Transport/at-rest encryption gap | reverse proxy/VPS target | không có config/scan evidence | High/Unverified |

## Ma trận quyền rút gọn

| Hành động | Patient | Doctor | Admin |
|---|:---:|:---:|:---:|
| Xem/sửa dữ liệu sức khỏe của mình | Có | — | theo quyền admin implementation |
| Xem patient được assign | — | Có | Có |
| Xem patient không được assign | Không | Không | Có |
| Quản lý user/assignment | Không | Không | Có |
| Xác nhận care plan/clinical review | Không | Có trong phạm vi assign | cần xác minh route |

## Retention, deletion và incident

Retention/deletion chưa có policy và test hoàn chỉnh: trước dữ liệu thật cần định nghĩa loại dữ liệu, thời hạn, legal hold, export/delete, backup propagation và owner. Báo sự cố qua kênh nội bộ hạn chế quyền (**TODO địa chỉ/owner**); không đăng PHI vào GitHub issue. SEV-1 cô lập access/token/integration, bảo toàn log, xác định phạm vi và đánh giá nghĩa vụ thông báo.
