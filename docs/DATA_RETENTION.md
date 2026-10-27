# Lưu trữ, xuất và xóa dữ liệu

> Task 6.5 (Epic 6). Cập nhật 2026-10-27. Thời hạn dưới đây là **mặc định kỹ thuật**, cần đội lâm sàng và pháp lý chốt trước khi có dữ liệu thật. Đổi bằng biến môi trường, không cần sửa code.

## Thời hạn lưu trữ

| Loại dữ liệu | Bảng | Thời hạn | Cơ chế |
|---|---|---|---|
| Chỉ số sức khỏe, bữa ăn, hoạt động, thuốc, ghi chú khám, mục tiêu điều trị, lịch hẹn | `glucose_records`, `blood_pressure_records`, `meal_records`, `meal_analyses`, `activity_records`, `medications`, `medication_notes`, `clinical_notes`, `care_plan_targets`, `appointments` | **Không xóa theo tuổi** — là hồ sơ bệnh án | Chỉ xóa khi tài khoản bị xóa (dưới) |
| Cuộc trò chuyện với trợ lý AI | `chat_sessions`, `agent_checkpoint*`, `pending_record_actions` | **365 ngày** kể từ lượt chat cuối (`RETENTION_CHAT_DAYS`) | Job hằng ngày 03:30. Bản ghi sức khỏe đã lưu từ chat **được giữ**, chỉ mất liên kết tới chat |
| Tóm tắt AI (snapshot) | `agent_summary_snapshots` | **730 ngày** kể từ lúc sinh (`RETENTION_AI_SUMMARY_DAYS`) | Job hằng ngày. Brief tái khám và báo cáo tuần có bản sao nội dung riêng nên **được giữ** |
| Insight dài hạn | `patient_insights` + Qdrant | Đến khi bị thay/hết hiệu lực theo logic reflect, hoặc xóa tài khoản | — |
| Thông báo | `notifications` | Theo tài khoản | — |
| Ảnh bữa ăn | — | **Không lưu** (Epic 1) | — |
| Nhật ký truy vết | `audit_events` | **Không xóa** (append-only ở tầng DB); chỉ chứa định danh, không chứa nội dung | Giữ cả sau khi xóa tài khoản |
| Bản sao lưu | ngoài DB | 72 bản theo giờ trên máy chủ (`BACKUP_KEEP`); bản ngoài máy chủ theo lifecycle của kho lưu | `scripts/backup_db.sh`, [BACKUP_RESTORE.md](BACKUP_RESTORE.md) |

`RETENTION_ENABLED=false` tắt toàn bộ job; đặt một thời hạn bằng `0` để giữ vĩnh viễn loại đó. Mỗi lần job xóa được gì, một sự kiện `retention.purge` (số lượng theo loại) vào nhật ký truy vết.

Code: `codebase/backend/privacy/retention.py`, lịch trong `codebase/backend/insights/scheduler.py`. Test: `tests/test_backend/test_privacy.py`.

## Bệnh nhân xuất dữ liệu

Hồ sơ → Cài đặt → **Dữ liệu của bạn** → *Tải JSON* hoặc *Tải CSV (Excel)* (`GET /api/v1/privacy/me/export?format=json|csv`).

Gồm: thông tin tài khoản (không có mật khẩu băm), mọi bảng có `patient_id` của bệnh nhân (chỉ số, bữa ăn, thuốc, ghi chú khám, mục tiêu điều trị, lịch hẹn, tóm tắt AI, brief, báo cáo tuần, insight, thông báo…) và nguyên văn các cuộc trò chuyện của chính bệnh nhân với trợ lý. Không gồm nhật ký truy vết (chứa IP và hành động của người khác). Danh sách bảng được lấy tự động từ schema nên bảng mới có `patient_id` tự có trong bản xuất. Mỗi lần xuất ghi sự kiện `patient_data.export`.

## Xóa tài khoản

1. **Bệnh nhân gửi yêu cầu** ở cùng mục trên. Có thể hủy khi còn chờ. Tài khoản vẫn dùng bình thường đến khi được duyệt.
2. **Admin xem** ở Quản trị → Nhật ký (khung *Yêu cầu xóa tài khoản chờ duyệt*). Trước khi duyệt, kiểm tra:
   - nghĩa vụ lưu trữ hồ sơ bệnh án theo quy định hiện hành và theo hợp đồng với cơ sở y tế;
   - tranh chấp, khiếu nại hoặc yêu cầu của cơ quan có thẩm quyền đang mở (legal hold);
   - bệnh nhân đã tải dữ liệu về nếu cần.
   Nếu phải giữ: **Từ chối** và ghi lý do (bệnh nhân thấy lý do này — không ghi dữ liệu sức khỏe).
3. **Duyệt**: admin gõ lại email bệnh nhân để xác nhận. Hệ thống xóa trong **một transaction**: tài khoản, mọi dòng tham chiếu tới tài khoản qua khóa ngoại và mọi dòng tham chiếu tới các dòng đó (duyệt theo schema, bảng mới tự được bao phủ), checkpoint chat; sau khi commit xóa vector insight trong Qdrant. Được giữ: yêu cầu xóa (bằng chứng đã thực hiện) và nhật ký truy vết (chỉ định danh).
4. **Bản sao lưu** vẫn chứa dữ liệu đến khi hết hạn giữ (72 giờ trên máy chủ + lifecycle kho ngoài). Nếu khôi phục từ bản sao lưu cũ hơn thời điểm xóa, **phải xóa lại** các tài khoản có yêu cầu `approved` sau mốc bản sao lưu — danh sách lấy từ bảng `account_deletion_requests` của DB hiện hành trước khi khôi phục (xem [BACKUP_RESTORE.md](BACKUP_RESTORE.md#sau-khi-khôi-phục)).

Chỉ xóa được tài khoản **bệnh nhân** qua quy trình này. Code: `codebase/backend/privacy/`. Test: `tests/test_backend/test_privacy.py`.

## Việc còn mở

- Đội lâm sàng/pháp lý chốt các thời hạn trên và nghĩa vụ lưu hồ sơ bệnh án áp dụng cho VDiab.
- Owner của quy trình duyệt xóa (hiện: admin hệ thống) và SLA phản hồi yêu cầu.
