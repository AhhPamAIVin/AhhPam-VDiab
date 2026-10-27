# Epic 6 — Nền tảng bảo mật & tuân thủ

**Ưu tiên:** P0 **trước khi dùng dữ liệu bệnh nhân thật** · **Độ lớn:** M · **Phụ thuộc:** — · **Trạng thái:** Xong code (6.1–6.6); còn việc ngoài code — ký checklist rà log, chốt thời hạn lưu trữ, cài cron backup + diễn tập trên production

## Mục tiêu

Đóng các rủi ro mức **High** trong `docs/THREAT_MODEL.md` và các mục "Not implemented" trong
`docs/IMPLEMENTATION_TRACEABILITY.md`, đáp ứng NFR bảo mật/truy vết/phục hồi của PRD §7.

## Hiện trạng trước epic (đã kiểm tra trong code)

- **Audit log append-only: chưa có** (THREAT_MODEL: High).
- **Rate limiting: chưa có** — endpoint `/agent/*` gọi LLM có thể bị spam tốn chi phí; `/auth/login` dễ bị brute-force.
- **Backup/restore: chưa từng thử** (PRD yêu cầu RPO ≤ 1h, RTO ≤ 4h).
- PHI có thể đi ra ngoài qua LangSmith tracing (THREAT_MODEL: High).
- Đã có: JWT + token version, `SQLInjectionGuardMiddleware`, CORS, `ensure_patient_access`,
  `User.ai_memory_consent`.
- Chưa có chính sách retention/xóa dữ liệu, chưa có secret scan trong CI.

## Phạm vi

**In:** audit log, rate limit, backup/restore + drill, kiểm soát PHI ra bên thứ ba, quyền dữ liệu, secret scan.
**Out:** chứng nhận chính thức (ISO/HIPAA), WAF, SSO.

## Task

### 6.1 Audit log
- [x] Module `codebase/backend/audit/`: bảng `audit_events` — `id`, `ts`, `actor_id`, `actor_role`,
      `action`, `resource_type`, `resource_id`, `patient_id`, `request_id`, `ip`, `outcome`.
      **Không lưu nội dung PHI** (chỉ định danh).
      *Thêm cột `detail` cho ngữ cảnh không phải PHI (route template của lượt đọc, cờ consent đổi). Không có
      khóa ngoại: sự kiện phải sống lâu hơn tài khoản nó nhắc tới.*
- [x] Append-only ở tầng DB: trigger Postgres chặn `UPDATE`/`DELETE` trên `audit_events`
      (tạo trong `db.py::create_db_and_tables`).
      *Chặn cả `TRUNCATE`; trigger tương đương trên SQLite cho test. Gắn lại mỗi lần khởi động.*
- [x] Ghi sự kiện: đăng nhập (thành công/thất bại), bác sĩ đọc dữ liệu bệnh nhân, tạo/sửa/xóa bản ghi
      sức khỏe, đổi care-plan target, sinh AI summary, thao tác admin, đổi consent.
      *Ghi/sửa/xóa dữ liệu: hook `before_flush` của ORM — mọi đường ghi (route, record action của chat agent,
      scheduler) đều có sự kiện, cùng transaction với dữ liệu. Đọc: `ensure_patient_access` (bác sĩ/admin, một
      sự kiện mỗi request mỗi bệnh nhân; mọi lần bị từ chối). Request id/IP/actor qua middleware
      `AuditContextMiddleware`; response trả header `X-Request-ID`.*
- [x] Trang admin xem/lọc audit (chỉ đọc). *`GET /api/v1/admin/audit-events`, tab Nhật ký trong trang quản trị
      (thay dữ liệu minh họa cũ).*
- **AC:** test — thử `UPDATE` audit row bị DB từ chối; mỗi hành động trong danh sách sinh đúng 1 event.
  *`tests/test_backend/test_audit_log.py` (16 test); trên Postgres thật: `scripts/check_schema_bootstrap.py` (CI).*

### 6.2 Rate limiting
- [x] Middleware/thư viện (vd `slowapi`) theo user (JWT) và IP: `/auth/login` chặt (vd 5/phút/IP),
      `/agent/*` theo user (vd 20/phút), còn lại nới.
      *`codebase/backend/security/rate_limit.py`: sliding window trong tiến trình, không thêm thư viện;
      mỗi worker đếm riêng (nhiều worker → cần store dùng chung).*
- [x] Trả `429` + `Retry-After`; frontend hiển thị thông báo tiếng Việt.
- **AC:** test vượt ngưỡng → 429; giới hạn cấu hình được qua env.

### 6.3 Backup & restore
- [x] `scripts/backup_db.sh`: `pg_dump` định kỳ (cron/container riêng) mỗi giờ, mã hóa, giữ N bản, đẩy
      ra nơi lưu ngoài máy chủ. Bao gồm volume Chroma và thư mục ảnh bữa ăn (Epic 1.1).
      *AES-256 khi stream (không có bản rõ trên đĩa), `BACKUP_KEEP` bản, `rclone` sang `BACKUP_REMOTE`. Vector
      store giờ là Qdrant (snapshot từng collection) thay cho Chroma; ảnh bữa ăn không còn được lưu nên không có
      thư mục cần sao lưu. Lịch: dòng cron trong `docs/BACKUP_RESTORE.md` — **chưa cài trên máy chủ**.*
