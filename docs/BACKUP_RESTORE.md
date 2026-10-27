# Sao lưu và khôi phục

> Task 6.3 (Epic 6). Mục tiêu PRD §7: **RPO ≤ 1 giờ, RTO ≤ 4 giờ**. Cập nhật 2026-10-27.

## Sao lưu những gì

| Thành phần | Cách | Ghi chú |
|---|---|---|
| PostgreSQL (toàn bộ dữ liệu ứng dụng, gồm nhật ký truy vết) | `pg_dump -Fc`, mã hóa AES-256 (openssl, PBKDF2 200k vòng) ngay khi stream — không có bản rõ nào ghi xuống đĩa | `postgres.dump.enc` |
| Qdrant (kho tri thức + vector insight bệnh nhân) | Snapshot từng collection, mã hóa như trên | `qdrant-<collection>.snapshot.enc`. Có thể dựng lại thay vì khôi phục: `make ingest` + `make reindex-insights` |
| Ảnh bữa ăn | — | Không lưu ở đâu (Epic 1) nên không có thư mục cần sao lưu |

Mỗi lần chạy tạo một thư mục `backups/<UTC timestamp>/` kèm `SHA256SUMS`, giữ `BACKUP_KEEP` (mặc định 72 = 3 ngày theo giờ) bản mới nhất, và đẩy sang kho ngoài máy chủ bằng `rclone` khi đặt `BACKUP_REMOTE`.

## Thiết lập trên máy chủ

1. Cài `openssl`, `curl`, `rclone` và `pg_dump` cùng hoặc mới hơn major version của server (hoặc dùng `PG_CONTAINER`/`PG_IMAGE` — xem đầu `scripts/backup_db.sh`).
2. Tạo passphrase: `openssl rand -base64 32`. Lưu ở **hai nơi**: biến môi trường của máy chủ và trình quản lý mật khẩu của nhóm. Mất passphrase = mất mọi bản sao lưu.
3. Cấu hình kho ngoài: `rclone config` (S3/R2/B2/Google Drive…), bật lifecycle xóa bản cũ hơn 30 ngày và versioning/object-lock nếu kho hỗ trợ.
4. Cron mỗi giờ (phút 7, tránh đầu giờ khi các job khác chạy):

   ```cron
   7 * * * * cd /opt/vdiab && set -a && . ./.env.backup && set +a && scripts/backup_db.sh >>/var/log/vdiab-backup.log 2>&1
   ```

   `.env.backup` (quyền 600) chứa `DB_*`, `BACKUP_PASSPHRASE`, `BACKUP_REMOTE`, `QDRANT_URL`, `QDRANT_API_KEY`. Với Supabase, dùng **kết nối trực tiếp/session (cổng 5432)**, không dùng transaction pooler (6543) — `pg_dump` cần session.
5. Giám sát: cảnh báo khi `/var/log/vdiab-backup.log` không có dòng `backup ... written` trong 2 giờ, hoặc script thoát mã khác 0.

## Khôi phục

```bash
# 1. Lấy bản sao lưu về (nếu chỉ còn ở kho ngoài)
rclone copy "$BACKUP_REMOTE/20261027T130700Z" backups/20261027T130700Z

# 2. Khôi phục vào DB đích (đã tạo, đang trống)
BACKUP_PASSPHRASE=... DB_HOST=... DB_PORT=5432 DB_USER=... DB_PASSWORD=... DB_NAME=diab \
QDRANT_URL=http://qdrant:6333 QDRANT_API_KEY=... \
  scripts/restore_db.sh backups/20261027T130700Z
```

Script kiểm tra checksum, từ chối DB đã có bảng trừ khi đặt `RESTORE_INTO_NON_EMPTY=yes` (tránh ghi đè production do nhầm), và khôi phục Postgres trong **một transaction** (`--single-transaction --exit-on-error`). Sau đó khởi động backend như bình thường — `create_db_and_tables` thêm bảng/cột mà bản sao lưu cũ chưa có và gắn lại trigger append-only của `audit_events`.

