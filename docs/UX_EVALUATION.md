# UX evaluation

> Trạng thái: protocol + code evidence; chưa có usability sessions, Lighthouse/axe artifact hoặc screenshot matrix hoàn chỉnh.

## Journey cần đánh giá

1. Patient nhập glucose và hiểu trạng thái/xu hướng.
2. Patient hỏi AI, nhận biết nguồn và giới hạn y khoa.
3. Doctor tìm patient ưu tiên, review summary và cập nhật care plan.

| Task | Người thử | Hoàn thành | Thời gian | Lỗi/hỗ trợ | Friction | Evidence |
|---|---|---|---|---|---|---|
| Nhập glucose | Chưa test | — | — | — | — | TODO recording/note |
| Hỏi AI có nguồn | Chưa test | — | — | — | — | TODO |
| Xem patient ưu tiên | Chưa test | — | — | — | — | TODO |

Tuyển tối thiểu 3 bệnh nhân và 2 bác sĩ không thuộc đội; dùng dữ liệu demo. Đo completion không trợ giúp, completion có trợ giúp, time-on-task và lỗi; quote phải ẩn danh. Friction chưa sửa được ghi known issue, không xóa khỏi báo cáo.

## Accessibility và state catalog

Chạy keyboard-only, focus order/visibility, labels, headings, zoom 200%, mobile viewport, contrast, Lighthouse và axe trên login, patient dashboard/health/AI, doctor list/detail. Capture loading, empty, validation, 401/403, network error, LLM timeout và successful state. Chưa có artifact nên toàn bộ mục này là `Unverified`.
