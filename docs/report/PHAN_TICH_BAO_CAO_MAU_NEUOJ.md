# Phân tích khóa luận mẫu NEU OJ

> **Tài liệu gốc:** [11226912_TranDuyViet_ThietKeVaPhatTrienNenTangChamBaiVaToChucThiLapTrinhTrucTuyenNEUOJ.pdf](11226912_TranDuyViet_ThietKeVaPhatTrienNenTangChamBaiVaToChucThiLapTrinhTrucTuyenNEUOJ.pdf)
> Khóa luận tốt nghiệp ngành CNTT, Trường Công nghệ – ĐH Kinh tế Quốc dân, 12/2025. Đề tài: *"Thiết kế và phát triển nền tảng chấm bài và tổ chức thi lập trình trực tuyến NEU OJ"*.
>
> **Mục đích file này:** ghi lại *mẫu đã làm gì* — cấu trúc, cách viết, định dạng, nội dung từng đầu mục, điểm mạnh và lỗi — để làm chuẩn tham chiếu khi viết khóa luận VDiab.
> Hướng dẫn thao tác cho agent (viết gì, lấy dữ liệu ở đâu, sửa ở đâu) nằm ở [HUONG_DAN_AGENT_VIET_KHOA_LUAN.md](HUONG_DAN_AGENT_VIET_KHOA_LUAN.md).

---

## 1. Số liệu tổng quan

| Thuộc tính | Giá trị |
|---|---|
| Tổng số trang PDF | 118 (A4, 595×842 pt) |
| Phần đầu (bìa → danh mục bảng) | 14 trang PDF, đánh số La Mã i–xi |
| Thân bài + phụ lục | đánh số Ả Rập 1–105 (trang PDF = số in + 13) |
| Số hình | 78 (Chương 3: 49 hình; Chương 4: 29 hình) |
| Số bảng | 3 |
| Tài liệu tham khảo | 23 mục, chuẩn IEEE, phần lớn có DOI, đa số 2019–2025 |
| Turnitin | 14% (trang cuối) |

**Phân bổ độ dài thân bài (theo số trang in):**

| Phần | Trang | Số trang | Ghi chú |
|---|---|---|---|
| Mở đầu | 2 | 1 | |
| Chương 1 – Tổng quan đề tài | 3–7 | 5 | thuần văn bản |
| Chương 2 – Cơ sở lý thuyết | 8–17 | 10 | 1 bảng, không hình |
| Chương 3 – Phân tích & thiết kế | 18–74 | 57 | ~48 trang là sơ đồ, mỗi sơ đồ 1 trang |
| Chương 4 – Kết quả & đánh giá | 75–94 | 20 | 29 ảnh chụp/biểu đồ, 2 bảng |
| Kết luận | 95 | 1 | |
| Tài liệu tham khảo | 96–98 | 3 | |
| Phụ lục | 99–104 | 6 | thuần văn bản |
| Kết quả Turnitin | 105 | 1 | ảnh chụp báo cáo |

→ Văn bản thực chất chỉ khoảng **45–50 trang**; độ dày còn lại đến từ sơ đồ UML và ảnh giao diện.

---

## 2. Cây đầu mục đầy đủ

