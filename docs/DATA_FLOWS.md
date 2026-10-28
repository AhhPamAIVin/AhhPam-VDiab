# Luồng dữ liệu ra bên thứ ba

> Task 6.4 (Epic 6). Cập nhật 2026-10-27. Khi thêm một tích hợp gửi dữ liệu ra ngoài, thêm một dòng vào bảng dưới **trong cùng PR**.

## Tóm tắt

| Đích | Dữ liệu gửi đi | Khi nào | Cơ sở / điều kiện | Tắt bằng |
|---|---|---|---|---|
| **LLM API** tương thích OpenAI (`AI_API_URL`, mặc định api.openai.com) | Câu hỏi chat; dữ liệu sức khỏe cần cho câu trả lời (chỉ số, bữa ăn, thuốc, ghi chú khám tóm lược); mô tả bữa ăn (kiểm tra an toàn, phân tích dinh dưỡng); dữ liệu cho tóm tắt AI, brief tái khám, báo cáo tuần; nội dung ghi chú khám (lý do khám, bệnh sử, HbA1c, chẩn đoán, hướng điều trị) kèm số liệu giai đoạn đó để rút insight cho bác sĩ | Mỗi lượt chat, mỗi lần sinh tóm tắt/báo cáo, mỗi bữa ăn ghi nhận, mỗi ghi chú khám mới | Cần để cung cấp tính năng AI mà bệnh nhân/bác sĩ chủ động dùng. **Hợp đồng xử lý dữ liệu (DPA) với nhà cung cấp phải có trước dữ liệu thật** — xem "Việc còn mở" | Không có cờ tắt toàn bộ AI; từng job có cờ riêng (`WEEKLY_REPORTS_ENABLED`, `PREVISIT_BRIEFS_ENABLED`, `APPOINTMENT_REFLECTION_ENABLED`, …) |
| LLM API — **ảnh bữa ăn** | Ảnh do bệnh nhân chụp | Chỉ khi bệnh nhân bấm phân tích ảnh | Đồng ý riêng `meal_photo_ai_consent` (hỏi lần đầu, thu hồi được). Ảnh không lưu, không log, không trace (`get_llm(trace=False)`) | `MEAL_PHOTO_ANALYSIS_ENABLED=false` |
| LLM API — **trí nhớ dài hạn từ chat** | Đoạn chat của chính bệnh nhân (job phản tư hằng tuần) | Thứ Hai 02:00 | Đồng ý `ai_memory_consent` (opt-in, mặc định tắt) | Bệnh nhân tắt trong Hồ sơ |
| **Zalo Bot / Zalo OA** | Tiêu đề + nội dung thông báo: nhắc đo/uống thuốc (tên lịch nhắc, có thể là tên thuốc); **cảnh báo đường huyết có giá trị mg/dL và ngưỡng**; báo cáo tuần chỉ gửi đường dẫn | Theo lịch nhắc / khi có chỉ số vượt ngưỡng / Chủ nhật | Bệnh nhân tự liên kết Zalo bằng mã trong app (opt-in). Không liên kết = không gửi | `ZALO_BOT_ENABLED=false`, `ZALO_NOTIFICATIONS_ENABLED=false`; bệnh nhân tắt từng loại nhắc |
| **Langfuse** (tự host, `LANGFUSE_HOST`) | Trace của mọi lời gọi LLM. **Production: nội dung prompt/câu trả lời bị thay bằng `[redacted]`**; chỉ còn tên bước, thời gian, token, model, id người dùng/phiên dạng UUID | Mỗi lời gọi LLM khi có `LANGFUSE_*_KEY` | Hạ tầng của dự án; vẫn che nội dung để trace không thành bản sao PHI | Bỏ trống `LANGFUSE_PUBLIC_KEY`/`SECRET_KEY`; `LANGFUSE_REDACT_CONTENT` |
| ~~LangSmith~~ | — | — | **Backend từ chối khởi động** nếu `APP_ENV=production` mà `LANGCHAIN_TRACING_V2`/`LANGSMITH_TRACING` bật (LangChain sẽ tự gửi nguyên văn prompt lên cloud LangSmith). Chỉ dùng cho `make eval-*` trên máy dev với dữ liệu tổng hợp | — |

**Không còn gửi ra ngoài:**

