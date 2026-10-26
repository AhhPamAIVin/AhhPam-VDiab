# Tổng quan backend VDiab

Tài liệu này giúp thành viên mới nắm nhanh cấu trúc backend trước khi bắt đầu phát triển.

## 1. Bức tranh tổng thể

Backend hiện là một **modular monolith bằng FastAPI**, phục vụ hệ thống quản lý sức khỏe cho ba vai trò:

- Bệnh nhân (`PATIENT`)
- Bác sĩ (`DOCTOR`)
- Quản trị viên (`ADMIN`)

Luồng chính:

```text
Frontend Next.js
      ↓ REST + JWT
FastAPI routers
      ↓
Service / business logic
      ↓
SQLModel
      ↓
PostgreSQL

FastAPI Agent API
      ↓
LangGraph → LLM API (OpenAI-compatible) + Qdrant (embedding bge-m3 local) + PostgreSQL
```

Entry point nằm tại [`codebase/backend/main.py`](codebase/backend/main.py). Khi backend khởi động, nó:

- Tạo các bảng còn thiếu và bổ sung một số cột mới vào database.
- Khởi động scheduler cho notification và patient insight.
- Cấu hình CORS.
- Gắn middleware chống một số mẫu SQL injection.
- Mount toàn bộ router dưới `/api/v1/*`.

## 2. Các module nghiệp vụ

Mỗi module nhìn chung có cấu trúc:

```text
module/
├── router.py    # HTTP endpoint, auth và validation
├── schemas.py   # Pydantic request/response schema
├── service.py   # Nghiệp vụ và truy vấn database
└── models.py    # SQLModel/table
```

Các module chính trong `codebase/backend/`:

| Module | Chức năng |
| --- | --- |
| `auth` | Đăng nhập, JWT, đổi mật khẩu và lấy thông tin user hiện tại |
| `admin` | Tạo user, reset mật khẩu, phân công bác sĩ–bệnh nhân |
| `patients` | Danh sách bệnh nhân của bác sĩ và clinical notes |
| `health` | Đường huyết, huyết áp, bữa ăn và vận động |
| `medications` | Thuốc đang dùng, lịch sử thay đổi/ngừng thuốc và ghi chú |
| `appointments` | Lịch hẹn và trạng thái lịch hẹn |
| `notifications` | Thông báo và cấu hình nhắc nhở |
| `insights` | Nhận xét dài hạn được AI phát hiện cho bệnh nhân |
| `agent` | Chat tư vấn và tổng hợp hồ sơ trước tái khám |

Nên bắt đầu đọc từ [`codebase/backend/health/router.py`](codebase/backend/health/router.py), sau đó lần lượt xem `service.py`, `models.py` và `schemas.py` trong cùng thư mục. Module này thể hiện khá rõ convention chung của backend.

## 3. API hiện có

Các router được mount trong `main.py`:

| Prefix | Nội dung chính |
| --- | --- |
| `/api/v1/auth` | Login, `/me`, đổi mật khẩu |
| `/api/v1/admin` | User và patient assignment |
| `/api/v1/doctor` | Danh sách bệnh nhân, clinical notes và insights |
| `/api/v1/appointments` | CRUD lịch hẹn |
| `/api/v1/health` | Glucose, huyết áp, bữa ăn và vận động |
| `/api/v1/medications` | Thuốc và medication notes |
| `/api/v1/agent` | AI chat và AI summary |
| `/api/v1/notifications` | Notification và preferences |

Swagger UI có tại `http://localhost:8100/docs` khi backend đang chạy.

## 4. Authentication và phân quyền

Backend dùng Bearer JWT. Dependency trung tâm nằm tại [`codebase/backend/auth/dependencies.py`](codebase/backend/auth/dependencies.py):

- `get_current_user`: giải mã JWT và tải user từ database.
- `require_roles(...)`: giới hạn endpoint theo role.
- Token chứa `token_version`; đổi hoặc reset mật khẩu sẽ làm token cũ hết hiệu lực.

Ngoài kiểm tra role, endpoint truy cập hồ sơ còn gọi `ensure_patient_access(...)` trong [`codebase/backend/patients/access.py`](codebase/backend/patients/access.py).

Khi thêm API liên quan đến dữ liệu bệnh nhân, không nên chỉ kiểm tra `DOCTOR`. Cần kiểm tra bác sĩ đó có thực sự được phân công cho bệnh nhân mục tiêu hay không.

## 5. Database

Database chính là PostgreSQL, ORM dùng SQLModel. Cấu hình tập trung tại:

- [`codebase/backend/config.py`](codebase/backend/config.py)
- [`codebase/backend/db.py`](codebase/backend/db.py)

Một số điểm cần lưu ý:

- ID chủ yếu sử dụng UUID.
- Database session hiện là synchronous SQLAlchemy/SQLModel session.
- Test dùng SQLite in-memory thay vì PostgreSQL.
- Startup gọi `SQLModel.metadata.create_all()`.
- Repo có cơ chế tự thêm cột còn thiếu, nhưng đây **không phải migration system đầy đủ**.
- Cơ chế hiện tại không phù hợp với rename/drop cột, thay đổi type hoặc một số thay đổi PostgreSQL enum.
- Chưa có Alembic trong dependency hiện tại.
- PostgreSQL được cấu hình `idle_in_transaction_session_timeout=30s` để hạn chế connection bị giữ quá lâu.

Khi viết service:

- Tránh query hoặc flush từng dòng trong vòng lặp lớn.
- Ưu tiên batch query/update.
- Giữ transaction ngắn.
- Commit tại ranh giới nghiệp vụ rõ ràng.

## 6. AI Agent

Agent nằm ngoài thư mục backend:

