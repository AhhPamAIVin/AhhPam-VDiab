# Epic 7 — Người thân hỗ trợ (caregiver)

**Ưu tiên:** P3 · **Độ lớn:** L · **Phụ thuộc:** Epic 6.1 (audit log) · **Trạng thái:** Chưa bắt đầu

> PRD §5 xếp mục này vào **Could/Future**: "chỉ xem xét sau khi có evidence từ pilot". Chỉ bắt đầu khi
> pilot cho thấy nhu cầu (vd bệnh nhân lớn tuổi cần con cháu theo dõi giúp).

## Mục tiêu

Bệnh nhân **chủ động ủy quyền** cho người thân xem dữ liệu và nhận cảnh báo nguy hiểm, có phạm vi,
thời hạn và thu hồi được bất cứ lúc nào.

## Hiện trạng (đã kiểm tra trong code)

- `UserRole` chỉ có `admin|doctor|patient` (`codebase/backend/auth/models.py`).
- Quyền xem dữ liệu bệnh nhân tập trung ở `patients/access.py::ensure_patient_access`.
- Đã có pattern mã liên kết có hạn dùng (`ZaloLinkCode`, `notifications/service.py::create_link_code`)
  — tái dùng cho lời mời caregiver.
- Cảnh báo glucose nguy hiểm: `notifications/service.py::create_glucose_alert_notification` (+ Zalo).

## Phạm vi

**In:** vai trò caregiver, mời/chấp nhận/thu hồi, quyền chỉ đọc theo phạm vi, chuyển tiếp cảnh báo nguy hiểm.
**Out:** caregiver nhập dữ liệu thay bệnh nhân, caregiver chat với AI về bệnh nhân, caregiver liên lạc bác sĩ.

## Task

### 7.1 Mô hình & quyền
- [ ] `UserRole.CAREGIVER`; bảng `caregiver_links`: `patient_id`, `caregiver_id`, `scopes`
      (`glucose|bp|meals|medications|alerts`), `status` (`invited|active|revoked`), `expires_at`,
      `created_at`, `revoked_at`.
- [ ] Mở rộng `ensure_patient_access` nhận caregiver + kiểm tra scope; caregiver **không bao giờ**
      có quyền ghi.
- **AC:** test ma trận quyền — caregiver chỉ đọc đúng scope, link revoked/expired → 403.

### 7.2 Luồng mời
- [ ] Bệnh nhân tạo mã mời (TTL ngắn, dùng lại pattern `create_link_code`), chọn scope.
- [ ] Người thân đăng ký/đăng nhập rồi nhập mã → link `active`.
- [ ] Bệnh nhân xem danh sách người được ủy quyền, sửa scope, thu hồi.
- **AC:** mã dùng một lần; thu hồi có hiệu lực ngay.

### 7.3 Cảnh báo
- [ ] Khi tạo glucose alert mức nguy hiểm → gửi thêm cho caregiver có scope `alerts` (in-app + Zalo).
- [ ] Giới hạn tần suất giống `_count_glucose_alerts_today` để không spam.
- [ ] Nội dung cảnh báo cho người thân: tối giản, không kèm chẩn đoán/khuyến nghị điều trị.

### 7.4 Giao diện
- [ ] Trang caregiver: danh sách bệnh nhân đang theo dõi, xem chỉ số theo scope (tái dùng component
      `health-monitoring.tsx`/dashboard ở chế độ chỉ đọc).
- [ ] Trang bệnh nhân: quản lý người thân.

### 7.5 Audit & test
- [ ] Mọi lượt caregiver đọc dữ liệu ghi `audit_events` (Epic 6.1).
- [ ] `tests/test_backend/test_caregiver_access.py`.

## Definition of Done
- Luồng mời → xem → nhận cảnh báo → thu hồi chạy end-to-end; ma trận quyền có test; audit đầy đủ.

## Câu hỏi mở
- Một caregiver theo dõi nhiều bệnh nhân? (đề xuất: có)
- Bác sĩ có thấy danh sách caregiver của bệnh nhân không?
- Cần đồng ý pháp lý dạng nào khi chia sẻ dữ liệu sức khỏe cho người thân?