```
Bìa ngoài (có gáy: năm học · ngành · tên SV)
Bìa trong (tên SV, MSSV, lớp, ngành, khoa, GVHD, chữ ký GVHD)
MỤC LỤC
LỜI CAM ĐOAN
LỜI CẢM ƠN
DANH MỤC CÁC TỪ VIẾT TẮT VÀ THUẬT NGỮ
DANH MỤC HÌNH
DANH MỤC BẢNG BIỂU
MỞ ĐẦU
CHƯƠNG 1. TỔNG QUAN VỀ ĐỀ TÀI
  1.1. Đặt vấn đề
  1.2. Mục tiêu nghiên cứu
    1.2.1. Mục tiêu tổng quát
    1.2.2. Mục tiêu cụ thể
  1.3. Đối tượng nghiên cứu
  1.4. Phạm vi nghiên cứu
    1.4.1. Phạm vi về đối tượng sử dụng và phạm vi triển khai
    1.4.2. Phạm vi về chức năng
    1.4.3. Phạm vi về phi chức năng
    1.4.4. Các vấn đề ngoài phạm vi nghiên cứu
  1.5. Đóng góp của đề tài
CHƯƠNG 2. CƠ SỞ LÝ THUYẾT VÀ TỔNG QUAN HỆ THỐNG LIÊN QUAN
  2.1. Lịch sử phát triển của phương pháp giảng dạy, đánh giá lập trình
    2.1.1. Các phương pháp giảng dạy và đánh giá truyền thống
    2.1.2. Sự ra đời của các hệ thống chấm bài tự động
      2.1.2.1. Giai đoạn 1: Các hệ thống quản lý kỳ thi cục bộ
      2.1.2.2. Giai đoạn 2: ... dựa trên nền tảng website
      2.1.2.3. Giai đoạn 3: Kỷ nguyên đánh giá thông minh ... dựa trên AI
    2.1.3. Xu hướng giáo dục lập trình hiện đại
      2.1.3.1. Phản hồi tức thì
      2.1.3.2. Học tập trên nền web và IDE trực tuyến
      2.1.3.3. Xu hướng ứng dụng trí tuệ nhân tạo trong giảng dạy
  2.2. Cơ sở lý thuyết về kỹ thuật xây dựng hệ thống
    2.2.1. Nguyên lý hoạt động chung của quá trình thực thi mã nguồn (2 tiểu mục)
    2.2.2. Mô hình phân lớp của hệ sinh thái OJ (4 tiểu mục – 4 lớp)
    2.2.3. Kỹ thuật ảo hóa và cô lập môi trường (so sánh · cơ chế · nguy cơ)
  2.3. Tổng quan và đánh giá các hệ thống hiện có
    2.3.1. Các hệ thống thương mại và thi đấu
    2.3.2. Các hệ thống OJ mã nguồn mở khác
CHƯƠNG 3. PHÂN TÍCH VÀ THIẾT KẾ HỆ THỐNG
  3.1. Phân tích hệ thống
    3.1.1. Phân tích chức năng
      3.1.1.1. Biểu đồ Use Case tổng quát hệ thống
      3.1.1.2. Biểu đồ hoạt động (22 biểu đồ)
    3.1.2. Phân tích cấu trúc (biểu đồ lớp)
    3.1.3. Phân tích hành vi (22 biểu đồ tuần tự)
  3.2. Thiết kế hệ thống
    3.2.1. Nền tảng công nghệ và môi trường phát triển (8 tiểu mục, mỗi công nghệ 1 mục)
    3.2.2. Biểu đồ gói
    3.2.3. Biểu đồ triển khai (+ sơ đồ kiến trúc tổng quan)
CHƯƠNG 4. KẾT QUẢ TRIỂN KHAI, ĐÁNH GIÁ
  4.1. Môi trường triển khai và thử nghiệm
  4.2. Kết quả xây dựng chức năng
  4.3. Đánh giá hiệu năng hệ thống
    4.3.1. Phương pháp và công cụ đo lường
    4.3.2. Đánh giá thời gian xử lý
    4.3.3. Kiểm thử chịu tải
  4.4. Kết quả triển khai thực tế
  4.5. Đánh giá tổng thể, hạn chế và rủi ro
    4.5.1. So sánh tính năng giữa NEU OJ và các hệ thống phổ biến
    4.5.2. Ưu điểm
    4.5.3. Hạn chế
    4.5.4. Rủi ro (vận hành/tải · sandbox · tích hợp AI)
  4.6. Đánh giá từ người dùng
  4.7. Định hướng phát triển
    4.7.1. Phát triển trợ lý CodeMentor
    4.7.2. Xây dựng quy trình đánh giá, chấm điểm kết hợp AI
KẾT LUẬN
TÀI LIỆU THAM KHẢO
PHỤ LỤC
  5.1. Phụ lục A: Xây dựng hệ thống
    5.1.1. Xây dựng hệ thống back-end (NestJS · CSDL · Judge0 · cân bằng tải)
    5.1.2. Trực quan hoá dữ liệu bằng Grafana và Prometheus
    5.1.3. Xây dựng hệ thống giao diện (Next.js · kiến trúc thành phần)
    5.1.4. Triển khai lên Production (hạ tầng · khởi chạy & CI/CD)
KẾT QUẢ TURNITIN
```

