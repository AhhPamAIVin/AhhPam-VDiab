# Roadmap task — VDiab

Mỗi epic một file. Mỗi task có checklist, file dự kiến sửa và acceptance criteria (AC).
Đánh dấu `[x]` khi xong; cập nhật cột Trạng thái bên dưới khi chuyển phase.

| # | Epic | File | Ưu tiên | Độ lớn | Phụ thuộc | Trạng thái |
|---|---|---|---|---|---|---|
| 1 | Meal Intelligence — agent phân tích bữa ăn | [01-meal-intelligence.md](01-meal-intelligence.md) | P0 | L | — | Gần xong — 1.1–1.6, 1.8 xong; 1.7, 1.9 lịch 29/10 |
| 2 | Báo cáo tuần cho bệnh nhân | [02-patient-weekly-report.md](02-patient-weekly-report.md) | P1 | M | 1, 3 (cho các mục tương ứng) | Xong code (cả mục thuốc); còn bác sĩ review báo cáo mẫu |
| 3 | Tuân thủ thuốc & nhắc nhở thông minh | [03-medication-adherence.md](03-medication-adherence.md) | P2 | M | — | Xong code (kể cả ngày quên trên biểu đồ glucose); còn bác sĩ chốt định nghĩa |
| 4 | Brief tái khám cho bác sĩ | [04-doctor-previsit-brief.md](04-doctor-previsit-brief.md) | P1 | M | 1, 3 (cho các mục tương ứng) | Xong code (kể cả reflect khi lưu ghi chú khám, HbA1c/mục tiêu trong brief); còn đo ≤ 2 phút với bác sĩ + số liệu chấp thuận insight thật |
| 5 | Hiệu năng & chất lượng AI | [05-ai-performance-quality.md](05-ai-performance-quality.md) | P1 (song song) | M | — | Gần xong — mọi task đã làm; p95 10.3–11.8s (đo trước Epic 8) chưa đạt ≤ 10s, đo lại cùng E2E lịch 29/10 |
| 6 | Nền tảng bảo mật & tuân thủ | [06-security-compliance.md](06-security-compliance.md) | P0 trước dữ liệu thật | M | — | Xong code (6.1–6.6); còn ký checklist log, chốt thời hạn lưu trữ, cron backup + diễn tập trên production |
| 8 | Chatbot bệnh nhân thành agent đa chức năng | [08-patient-agent.md](08-patient-agent.md) | P1 | L | 1, 5 | Xong code (8.1–8.6, kể cả đặt nhắc thuốc qua chat): chỉ hỏi đáp + ghi số liệu bằng LLM, không lời khuyên; golden scenarios lịch 29/10 |

## Thứ tự đề xuất

```
Phase 1:  Epic 1 (1.1 → 1.2 → 1.3 → 1.4)          ║  Epic 5, Epic 6 xen kẽ
Phase 2:  Epic 2 + Epic 4 (dùng dữ liệu Epic 1)    ║  (nợ kỹ thuật, không chặn feature)
Phase 3:  Epic 3, rồi Epic 1 phần ảnh/giọng nói
Sau pilot: người thân hỗ trợ (caregiver) — đã gỡ khỏi roadmap 2026-10-27, phát triển sau;
          bản kế hoạch cũ còn trong git history (docs/tasks/07-caregiver-access.md)
```

## Quy ước chung cho mọi epic

- **Schema:** dự án không dùng Alembic. `codebase/backend/db.py::create_db_and_tables()` tạo bảng mới
  và chỉ *thêm* cột thiếu — nên cột mới phải nullable hoặc có default; không đổi tên/xóa cột.
- **An toàn y khoa (PRD §7):** AI chỉ tổng hợp/gắn cờ — không chẩn đoán, không kê đơn, không gợi ý
  liều thuốc/insulin. Mọi output mới của LLM phải đi qua `guardrail.py` hoặc có guardrail tương đương + test.
- **Số liệu do code tính, không do LLM bịa:** LLM chỉ diễn giải kết quả tool (nguyên tắc sẵn có của agent).
- **Không ghi PHI vào log kỹ thuật.**
- **Test:** mỗi task backend có test trong `tests/test_backend/` hoặc `tests/test_models/`; mock LLM
  bằng fixture `mock_llm` có sẵn trong `tests/conftest.py`.
- **Quyền truy cập:** endpoint đọc dữ liệu bệnh nhân luôn qua `patients/access.py::ensure_patient_access`.
