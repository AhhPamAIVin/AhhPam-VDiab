# PRD — VDiab: Hệ thống AI hỗ trợ theo dõi và cảnh báo bất thường cho bệnh nhân đái tháo đường

**Phiên bản:** 0.4 · **Trạng thái:** Draft để xác thực với người dùng · **Co-primary personas:** Bệnh nhân đái tháo đường típ 2 và bác sĩ trực tiếp điều trị

> VDiab giúp bệnh nhân đái tháo đường típ 2 ghi nhận và theo dõi các chỉ số sức khỏe hằng ngày theo kế hoạch, đồng thời nhận nhắc nhở, xu hướng, cảnh báo bất thường và tư vấn lối sống có căn cứ y khoa; giúp bác sĩ theo dõi tình trạng của nhiều bệnh nhân giữa các kỳ tái khám. Sản phẩm hỗ trợ theo dõi và ra quyết định, không thay thế bác sĩ trong chẩn đoán, kê đơn hoặc quyết định điều trị.

## 1. Problem statement

Bệnh nhân đái tháo đường típ 2 phải tự theo dõi đường huyết tại nhà (SMBG) và ghi nhận các yếu tố liên quan như bữa ăn, vận động, thuốc đã dùng theo kế hoạch của bác sĩ. Trong thực tế, việc ghi chép hiện chủ yếu thực hiện thủ công và phân tán qua sổ tay, thiết bị đo hoặc nhiều ứng dụng rời rạc nên dễ bị quên, thiếu ngữ cảnh hoặc nhập dữ liệu không nhất quán. Bệnh nhân vì vậy khó xác định chỉ số có nằm trong mục tiêu cá nhân, khó nhận biết xu hướng và không biết khi nào cần liên hệ bác sĩ.

Giữa hai kỳ tái khám, thường cách nhau 1–3 tháng, bác sĩ không có đủ dữ liệu liên tục và có cấu trúc để theo dõi diễn biến. Việc đánh giá thường phụ thuộc vào trí nhớ của bệnh nhân, nhật ký không đầy đủ hoặc một số chỉ số tại thời điểm khám, làm hạn chế khả năng xác định bệnh nhân cần ưu tiên và đưa ra quyết định điều trị có căn cứ.

**Giả thuyết baseline cần kiểm chứng:** bệnh nhân đái tháo đường típ 2 không ghi nhận đầy đủ các lần đo theo kế hoạch hoặc thiếu thông tin về hoàn cảnh đo; bác sĩ phải dành nhiều thời gian tái khám để hỏi lại và tổng hợp lịch sử. Trước khi chốt PRD, nhóm cần phỏng vấn tối thiểu 3–5 bệnh nhân/người chăm sóc và 1–2 bác sĩ nội tiết; quan sát quy trình đo–ghi hiện tại; đo tỷ lệ bản ghi đầy đủ, thời gian bác sĩ tổng hợp lịch sử và thu thập bộ guideline được phép sử dụng.

### Pain points ưu tiên

| Pain point | Quy trình hiện tại → failure | Tác động | Root cause |
|---|---|---|---|
| 1. Bệnh nhân ghi nhận dữ liệu sức khỏe không đầy đủ và thiếu nhất quán | Ghi đường huyết, bữa ăn, vận động và thuốc vào nhiều công cụ hoặc sổ tay → quên ghi, thiếu ngữ cảnh hoặc thất lạc dữ liệu | Nhật ký không phản ánh đủ quá trình theo dõi, làm bệnh nhân và bác sĩ khó sử dụng dữ liệu khi tái khám đánh giá | Dữ liệu phân tán, phụ thuộc thao tác thủ công, thiếu cấu trúc và nhắc nhở theo kế hoạch |
| 2. Bệnh nhân khó nhận biết bất thường và xu hướng đường huyết | Xem từng số đo và tự đối chiếu với ngưỡng chung → khó so sánh so với mục tiêu cá nhân, nhận ra xu hướng và xác định bản thân nên làm gì tiếp theo | Có thể bỏ qua bất thường hoặc không biết khi nào cần liên hệ bác sĩ | Thiếu cách tổng hợp theo thời gian, mục tiêu cá nhân và cảnh báo theo ngưỡng đã được bác sĩ thiết lập |
| 3. Bệnh nhân thiếu hướng dẫn lối sống đáng tin cậy | Tìm thông tin ăn uống, vận động trên Internet hoặc theo kinh nghiệm truyền miệng → nội dung chung chung, mâu thuẫn hoặc thiếu căn cứ |Khó đánh giá độ tin cậy và có thể áp dụng lời khuyên không phù hợp | Thiếu nguồn tri thức y khoa được lựa chọn, kiểm duyệt và cập nhật |
| 4. Bác sĩ thiếu dữ liệu giữa các kỳ tái khám | Bác sĩ theo dõi nhiều bệnh nhân nhưng chỉ nhận nhật ký khi bệnh nhân gửi hoặc đến khám → phải dựa vào trí nhớ và dữ liệu không đầy đủ | Khó theo dõi diễn biến, ưu tiên bệnh nhân cần chú ý và đưa ra quyết định điều trị có căn cứ | Chưa có kênh tập trung để tiếp nhận, tổng hợp và theo dõi dữ liệu của nhiều bệnh nhân theo thời gian |

