# Implementation traceability

> Snapshot tại commit `20f147edd40c06530c0fbfc0cbf851be6174c831`, ngày 2026-09-02. Các dòng Epic 6 (rate limit, audit log, backup/restore, PHI, retention, CI bảo mật) cập nhật 2026-10-27. `Implemented` nghĩa là có code; `Verified` cần test hoặc runtime evidence; `Conditional` phụ thuộc config/external service.

| Capability | Implementation | Test/evidence | Runtime evidence | Status |
|---|---|---|---|---|
| JWT + RBAC | `codebase/backend/auth/`, `auth/dependencies.py` | `tests/test_backend/test_auth.py` | Chưa gắn screenshot | Verified by test |
| Patient/doctor isolation | `patients/access.py`, assignment service | `test_patient_assignments.py`, route/health tests | Chưa gắn screenshot | Verified by test |
| Personalized threshold | `health/threshold_service.py`, care-plan targets | `test_threshold_service.py`, watch classifier artifacts | Local run lỗi vì thiếu sklearn | Partial/environment-dependent |
| Glucose alert không phụ thuộc LLM | `health/service.py`, `notifications/service.py` | `test_glucose_alert.py` | Chưa gắn screenshot | Verified by test |
| AI guardrail | `models/agents/nodes/guardrail.py`, safety patterns | `test_agent_guardrails.py` + eval artifacts | LangSmith URL chưa cung cấp | Verified by test; trace conditional |
| Citation verification | `nodes/verify_citations.py` | `test_verify_citations.py` | Chưa gắn screenshot | Verified by test |
| Scheduler/reminders | notification + insight schedulers | `test_notifications.py`, `test_insights_scheduler.py` | Chưa có scheduler log | Verified by test only |
| Zalo integration | `notifications/zalo.py` và config flags | `test_zalo.py` dùng mock | Credential/runtime chưa xác minh | Conditional |
| LLM tracing (Langfuse; LangSmith chỉ cho eval) | `codebase/models/tracing.py`; production che nội dung, chặn LangSmith (`main.py::check_production_settings`) | `test_phi_controls.py`; eval reports trong `docs/evaluation/` | Trace production chưa đính kèm | Implemented; redaction verified by test |
| SQL injection guard | `security/sql_injection_guard.py` | route/security tests cần chỉ rõ | Chưa có attack log | Implemented; partial evidence |
| Rate limiting | `security/rate_limit.py` (login theo IP, `/agent/*` theo user) | `test_rate_limit.py` | — | Verified by test |
| Audit log append-only | `backend/audit/` (hook ORM + `record()`), trigger DB trong `audit/models.py`, trang admin `components/admin-audit-panel.tsx` | `test_audit_log.py`; `scripts/check_schema_bootstrap.py` trên Postgres (CI) | Postgres 16 + dữ liệu dev 2026-10-27: UPDATE/DELETE/TRUNCATE bị từ chối, trigger còn sau khôi phục ([BACKUP_RESTORE.md](BACKUP_RESTORE.md)) | Verified by test + runtime drill |
| AES-256 at rest | Hạ tầng ngoài repo | — | VPS config chưa đính kèm | Unverified |
| TLS 1.3 | Reverse proxy ngoài repo | — | TLS scan/config chưa đính kèm | Unverified |
| CI | `.github/workflows/ci.yml` | ruff + pytest steps; job `security` (gitleaks, pip-audit), `npm audit` ([SECURITY_CI.md](SECURITY_CI.md)) | Run URL chưa đính kèm | Implemented; runtime evidence missing |
| CD + auto rollback | deploy workflows | workflow logic | Run/rollback drill URL chưa đính kèm | Implemented; drill unverified |
| Backup/restore | `scripts/backup_db.sh`, `restore_db.sh`, `backup_restore_drill.sh` | drill so số dòng từng bảng | Diễn tập local 2026-10-27: 110 MB, khôi phục 4 giây, 266.619 dòng khớp ([BACKUP_RESTORE.md](BACKUP_RESTORE.md)); chưa diễn tập trên production | Verified locally; production drill pending |
| PHI ra bên thứ ba / log | `tracing.py`, `db.py` (`hide_parameters`), log review ([DATA_FLOWS.md](DATA_FLOWS.md)) | `test_phi_controls.py`, `test_rag_knowledge_tools.py` | Checklist rà log chờ ký | Verified by test; sign-off pending |
| Xuất dữ liệu / xóa tài khoản / retention | `backend/privacy/` ([DATA_RETENTION.md](DATA_RETENTION.md)) | `test_privacy.py` | Chưa gắn screenshot | Verified by test; thời hạn chờ lâm sàng/pháp lý chốt |

## Quy tắc cập nhật

Không đổi `Partial/Conditional/Unverified` thành `Verified` nếu chỉ có thiết kế. Evidence hợp lệ là test chạy production path, artifact có timestamp+commit, screenshot/video runtime, workflow URL hoặc cấu hình hạ tầng đã redacted và được owner xác nhận.