- **Embedding**: từ Epic 5.8, văn bản tài liệu và insight bệnh nhân được embed **cục bộ** bằng BAAI/bge-m3 (`codebase/models/rag/embeddings.py`). Không còn lời gọi OpenAI embeddings. Vector nằm trong Qdrant của dự án (production bắt buộc `QDRANT_API_KEY`).
- **Ảnh bữa ăn**: không lưu ở đâu cả (Epic 1, 2026-10-27).

## Kiểm soát trong code

| Kiểm soát | Ở đâu | Test |
|---|---|---|
| Che nội dung trace Langfuse (mặc định bật ở production) | `codebase/models/tracing.py::redact_all`, `redact_content_enabled` | `tests/test_backend/test_phi_controls.py` |
| Chặn LangSmith ở production | `codebase/backend/main.py::check_production_settings` | như trên |
| Lỗi SQL không kèm giá trị tham số | `codebase/backend/db.py` (`hide_parameters=True`) | như trên |
| Không log URL request của httpx (URL Zalo Bot chứa token) | `codebase/backend/main.py` | như trên |
| Log truy xuất RAG chỉ ghi độ dài câu truy vấn | `codebase/models/agents/tools/knowledge_tools.py::_log_retrieval` | `tests/test_models/test_rag_knowledge_tools.py` |
| Lỗi 500 không trả nội dung lỗi về client | `codebase/backend/main.py::unhandled_exception_handler` | — |

## Rà soát log ứng dụng (2026-10-27)

Phạm vi: mọi `logger.*`/`print` trong `codebase/backend` và `codebase/models` (không gồm `stt-service`, `tts-service`, `scripts/` chạy tay), cùng log của thư viện chạy trong tiến trình backend.

| # | Phát hiện | Mức | Xử lý |
|---|---|---|---|
| 1 | `knowledge_tools._log_retrieval` ghi nguyên văn câu truy vấn RAG (dựng từ câu hỏi của bệnh nhân) ở mức INFO | PHI | Chỉ ghi `query_chars` |
| 2 | `content_safety`, `plan_response`, `verify_citations` ghi nguyên output LLM khi không parse được (có thể chứa lời bệnh nhân / câu trả lời có số liệu) | PHI | Chỉ ghi độ dài |
| 3 | `notifications/service.py` ghi `chat_id` Zalo khi gửi lỗi | Định danh | Bỏ `chat_id` |
| 4 | httpx ghi URL mọi request ở mức INFO; URL Zalo Bot chứa bot token | Bí mật | httpx/httpcore ≥ WARNING |
| 5 | Lỗi SQL (in kèm traceback bởi handler 500) chứa giá trị tham số bị bind | PHI | `hide_parameters=True` |
| 6 | Log còn lại: id (UUID), tên bảng/index, tên loại lỗi, mã trạng thái | Không PHI | Giữ |

**Quy tắc cho code mới:** log định danh (UUID) và loại lỗi, không log giá trị sức khỏe, tên, SĐT, email, nội dung chat/ghi chú, output LLM, URL có token. Sự kiện cần truy vết ai-làm-gì → audit log (`codebase/backend/audit/`), không phải log kỹ thuật.

### Checklist ký duyệt

Rà soát lại trước khi có dữ liệu thật và sau mỗi thay đổi lớn ở logging:

- [x] Không `logger.*`/`print` nào in giá trị sức khỏe, tên, SĐT, email, nội dung chat/ghi chú/output LLM (rà 2026-10-27, bảng trên)
- [x] Log thư viện: httpx/httpcore không ghi URL; SQLAlchemy không ghi tham số; `echo=False`
- [x] Production không gửi nội dung tới Langfuse; không thể bật LangSmith
- [ ] Log hạ tầng ngoài repo (reverse proxy, nền tảng deploy) không ghi body request — **owner hạ tầng xác nhận**
- [ ] Ký duyệt: người rà soát ______ · ngày ______ · owner bảo mật ______ · ngày ______

## Việc còn mở

- DPA/điều khoản xử lý dữ liệu với nhà cung cấp LLM và Zalo; vùng lưu trữ dữ liệu của nhà cung cấp (`AI_API_URL` hiện có thể là proxy bên thứ ba — cần xác định ai xử lý dữ liệu).
- Văn bản đồng ý chung (điều khoản sử dụng) cho việc gửi dữ liệu sức khỏe tới LLM ở các tính năng không có công tắc riêng.
- Cảnh báo đường huyết qua Zalo chứa giá trị đo: cân nhắc chỉ gửi "có cảnh báo mới, mở app để xem" nếu bên pháp lý yêu cầu.