- [x] `scripts/restore_db.sh` + tài liệu `docs/BACKUP_RESTORE.md`.
- [x] **Diễn tập khôi phục** lên môi trường trống, ghi thời gian thực tế (RTO) và mốc dữ liệu (RPO).
      *`scripts/backup_restore_drill.sh` (`make backup-drill`). 2026-10-27 trên DB dev 110 MB: sao lưu 4s,
      khôi phục 4s, 28 bảng/266.619 dòng và 1.146 điểm Qdrant khớp; lần 2 với schema Epic 6: trigger
      append-only còn sau khôi phục.*
- **AC:** drill thành công, RPO ≤ 1h, RTO ≤ 4h có số đo thật. *Đạt ở môi trường local (đánh giá trong
  `docs/BACKUP_RESTORE.md`); diễn tập trên hạ tầng production còn chờ môi trường staging.*

### 6.4 PHI ra bên thứ ba
- [x] Cờ cấu hình tắt LangSmith tracing ở production hoặc che (redact) dữ liệu bệnh nhân trước khi gửi.
      *Tracing runtime đã là Langfuse: `LANGFUSE_REDACT_CONTENT` (mặc định bật khi `APP_ENV=production`) thay
      input/output/metadata bằng `[redacted]`. Backend không khởi động ở production nếu
      `LANGCHAIN_TRACING_V2`/`LANGSMITH_TRACING` bật.*
- [x] Rà soát log ứng dụng: không có giá trị sức khỏe/tên/SĐT trong log.
      *5 điểm đã sửa (câu truy vấn RAG, output LLM khi lỗi parse, chat_id Zalo, URL httpx chứa bot token, tham
      số SQL trong lỗi) — bảng trong `docs/DATA_FLOWS.md`.*
- [x] Ghi rõ trong tài liệu dữ liệu nào gửi OpenAI/Zalo và cơ sở đồng ý. *`docs/DATA_FLOWS.md`.*
- [x] Embedding không còn gửi ra ngoài sau task 5.8 (BGE-M3 local) — cập nhật tài liệu luồng dữ liệu tương ứng.
- **AC:** checklist rà soát log ký duyệt; production mặc định không gửi PHI tới LangSmith.
  *Phần code đạt (`tests/test_backend/test_phi_controls.py`). **Checklist chờ người rà soát và owner bảo mật ký**
  (`docs/DATA_FLOWS.md`).*

### 6.5 Quyền dữ liệu & retention
- [x] Bệnh nhân xuất dữ liệu của mình (JSON/CSV) và yêu cầu xóa tài khoản (có quy trình bác sĩ/admin xác nhận).
      *`codebase/backend/privacy/`. Xuất: Hồ sơ → Cài đặt → Dữ liệu của bạn (JSON, hoặc zip CSV). Xóa: bệnh nhân gửi
      yêu cầu → admin duyệt (gõ lại email) hoặc từ chối kèm lý do; xóa trong một transaction theo đồ thị khóa ngoại,
      rồi vector Qdrant. Giữ yêu cầu xóa và audit log.*
- [x] Chính sách retention (ảnh bữa ăn, chat, snapshot AI) — viết trong `docs/` và job dọn dẹp tương ứng.
      *Ảnh bữa ăn không còn được lưu (Epic 1, 2026-10-27) — chỉ còn chat và snapshot AI.*
      *`docs/DATA_RETENTION.md`; job hằng ngày `privacy/retention.py`: chat 365 ngày, snapshot AI 730 ngày (env).
      **Thời hạn chờ lâm sàng/pháp lý chốt.***

### 6.6 CI
- [x] Thêm secret scan (vd `gitleaks`) và dependency audit (`pip-audit`, `npm audit`) vào `.github/workflows/ci.yml`.
      *Job `security` (GitHub-hosted): gitleaks trên commit mới + pip-audit trên tập gói `requirements.txt` phân giải
      ra; `npm audit --omit=dev --audit-level=high` trong job `frontend`. Bật được ngay nhờ nâng `next` 16.2.6 →
      16.4.3 + `npm audit fix` (17 → 0 lỗ hổng production). `docs/SECURITY_CI.md`.*
- **AC:** CI fail khi commit chứa key dạng `sk-...`. *Rule `sk-prefixed-api-key` trong `.gitleaks.toml`. Chưa chạy
  được gitleaks trên máy làm task — xác nhận ở lần chạy CI đầu tiên.*

## Definition of Done
- Các rủi ro High trong THREAT_MODEL có biện pháp + bằng chứng; cập nhật `THREAT_MODEL.md` và
  `IMPLEMENTATION_TRACEABILITY.md`. *Đã cập nhật. Còn High: mã hóa đường truyền/lưu trữ (hạ tầng ngoài repo,
  ngoài phạm vi epic).*

## Còn lại (ngoài code)
- Ký checklist rà soát log (`docs/DATA_FLOWS.md`).
- Lâm sàng/pháp lý chốt thời hạn lưu trữ + nghĩa vụ lưu hồ sơ bệnh án; chỉ định owner duyệt xóa tài khoản.
- Cài cron backup + giám sát trên máy chủ; diễn tập trên staging/production, ghi vào `docs/BACKUP_RESTORE.md`.
- Quét toàn bộ lịch sử git bằng gitleaks (`docs/SECURITY_CI.md`).
- DPA với nhà cung cấp LLM/Zalo; tách role DB owner/app để trigger audit không gỡ được từ app.