Mạch logic toàn bài: **Vấn đề (Ch1) → Nền tảng lý thuyết & khoảng trống của giải pháp hiện có (Ch2) → Thiết kế đáp ứng (Ch3) → Bằng chứng đã làm được + giới hạn (Ch4) → Tổng kết (Kết luận)**. Chi tiết cài đặt kỹ thuật bị đẩy xuống Phụ lục để thân bài gọn.

---

## 3. Định dạng trình bày (đo trực tiếp từ PDF)

| Thành phần | Quy cách quan sát được |
|---|---|
| Khổ giấy | A4 dọc |
| Font | Times New Roman toàn bài (bìa có thể dùng Arial Bold cho một số chữ) |
| Thân văn bản | 13 pt, căn đều hai bên, giãn dòng ~1.5, thụt đầu dòng ~1.27 cm |
| Lề trái | ~3.5 cm (lề đóng gáy); các lề còn lại ~2–2.5 cm |
| Tên chương | 18 pt, **đậm**, IN HOA, căn giữa, bắt đầu trang mới; tiêu đề dài ngắt thành 2 dòng |
| Mục cấp 2 (`1.1.`) | 13 pt **đậm** |
| Mục cấp 3 (`3.1.1.`) | 13 pt ***đậm nghiêng*** |
| Mục cấp 4 (`3.1.1.1.`) | 13 pt *nghiêng* |
| Tiêu đề không đánh số trong mục | 13 pt **đậm**, không thụt (vd. "Kiến trúc RAG", "Với vai trò làm bộ nhớ đệm") |
| Gạch đầu dòng | cấp 1 dùng `-`, cấp 2 dùng `o`; phụ lục dùng `•`; danh sách có thứ tự dùng `1. 2. 3.` |
| Mẫu gạch đầu dòng | `- **Nhãn ngắn**: câu giải thích.` (nhãn in đậm, theo sau là dấu hai chấm) |
| Chú thích hình | **dưới** hình, căn giữa, *nghiêng*, dạng `Hình <chương>.<stt>. <Mô tả>.` |
| Chú thích bảng | **trên** bảng, căn giữa, *nghiêng*, dạng `Bảng <stt>. <Mô tả>.` (đánh số toàn bài, không theo chương) |
| Bảng | kẻ khung đầy đủ, dòng tiêu đề in đậm; dùng ✓ / × cho bảng so sánh |
| Số trang | 12 pt, giữa chân trang; phần đầu số La Mã thường (i, ii…), thân bài số Ả Rập |
| Trích dẫn | IEEE: số trong ngoặc vuông `[1]`, `[14], [15]`, đặt cuối câu trước dấu chấm |
| Tài liệu tham khảo | IEEE đầy đủ: tác giả, "tên bài", tạp chí *viết tắt*, vol., no., pp., tháng năm, doi; web có `Accessed:` + `[Online]. Available:` — dễ sinh bằng Zotero/Mendeley |
| Danh mục viết tắt | bảng 2 cột "Từ viết tắt – Ý nghĩa"; ý nghĩa ghi tiếng Việt, kèm tên tiếng Anh trong ngoặc đơn ở dòng dưới |
| Sơ đồ UML | mỗi sơ đồ chiếm gần trọn 1 trang, nền trắng, nét đen; activity diagram dạng swimlane (Người dùng / Hệ thống / Dịch vụ ngoài). Phân tích chi tiết từng loại sơ đồ (đọc từ ảnh) và lỗi trên hình: [HUONG_DAN_AGENT_VIET_KHOA_LUAN.md §10.1](HUONG_DAN_AGENT_VIET_KHOA_LUAN.md#101-bản-mẫu-vẽ-sơ-đồ-thế-nào) |

---

## 4. Phân tích cách viết (văn phong)

### 4.1. Giọng văn
- **Học thuật – trang trọng**, ngôi thứ ba ("đề tài", "khóa luận", "hệ thống"). Ngôi "tôi" chỉ dùng ở Lời cam đoan, Lời cảm ơn và đoạn cuối Kết luận.
- Câu dài, nhiều mệnh đề, hay dùng cặp từ nối: *"Tuy nhiên"*, *"Bên cạnh đó"*, *"Hơn nữa"*, *"Do đó"*, *"Chính vì vậy"*, *"Đồng thời"*, *"Điều này cho thấy/chứng minh…"*.
- Thuật ngữ tiếng Anh giữ nguyên hoặc đặt trong ngoặc sau từ tiếng Việt lần đầu: *"chu trình phản hồi chậm (slow feedback loop)"*, *"Lớp hệ thống thực thi mã trực tuyến (OCES - Online Code Execution System)"*. Sau đó dùng viết tắt và đưa vào Danh mục viết tắt.
- Dùng ẩn dụ để nhấn vai trò thành phần: Judge0 là *"trái tim của hệ thống"*, NestJS là *"bộ não xử lý trung tâm"*, sandbox là *"hộp kín dùng một lần"*.

### 4.2. Các khuôn đoạn lặp lại

**a) Khuôn "đặt vấn đề" (1.1, Mở đầu):**
bối cảnh rộng → *"Về phía người học…"* → *"Về phía giảng viên và nhà trường…"* → gọi tên *"khoảng trống"* → *"Chính vì vậy, đề tài '…' được thực hiện nhằm…"*. Mỗi nhận định về khó khăn đều có trích dẫn [n].

