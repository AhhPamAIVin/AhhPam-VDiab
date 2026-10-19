# Business model — VDiab

> Trạng thái: giả thuyết cần kiểm chứng, cập nhật 2026-09-02. Tài liệu này không khẳng định đã có doanh thu, hợp đồng hoặc willingness-to-pay.

## Khách hàng và giá trị

VDiab phục vụ hai nhóm sử dụng: bệnh nhân đái tháo đường cần ghi nhận và hiểu xu hướng; bác sĩ/phòng khám cần ưu tiên ca cần chú ý và giảm thời gian tổng hợp nhật ký trước tái khám. Giả thuyết người trả tiền ưu tiên là phòng khám hoặc bệnh viện; bệnh nhân là người dùng hưởng lợi. Mô hình khởi đầu phù hợp nhất là **B2B2C pilot theo cơ sở y tế**, sau đó mới đánh giá B2B SaaS.

Giá trị cần chứng minh trong pilot:

- giảm thời gian bác sĩ chuẩn bị hồ sơ trước khám;
- tăng tỷ lệ bệnh nhân ghi nhận đủ dữ liệu theo kế hoạch;
- phát hiện đúng số đo cần chú ý bằng rule/threshold không phụ thuộc LLM;
- AI Summary và AI Consultation có nguồn, không tạo quyết định điều trị tự động.

## Đơn vị giá và unit economics

| Thành phần | Cách tính | Trạng thái |
|---|---|---|
| Giá đề xuất | active patient/tháng hoặc gói cơ sở/tháng | Giả thuyết; chưa phỏng vấn người mua |
| LLM/patient/tháng | lượt chat + summary × input/output token × giá model tại thời điểm dùng | Cần đo từ trace/billing |
| Hạ tầng | VPS + database + backup + monitoring + tên miền | Cần lấy hóa đơn thực tế |
| Onboarding | giờ cấu hình, đào tạo bác sĩ/bệnh nhân và import dữ liệu/cơ sở | Cần pilot |
| Support | ticket hoặc giờ hỗ trợ/cơ sở/tháng | Cần pilot |
| Gross margin | doanh thu trừ LLM, hạ tầng và support trực tiếp | Chưa đủ dữ liệu |

Không đưa con số tiền giả định vào release evidence trước khi có báo giá/hóa đơn hoặc biên bản phỏng vấn. Owner cần cập nhật bảng đo với nguồn, ngày và người xác nhận.

## Kênh và quy trình bán hàng giả định

1. Tiếp cận bác sĩ nội tiết/chủ phòng khám qua đối tác lâm sàng hoặc demo trực tiếp.
2. Discovery về quy trình hiện tại, volume bệnh nhân, phê duyệt dữ liệu và tiêu chí thành công.
3. Pilot giới hạn, dùng dữ liệu mô phỏng trước; chỉ dùng dữ liệu thật sau phê duyệt, consent và security review.
4. Đánh giá sau 4–8 tuần bằng time-on-task, adoption, safety incidents, cost/patient và feedback.
5. Chỉ đề xuất hợp đồng khi đạt tiêu chí pilot và có chủ thể chịu trách nhiệm vận hành.

## Giả định cần kiểm chứng

| Giả định | Cách kiểm chứng | Tiêu chí gợi ý | Evidence hiện có |
|---|---|---|---|
| Bác sĩ mất thời gian tổng hợp nhật ký | 5–8 phỏng vấn + đo baseline | ≥60% xác nhận pain point; có thời gian baseline | Chưa có research hoàn thành |
| Cơ sở sẵn sàng trả theo active patient | phỏng vấn 3 người có quyền mua + price test | ít nhất 2 người chấp nhận pilot có ngân sách | Chưa có |
| Summary giúp tiết kiệm thời gian | usability test có đối chứng | giảm ≥30% median preparation time | Chưa đo |
| Chi phí LLM chấp nhận được | đo token và số lượt trong pilot | cost/active patient nằm trong biên giá | Chưa đo |

## Rủi ro kinh doanh và pháp lý

- Chu kỳ mua hàng y tế dài, cần chủ thể phê duyệt và tích hợp workflow.
- Dữ liệu sức khỏe là dữ liệu nhạy cảm; cần cơ sở xử lý, consent, retention/deletion, kiểm soát nhà cung cấp và incident response.
- Chưa chứng minh encryption-at-rest, rate limiting, audit trail hay backup/restore; không dùng claim “production-ready for real PHI”.
- AI không thay thế bác sĩ và không được tự đổi liều/chẩn đoán; liability và quy trình escalation phải được cơ sở phê duyệt.

## Quyết định sau pilot

Go nếu đạt các metric về time saved, task completion, safety, reliability và willingness-to-pay đã định trước. Pivot nếu giá trị chủ yếu nằm ở dashboard/summary thay vì AI chat. Stop nếu không có người mua chịu trách nhiệm, không đạt security gate, hoặc workflow tạo thêm gánh nặng cho bác sĩ.
