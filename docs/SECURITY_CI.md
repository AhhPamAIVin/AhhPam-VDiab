# Kiểm tra bảo mật trong CI

> Task 6.6 (Epic 6). Job `security` và bước *Dependency audit* của job `frontend` trong [.github/workflows/ci.yml](../.github/workflows/ci.yml).

| Kiểm tra | Công cụ | Phạm vi | Fail khi |
|---|---|---|---|
| Lộ bí mật | gitleaks 8.28.0 (checksum được xác minh khi tải) + [.gitleaks.toml](../.gitleaks.toml) | Các commit mà push/PR mang vào | Có bất kỳ phát hiện nào — gồm key dạng `sk-...` (rule riêng `sk-prefixed-api-key`) |
| Lỗ hổng Python | pip-audit | Đúng tập gói mà `requirements.txt` phân giải ra (pip `--dry-run --report`, không cài) | Có lỗ hổng đã biết, trừ danh sách bỏ qua dưới |
| Lỗ hổng frontend | `npm audit --omit=dev --audit-level=high` | Dependency production của `codebase/frontend` | Có lỗ hổng mức high/critical |

## Danh sách bỏ qua (pip-audit)

| ID | Gói | Lý do | Xem lại khi |
|---|---|---|---|
| PYSEC-2026-3046, PYSEC-2026-3047 | ragas 0.2.15 | Đọc file/URL tùy ý trong metric đa phương thức; dự án không dùng metric đó. ragas chỉ chạy trong `make eval-*` trên máy dev, backend không import | Nâng ragas (đang ghim, lý do ở `requirements.txt`) |
| PYSEC-2026-2447 | diskcache 5.6.3 (qua ragas) | pickle trong thư mục cache cục bộ; cần quyền ghi vào thư mục đó | Như trên |

Thêm ID vào danh sách chỉ khi có lý do cụ thể, ghi vào bảng này và vào comment trong `ci.yml` cùng PR.

## Khi CI báo lộ bí mật

1. **Thu hồi/đổi key ngay** ở nhà cung cấp — coi như đã lộ, kể cả khi PR chưa merge (fork/clone/log CI đã có thể giữ bản sao).
2. Xóa key khỏi commit (amend/rebase nhánh chưa merge). Nếu đã lên `main`: dọn lịch sử bằng `git filter-repo` và báo mọi người clone lại.
3. Báo sự cố theo [THREAT_MODEL.md](THREAT_MODEL.md#retention-deletion-và-incident).

Phát hiện sai (false positive): thêm `# gitleaks:allow` cuối dòng đó, kèm lý do trong PR.

## Quét toàn bộ lịch sử (một lần, và sau khi đổi rule)

CI chỉ quét commit mới để một phát hiện cũ không chặn mọi PR. Quét toàn bộ lịch sử bằng tay:

```bash
gitleaks git --config .gitleaks.toml --redact --verbose
```

Lần kiểm tra 2026-10-27 (không có gitleaks trên máy làm task): rule `sk-...` được chạy bằng regex tương đương trên mọi file đang track và toàn bộ lịch sử `git log --all -p` — **không có kết quả**. Lần quét đầy đủ với bộ rule mặc định của gitleaks: **chưa chạy** — chạy lệnh trên và ghi kết quả vào đây.

## Tình trạng khi bật (2026-10-27)

- pip-audit: 228 gói phân giải từ `requirements.txt`; chỉ còn các mục trong danh sách bỏ qua.
- npm audit: 17 lỗ hổng production (2 critical, 11 high) trước khi bật → nâng `next` 16.2.6 → 16.4.3 và `npm audit fix` (không nâng major) → **0**. Type-check, unit test, lint (0 error) và `next build` đều qua sau khi nâng.
