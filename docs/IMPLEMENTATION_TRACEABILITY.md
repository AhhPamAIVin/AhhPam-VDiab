# Implementation traceability

> Snapshot tại commit `20f147edd40c06530c0fbfc0cbf851be6174c831`, ngày 2026-09-02. `Implemented` nghĩa là có code; `Verified` cần test hoặc runtime evidence; `Conditional` phụ thuộc config/external service.

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
| LangSmith tracing | LangChain env vars + eval runners | eval reports trong `docs/evaluation/` | Shared trace URL chưa có | Conditional/unverified runtime |
| SQL injection guard | `security/sql_injection_guard.py` | route/security tests cần chỉ rõ | Chưa có attack log | Implemented; partial evidence |
| Rate limiting | — | — | — | Not implemented |
| Audit log append-only | — (architecture target only) | — | — | Not implemented |
| AES-256 at rest | Hạ tầng ngoài repo | — | VPS config chưa đính kèm | Unverified |
| TLS 1.3 | Reverse proxy ngoài repo | — | TLS scan/config chưa đính kèm | Unverified |
| CI | `.github/workflows/ci.yml` | ruff + pytest steps | Run URL chưa đính kèm | Implemented; runtime evidence missing |
| CD + auto rollback | deploy workflows | workflow logic | Run/rollback drill URL chưa đính kèm | Implemented; drill unverified |
| Backup/restore | — | — | — | Planned/not verified |

## Quy tắc cập nhật

Không đổi `Partial/Conditional/Unverified` thành `Verified` nếu chỉ có thiết kế. Evidence hợp lệ là test chạy production path, artifact có timestamp+commit, screenshot/video runtime, workflow URL hoặc cấu hình hạ tầng đã redacted và được owner xác nhận.