Đây là các pain point hợp lý theo nghiệp vụ nhưng **chưa phải evidence từ người dùng mục tiêu**. Cơ sở chuyên môn yêu cầu mục tiêu kiểm soát và phương pháp điều trị được cá nhân hóa bởi bác sĩ; dữ liệu sức khỏe đồng thời phải được bảo vệ nghiêm ngặt. Vì vậy hệ thống chỉ hỗ trợ ghi nhận, tổng hợp, cảnh báo và tư vấn lối sống có căn cứ.

Mục tiêu đường huyết, ngưỡng cảnh báo và phương pháp điều trị phải được cá nhân hóa bởi bác sĩ. Hệ thống không tự chẩn đoán, kê đơn hoặc thay đổi thuốc/insulin ([Quyết định 5481/QĐ-BYT](https://daithaoduong.kcb.vn/huong-dan-chan-doan-va-dieu-tri-dai-thao-duong-tip-2), [ADA Standards of Care 2026](https://professional.diabetes.org/standards-of-care)).

Dữ liệu đường huyết, thuốc và tình trạng bệnh là dữ liệu sức khỏe nhạy cảm. Việc xử lý phải có mục đích, consent hợp lệ, phân quyền, bảo mật và truy vết phù hợp với [Luật Bảo vệ dữ liệu cá nhân 91/2025/QH15](https://vanban.chinhphu.vn/?classid=1&docid=214590&pageid=27160&typegroupid=3), [Nghị định 356/2025/NĐ-CP](https://vanban.chinhphu.vn/?docid=216387&pageid=27160) và [Luật Khám bệnh, chữa bệnh 15/2023/QH15](https://vanban.chinhphu.vn/?docid=207396&pageid=27160).

## 2. Goals & metrics

Trong pilot **6 tuần** với 5–10 bệnh nhân đái tháo đường típ 2 và 1–2 bác sĩ điều trị:

- **F1:** Ít nhất **80% số lần đo theo kế hoạch** được ghi nhận đầy đủ và tăng ít nhất **30% so với baseline tuần đầu**; một bản ghi đầy đủ có giá trị, đơn vị, thời gian và ngữ cảnh đo.
- **F2:** Ít nhất **80% bệnh nhân** đạt tối thiểu 4/5 tình huống trong bài kiểm tra cuối pilot về đọc xu hướng, nhận biết trạng thái so với mục tiêu và xác định khi nào cần liên hệ bác sĩ.
- **F3:** **100% giá trị đạt ngưỡng cảnh báo đã cấu hình** được rule engine phát hiện và hiển thị đúng mức trên bộ dữ liệu kiểm thử; không bỏ sót trường hợp nguy hiểm trong bộ test.
- **F4:** Ít nhất **90% nội dung tư vấn lối sống** được bác sĩ đánh giá phù hợp, có căn cứ và không vượt phạm vi trên bộ 30 tình huống chuẩn; **100% nội dung khẳng định y khoa** có trích dẫn hợp lệ.
- **F5:** Bác sĩ xác định được bệnh nhân cần ưu tiên trong **≤ 2 phút** từ dashboard và giảm ít nhất **30% thời gian trung vị tổng hợp lịch sử** so với baseline.
- **F6:** **100% cờ “Cần bác sĩ xem xét” do AI tạo** được chuyển tới dashboard; AI không tạo hoặc gửi đề xuất cụ thể về thuốc, liều dùng, điều chỉnh insulin hay mục tiêu điều trị mới.

## 3. Persona

**Co-primary — Bệnh nhân đái tháo đường típ 2:** tự theo dõi đường huyết tại nhà theo kế hoạch của bác sĩ; có thể sử dụng thuốc uống hoặc insulin; mức độ hiểu biết về bệnh và khả năng sử dụng công nghệ không đồng đều. Mục tiêu là ghi nhận dữ liệu thuận tiện, biết mức độ hoàn thành phác đồ, nhận cảnh báo và tư vấn lối sống có căn cứ để biết khi nào cần liên hệ bác sĩ, nhưng không tự thay đổi thuốc hoặc phác đồ.

**Co-primary — Bác sĩ điều trị:** trực tiếp quản lý nhiều bệnh nhân đái tháo đường típ 2; chịu trách nhiệm thiết lập mục tiêu, ngưỡng, đánh giá tình trạng và quyết định điều trị. Mục tiêu là xem dữ liệu được tổng hợp giữa các kỳ tái khám, xác định nhanh bệnh nhân cần ưu tiên và giữ toàn quyền đối với quyết định chuyên môn.

**Vai trò liên quan:** Admin quản lý tài khoản, quyền truy cập, nguồn tri thức và nhật ký hệ thống. Caregiver/người thân có thể là vai trò hỗ trợ trong tương lai nếu nghiên cứu người dùng xác nhận nhu cầu; hai vai trò này không phải co-primary persona trong MVP.

## 4. Input

| Chiều input | Phạm vi MVP |
|---|---|
| Dữ liệu bệnh nhân | Đường huyết SMBG, bữa ăn, vận động, thuốc đã dùng, triệu chứng và ghi chú |
| Dữ liệu bắt buộc của SMBG | Giá trị, đơn vị `mg/dL` hoặc `mmol/L`, thời gian và ngữ cảnh đo: lúc đói, trước ăn, sau ăn, trước ngủ hoặc khác |
| Cấu hình bác sĩ | Hồ sơ điều trị cần thiết, mục tiêu và ngưỡng đường huyết, kế hoạch đo, kế hoạch thuốc và lịch tái khám |
| Nguồn tri thức | PDF/DOCX dạng text từ Bộ Y tế, ADA hoặc nguồn chuyên môn được bác sĩ phụ trách lựa chọn; phạm vi áp dụng và trạng thái đánh dấu **Đã duyệt** |
| Ngôn ngữ | Tiếng Việt và tiếng Anh đối với nguồn; nội dung trả cho bệnh nhân bằng tiếng Việt |
| Chất lượng | Kiểm tra dữ liệu thiếu, trùng, sai định dạng hoặc ngoài khoảng hợp lệ; tài liệu phải đọc được, có tổ chức ban hành, phiên bản/năm và trạng thái hiệu lực |
| Khối lượng pilot | 5–10 bệnh nhân, 1–2 bác sĩ trong 6 tuần; tần suất ghi nhận theo kế hoạch do bác sĩ thiết lập |
| Nguồn dữ liệu | Bệnh nhân nhập thủ công; bác sĩ trực tiếp thiết lập dữ liệu điều trị; chỉ tài liệu đã duyệt được dùng để tư vấn |
| Ngoài MVP | Tích hợp máy đo/CGM/thiết bị đeo, OCR file scan, chữ viết tay và tự động lấy dữ liệu từ hồ sơ bệnh án |

## 5. Scope & priority

| Priority | Feature | Giá trị |
|---|---|---|
| **Must** | F1 — Nhật ký sức khỏe theo kế hoạch | Tập trung dữ liệu, hướng dẫn nhập đúng và nhắc bệnh nhân hoàn thành kế hoạch theo dõi |
| **Must** | F2 — Theo dõi xu hướng và cảnh báo cá nhân hóa | Giúp bệnh nhân hiểu diễn biến so với mục tiêu và biết hành động tiếp theo khi có bất thường |
| **Must** | F3 — Trợ lý tư vấn lối sống có căn cứ | Cung cấp thông tin dễ kiểm chứng từ nguồn y khoa đã được phê duyệt |
| **Must** | F4 — Theo dõi bệnh nhân giữa các kỳ tái khám | Giúp bác sĩ theo dõi nhiều bệnh nhân, ưu tiên trường hợp cần xem xét và sử dụng dữ liệu tổng hợp khi đánh giá |
| **Should** | Cá nhân hóa thời điểm nhắc nhở | Điều chỉnh nhắc nhở theo hành vi sau khi có dữ liệu sử dụng |
| **Could** | Dự báo xu hướng và xuất báo cáo PDF | Mở rộng khả năng theo dõi khi có đủ dữ liệu và đánh giá an toàn |
| **Won't** | Tự chẩn đoán, kê đơn, thay đổi thuốc/insulin; tích hợp CGM/thiết bị đeo; tự động liên hệ cấp cứu; cam kết giám sát 24/7; quản lý bệnh mạn tính khác | Giữ MVP trong một hành trình theo dõi đái tháo đường típ 2 an toàn |

### Could/Future consideration

- Caregiver/người thân được bệnh nhân ủy quyền, chỉ xem xét sau khi có evidence từ pilot.

## 6. Features & acceptance criteria

> **Quy ước:** Feature và AC ở cấp feature là phạm vi cam kết của PRD. Các user stories bên dưới chỉ là gợi ý phân rã, **không bắt buộc và chưa được chốt**; team sẽ refinement, bổ sung và chốt AC cấp story trong buổi grooming trước từng sprint. Xác thực, phân quyền, consent, audit log, bảo mật, sao lưu và quản lý nguồn tri thức là capability dùng chung, được quy định tại mục 7 thay vì tách thành feature độc lập.

### F1 — Nhật ký sức khỏe theo kế hoạch (Must)

**Pain point giải quyết:** Bệnh nhân ghi nhận dữ liệu sức khỏe không đầy đủ và thiếu nhất quán khi tự theo dõi trong sinh hoạt hằng ngày, làm nhật ký khó sử dụng để theo dõi và đánh giá.

**User stories**

1. Là bệnh nhân, tôi muốn ghi dữ liệu sức khỏe tại một nơi để không phải quản lý nhiều nguồn rời rạc.
2. Là bệnh nhân, tôi muốn được chỉ rõ dữ liệu thiếu, sai, trùng hoặc có khả năng nhập nhầm trước khi hoàn tất.
3. Là bệnh nhân, tôi muốn được nhắc theo kế hoạch đo và biết mình còn thiếu lần ghi nhận nào.
4. Là bệnh nhân, tôi muốn biết hôm nay cần đo vào thời điểm nào để không quên kế hoạch bác sĩ đã thiết lập.


**AC**

- Bệnh nhân ghi được đường huyết, bữa ăn, vận động, thuốc đã dùng, triệu chứng và ghi chú trong một nhật ký.
- Một bản ghi SMBG đầy đủ có giá trị, đơn vị, thời gian và ngữ cảnh; hệ thống lưu giá trị gốc và quy đổi về một đơn vị chuẩn để phân tích.
- Bản ghi HbA1c có giá trị và ngày xét nghiệm, được lưu riêng với SMBG và không được coi là chỉ số đo hằng ngày.
- Thuốc được chọn từ kế hoạch của bác sĩ; bệnh nhân chỉ xác nhận **Đã dùng**, **Chưa dùng** hoặc **Bỏ lỡ**, không sửa tên thuốc hay liều.
- Dữ liệu thiếu, sai định dạng hoặc có thời gian tương lai bị từ chối với lý do cụ thể; dữ liệu trùng hoặc ngoài khoảng hợp lệ phải được cảnh báo, hệ thống từ chối lưu và chỉ rõ trường cần sửa.
- Bệnh nhân sửa được bản ghi trong ngày; hệ thống lưu giá trị trước/sau, người sửa và thời gian.
- Reminder được tạo từ kế hoạch đo; bệnh nhân có thể hoàn thành, bỏ qua hoặc hoãn và không nhận reminder trùng.

### F2 — Theo dõi xu hướng và cảnh báo cá nhân hóa (Must)

**Pain point giải quyết:** bệnh nhân chỉ thấy từng số đo và không biết trạng thái so với mục tiêu cá nhân hoặc hành động tiếp theo, nên có thể bỏ qua bất thường hoặc không biết khi nào cần liên hệ bác sĩ.

**User stories**

1. Là bệnh nhân, tôi muốn xem đường huyết theo thời gian và so sánh với mục tiêu cá nhân.
2. Là bệnh nhân, tôi muốn xem bữa ăn, vận động và thuốc cạnh số đo để nhận biết yếu tố có liên quan.
3. Là bệnh nhân, tôi muốn được cảnh báo theo ngưỡng cùng hướng dẫn hành động đã được bác sĩ phê duyệt.
4. Là bác sĩ, tôi muốn cảnh báo được gắn cờ và truy ngược tới số đo cùng rule đã kích hoạt.


**AC**

- Biểu đồ hỗ trợ tối thiểu 7, 14 và 30 ngày; mỗi điểm hiển thị giá trị, thời gian, ngữ cảnh và trạng thái so với mục tiêu cá nhân có hiệu lực tại thời điểm đo.
- Người dùng lọc được theo ngữ cảnh và khoảng thời gian; bữa ăn, vận động, thuốc đã dùng và triệu chứng được hiển thị trên timeline hoặc chi tiết liên quan.
- Hệ thống không khẳng định quan hệ nhân quả giữa đường huyết với bữa ăn, vận động hoặc thuốc chỉ từ tương quan dữ liệu; khi dữ liệu không đủ, hệ thống hiển thị **Không đủ dữ liệu để kết luận xu hướng**.
- Rule engine hoạt động độc lập với LLM/AI, kích hoạt cảnh báo theo ngưỡng có cấu trúc do bác sĩ thiết lập và phân loại tối thiểu mức **Cần chú ý** và **Nguy hiểm**.
- Khi số đo đạt điều kiện, cảnh báo được hiển thị ngay cho bệnh nhân và gắn cờ trên dashboard bác sĩ, kể cả khi dịch vụ AI không khả dụng.
- Cảnh báo hiển thị giá trị, thời gian, mức cảnh báo, lý do kích hoạt và hướng dẫn hành động đã được bác sĩ phê duyệt.
- Nếu số đo vừa có khả năng nhập sai vừa đạt ngưỡng nguy hiểm, hệ thống yêu cầu kiểm tra/đo lại nhưng không trì hoãn cảnh báo nguy hiểm.
- Cảnh báo nguy hiểm không tạo kỳ vọng bác sĩ giám sát 24/7 và hướng dẫn liên hệ cơ sở y tế khi cần.
- Mỗi cảnh báo lưu dữ liệu nguồn, rule/phiên bản, thời gian và trạng thái; phát hiện đúng 100% trường hợp đạt ngưỡng trên bộ test được bác sĩ duyệt.

### F3 — Trợ lý tư vấn lối sống có căn cứ (Must)

**Pain point giải quyết:** bệnh nhân tìm hướng dẫn ăn uống và vận động từ nhiều nguồn không được kiểm chứng, các nguồn chung chung,khó đánh giá độ tin cậy và có nguy cơ áp dụng lời khuyên không phù hợp.

**User stories**

1. Là bệnh nhân, tôi muốn hỏi về ăn uống, vận động và tự theo dõi để nhận câu trả lời dễ hiểu dựa trên nguồn đã phê duyệt.
2. Là bệnh nhân, tôi muốn mở đúng nguồn để tự kiểm tra căn cứ.
3. Là bệnh nhân, tôi muốn hệ thống nói rõ khi không đủ căn cứ, nguồn mâu thuãn hoặc câu hỏi vượt phạm vi thay vì tự tạo câu trả lời.
4. Là bác sĩ phụ trách, tôi muốn các câu hỏi có dấu hiệu nguy hiểm hoặc yêu cầu thay đổi điều trị được xử lý đúng giới hạn an toàn.

**AC**

- Câu trả lời chỉ dùng tài liệu **Đã duyệt**, còn hiệu lực và thuộc phạm vi tư vấn lối sống.
- Mỗi nội dung khẳng định y khoa kèm tên tài liệu, tổ chức ban hành, phiên bản/năm và vị trí nguồn; người dùng mở được đúng tài liệu hoặc đoạn nguồn hỗ trợ khẳng định đó.
- **Định nghĩa “đủ căn cứ”:** mỗi khẳng định y khoa phải gắn với ít nhất một đoạn nguồn trực tiếp hỗ trợ nội dung đó; nếu chỉ suy diễn từ nhiều đoạn mà không có đoạn nào hỗ trợ trực tiếp thì coi là thiếu căn cứ.
- Khi thiếu căn cứ, hệ thống báo **Chưa đủ căn cứ trong kho tri thức**.
- Khi các nguồn được duyệt mâu thuẫn, hệ thống nêu rõ nội dung mâu thuẫn, không tự chọn một kết luận và chuyển để bác sĩ/quản trị chuyên môn xem xét.
- AI chỉ cung cấp thông tin chung về lối sống và tự theo dõi; không chẩn đoán, kê đơn hoặc đề nghị thay đổi thuốc/insulin.
- Khi câu hỏi vượt phạm vi, hệ thống từ chối rõ ràng; không chẩn đoán, kê đơn hoặc khuyến nghị thay đổi thuốc, liều dùng hay insulin.
- Câu hỏi có dấu hiệu nguy hiểm, hệ thống dừng luồng tư vấn thông thường được chuyển sang luồng cảnh báo F2 với hướng dẫn đã được bác sĩ phê duyệt.
- Mỗi câu trả lời lưu phiên bản mô hình, nguồn, thời gian và nội dung; trên bộ 30 tình huống chuẩn do bác sĩ phê duyệt, F3 đạt các ngưỡng chất lượng và an toàn tại mục 2 và mục 7.

### F4 — Theo dõi bệnh nhân giữa các kỳ tái khám (Must)

**Pain point giải quyết:** bác sĩ theo dõi nhiều bệnh nhân nhưng không có dữ liệu liên tục giữa hai kỳ khám và phải dựa vào trí nhớ hoặc nhật ký thiếu, nên khó nhận biết trường hợp cần chú ý và mất thời gian tổng hợp lịch sử để đánh giá.

**User stories**

1. Là bác sĩ, tôi muốn thiết lập mục tiêu và kế hoạch riêng để dữ liệu được đánh giá đúng bối cảnh cho từng bệnh nhân.
2. Là bác sĩ, tôi muốn lọc và xem danh sách bệnh nhân theo mức cần chú ý.
3. Là bác sĩ, tôi muốn xem lịch sử, xu hướng, cảnh báo, HbA1c và dữ liệu gốc của từng bệnh nhân.
4. Là bác sĩ, tôi muốn xem lý do AI gắn cờ để quyết định hành động chuyên môn.
5. Là bác sĩ, tôi muốn xem bản tóm tắt trước tái khám và cập nhật kế hoạch theo dõi hoặc điều trị sau khi đánh giá.
6. Là bệnh nhân, tôi chỉ muốn nhận thay đổi điều trị do bác sĩ trực tiếp xác nhận.

**AC**

- Bác sĩ chỉ xem bệnh nhân có quan hệ theo dõi hợp lệ
- Bác sĩ có thể thiết lập được mục tiêu, ngưỡng, kế hoạch đo, kế hoạch thuốc và lịch tái khám; bệnh nhân chỉ xem được phiên bản đã được bác sĩ xác nhận.
- Bệnh nhân xem được kế hoạch hiện hành nhưng không tự thay đổi; mọi thay đổi lưu bác sĩ, người thực hiện, thời gian, giá trị cũ/mới, lý do và ngày hiệu lực.
- Dashboard hiển thị bệnh nhân theo mức cảnh báo hiện tại; hỗ trợ lọc/sắp xếp theo mức cảnh báo, thời gian gần nhất, mức độ hoàn thành và lịch tái khám.
- Trang chi tiết hiển thị mục tiêu, xu hướng, nhật ký, cảnh báo và lịch sử xử lý; mọi nội dung tổng hợp có thể truy về dữ liệu gốc.
- Hệ thống tạo bản tóm tắt kỳ theo dõi gồm mục tiêu, mức độ hoàn thành, xu hướng, HbA1c, cảnh báo và dữ liệu liên quan; phần thiếu ghi **Không đủ dữ liệu**.
- Nội dung AI được gắn nhãn, liên kết dữ liệu gốc và không chứa chẩn đoán hoặc đề xuất thay đổi điều trị.
- AI chỉ tạo cờ **Cần bác sĩ xem xét** kèm lý do và dữ liệu liên quan; không đề xuất hay thay đổi thuốc, liều, cách điều chỉnh insulin hoặc mục tiêu điều trị mới.
- Bác sĩ có thể yêu cầu thêm dữ liệu, cập nhật kế hoạch hoặc đóng trường hợp; mọi thay đổi điều trị phải do bác sĩ trực tiếp nhập và xác nhận..
- Bệnh nhân chỉ nhận phiên bản đã được bác sĩ xác nhận; hệ thống lưu cờ AI, dữ liệu sử dụng, hành động, người thực hiện, thời gian và lý do.
- Bác sĩ xác định được bệnh nhân cần ưu tiên trong không quá 2 phút trong bài kiểm tra khả dụng.

## 7. Non-functional requirements

- **Bảo mật:** xác thực người dùng, kiểm soát quyền ở API và giao diện, mã hóa dữ liệu sức khỏe khi truyền/lưu; không ghi dữ liệu sức khỏe vào log kỹ thuật, thông báo lỗi hoặc huấn luyện nếu chưa được phép.
- **Quyền riêng tư và tuân thủ:** có cơ sở xử lý dữ liệu hợp lệ, consent, nhật ký truy cập và cơ chế thực hiện quyền dữ liệu; quy trình dùng dữ liệu thật phải được rà soát theo pháp luật và xác nhận bởi người phụ trách.
- **An toàn y khoa:** rule engine độc lập với LLM, quyết định ngưỡng và kích hoạt cảnh báo; nhập dữ liệu và cảnh báo vẫn hoạt động khi AI/RAG lỗi. AI chỉ gắn cờ và tổng hợp, không chẩn đoán, kê đơn hoặc quyết định điều trị; mọi nội dung tư vấn luôn kèm cảnh báo không thay thế bác sĩ, bác sĩ quyết định mọi thay đổi điều trị.
- **Độ tin cậy và truy vết:** lưu dữ liệu gốc, đơn vị, lịch sử chỉnh sửa, phiên bản rule/nguồn/mô hình và audit log cho các hành động quan trọng; không tạo bản ghi trùng khi gửi lại yêu cầu.
- **Hiệu năng:** p95 nhập/xem nhật ký ≤ 2 giây; p95 dashboard ≤ 3 giây; p95 phản hồi AI ≤ 10 giây; cảnh báo hiển thị trong ≤ 2 giây sau khi lưu số đo.
- **Khả dụng và phục hồi:** uptime ≥ 99,5% trong pilot, không tính bảo trì đã báo; RPO ≤ 1 giờ, RTO ≤ 4 giờ; sao lưu và khôi phục phải được kiểm thử khôi phục trước pilot. Tech Lead phải xác nhận tính khả thi của RPO trước khi ký duyệt.
- **Đánh giá AI:** bộ 30 tình huống cố định — 10 đủ nguồn · 5 thiếu nguồn · 5 nguy hiểm · 5 yêu cầu thay đổi điều trị · 5 nguồn mâu thuẫn; ngưỡng đạt ≥ 90% phù hợp/có căn cứ, 100% trích dẫn hợp lệ, 100% yêu cầu điều trị bị chặn hoặc chuyển bác sĩ và 100% tình huống nguy hiểm vào đúng luồng cảnh báo.
- **Khả dụng:** giao diện tiếng Việt, không chỉ dùng màu để biểu thị cảnh báo, hỗ trợ cỡ chữ dễ đọc responsive từ 360 px, hỗ trợ hai phiên bản gần nhất của Chrome/Edge/Safari và hướng tới WCAG 2.2 AA.

## 8. Definition of Done

MVP done khi 5–10 bệnh nhân và 1–2 bác sĩ hoàn thành các hành trình **bệnh nhân: đăng nhập → nhập nhật ký theo kế hoạch → xem xu hướng/cảnh báo → hỏi tư vấn và mở nguồn** và **bác sĩ: đăng nhập → thiết lập kế hoạch → xem danh sách → xác định bệnh nhân cần ưu tiên → kiểm tra dữ liệu/cờ AI → cập nhật kế hoạch**; toàn bộ **Must features (F1–F4) đạt AC cấp feature ở mục 6**, các capability dùng chung tại mục 7 hoạt động, không còn lỗi Critical/High về an toàn hoặc dữ liệu, rule engine và bộ 30 tình huống AI đạt ngưỡng quy định, dữ liệu được khôi phục thử thành công và các metric ở mục 2 đạt sau pilot 6 tuần.

## 8. Definition of Done

### 8.1. MVP ready for pilot

MVP done khi 5–10 bệnh nhân và 1–2 bác sĩ hoàn thành các hành trình **bệnh nhân: đăng nhập → nhập nhật ký theo kế hoạch → xem xu hướng/cảnh báo → hỏi tư vấn và mở nguồn** và **bác sĩ: đăng nhập → thiết lập kế hoạch → xem danh sách → xác định bệnh nhân cần ưu tiên → kiểm tra dữ liệu/cờ AI → cập nhật kế hoạch**. Bên cạnh đó:

- Toàn bộ **Must features (F1–F4) đạt AC cấp feature ở mục 6**.
- Không còn lỗi **Critical** hoặc **High** liên quan đến an toàn, mất dữ liệu, truy cập trái phép, cảnh báo hoặc AI vượt phạm vi.
- Rule engine đạt yêu cầu trên bộ kiểm thử được bác sĩ phê duyệt.
- Bộ 30 tình huống AI đạt đầy đủ ngưỡng chất lượng và an toàn tại mục 7.
- Sao lưu và khôi phục thử thành công; F1 và rule engine F2 vẫn hoạt động khi dịch vụ AI/RAG không khả dụng.
- Bác sĩ phụ trách xác nhận mục tiêu, ngưỡng, phân loại cảnh báo, hướng dẫn hành động và nguồn tri thức dùng trong pilot.

### 8.2. Pilot success

Pilot thành công khi:

- Triển khai đủ **6 tuần** với 5–10 bệnh nhân đái tháo đường típ 2 và 1–2 bác sĩ.
- Đạt goals và metrics tại mục 2.
- Không xảy ra sự cố an toàn y khoa hoặc quyền riêng tư nghiêm trọng.
- Các giả thuyết baseline được xác nhận, bác bỏ hoặc cập nhật bằng dữ liệu pilot.
- Phản hồi của bệnh nhân và bác sĩ được tổng hợp thành phát hiện và đề xuất cải tiến.

Việc hoàn thành pilot không phải điều kiện để kết luận **MVP ready for pilot**.

## 9. Open questions

| Câu hỏi cần chốt | Owner | Hạn |
|---|---|---|
| Tiêu chí tuyển bệnh nhân pilot: phác đồ, tuổi, thai kỳ và bệnh nền? | PO + Bác sĩ phụ trách | Trước khi tuyển người tham gia |
| Baseline về tỷ lệ bản ghi đầy đủ và thời gian bác sĩ tổng hợp lịch sử? | PO/BA | Trước khi Sprint 1 bắt đầu |
| Mục tiêu, ngưỡng và nội dung hướng dẫn cho từng mức cảnh báo? | Bác sĩ phụ trách | Trước grooming F2/F4 |
| Bác sĩ có theo dõi ngoài giờ và thời gian phản hồi kỳ vọng là bao lâu? | PO + Bác sĩ phụ trách | Trước UAT F2/F4 |
| Kênh gửi reminder và cảnh báo trong MVP? | PO + Tech Lead | Trước grooming F1/F2 |
| Danh sách nguồn tri thức, người duyệt và cách xử lý nguồn hết hiệu lực/mâu thuẫn? | Bác sĩ phụ trách + Admin | Trước grooming F3 |
| Đáp án chuẩn, rubric và định nghĩa lỗi nghiêm trọng cho bộ 30 tình huống AI? | PO + Bác sĩ phụ trách | Trước kiểm thử AI |
| Cơ sở xử lý dữ liệu, quy trình consent, lưu trữ và xóa dữ liệu pilot? | PO + Phụ trách pháp lý | Trước khi dùng dữ liệu thật |
| Dữ liệu thật, ẩn danh hay mô phỏng được dùng ở từng giai đoạn? | PO + Bác sĩ phụ trách | Trước khi Sprint 1 bắt đầu |

## 10. Sign-off

PRD chỉ chuyển từ **Draft → Approved** khi đủ các xác nhận dưới đây. Mỗi người ký xác nhận đúng phạm vi trách nhiệm của mình.

| Vai trò | Phạm vi xác nhận | Người | Ngày | Trạng thái |
|---|---|---|---|---|
| PO | Nội dung, bốn feature theo pain point, phạm vi F1-F4, ưu tiên MoSCoW và metric khả thi về nghiệp vụ | — | — | ☐ Chờ ký |
| Tech Lead | Tính khả thi F1–F4, platform capabilities, rule engine, bảo mật, hiệu năng, RPO/RTO và khả năng vận hành pilot | — | — | ☐ Chờ ký |
| Bác sĩ phụ trách | Đúng chuyên môn; duyệt mục tiêu, ngưỡng, hướng dẫn, nguồn tri thức và bộ 30 tình huống AI | — | — | ☐ Chờ ký |
| Phụ trách pháp lý/bảo vệ dữ liệu | Cơ sở xử lý, consent, phân quyền, lưu trữ, chia sẻ và xóa dữ liệu sức khỏe trong pilot| — | — | ☐ Chờ ký |

Việc ký duyệt PRD không đồng nghĩa sản phẩm được phép chẩn đoán hoặc điều trị độc lập. Mọi thay đổi liên quan đến cảnh báo, thuốc, insulin, dữ liệu sức khỏe hoặc chức năng AI phải được đánh giá tác động và phê duyệt lại.

## 11. Assumptions & dependencies

- Pilot chỉ triển khai với người đã được chẩn đoán đái tháo đường típ 2 và đáp ứng tiêu chí tuyển chọn do bác sĩ xác nhận.
- Bệnh nhân có thiết bị truy cập ứng dụng và có khả năng tự đo đường huyết tại nhà theo kế hoạch.
- Bác sĩ phụ trách cung cấp mục tiêu, ngưỡng cảnh báo, nội dung xử trí và nguồn tri thức đã được duyệt.
- Hệ thống không được xem là kênh cấp cứu hoặc dịch vụ giám sát 24/7.
- Việc sử dụng dữ liệu bệnh nhân thật phụ thuộc vào phê duyệt của cơ sở y tế và quy trình bảo vệ dữ liệu.
- Chất lượng phân tích phụ thuộc vào tính đầy đủ và chính xác của dữ liệu người dùng nhập.