**b) Khuôn "giới thiệu công nghệ" (toàn bộ 3.2.1.x) — dùng nguyên văn được:**
1. *Câu mở:* "Để [giải quyết nhu cầu X] cho hệ thống NEU OJ, đề tài lựa chọn **[Công nghệ]** – [định nghĩa 1 câu]."
2. *Câu chuyển:* "Việc lựa chọn [Công nghệ] … dựa trên một số ưu thế/đặc tính như sau:"
3. *3–4 gạch đầu dòng* `- **Ưu điểm**: giải thích, nối về nhu cầu của hệ thống.`
4. *Đoạn chốt:* "Trong kiến trúc/hệ thống NEU OJ, [Công nghệ] đóng vai trò … chịu trách nhiệm …"

**c) Khuôn "lịch sử – giai đoạn" (2.1.2):** chia giai đoạn theo kiến trúc; mỗi giai đoạn: đại diện tiêu biểu + năm + nơi phát triển [trích dẫn] → cơ chế hoạt động → danh sách điểm bất lợi dạng `- **Nhãn**: …`. Điểm bất lợi của giai đoạn trước chính là lý do cho giai đoạn sau → dẫn tới hệ thống của đề tài.

**d) Khuôn "dẫn chứng nghiên cứu" (2.1.2.3, 2.1.3):** *"Nghiên cứu của [Tác giả] et al. (năm) trên [cỡ mẫu] cho thấy [số liệu cụ thể: Pearson 0.91, MAE 0.68/10, 63% chính xác] [n]."* → *"Tuy nhiên, [Tác giả] lưu ý rằng…"* → kết luận xu hướng (mô hình lai có con người giám sát).

**e) Khuôn "so sánh để chọn" (2.2.3.1):** đặt 3 phương án vào bảng theo các tiêu chí giống nhau (kiến trúc, mức cô lập, thời gian khởi động, tài nguyên) và dòng cuối "Đánh giá cho OJ" → kết luận phương án tối ưu.

**f) Khuôn "đánh giá đối thủ" (2.3):** điểm mạnh → *"Đặt trong bối cảnh [giáo dục đại học], những hệ thống này có một vài điểm không phù hợp…"* → gạch đầu dòng có nhãn đậm (Mã nguồn đóng, Chi phí, Thiếu tính năng…). Mỗi điểm yếu ở đây được NEU OJ đáp lại ở Bảng 3 (4.5.1).

**g) Khuôn "kết quả chức năng" (4.2):** 1–2 câu dẫn theo góc nhìn người dùng (*"Với tính năng tham gia kỳ thi, người dùng chỉ cần…"*) → ảnh chụp màn hình → chú thích. Gom ảnh theo nhóm vai trò: người thi → người quản lý → người tổ chức → luyện tập → báo lỗi.

**h) Khuôn "đánh giá số liệu" (4.3):** phương pháp + công cụ + kịch bản (số user, tốc độ tăng, cấu hình cụm) → số đo → **diễn giải** (vì sao RPS đi ngang, nút thắt ở đâu) → kết luận ngưỡng vận hành + hướng mở rộng.

**i) Khuôn "rủi ro" (4.5.4):** mô tả kịch bản rủi ro → hệ quả → hiện trạng giảm thiểu → rủi ro còn lại. Riêng rủi ro tương lai (AI) có cặp "vấn đề – giải pháp" (độ trễ → streaming; ảo giác → RAG; chi phí → cache/mô hình nhỏ).