```text
codebase/models/agents/
├── graph.py
├── state.py
├── nodes/
├── tools/
└── prompts/
```

API gọi agent nằm tại [`codebase/backend/agent/router.py`](codebase/backend/agent/router.py), với hai endpoint chính:

- `POST /api/v1/agent/chat`
- `POST /api/v1/agent/summary`

Graph chính chạy đại khái theo luồng:

```text
resolve_context
→ classify_intent
→ retrieve_knowledge
→ fetch_data
→ check_data_quality
→ summarize_history
→ synthesize
→ guardrail_check
→ respond
```

Các nguyên tắc an toàn quan trọng:

- LLM không được tự bịa số liệu bệnh nhân; dữ liệu phải lấy qua tool từ database.
- Kiến thức y khoa lấy từ Qdrant/RAG.
- Có guardrail cho chẩn đoán, kê đơn và thay đổi điều trị.
- Cảnh báo theo ngưỡng phải thuộc rule engine, không phụ thuộc LLM.
- AI review flag và cảnh báo y khoa cần được xem là hai loại dữ liệu khác nhau.

## 7. Chạy local

Port thực tế trong code, Dockerfile và Makefile là `8100`. README cũ vẫn có đoạn ghi `8000`, vì vậy nên dùng `8100`.

Khởi động PostgreSQL:

```powershell
docker compose up -d db
```

Kích hoạt virtualenv và chạy backend:

```powershell
.\.venv\Scripts\Activate.ps1
uvicorn codebase.backend.main:app --reload --host 0.0.0.0 --port 8100
```

Các địa chỉ hữu ích:

- Health check: `http://localhost:8100/health`
- Swagger UI: `http://localhost:8100/docs`
- OpenAPI JSON: `http://localhost:8100/openapi.json`

Các biến môi trường tối thiểu cần kiểm tra trong `.env`:

- `DB_HOST`, `DB_PORT`, `DB_USER`, `DB_PASSWORD`, `DB_NAME`
- `JWT_SECRET_KEY`
- `OPENAI_API_KEY` nếu làm agent
- `QDRANT_URL` / `QDRANT_API_KEY`
- `EMBEDDING_MODEL` / `EMBEDDING_DEVICE`
- `CORS_ORIGINS`

Tạo admin đầu tiên:

```powershell
python -m scripts.seed_admin
```

Chạy cả backend và PostgreSQL bằng Docker:

```powershell
docker compose up -d --build
```

## 8. Testing và chất lượng code

Test được chia thành:

- `tests/test_backend`: API, auth, health, thuốc, lịch hẹn, notification và agent.
- `tests/test_models`: LangGraph, checkpoint và RAG.

Fixture trong [`tests/conftest.py`](tests/conftest.py) override database dependency bằng SQLite in-memory và gọi app qua `httpx.AsyncClient`.

Chạy test:

```powershell
.\.venv\Scripts\python.exe -m pytest -q
```

Chạy lint:

```powershell
.\.venv\Scripts\python.exe -m ruff check codebase tests
```

Trong lần kiểm tra khi viết tài liệu này, hơn 130 test đầu đã chạy qua, nhưng tiến trình công cụ không trả summary cuối nên chưa thể xác nhận toàn bộ test suite đang xanh.

## 9. Thứ tự onboarding đề xuất

1. Đọc [`codebase/backend/main.py`](codebase/backend/main.py) để biết app được lắp ghép như thế nào.
2. Đọc [`codebase/backend/config.py`](codebase/backend/config.py) và [`codebase/backend/db.py`](codebase/backend/db.py).
3. Theo một request từ `health/router.py` xuống `service.py`, `schemas.py` và `models.py`.
4. Đọc auth dependency và `patients/access.py` để hiểu RBAC cùng patient-level authorization.
5. Chạy Swagger, đăng nhập và thử một API health.
6. Đọc test của module định sửa trước khi viết code.
7. Chỉ đi sâu vào LangGraph sau khi đã hiểu data model và quyền truy cập bệnh nhân.

## 10. Lưu ý trước khi phát triển

- [`ARCHITECTURE.md`](ARCHITECTURE.md) vẫn ghi trạng thái draft.
- Tài liệu và code có một vài điểm chưa đồng bộ; ví dụ tài liệu kiến trúc nói huyết áp ngoài scope nhưng backend hiện đã có API và model huyết áp.
- README gốc mang nhiều nội dung của starter template và có chỗ chưa phản ánh trạng thái hiện tại của dự án.
- Khi tài liệu mâu thuẫn, nên đối chiếu PRD mới nhất, code hiện tại, test và API mà frontend đang gọi.
- Thay đổi schema không chỉ là sửa SQLModel; cần cân nhắc dữ liệu PostgreSQL đang tồn tại và chiến lược migration.
- Đây là ứng dụng y tế: validation, authorization, auditability và ranh giới giữa rule engine với LLM là các phần không nên bỏ qua.

## 11. Các file nên mở đầu tiên

- [`codebase/backend/main.py`](codebase/backend/main.py)
- [`codebase/backend/config.py`](codebase/backend/config.py)
- [`codebase/backend/db.py`](codebase/backend/db.py)
- [`codebase/backend/auth/dependencies.py`](codebase/backend/auth/dependencies.py)
- [`codebase/backend/patients/access.py`](codebase/backend/patients/access.py)
- [`codebase/backend/health/router.py`](codebase/backend/health/router.py)
- [`codebase/backend/health/service.py`](codebase/backend/health/service.py)
- [`codebase/backend/agent/router.py`](codebase/backend/agent/router.py)
- [`codebase/models/agents/graph.py`](codebase/models/agents/graph.py)
- [`tests/conftest.py`](tests/conftest.py)

