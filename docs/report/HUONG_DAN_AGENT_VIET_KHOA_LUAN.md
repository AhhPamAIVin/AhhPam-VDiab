# Hướng dẫn cho agent: viết và sửa khóa luận VDiab

> **Đọc file này trước khi viết hoặc sửa bất kỳ phần nào của khóa luận VDiab.**
> Chuẩn tham chiếu là khóa luận mẫu NEU OJ (cùng trường, cùng ngành) — phân tích đầy đủ ở [PHAN_TICH_BAO_CAO_MAU_NEUOJ.md](PHAN_TICH_BAO_CAO_MAU_NEUOJ.md). File này nói **viết gì, lấy dữ liệu ở đâu, đặt ở mục nào và khi sửa một chỗ thì phải cập nhật những chỗ nào khác**.

Thứ tự đọc khi mới vào việc:
1. File này (toàn bộ).
2. [PHAN_TICH_BAO_CAO_MAU_NEUOJ.md](PHAN_TICH_BAO_CAO_MAU_NEUOJ.md) §3 (định dạng), §4 (khuôn văn), §7 (lỗi cần tránh).
3. Nguồn sự thật của dự án: [docs/PRD.md](../PRD.md), [ARCHITECTURE.md](../../ARCHITECTURE.md), [README.md](../../README.md), rồi file evidence của mục mình đang viết (bảng ở §4).

---

## 1. Nguyên tắc bắt buộc

