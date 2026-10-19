# Competitor analysis — VDiab

> Desk research ngày 2026-09-02. Chỉ mô tả điều nguồn chính thức xác nhận; ô “chưa xác minh” không đồng nghĩa đối thủ không có tính năng.

## So sánh theo pain point

| Pain point / lựa chọn | VDiab (repo hiện tại) | mySugr | Abbott LibreView | Omada | Sổ tay/quy trình thủ công |
|---|---|---|---|---|---|
| Ghi nhật ký glucose/bữa ăn/hoạt động | Có | Có; manual/device logging | Tập trung dữ liệu thiết bị Libre | Thiết bị kết nối và tracking | Có, thủ công |
| Chia sẻ/xem xu hướng với bác sĩ | Dashboard theo role; cần demo evidence | Report chia sẻ healthcare team | Điểm mạnh cốt lõi: AGP, Time in Range, remote sharing | Tích hợp dữ liệu với care team/PCP | Ảnh/sổ, bác sĩ tự tổng hợp |
| Ngưỡng theo care plan bác sĩ | Có code/test threshold và version target | Có target range; quyền bác sĩ cấu hình chưa xác minh | Có target range/report; workflow VDiab-equivalent chưa xác minh | Personalized care plan; cơ chế rule chưa xác minh | Bác sĩ ghi/dặn thủ công |
| Cảnh báo không phụ thuộc LLM | Có rule/threshold path và test | Chưa xác minh | CGM alerts thuộc ecosystem; so sánh trực tiếp chưa xác minh | Chưa xác minh | Người dùng tự nhận biết |
| AI hỏi đáp cá nhân hóa | Có agent/guardrail/citation; quality còn gap | Chưa xác minh | Không được dùng thay quyết định điều trị | Human coach/specialist là trọng tâm | Không |
| Doctor summary trước tái khám | Có implementation/test; cần runtime screenshot | Có report cho bác sĩ | Có standardized glucose reports | Care team theo dõi; summary tương đương chưa xác minh | Bác sĩ tự tổng hợp |
| Tiếng Việt/local workflow | Có UI/nội dung tiếng Việt | Chưa xác minh | Chưa xác minh | Chưa xác minh | Có |

## Nguồn đã kiểm chứng

- mySugr mô tả logging glucose, meal, activity, kết nối thiết bị và report chia sẻ với healthcare team: https://www.mysugr.com/diabetes-app
- Abbott mô tả LibreView cloud sharing, AGP/Time in Range và giới hạn không thay thế tư vấn/quyết định điều trị: https://www.freestyle.abbott/uk-en/managing-your-diabetes/start-your-journey/connect-with-healthcare-professionals.html
- Omada mô tả coaching 1:1, diabetes specialist, connected devices và personalized care plan: https://www.omadahealth.com/for-individuals/diabetes
- “Sổ tay” là baseline workflow cần xác thực bằng `USER_RESEARCH.md`, chưa phải kết luận thị trường.

## Kết luận định vị

Khác biệt **cần kiểm chứng**, không phải lợi thế đã chứng minh, là kết hợp workflow Việt ngữ cho patient/doctor, threshold/cảnh báo deterministic và một AI Agent có guardrail/citation phục vụ cả consultation lẫn summary. VDiab chưa khác biệt rõ ở logging, biểu đồ và report: các hệ sinh thái lớn đã làm tốt. VDiab cũng chưa có evidence về device integration, clinical outcomes, procurement fit hoặc security certifications.

Lý do chuyển hợp lý chỉ tồn tại nếu pilot chứng minh bác sĩ tiết kiệm thời gian và bệnh nhân hoàn thành task tốt hơn quy trình hiện tại mà không tăng rủi ro. Nếu không đạt hai điều này, switching cost (đào tạo, nhập dữ liệu, governance) lớn hơn lợi ích.