### 4.3. Chiến lược lập luận đáng học
- **Truy vết hai đầu:** mục tiêu cụ thể ở 1.2.2 có con số (≥500 user đồng thời, xử lý < 5 s, trễ hàng đợi < 500 ms) → Chương 4 đo lại (0.3–0.4 s trễ hệ thống; chịu được 1500 user). Điểm yếu đối thủ ở 2.3 → Bảng so sánh 4.5.1.
- **Phân định rõ đã làm / chưa làm:** AI (CodeMentor, chấm điểm AI) được khai báo ngay ở 1.4.4 là *"chỉ ở mức đề xuất ý tưởng và thiết kế"*, có sơ đồ ở Ch3, rồi được nhắc lại ở 4.5.3 Hạn chế và 4.7 Định hướng → không bị bắt lỗi "nói quá".
- **Thừa nhận giới hạn chủ động:** 4.6 tự nêu "chưa khảo sát diện rộng bằng bảng hỏi", thay bằng phỏng vấn nhanh + 40 phản hồi qua chức năng báo lỗi.
- **Bằng chứng thực tế:** số người dùng (>750, ~20 giảng viên), ~250 bài tập, 9.212 lượt thực thi trong kỳ thi Olympic, ảnh leaderboard thật.

---

## 5. Mô tả chi tiết nội dung từng đầu mục

### Phần đầu
| Mục | Nội dung trong mẫu |
|---|---|
| **Bìa ngoài** | Tên trường/đơn vị, logo, "KHÓA LUẬN TỐT NGHIỆP", tên đề tài (đậm, cỡ lớn, 3 dòng), tên SV, GVHD, "Hà Nội, 12/2025". Gáy bìa in dọc: niên khóa `2025-2026.1` · `CÔNG NGHỆ THÔNG TIN` · tên SV. Có khung viền trang. |
| **Bìa trong** | Như bìa ngoài + Mã SV, Lớp, Ngành, Khoa, chỗ "(Chữ ký GVHD)". |
| **Mục lục** | Tự động đến cấp 4, có dot leader và số trang. |
| **Lời cam đoan** | 2 đoạn: công trình cá nhân dưới hướng dẫn của GVHD; số liệu trung thực, có trích dẫn, chịu trách nhiệm. Ngày ký + "(ký, ghi rõ họ tên)". |
| **Lời cảm ơn** | 4 đoạn ngắn: trường/khoa → GVHD (đoạn riêng, "đặc biệt nhất") → thầy cô, bạn bè → lời khiêm tốn xin góp ý. |
| **Danh mục viết tắt** | ~25 mục, sắp theo thứ tự xuất hiện (không theo ABC). |
| **Danh mục hình / bảng** | Tự động sinh từ caption. |

### Mở đầu (1 trang, 3 đoạn)
1. Bối cảnh vĩ mô (chuyển đổi số, nhu cầu nhân lực CNTT) và hạn chế của phương pháp truyền thống.
2. Giới thiệu đề tài (tên in đậm trong ngoặc kép) + liệt kê tính năng cốt lõi bằng văn xuôi.
3. Mục tiêu và tầm nhìn mở rộng (AI trong tương lai).
→ Không có số liệu, không trích dẫn; vai trò như "abstract" tiếng Việt.

### Chương 1 – Tổng quan về đề tài
- **1.1 Đặt vấn đề (~1 trang):** nỗi đau hai phía (người học: vòng phản hồi chậm, thiếu kho bài tập; người dạy: chấm thủ công tốn công, khó tổ chức thi lớn, gian lận) có trích dẫn → "khoảng trống" → tên đề tài.
- **1.2 Mục tiêu:** 1.2.1 *tổng quát* = 1 câu "Xây dựng thành công…". 1.2.2 *cụ thể* = 6 nhiệm vụ đánh số, mỗi mục `**Nhãn**: mô tả`: nghiên cứu lý thuyết → phân tích thiết kế → tích hợp lõi → xây phân hệ → **hiệu năng có chỉ tiêu định lượng** → kiểm thử đánh giá.
- **1.3 Đối tượng nghiên cứu:** 5 gạch đầu dòng — quy trình nghiệp vụ, lịch sử phương pháp, kỹ thuật cô lập (namespaces/cgroups), kiến trúc phân tán, kỹ thuật web hiệu năng cao. (Đối tượng = *thứ được nghiên cứu*, không phải người dùng.)
- **1.4 Phạm vi:** tách 4 tiểu mục — người dùng & nơi triển khai; chức năng (7 nhóm, gạch đầu dòng); phi chức năng (hiệu năng, an toàn, UI/UX, mở rộng); **ngoài phạm vi** (giám sát sâu, AI chỉ ở mức thiết kế, không Kubernetes/serverless).
- **1.5 Đóng góp (3 đoạn):** sản phẩm hoàn chỉnh → đóng góp kỹ thuật (kiến trúc microservices + sandbox) → tính thực tiễn (đã dùng cho Olympic, thi kết thúc học phần).