### Sau khi khôi phục

1. `GET /health` trả `ok`; đăng nhập được bằng một tài khoản admin.
2. **Xóa lại tài khoản đã xóa sau mốc bản sao lưu**: trước khi khôi phục, nếu DB cũ còn đọc được, lấy `select patient_id, decided_at from account_deletion_requests where status = 'approved'`; sau khi khôi phục, mọi `patient_id` có `decided_at` muộn hơn mốc bản sao lưu phải được xóa lại (admin → duyệt lại, hoặc gọi `codebase.backend.privacy.service.delete_patient_account`). Xem [DATA_RETENTION.md](DATA_RETENTION.md).
3. Ghi sự cố, mốc dữ liệu (RPO thực tế) và thời gian (RTO thực tế) vào mục *Nhật ký diễn tập* bên dưới.

## Diễn tập

`make backup-drill` (`scripts/backup_restore_drill.sh`): sao lưu nguồn, khôi phục vào Postgres + Qdrant **trống** (container riêng), so số dòng **từng bảng** và số điểm **từng collection**, thử `DELETE` trên `audit_events` sau khôi phục, in thời gian. Không ghi gì vào nguồn. Chạy lại mỗi quý và sau mỗi thay đổi schema lớn.

### Nhật ký diễn tập

| Ngày (UTC) | Nguồn | Kích thước | Sao lưu | Khôi phục (RTO kỹ thuật) | Kết quả |
|---|---|---|---|---|---|
| 2026-10-27 14:09 | DB dev (Postgres 16, 110 MB: 630 tài khoản, ~100k chỉ số đường huyết) + Qdrant local | 40 MB mã hóa | 4 giây | 4 giây | 28 bảng / 266.619 dòng khớp; 1 collection / 1.146 điểm khớp |
| 2026-10-27 14:10 | Bản sao của DB trên, đã nâng schema Epic 6 (có `audit_events`) | 40 MB mã hóa | 3 giây | 4 giây | 30 bảng / 266.620 dòng khớp; 1.146 điểm khớp; `audit_events` vẫn từ chối `DELETE` sau khôi phục |

Cùng phiên, trên Postgres 16 với dữ liệu thật: `create_db_and_tables` chạy 2 lần liên tiếp không lỗi, `UPDATE`/`DELETE`/`TRUNCATE` trên `audit_events` đều bị từ chối (`scripts/check_schema_bootstrap.py`, cũng chạy trong CI).

### Đánh giá RPO/RTO

- **RPO**: sao lưu mỗi giờ → mất tối đa ~1 giờ dữ liệu cộng thời gian chạy một lần sao lưu (vài giây ở quy mô hiện tại). **Đạt ≤ 1 giờ** với điều kiện cron chạy đều và có giám sát (bước 5). Muốn nhỏ hơn: bật PITR/WAL archiving của Supabase.
- **RTO**: phần kỹ thuật (giải mã + khôi phục) đo được **4 giây** cho 110 MB; thời gian tăng gần tuyến tính theo kích thước DB. RTO thực tế bị chi phối bởi phần con người: phát hiện sự cố, lấy passphrase, cấp DB mới, đổi cấu hình backend — ước tính **< 1 giờ** theo quy trình trên. **Đạt ≤ 4 giờ**.
- **Chưa đo**: diễn tập trên hạ tầng production (Supabase + kho ngoài thật, gồm thời gian `rclone copy`). Làm khi có môi trường staging, ghi thêm một dòng vào bảng trên.

## Nhật ký truy vết

Trigger chặn `UPDATE`/`DELETE`/`TRUNCATE` trên `audit_events` có hiệu lực với role của ứng dụng. Role **sở hữu bảng** vẫn có thể gỡ trigger; để chặn cả trường hợp đó, chạy ứng dụng bằng một role không sở hữu bảng (chỉ `SELECT`, `INSERT` trên `audit_events`) và chỉ dùng role owner cho migration — việc của hạ tầng, chưa làm.