1. **Không bịa số liệu.** Mọi con số (độ chính xác, latency, số người dùng, số test, coverage…) phải lấy từ file evidence trong repo hoặc từ lần chạy thực tế, và được ghi vào sổ số liệu ([§3](#3-cấu-trúc-thư-mục-bản-thảo)). Nếu chưa đo: viết "chưa đo" hoặc chạy đo — không ước lượng. Ví dụ: [docs/PERFORMANCE_REPORT.md](../PERFORMANCE_REPORT.md) hiện ghi mọi workload là *Unverified* → Chương 4 phần hiệu năng **chưa có dữ liệu**, phải đo trước khi viết.
2. **Code là nguồn sự thật cuối cùng cho phần kỹ thuật.** Tài liệu trong `docs/` có chỗ lệch nhau (xem [§8](#8-mâu-thuẫn-đã-biết-trong-repo--phải-xác-minh-trước-khi-viết)). Trước khi viết tên công nghệ, số node agent, số bảng CSDL, số endpoint… phải kiểm tra trong `codebase/`, `docker-compose*.yml`, `requirements*.txt`, `package.json`.
3. **Phân định rõ "đã làm" và "thiết kế/định hướng".** Theo đúng cách mẫu xử lý AI: thứ chưa triển khai được khai ở *1.4 Ngoài phạm vi*, nếu có sơ đồ ở Ch3 thì đánh dấu "(dự kiến)", nhắc lại ở *Hạn chế* và *Định hướng*. Các epic trong [docs/tasks/](../tasks/README.md) có trạng thái "Chưa bắt đầu" → chỉ được xuất hiện ở *Định hướng phát triển* cho đến khi xong.
4. **An toàn y khoa là lập luận trung tâm.** Mọi đoạn mô tả AI phải nhất quán với ràng buộc của PRD: AI không chẩn đoán, không kê đơn, không gợi ý liều thuốc/insulin; Rule Engine cảnh báo ngưỡng chạy độc lập, không phụ thuộc LLM; AI chỉ tạo cờ "Cần bác sĩ xem xét".
5. **Truy vết hai đầu.** Mỗi mục tiêu cụ thể ở 1.2.2 phải có kết quả tương ứng ở Ch4; mỗi điểm yếu của giải pháp hiện có ở Ch2 phải được đối chiếu trong bảng so sánh ở Ch4. Cập nhật bảng truy vết ở [§6](#6-bảng-truy-vết-mục-tiêu--kết-quả) mỗi khi thêm/sửa mục tiêu hoặc kết quả.
6. **Mọi nhận định về y khoa, giáo dục sức khỏe, AI trong y tế phải có trích dẫn** `[n]` (IEEE). Ưu tiên guideline (ADA Standards of Care, Bộ Y tế), bài báo có DOI 2020 trở về sau. Không trích dẫn blog khi có nguồn học thuật thay thế.
7. **Mỗi hình/bảng phải có câu dẫn trong văn bản** ("…như Hình 3.5", "Bảng 4.2 tổng hợp…") và 2–4 câu giải thích. Đây là lỗi lớn nhất của bản mẫu (44 sơ đồ không có lời).
8. **Viết bằng tiếng Việt học thuật, ngôi thứ ba** ("đề tài", "hệ thống VDiab", "khóa luận"). Chỉ dùng "tôi" ở Lời cam đoan, Lời cảm ơn và đoạn cuối Kết luận.
9. **Thuật ngữ:** lần đầu viết đầy đủ tiếng Việt + (tên tiếng Anh, viết tắt), sau đó dùng viết tắt; thêm ngay vào `viet-tat.md`. Tên riêng/công cụ viết đúng chính tả gốc (Locust, LangGraph, FastAPI, Qdrant…).
10. **Không dán nguyên văn tài liệu dự án** (PRD, ARCHITECTURE…) vào khóa luận: viết lại theo văn phong khóa luận. Các file đó viết cho đội phát triển, có TODO, link nội bộ và giọng văn kỹ thuật.

---

## 2. Quy ước định dạng (tóm tắt)

Chi tiết đo từ bản mẫu ở [PHAN_TICH_BAO_CAO_MAU_NEUOJ.md §3](PHAN_TICH_BAO_CAO_MAU_NEUOJ.md#3-định-dạng-trình-bày-đo-trực-tiếp-từ-pdf). Những điểm VDiab **cố ý làm khác** bản mẫu để sửa lỗi của mẫu:

| Hạng mục | Quy ước VDiab |
|---|---|
| Đánh số hình | `Hình <chương>.<stt>. <Mô tả>.` — caption dưới hình, nghiêng, căn giữa |
| Đánh số bảng | `Bảng <chương>.<stt>. <Mô tả>.` — **theo chương** (mẫu đánh toàn bài là sai), caption trên bảng |
| Phụ lục | `PHỤ LỤC A`, `A.1`, `A.1.1` (mẫu đánh `5.1` là sai) |
| Số trang | La Mã cho toàn bộ phần đầu đến hết Danh mục bảng; "Mở đầu" là trang 1 |
| Heading | Chương: 18pt đậm IN HOA căn giữa, sang trang mới · cấp 2 đậm · cấp 3 đậm nghiêng · cấp 4 nghiêng · tối đa 4 cấp |
| Thân bài | Times New Roman 13pt, giãn dòng 1.5, căn đều, thụt dòng đầu 1.27 cm, lề trái 3.5 cm |
| Gạch đầu dòng | `- **Nhãn**: giải thích.` cấp 1 `-`, cấp 2 `o` |
| Trích dẫn | IEEE `[n]`, đánh số theo thứ tự xuất hiện lần đầu |

Trong bản thảo Markdown:
- Heading Markdown `#` = Chương, `##` = x.y, `###` = x.y.z, `####` = x.y.z.w. Ghi số mục ngay trong heading (`## 3.2. Thiết kế hệ thống`).
- Hình: `![Hình 3.1. Biểu đồ ca sử dụng tổng quát hệ thống VDiab.](figures/hinh-3-1_usecase-tong-quat.png)` rồi dòng caption nghiêng ngay dưới: `*Hình 3.1. Biểu đồ ca sử dụng tổng quát hệ thống VDiab.*`
- Bảng: dòng caption nghiêng **trên** bảng: `*Bảng 2.1. So sánh các phương pháp truy xuất tri thức.*`
- Trích dẫn: `[n]` trong văn bản; danh sách ở `95_tai-lieu-tham-khao.md`.

---

## 3. Cấu trúc thư mục bản thảo

Toàn bộ bản thảo đặt trong `docs/report/khoaluan/` (tạo nếu chưa có). Mỗi chương một file để agent sửa song song không đụng nhau.

```
docs/report/
├── 11226912_...NEUOJ.pdf                # bản mẫu — chỉ đọc
├── PHAN_TICH_BAO_CAO_MAU_NEUOJ.md       # phân tích bản mẫu
├── HUONG_DAN_AGENT_VIET_KHOA_LUAN.md    # file này
└── khoaluan/
    ├── 00_phan-dau.md          # bìa (thông tin), lời cam đoan, lời cảm ơn
    ├── 01_mo-dau.md
    ├── 10_chuong-1.md
    ├── 20_chuong-2.md
    ├── 30_chuong-3.md
    ├── 40_chuong-4.md
    ├── 90_ket-luan.md
    ├── 95_tai-lieu-tham-khao.md   # danh sách IEEE, số [n] cố định theo thứ tự xuất hiện
    ├── 99_phu-luc.md
    ├── viet-tat.md                # bảng "Từ viết tắt | Ý nghĩa"
    ├── so-lieu.md                 # sổ số liệu: mỗi con số dùng trong bài → nguồn + ngày lấy
    ├── danh-muc-hinh-bang.md      # danh sách Hình/Bảng hiện có, file ảnh, mục chứa
    └── figures/
        ├── hinh-3-1_usecase-tong-quat.png
        └── src/                   # nguồn sơ đồ (.mmd / .puml / .drawio) để sửa lại được
```

Quy ước:
- Tên ảnh: `hinh-<chương>-<stt>_<slug-khong-dau>.png`. Đổi số hình → đổi tên file và cập nhật `danh-muc-hinh-bang.md`.
- Mọi sơ đồ vẽ mới phải có file nguồn trong `figures/src/`. Có thể tái dùng/vẽ lại từ [docs/diagrams/](../diagrams/) và các block Mermaid trong [ARCHITECTURE.md](../../ARCHITECTURE.md), [docs/architecture_diagram.md](../architecture_diagram.md).
- Ảnh chụp giao diện lấy từ app chạy thật với tài khoản demo ([docs/demo_accounts.csv](../demo_accounts.csv)); không để lộ dữ liệu bệnh nhân thật.
- `so-lieu.md` mỗi dòng: `| Con số | Ý nghĩa | Mục dùng | Nguồn (đường dẫn file / lệnh chạy) | Commit hoặc ngày |`.

---

## 4. Khung khóa luận VDiab: mục nào trình bày gì, lấy dữ liệu ở đâu

Cột "Khuôn" trỏ tới khuôn văn ở [PHAN_TICH_BAO_CAO_MAU_NEUOJ.md §4.2](PHAN_TICH_BAO_CAO_MAU_NEUOJ.md#42-các-khuôn-đoạn-lặp-lại). Độ dài là định hướng theo bản mẫu, tính bằng trang A4 đã dàn.

### Phần đầu — `00_phan-dau.md`
| Mục | Trình bày | Nguồn |
|---|---|---|
| Bìa ngoài / bìa trong | Tên trường, "KHÓA LUẬN TỐT NGHIỆP", tên đề tài, SV, MSSV, lớp, ngành, khoa, GVHD, nơi + tháng năm; gáy: niên khóa · ngành · tên SV | Hỏi người dùng (không đoán tên/MSSV/GVHD) |
| Lời cam đoan | 2 đoạn theo mẫu, thay tên đề tài và GVHD | Mẫu trang v |
| Lời cảm ơn | trường/khoa → GVHD → thầy cô, bạn bè, (bác sĩ/bệnh nhân tham gia pilot nếu có) → lời khiêm tốn | Mẫu trang vi |
| Danh mục viết tắt / hình / bảng | Sinh từ `viet-tat.md`, `danh-muc-hinh-bang.md` | — |

### Mở đầu — `01_mo-dau.md` (≈1 trang, 3 đoạn, không trích dẫn)
Bối cảnh (gánh nặng ĐTĐ típ 2, tự theo dõi tại nhà, chuyển đổi số y tế) → giới thiệu đề tài và tính năng cốt lõi bằng văn xuôi → mục tiêu và tầm nhìn. Nguồn: [PRD §1](../PRD.md), [README](../../README.md) đoạn mở.

### Chương 1. Tổng quan về đề tài — `10_chuong-1.md` (≈5–6 trang)
| Mục | Trình bày | Nguồn trong repo | Khuôn |
|---|---|---|---|
| 1.1. Đặt vấn đề | "Về phía bệnh nhân…" (ghi nhận thiếu, khó nhận biết bất thường, thiếu hướng dẫn tin cậy) → "Về phía bác sĩ…" (thiếu dữ liệu giữa hai kỳ tái khám) → khoảng trống → tên đề tài. Mỗi nhận định có `[n]` | [PRD §1 Pain points](../PRD.md), [docs/USER_RESEARCH.md](../USER_RESEARCH.md) | 4.2.a |
| 1.2.1. Mục tiêu tổng quát | 1 câu "Xây dựng thành công…" | PRD đoạn tóm tắt | — |
| 1.2.2. Mục tiêu cụ thể | 5–7 nhiệm vụ đánh số `**Nhãn**: …`; **ít nhất 2 mục có chỉ tiêu định lượng** (vd. 100% giá trị vượt ngưỡng được Rule Engine phát hiện; ≥90% tư vấn được bác sĩ đánh giá phù hợp; tỉ lệ trích dẫn hợp lệ) — chỉ đặt chỉ tiêu mà Ch4 sẽ đo được | [PRD §2 Goals & metrics](../PRD.md), [eval/README.md §4](../../eval/README.md) | — |
| 1.3. Đối tượng nghiên cứu | *Thứ được nghiên cứu*: quy trình theo dõi ĐTĐ típ 2 giữa hai kỳ tái khám; kiến trúc AI Agent (LangGraph); RAG trên guideline; cơ chế guardrail an toàn y khoa; mô hình ML cá nhân hóa ngưỡng theo dõi; kiến trúc web | ARCHITECTURE, [docs/AI_Summary_Respond_System_Design.md](../AI_Summary_Respond_System_Design.md), [docs/ML_PERSONALIZED_WATCH_SYSTEM_PLAN.md](../ML_PERSONALIZED_WATCH_SYSTEM_PLAN.md) | gạch đầu dòng |
| 1.4.1. Phạm vi người dùng & triển khai | Bệnh nhân ĐTĐ típ 2, bác sĩ điều trị, admin; quy mô pilot (5–10 BN, 1–2 BS, 6 tuần) — chỉ ghi quy mô **thực tế đã chạy** | PRD §3, §4 | — |
| 1.4.2. Phạm vi chức năng | Nhóm chức năng theo F1–F4 của PRD + các module đã có code (thuốc, lịch hẹn, thông báo/Zalo, nhập liệu giọng nói nếu đã chạy) | PRD §5–6, `codebase/backend/*/router.py` | gạch đầu dòng |
| 1.4.3. Phạm vi phi chức năng | Hiệu năng, bảo mật & quyền riêng tư dữ liệu sức khỏe, an toàn y khoa, UI/UX (mobile), khả năng mở rộng | PRD §7, ARCHITECTURE "Non-Functional Targets" | — |
| 1.4.4. Ngoài phạm vi | Danh sách "Won't" của PRD (chẩn đoán, kê đơn, CGM/wearable, cấp cứu tự động, giám sát 24/7…) + mọi tính năng chỉ ở mức thiết kế | PRD §5 Won't, ARCHITECTURE "Out of Scope", [docs/tasks/](../tasks/README.md) | — |
| 1.5. Phương pháp nghiên cứu *(mẫu thiếu — nên thêm)* | Nghiên cứu tài liệu; phân tích yêu cầu (PRD, phỏng vấn); phát triển lặp; đánh giá bằng golden dataset + chuyên gia + kiểm thử | eval/, docs/evaluation/ | — |
| 1.6. Đóng góp của đề tài | 3 đoạn: sản phẩm → đóng góp kỹ thuật (kiến trúc tách Rule Engine khỏi LLM, RAG có kiểm chứng trích dẫn, …) → thực tiễn (chỉ nêu cái đã có bằng chứng) | — | — |

### Chương 2. Cơ sở lý thuyết và tổng quan hệ thống liên quan — `20_chuong-2.md` (≈10–12 trang)
| Mục | Trình bày | Nguồn | Khuôn |
|---|---|---|---|
| 2.1. Bệnh ĐTĐ típ 2 và tự theo dõi | Khái niệm, chỉ số (đường huyết lúc đói/sau ăn, HbA1c, huyết áp), SMBG, mục tiêu cá nhân hóa theo ADA/Bộ Y tế, vai trò dữ liệu liên tục giữa các kỳ tái khám | `data/knowledge_base/` (danh mục guideline), tài liệu y khoa ngoài | 4.2.d |
| 2.2. Lịch sử phương pháp quản lý bệnh mạn tính | Chia giai đoạn: sổ tay giấy → ứng dụng ghi nhật ký/telehealth → hệ thống có AI. Mỗi giai đoạn: đại diện, cơ chế, điểm bất lợi | Tài liệu ngoài | 4.2.c |
| 2.3. Cơ sở lý thuyết kỹ thuật | LLM; AI Agent & đồ thị trạng thái (LangGraph); RAG (chunking, embedding, vector DB); guardrail & kiểm chứng trích dẫn; rule engine cảnh báo ngưỡng; mô hình ML (logistic regression, gradient boosting, quantile regression, hiệu chuẩn xác suất); (STT nếu dùng). Có **ít nhất 1 bảng so sánh phương án** (vd. fine-tune vs RAG vs prompt-only; hoặc các vector DB) | [docs/backendDocs/ai-flow.md](../backendDocs/ai-flow.md), `codebase/models/`, `artifacts/personalized_watch/*/model_card.md` | 4.2.e |
| 2.4. Hệ thống hiện có | Nhóm ứng dụng thương mại & nhóm nghiên cứu/mã nguồn mở: điểm mạnh → "Đặt trong bối cảnh bệnh nhân Việt Nam và bác sĩ điều trị, …không phù hợp" | [docs/COMPETITOR_ANALYSIS.md](../COMPETITOR_ANALYSIS.md) | 4.2.f |

### Chương 3. Phân tích và thiết kế hệ thống — `30_chuong-3.md` (≈25–40 trang)
| Mục | Trình bày | Nguồn | Ghi chú |
|---|---|---|---|
| 3.1.1. Tác nhân và use case tổng quát | Bảng tác nhân (Bệnh nhân, Bác sĩ, Admin, hệ thống ngoài: LLM provider, Zalo) + Hình use case tổng quát + 2–3 câu giải thích | PRD §3, [docs/Danh sách User Story.md](../Danh%20sách%20User%20Story.md) | |
| 3.1.2. Đặc tả use case chính *(mẫu thiếu)* | Bảng đặc tả cho 4–6 use case quan trọng: Ghi chỉ số sức khỏe, Nhận cảnh báo ngưỡng, Hỏi đáp AI, Xem tóm tắt bệnh nhân (bác sĩ), Quản lý thuốc, Đăng nhập. Mỗi bảng: tác nhân, tiền điều kiện, luồng chính, luồng thay thế/ngoại lệ, hậu điều kiện | PRD §6 acceptance criteria | |
| 3.1.3. Biểu đồ hoạt động | Chỉ cho luồng có rẽ nhánh đáng kể (ghi đường huyết → Rule Engine → cảnh báo; hỏi đáp AI → guardrail → từ chối/trả lời có trích dẫn). **Gộp CRUD đơn giản**, không vẽ 1 hình/1 thao tác. Mỗi hình có lời dẫn | — | sửa lỗi mẫu |
| 3.1.4. Phân tích cấu trúc | Biểu đồ lớp/ERD + giải thích các thực thể chính và quan hệ | `codebase/backend/*/models.py` | |
| 3.1.5. Phân tích hành vi | Biểu đồ tuần tự cho: đăng nhập JWT, ghi chỉ số + cảnh báo, AI Consultation (resolve context → retrieve → synthesize → verify citations → guardrail), AI Summary cho bác sĩ, nhắc lịch/Zalo | `codebase/models/agents/graph.py`, `nodes/`, `codebase/backend/notifications/` | |
| 3.2.1. Kiến trúc tổng thể | Modular monolith, lý do không dùng microservice (quy mô đội, pilot), Hình kiến trúc tổng quan có icon + luồng dữ liệu (như Hình 3.49 mẫu) | ARCHITECTURE "System Overview", "Design Decisions", [docs/diagrams/system_overview.png](../diagrams/system_overview.png) | |
| 3.2.2. Thiết kế AI Agent | Đồ thị node của LangGraph, vai trò từng node, Tool Registry (agent không tự nhớ số liệu), guardrail, cờ "Cần bác sĩ xem xét" | [docs/AI_Summary_Respond_System_Design.md](../AI_Summary_Respond_System_Design.md), [docs/diagrams/agent_flow.png](../diagrams/agent_flow.png), `codebase/models/agents/` | trọng tâm của đề tài |
| 3.2.3. Thiết kế RAG & quản lý nguồn tri thức | Ingest, chunking, vector store, chỉ dùng nguồn "Đã duyệt" | `codebase/models/rag/` | |
| 3.2.4. Thiết kế mô hình ML theo dõi cá nhân hóa | Bài toán, đặc trưng, các phiên bản v0/v1/v2, lý do chọn | `artifacts/personalized_watch/*/model_card.md`, `feature_schema.json`, [docs/ML_HANDOFF_PERSONALIZED_ATTENTION.md](../ML_HANDOFF_PERSONALIZED_ATTENTION.md) | chỉ viết nếu đã tích hợp vào hệ thống |
| 3.2.5. Thiết kế cơ sở dữ liệu *(mẫu thiếu)* | Bảng các bảng/thực thể chính: tên, mô tả, khóa, ràng buộc quan trọng | `models.py` các module, `codebase/backend/db.py` | |
| 3.2.6. Thiết kế API và bảo mật | Nhóm endpoint theo module, JWT + RBAC (Patient/Doctor/Admin), SQL-injection guard, phân quyền truy cập bệnh nhân | [docs/api/openapi.yaml](../api/openapi.yaml), `codebase/backend/auth/`, `security/`, [docs/THREAT_MODEL.md](../THREAT_MODEL.md) | |
| 3.2.7. Nền tảng công nghệ | Mỗi công nghệ 1 tiểu mục theo khuôn 4.2.b (định nghĩa → lý do chọn → vai trò trong VDiab): FastAPI, SQLModel/PostgreSQL, LangGraph/LangChain, LLM provider, vector store, Next.js, Docker, tracing (Langfuse/LangSmith), … **Danh sách lấy từ code, không từ README** | `requirements*.txt`, `codebase/frontend/package.json`, `docker-compose*.yml` | xem §8 |
| 3.2.8. Biểu đồ gói & triển khai | Gói theo tầng; triển khai theo container thật + giải thích từng node và giao thức | [docs/diagrams/deployment.png](../diagrams/deployment.png), ARCHITECTURE "Deployment Architecture" | thành phần chưa chạy ghi "(dự kiến)" |

### Chương 4. Kết quả triển khai, đánh giá — `40_chuong-4.md` (≈15–25 trang)
| Mục | Trình bày | Nguồn | Trạng thái dữ liệu hiện tại |
|---|---|---|---|
| 4.1. Môi trường triển khai và thử nghiệm | Máy/VPS, CPU, RAM, ổ đĩa, OS, phiên bản Python/Node, model LLM dùng | Hỏi người dùng / máy chạy thật | cần thu thập |
| 4.2. Kết quả xây dựng chức năng | Ảnh chụp theo vai trò: Bệnh nhân (dashboard, ghi chỉ số, bữa ăn, thuốc, trợ lý AI) → Bác sĩ (danh sách bệnh nhân, chi tiết, tóm tắt AI, ghi chú) → Admin. Mỗi ảnh 1–2 câu dẫn | `codebase/frontend/app/(app)/*` | cần chụp |
| 4.3. Đánh giá chất lượng AI *(trọng tâm thay cho "chấm bài")* | Phương pháp (golden dataset, tiêu chí, người chấm) → bảng kết quả (độ đúng, tỉ lệ trích dẫn hợp lệ, tỉ lệ từ chối đúng câu hỏi vượt phạm vi, vi phạm an toàn) → diễn giải | [eval/README.md](../../eval/README.md), `eval/results/`, [docs/evaluation/](../evaluation/README.md), [docs/AI_Eval_Metrics_Plan.md](../AI_Eval_Metrics_Plan.md) | đọc `eval/results/` lấy lần chạy mới nhất |
| 4.4. Đánh giá mô hình ML | Bảng metrics các phiên bản v0/v1/v2, so sánh, lý do chọn bản dùng | `artifacts/personalized_watch/*/metrics.json` | có sẵn |
| 4.5. Kiểm thử phần mềm | Số test, phạm vi, coverage, kết quả CI | [docs/QUALITY_REPORT.md](../QUALITY_REPORT.md), `tests/`, `.github/workflows/ci.yml` | lấy "Latest verified local run" |
| 4.6. Đánh giá hiệu năng | Phương pháp + công cụ + kịch bản → bảng (mức tải, p50/p95, error rate) tách API thường và AI → diễn giải nút thắt | [docs/PERFORMANCE_REPORT.md](../PERFORMANCE_REPORT.md) (protocol) | **chưa đo** — phải chạy trước khi viết |
| 4.7. Kết quả triển khai thực tế / pilot | Số người dùng, số bản ghi, thời gian pilot — chỉ khi có thật | [docs/OPERATIONS_RUNBOOK.md](../OPERATIONS_RUNBOOK.md), DB pilot | hỏi người dùng |
| 4.8. Đánh giá tổng thể | Bảng so sánh ✓/× VDiab vs nhóm đối thủ (tiêu chí lấy từ điểm yếu ở 2.4); Ưu điểm; Hạn chế; Rủi ro (dữ liệu sức khỏe nhạy cảm, ảo giác LLM, phụ thuộc nhà cung cấp LLM, quá tải) theo khuôn 4.2.i | COMPETITOR_ANALYSIS, THREAT_MODEL | |
| 4.9. Đánh giá từ người dùng | Phỏng vấn/khảo sát bệnh nhân và bác sĩ, quote, thang đo nếu có; nếu chưa khảo sát thì nói rõ giới hạn như mẫu | [docs/USER_RESEARCH.md](../USER_RESEARCH.md), [docs/UX_EVALUATION.md](../UX_EVALUATION.md), `docs/evaluation/human_review.csv` | kiểm tra sổ người tham gia |
| 4.10. Định hướng phát triển | Các epic chưa làm: meal intelligence, báo cáo tuần, tuân thủ thuốc, brief tái khám, caregiver… mỗi hướng 1 đoạn: mục tiêu → cách tiếp cận → ràng buộc an toàn | [docs/tasks/](../tasks/README.md) | chuyển epic xong sang Ch3/Ch4 |

### Kết luận — `90_ket-luan.md` (≈1 trang)
Bối cảnh → kết quả kỹ thuật → kết quả thực tế/đánh giá → bài học (ngôi "tôi") → hạn chế và cam kết phát triển. **Không thêm số liệu mới, không lặp lời cảm ơn.**

### Tài liệu tham khảo — `95_tai-lieu-tham-khao.md`
IEEE, đánh số theo thứ tự xuất hiện lần đầu. Mục tiêu 20–30 nguồn: guideline y khoa, nghiên cứu về tự theo dõi ĐTĐ, LLM trong y tế, RAG, agent, đánh giá AI y tế, tài liệu kỹ thuật chính thức.

### Phụ lục — `99_phu-luc.md`
- **Phụ lục A – Xây dựng hệ thống:** cấu trúc module backend, cấu hình CSDL & `.env`, AI Agent (prompt hệ thống rút gọn, danh sách tool), RAG ingest, frontend (App Router, component, theme), giám sát/tracing.
- **Phụ lục B – Triển khai & vận hành:** Docker Compose, CI/CD, sao lưu, runbook rút gọn ([docs/OPERATIONS_RUNBOOK.md](../OPERATIONS_RUNBOOK.md)).
- **Phụ lục C – Mẫu golden dataset & phiếu đánh giá chuyên gia.**
- **Phụ lục D – Danh sách API** (rút gọn từ OpenAPI).
- **Kết quả Turnitin** (trang cuối, do người dùng cung cấp).

---

## 5. Bảng tra: muốn sửa X thì sửa ở đâu, kéo theo gì

| Thay đổi | Sửa ở | Phải cập nhật kèm |
|---|---|---|
| Thêm/bỏ tính năng trong phạm vi | 1.4.2 | 3.1 (use case, đặc tả), 4.2 (ảnh), bảng so sánh 4.8, Mở đầu, Kết luận |
| Tính năng chuyển từ "định hướng" sang "đã làm" | 4.10 → 3.x + 4.2 | 1.4.4 (bỏ khỏi ngoài phạm vi), Hạn chế 4.8, hình triển khai (bỏ "dự kiến") |
| Đổi/thêm mục tiêu cụ thể | 1.2.2 | Bảng truy vết §6, mục đo tương ứng ở Ch4, Kết luận |
| Có số liệu đo mới (eval, hiệu năng, test) | Mục tương ứng Ch4 | `so-lieu.md`, bảng truy vết §6, 1.6 Đóng góp nếu số nổi bật, Kết luận nếu là kết quả chính |
| Đổi công nghệ (vd. vector store) | 3.2.7 | 2.3 (lý thuyết), hình kiến trúc + triển khai, Phụ lục A/B, `viet-tat.md` |
| Đổi kiến trúc agent (thêm/bớt node, tool) | 3.2.2 | Biểu đồ tuần tự AI ở 3.1.5, nguồn hình trong `figures/src/`, Phụ lục A |
| Đổi schema CSDL | 3.2.5 | Biểu đồ lớp 3.1.4 |
| Thêm/xóa/đổi thứ tự hình hoặc bảng | Chương chứa | Đánh lại số trong chương, mọi câu "như Hình x.y", tên file ảnh, `danh-muc-hinh-bang.md` |
| Thêm trích dẫn | Vị trí trong văn bản | Đánh lại `[n]` các trích dẫn đứng sau nếu chèn giữa; `95_tai-lieu-tham-khao.md` |
| Thêm viết tắt / thuật ngữ | Lần xuất hiện đầu tiên | `viet-tat.md` |
| Thêm đối thủ / tiêu chí so sánh | 2.4 | Bảng so sánh 4.8 |
| Phát hiện rủi ro mới (bảo mật, an toàn y khoa) | 4.8 Rủi ro | 1.4.3 nếu thành yêu cầu phi chức năng; 3.2.6 nếu có biện pháp đã cài |

---

## 6. Bảng truy vết mục tiêu ↔ kết quả

Giữ bảng này trong `so-lieu.md` (hoặc đầu `40_chuong-4.md` dưới dạng comment) và cập nhật mỗi khi đổi 1.2.2 hoặc Ch4. Mục tiêu nào chưa có kết quả thì hoặc đo, hoặc hạ thành định hướng — không để treo.

| # | Mục tiêu cụ thể (1.2.2) | Chỉ tiêu | Mục kết quả | Kết quả thực tế | Nguồn |
|---|---|---|---|---|---|
| 1 | Nghiên cứu cơ sở lý thuyết | — | Ch2 | — | — |
| 2 | Phân tích & thiết kế hệ thống | — | Ch3 | — | — |
| 3 | Cảnh báo ngưỡng bằng Rule Engine | 100% giá trị vượt ngưỡng được phát hiện | 4.3 / 4.5 | *chưa điền* | tests/, eval/ |
| 4 | Trợ lý AI có căn cứ, an toàn | ≥90% phù hợp; 100% khẳng định y khoa có trích dẫn | 4.3 | *chưa điền* | eval/results/ |
| 5 | Hiệu năng | p95 CRUD ≤1s, AI ≤10s (đề xuất) | 4.6 | *chưa đo* | PERFORMANCE_REPORT |
| 6 | Kiểm thử & đánh giá người dùng | — | 4.5, 4.9 | *chưa điền* | QUALITY_REPORT, USER_RESEARCH |

---

## 7. Khuôn câu dùng nhanh

- **Đặt vấn đề:** "Trong bối cảnh …, việc … không chỉ dừng lại ở … mà đòi hỏi … Tuy nhiên, … đang bộc lộ một số bất cập [n]."
- **Chuyển sang đề tài:** "Những hạn chế nêu trên tạo ra một khoảng trống cấp thiết, đòi hỏi …. Chính vì vậy, đề tài **"…"** được thực hiện nhằm …"
- **Giới thiệu công nghệ:** "Để …, đề tài lựa chọn **X** – … Việc lựa chọn X dựa trên một số ưu thế như sau: … Trong hệ thống VDiab, X đóng vai trò … chịu trách nhiệm …"
- **Dẫn nghiên cứu:** "Nghiên cứu của <Tác giả> et al. (<năm>) trên <cỡ mẫu> cho thấy <số liệu> [n]. Tuy nhiên, <Tác giả> lưu ý rằng …"
- **Dẫn hình:** "Luồng xử lý khi bệnh nhân ghi nhận chỉ số đường huyết được thể hiện ở Hình 3.6. Sau khi …, hệ thống … Trường hợp …, …"
- **Diễn giải số liệu:** "Kết quả tại Bảng 4.3 cho thấy …. Điều này xuất phát từ …. Như vậy, …"
- **Giới hạn:** "Do giới hạn về thời gian thực hiện đề tài và phạm vi triển khai …, khóa luận chưa …. Thay vào đó, …"
- **Rủi ro:** "<Kịch bản>. Điều này dẫn đến <hệ quả>. Hiện tại hệ thống đã <biện pháp>, tuy nhiên <rủi ro còn lại>."

---

## 8. Mâu thuẫn đã biết trong repo — phải xác minh trước khi viết

Ghi nhận ngày 2026-10-26. Khi đã xác minh, sửa dòng tương ứng ở đây.

| Chủ đề | Tài liệu nói | Code/cấu hình cho thấy | Việc cần làm |
|---|---|---|---|
| Vector store | README, ARCHITECTURE: ChromaDB | `codebase/models/rag/vector_store.py` hỗ trợ cả hai: Chroma embedded (mặc định) hoặc Qdrant qua `VECTOR_STORE=qdrant`; `docker-compose.yml` có service `qdrant` | Xem `.env` của môi trường triển khai thật; viết đúng backend đang chạy, có thể nêu thiết kế cho phép chuyển đổi |
| Tracing AI | ARCHITECTURE: LangSmith | `codebase/models/tracing.py`: Langfuse (thay LangSmith), chỉ bật khi có key | Viết là Langfuse |
| Frontend | ARCHITECTURE: "React SPA" | Next.js 16 App Router (README, `codebase/frontend/`) | Viết là Next.js |
| Đánh số mục tiêu PRD | PRD §2 liệt kê mục tiêu F1–F6 | PRD §5–6 chỉ có tính năng F1–F4 | Trong khóa luận, đánh số mục tiêu theo 1.2.2 riêng, không chép mã F của PRD |
| Hiệu năng | — | PERFORMANCE_REPORT: mọi workload *Unverified* | Đo theo protocol trong file đó trước khi viết 4.6 |
| Dữ liệu pilot | ARCHITECTURE: "dữ liệu thật với consent" | Chưa thấy evidence pilot đã chạy | Hỏi người dùng; nếu chưa có, không viết mục 4.7 như đã triển khai |
| Dịch vụ phụ (STT, TTS, Zalo) | Có thư mục/compose riêng | Chưa rõ đã tích hợp vào luồng chính | Chỉ đưa vào phạm vi nếu chạy được end-to-end |

---

## 9. Checklist trước khi bàn giao một lần sửa

- [ ] Mọi con số mới đã có dòng trong `so-lieu.md` với nguồn.
- [ ] Không có tính năng chưa làm được mô tả như đã làm; thành phần dự kiến được đánh dấu.
- [ ] Hình/bảng mới: đúng số theo chương, có caption đúng vị trí, có câu dẫn và giải thích, có file nguồn trong `figures/src/`.
- [ ] Đã chạy lại đánh số hình/bảng/trích dẫn nếu chèn giữa; `danh-muc-hinh-bang.md` khớp.
- [ ] Viết tắt mới đã vào `viet-tat.md`.
- [ ] Đã làm hết các mục "kéo theo" ở [§5](#5-bảng-tra-muốn-sửa-x-thì-sửa-ở-đâu-kéo-theo-gì).
- [ ] Bảng truy vết §6 vẫn khớp giữa 1.2.2 và Ch4.
- [ ] Rà chính tả tiếng Việt và tên riêng (các lỗi điển hình của bản mẫu ở [PHAN_TICH_BAO_CAO_MAU_NEUOJ.md §7](PHAN_TICH_BAO_CAO_MAU_NEUOJ.md#7-lỗi-trong-mẫu--không-lặp-lại)).
- [ ] Nội dung AI vẫn tuân thủ ràng buộc an toàn y khoa (nguyên tắc 4).
- [ ] Không có thông tin định danh bệnh nhân thật trong văn bản hoặc ảnh chụp.