### Chương 2 – Cơ sở lý thuyết và tổng quan hệ thống liên quan
- **2.1 Lịch sử phương pháp:** câu mở mang tính triết lý (chuyển từ lấy giảng viên làm trung tâm → người học làm trung tâm). 2.1.1 phương pháp truyền thống và 4 điểm yếu (không mở rộng được, phản hồi chậm, chủ quan, kiểm thử không phủ biên). 2.1.2 ba giai đoạn (PC², UVa/Codeforces, LLM). 2.1.3 ba xu hướng (phản hồi tức thì, IDE web, AI – có Khanmigo/Socratic, quy trình chấm 2 lớp, RAG/LPITutor).
- **2.2 Lý thuyết kỹ thuật:** nguyên lý biên dịch/thực thi → mô hình 4 lớp của Judge0 (mỗi lớp: "Đây là…", "Nhiệm vụ…") với đoạn chốt bằng ký hiệu `⇒` → kỹ thuật cô lập: **Bảng 1** so sánh VM/Docker/isolate, cơ chế isolate (namespaces PID/Mount/Network; cgroups Memory/CPU/PIDs, gạch hai cấp), 3 nguy cơ (fork bomb, vòng lặp vô hạn, truy cập trái phép) và cách chặn.
- **2.3 Hệ thống hiện có:** thương mại (Codeforces, LeetCode, HackerRank) – 4 điểm không phù hợp; mã nguồn mở (CMS, DOMjudge) – nguyên khối, khó tuỳ biến.

### Chương 3 – Phân tích và thiết kế hệ thống
- **3.1.1.1 Use case tổng quát:** 1 câu ràng buộc (chỉ đăng nhập SSO) + Hình 3.1. Tác nhân: Người dùng vãng lai, Người dùng, Giảng viên/Quản trị viên.
- **3.1.1.2 Biểu đồ hoạt động:** 22 hình (3.2–3.23), **mỗi chức năng 1 hình, không có lời dẫn**. Thứ tự: đăng nhập → IDE → bài tập (đóng góp, cập nhật, xoá) → test case (thêm, sửa, xoá) → cấu hình ngôn ngữ → kỳ thi (thêm, sửa, xoá, tạo đề, xoá bài khỏi đề, thêm/xoá quản trị viên) → tham gia thi → làm bài → duyệt kết quả → giám sát → luyện tập → CodeMentor. Swimlane: Người dùng | Hệ thống NEU OJ | Dịch vụ ngoài; có nút quyết định True/False.
- **3.1.2 Phân tích cấu trúc:** 1 biểu đồ lớp (Contest, Problems, Users, Submissions, ProblemSubmissions, Testcases, Reports, Actionlogs, Tag) với thuộc tính kiểu `+name: String`, quan hệ có nhãn (contains, submits, tracks…). Không có văn bản giải thích.
- **3.1.3 Phân tích hành vi:** 22 biểu đồ tuần tự (3.25–3.46) song song 1-1 với biểu đồ hoạt động; lifeline: Actor → NEU OJ (FE) → Backend → Dịch vụ ngoài/CSDL; message đánh số `1.`, `2.`…
- **3.2.1 Nền tảng công nghệ:** 8 tiểu mục theo khuôn 4.2.b: Judge0, NestJS, Next.js, MongoDB, Docker, Socket.IO, Redis (2 vai trò: cache, hàng đợi), công nghệ phụ trợ (MinIO, Grafana/Prometheus, Nginx — gộp 1 đoạn).
- **3.2.2 Biểu đồ gói:** các tầng Presentation → Application → Business → Data Accessing → Data Storing, nối với package NextJS/NestJS.
- **3.2.3 Biểu đồ triển khai:** Hình 3.48 + giải thích từng node bằng gạch đầu dòng 2 cấp (Client, Application Server gồm Backend/Judge0/LLM/MinIO, CSDL & cache, Giám sát) + giao thức (HTTPS REST bên ngoài, TCP/IP trong mạng Docker) → **Hình 3.49 sơ đồ kiến trúc tổng quan** có icon, thể hiện luồng dữ liệu và cụm 2 máy chủ Judge0 sau Nginx.

