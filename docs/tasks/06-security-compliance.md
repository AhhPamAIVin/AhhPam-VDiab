# Epic 6 — Nền tảng bảo mật & tuân thủ

**Ưu tiên:** P0 **trước khi dùng dữ liệu bệnh nhân thật** · **Độ lớn:** M · **Phụ thuộc:** — · **Trạng thái:** Chưa bắt đầu

## Mục tiêu

Đóng các rủi ro mức **High** trong `docs/THREAT_MODEL.md` và các mục "Not implemented" trong
`docs/IMPLEMENTATION_TRACEABILITY.md`, đáp ứng NFR bảo mật/truy vết/phục hồi của PRD §7.

## Hiện trạng (đã kiểm tra trong code)

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
- [ ] Module `codebase/backend/audit/`: bảng `audit_events` — `id`, `ts`, `actor_id`, `actor_role`,
      `action`, `resource_type`, `resource_id`, `patient_id`, `request_id`, `ip`, `outcome`.
      **Không lưu nội dung PHI** (chỉ định danh).
- [ ] Append-only ở tầng DB: trigger Postgres chặn `UPDATE`/`DELETE` trên `audit_events`
      (tạo trong `db.py::create_db_and_tables`).
- [ ] Ghi sự kiện: đăng nhập (thành công/thất bại), bác sĩ đọc dữ liệu bệnh nhân, tạo/sửa/xóa bản ghi
      sức khỏe, đổi care-plan target, sinh AI summary, thao tác admin, đổi consent.
- [ ] Trang admin xem/lọc audit (chỉ đọc).
- **AC:** test — thử `UPDATE` audit row bị DB từ chối; mỗi hành động trong danh sách sinh đúng 1 event.

### 6.2 Rate limiting
- [x] Middleware/thư viện (vd `slowapi`) theo user (JWT) và IP: `/auth/login` chặt (vd 5/phút/IP),
      `/agent/*` theo user (vd 20/phút), còn lại nới.
      *`codebase/backend/security/rate_limit.py`: sliding window trong tiến trình, không thêm thư viện;
      mỗi worker đếm riêng (nhiều worker → cần store dùng chung).*
- [x] Trả `429` + `Retry-After`; frontend hiển thị thông báo tiếng Việt.
- **AC:** test vượt ngưỡng → 429; giới hạn cấu hình được qua env.

### 6.3 Backup & restore
- [ ] `scripts/backup_db.sh`: `pg_dump` định kỳ (cron/container riêng) mỗi giờ, mã hóa, giữ N bản, đẩy
      ra nơi lưu ngoài máy chủ. Bao gồm volume Chroma và thư mục ảnh bữa ăn (Epic 1.1).
- [ ] `scripts/restore_db.sh` + tài liệu `docs/BACKUP_RESTORE.md`.
- [ ] **Diễn tập khôi phục** lên môi trường trống, ghi thời gian thực tế (RTO) và mốc dữ liệu (RPO).
- **AC:** drill thành công, RPO ≤ 1h, RTO ≤ 4h có số đo thật.

### 6.4 PHI ra bên thứ ba
- [ ] Cờ cấu hình tắt LangSmith tracing ở production hoặc che (redact) dữ liệu bệnh nhân trước khi gửi.
- [ ] Rà soát log ứng dụng: không có giá trị sức khỏe/tên/SĐT trong log.
- [ ] Ghi rõ trong tài liệu dữ liệu nào gửi OpenAI/Zalo và cơ sở đồng ý.
- [ ] Embedding không còn gửi ra ngoài sau task 5.8 (BGE-M3 local) — cập nhật tài liệu luồng dữ liệu tương ứng.
- **AC:** checklist rà soát log ký duyệt; production mặc định không gửi PHI tới LangSmith.

### 6.5 Quyền dữ liệu & retention
- [ ] Bệnh nhân xuất dữ liệu của mình (JSON/CSV) và yêu cầu xóa tài khoản (có quy trình bác sĩ/admin xác nhận).
- [ ] Chính sách retention (ảnh bữa ăn, chat, snapshot AI) — viết trong `docs/` và job dọn dẹp tương ứng.
      *Ảnh bữa ăn không còn được lưu (Epic 1, 2026-10-27) — chỉ còn chat và snapshot AI.*

### 6.6 CI
- [ ] Thêm secret scan (vd `gitleaks`) và dependency audit (`pip-audit`, `npm audit`) vào `.github/workflows/ci.yml`.
- **AC:** CI fail khi commit chứa key dạng `sk-...`.

## Definition of Done
- Các rủi ro High trong THREAT_MODEL có biện pháp + bằng chứng; cập nhật `THREAT_MODEL.md` và
  `IMPLEMENTATION_TRACEABILITY.md`.
