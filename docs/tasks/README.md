# Roadmap task — VDiab

Mỗi epic một file. Mỗi task có checklist, file dự kiến sửa và acceptance criteria (AC).
Đánh dấu `[x]` khi xong; cập nhật cột Trạng thái bên dưới khi chuyển phase.

| # | Epic | File | Ưu tiên | Độ lớn | Phụ thuộc | Trạng thái |
|---|---|---|---|---|---|---|
| 1 | Meal Intelligence — agent phân tích bữa ăn | [01-meal-intelligence.md](01-meal-intelligence.md) | P0 | L | — | Đang làm — 1.1 + 1.6 xong (ảnh không lưu) |
| 2 | Báo cáo tuần cho bệnh nhân | [02-patient-weekly-report.md](02-patient-weekly-report.md) | P1 | M | 1, 3 (cho các mục tương ứng) | Chưa bắt đầu |
| 3 | Tuân thủ thuốc & nhắc nhở thông minh | [03-medication-adherence.md](03-medication-adherence.md) | P2 | M | — | Chưa bắt đầu |
| 4 | Brief tái khám cho bác sĩ | [04-doctor-previsit-brief.md](04-doctor-previsit-brief.md) | P1 | M | 1, 3 (cho các mục tương ứng) | Chưa bắt đầu |
| 5 | Hiệu năng & chất lượng AI | [05-ai-performance-quality.md](05-ai-performance-quality.md) | P1 (song song) | M | — | Gần xong — mọi task đã làm; p95 10.3–11.8s chưa đạt ≤ 10s |
| 6 | Nền tảng bảo mật & tuân thủ | [06-security-compliance.md](06-security-compliance.md) | P0 trước dữ liệu thật | M | — | Đang làm — 6.2 xong |
| 7 | Người thân hỗ trợ (caregiver) | [07-caregiver-access.md](07-caregiver-access.md) | P3 | L | 6 (audit log) | Chưa bắt đầu |

## Thứ tự đề xuất

```
Phase 1:  Epic 1 (1.1 → 1.2 → 1.3 → 1.4)          ║  Epic 5, Epic 6 xen kẽ
Phase 2:  Epic 2 + Epic 4 (dùng dữ liệu Epic 1)    ║  (nợ kỹ thuật, không chặn feature)
Phase 3:  Epic 3, rồi Epic 1 phần ảnh/giọng nói
Phase 4:  Epic 7 — chỉ khi có evidence từ pilot (PRD §5 Could/Future)
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