### Chương 4 – Kết quả triển khai, đánh giá
- **4.1 Môi trường:** 2 máy chủ của khoa, vai trò từng máy, cấu hình phần cứng dạng gạch đầu dòng (CPU, RAM, ổ cứng, băng thông, OS).
- **4.2 Kết quả chức năng:** 19 ảnh chụp màn hình (Hình 4.1–4.19) kèm 1–2 câu dẫn.
- **4.3 Hiệu năng:** 4.3.1 công cụ (Locust), mã Python dùng để tải (ảnh code + ảnh kết quả), cấu hình kịch bản (1000/1500/2000 user, +100 user/s, cụm 2 máy chấm). 4.3.2 thời gian xử lý 1 request: tổng 1.32 s, phía server 1.14 s, trừ thời gian chạy chương trình → trễ hệ thống 0.3–0.4 s. 4.3.3 **Bảng 2** (Mức tải / RPS / Thời gian phản hồi / Tỷ lệ lỗi) + 3 ảnh thống kê Locust + diễn giải nút thắt (~18 bài/giây) + kết luận ngưỡng tối ưu < 1500 user.
- **4.4 Triển khai thực tế:** >750 người dùng, ~20 giảng viên, ~250 bài; ảnh chủ đề ôn luyện; ảnh leaderboard Olympic; 30 thí sinh đồng thời, 9.212 lượt thực thi.
- **4.5 Đánh giá tổng thể:** **Bảng 3** so sánh 3 nhóm (thương mại / thi đấu cũ / NEU OJ) theo 7 tiêu chí ✓/×. Ưu điểm (3 gạch), Hạn chế (4 gạch: tải > 2000, chưa có AI, IDE chưa responsive, bài thao tác tệp), Rủi ro (3 tiểu mục theo khuôn 4.2.i).
- **4.6 Đánh giá người dùng:** thừa nhận chưa khảo sát bảng hỏi → phỏng vấn nhanh + quan sát; nhận xét tích cực của SV và GV; ~40 góp ý qua chức năng báo lỗi; 1 ảnh ví dụ báo lỗi.
- **4.7 Định hướng:** CodeMentor (đối thoại sư phạm, RAG bắt buộc, 2 chức năng: tra cứu cú pháp, giải thích lỗi biên dịch) và quy trình chấm AI 3 tầng (OJ → phong cách code → LLM tổng hợp theo rubric).

### Kết luận (1 trang, 5 đoạn)
Bối cảnh → kết quả kỹ thuật (kiến trúc, công nghệ, chức năng cốt lõi) → kết quả thực tế → bài học cá nhân + cảm ơn GVHD → hạn chế & cam kết phát triển tiếp. Không đưa số liệu mới.

### Tài liệu tham khảo
23 mục IEEE, đánh số theo thứ tự trích dẫn lần đầu. Nguồn chủ yếu: tạp chí MDPI (Applied Sciences, Electronics, Mathematics, Information), ACM Computing Surveys, IEEE conference, arXiv, sách (Aho – Compilers), 2 trang web có ngày truy cập.

### Phụ lục A – Xây dựng hệ thống
Chi tiết cài đặt mà thân bài không cần: kiến trúc module NestJS (Entities/Schemas → Services/Repositories → Controllers, DTO, quy ước snake_case qua Interceptor, API công khai/riêng tư + JWT); cấu hình CSDL (Docker, Mongoose schema/index, `.env`); kiến trúc Judge0 (Web API, Postgres + Redis, CEE + isolate); cân bằng tải Nginx round-robin + passive health check; giám sát Grafana/Prometheus/Loki/Promtail; frontend (TypeScript, Tailwind + shadcn, Monaco Editor, Markdown + KaTeX); hạ tầng production 2 máy chủ; Docker Compose + Nginx SSL + GitHub Actions CI/CD.

---

## 6. Điểm mạnh nên kế thừa
1. Mục tiêu cụ thể **có chỉ tiêu định lượng** và được **đo lại** ở Chương 4.
2. Phạm vi có mục **"ngoài phạm vi"** rõ ràng, nhất quán với Hạn chế và Định hướng.
3. Chương 2 có chiều sâu: lịch sử → lý thuyết kỹ thuật → so sánh phương án bằng bảng → đối thủ; trích dẫn nghiên cứu gần đây kèm con số.
4. Mỗi công nghệ được biện minh theo khuôn cố định: định nghĩa → lý do chọn → vai trò trong hệ thống.
5. Chương 4 có số liệu đo thật, có **diễn giải** (không chỉ dán ảnh) và kết luận ngưỡng vận hành.
6. Có bằng chứng triển khai thực tế và tự nhận hạn chế của phương pháp đánh giá.
7. Chi tiết cài đặt đặt ở Phụ lục, thân bài tập trung vào lập luận.

## 7. Lỗi trong mẫu — KHÔNG lặp lại
| Lỗi | Vị trí | Cách làm đúng cho VDiab |
|---|---|---|
| 44 sơ đồ hoạt động/tuần tự **không có một câu mô tả** nào | 3.1.1.2, 3.1.3 | Mỗi sơ đồ có 2–4 câu dẫn: tác nhân, điều kiện tiên quyết, luồng chính, ngoại lệ. Gom chức năng đơn giản (CRUD) để tránh 1 hình/1 thao tác. |
| Không có đặc tả use case, không có thiết kế CSDL dạng bảng (chỉ có biểu đồ lớp, không giải thích) | 3.1 | Thêm bảng đặc tả use case chính và mục "Thiết kế cơ sở dữ liệu" (bảng thực thể – thuộc tính – ràng buộc). |
| Bảng đánh số toàn bài (`Bảng 1`) trong khi hình đánh theo chương (`Hình 3.1`) | toàn bài | Thống nhất theo chương: `Hình 2.1`, `Bảng 2.1`. |
| Phụ lục đánh số `5.1`, `5.1.1` như thể Chương 5 | Phụ lục | Dùng `Phụ lục A`, `A.1`, `A.1.1`. |
| Trang "Danh mục bảng biểu" đánh số Ả Rập `1`, thân bài bắt đầu từ `2` | phần đầu | Phần đầu đánh La Mã đến hết danh mục; "Mở đầu" là trang 1. |
| Lỗi chính tả/tên riêng: "tiêu biển", "trả dài", "Đồng tời", "cộng động", "nhất quá", "từng có nhân", "Pitscitelli", "tính nữa" (caption Bảng 3), "Loctus" (đúng: **Locust**), "thi sinh" | rải rác | Rà chính tả bắt buộc; tên công cụ/tác giả phải khớp nguồn. |
| Caption không đồng nhất: "Hình 3.39. Thêm quản trị viên cho kỳ thi." thiếu "Biểu đồ tuần tự chức năng" | Hình 3.39 | Caption cùng loại theo cùng một mẫu. |
| Số liệu bất thường không được giải thích: RPS ở 2000 user (58.9) cao gấp 3 lần mức 1500 do request thất bại nhanh | Bảng 2 | Mọi con số lạ phải có 1 câu giải thích. |
| Mục tiêu "độ trễ xếp hàng < 500 ms" không được đo riêng | 1.2.2 ↔ 4.3 | Mỗi chỉ tiêu ở 1.2.2 phải có đúng một kết quả đo tương ứng (hoặc ghi rõ chưa đo). |
| Biểu đồ triển khai có node LLM dù AI chưa triển khai, không ghi chú | 3.2.3 | Thành phần chỉ ở mức thiết kế phải đánh dấu "(dự kiến)" trên hình/caption. |
| Mục 5.1.4.2 kết thúc đột ngột (3 trụ cột nhưng chỉ viết 2) | Phụ lục | Kiểm tra danh sách liệt kê đủ số mục đã hứa. |
| Kết luận lặp lại lời cảm ơn | Kết luận | Lời cảm ơn chỉ để ở trang Lời cảm ơn. |
| Đánh giá người dùng chỉ định tính, không có thang đo | 4.6 | Nếu có thể, dùng bảng hỏi chuẩn (SUS/Likert) với cỡ mẫu nêu rõ; nếu không, ghi rõ giới hạn như mẫu đã làm. |
