# Model card — personalized_watch v1_hgb

## Bài toán

Phân loại nhị phân WATCH (1) / NORMAL (0) trên các dòng sau baseline và không phải DANGER, theo `docs/ML_PERSONALIZED_WATCH_SYSTEM_PLAN.md` mục 4 (Lớp 2). Nhãn là weak label do rule engine sinh ra (`build_personalized_attention_dataset.py`), không phải nhãn bác sĩ xác nhận.

## Dữ liệu

- `data/ml/personalized_watch/feature_table.csv` — 81.920 dòng, 489 bệnh nhân.
- Train 44.950 dòng / Validation 20.062 dòng / Test (patient-holdout, 97 bệnh nhân chưa từng thấy) 16.908 dòng.
- Trong đó tập nhị phân WATCH/NORMAL (loại DANGER): train 16.002, validation tương tự, test 12.428.

## Kết quả

| | Validation | Test (patient-holdout) |
|---|---:|---:|
| PR-AUC | 0,99999 | 0,99999 |
| Recall WATCH | 0,9995 | 0,9987 |
| False negative rate | 0,00046 | 0,00125 |
| Brier score | 0,00042 | 0,00104 |

So sánh: Logistic Regression baseline (v0) chỉ đạt PR-AUC 0,924, F1 0,851 trên cùng tập validation — thấp hơn nhiều so với HGB.

## Vì sao điểm số gần như tuyệt đối — đọc phần này trước khi dùng số trên

Đây **không phải bằng chứng mô hình đã học được tín hiệu lâm sàng mới**. Đã chạy permutation importance (n=3.000 dòng validation, scoring=PR-AUC):

- `glucose_mg_dl` (giá trị đo hiện tại) một mình chiếm ~92% mức giảm PR-AUC khi hoán vị — vì `target_low`/`target_high` cố định 70/180 cho toàn bộ 489 bệnh nhân (mục 3.2 trong plan), nên "glucose > 180 hoặc < 70" gần như tái tạo đúng luật `ABOVE_TARGET_NOT_DANGER`/`LEVEL_1_LOW` chỉ từ một cột.
- `deviation_from_baseline_p10`, `rate_mg_dl_per_min`, `deviation_from_baseline_p90` cộng thêm phần còn lại — tái tạo các luật `BELOW_PERSONAL_BASELINE`, `NEAR_HIGH_AND_RISING`, `ABOVE_PERSONAL_BASELINE`.
- **Toàn bộ khối lâm sàng tĩnh (HbA1c, BMI, bệnh đồng mắc, risk_group...) đóng góp đúng bằng 0** (tổng importance ≈ 2×10⁻⁶, tức nhiễu). Khớp chính xác với phát hiện ở mục 3.3/5 của plan: rule engine hiện tại không hề dùng các cột này để quyết định WATCH/NORMAL — nên một model học lại đúng label đó cũng không có lý do gì để dùng chúng.
- `meal_context` đứng hạng 11/54 nhưng importance ≈ 7×10⁻⁷ (cùng bậc nhiễu) — model có thấy bối cảnh bữa ăn, chủ yếu qua `deviation_from_baseline_*` (baseline vốn đã tra theo đúng meal_context), không phải qua bản thân cờ meal_context. **Sửa lỗi:** `meal_context` từng bị gắn nhầm là "identifier" trong `build_watch_feature_table.py`, khiến v0/v1 ban đầu train mà không có feature này — phát hiện được nhờ câu hỏi review về việc bệnh nhân đo giờ bất kỳ có được model xử lý đúng không. Đã sửa và train lại; số liệu trong model card này là sau khi sửa.

Kết luận thực tế: v1_hgb đã đạt đúng mục tiêu doc gốc đặt ra cho ML v1 — "học gần đúng logic hiện tại" (`ML_HANDOFF_PERSONALIZED_ATTENTION.md` §6.1) — nhưng chưa chứng minh được giá trị của phần dữ liệu lâm sàng mới đưa vào. Giá trị thật của khối lâm sàng chỉ có thể đánh giá được sau khi có nhãn bác sĩ thật (Giai đoạn C — shadow mode + review) khác với nhãn rule hiện tại. Không được diễn giải các con số PR-AUC/recall ở trên như bằng chứng hiệu quả lâm sàng.

## Giới hạn

- Nhãn là synthetic + weak label, không phải outcome bệnh nhân thật.
- `class_weight="balanced"` dùng cho cả hai model; ngưỡng vận hành báo cáo ở đây là 0,5 mặc định — chưa tune theo mục 4 (điểm còn cần bàn).
- Calibration (Platt/Isotonic per risk_group) fit trên tập validation, chưa đánh giá trên tập test độc lập khác.
- GroupKFold 5-fold trên train_pool cho PR-AUC ổn định (mean 0,99989, std 0,00018) — nhất quán với các con số trên, không phát hiện bất ổn giữa các nhóm bệnh nhân.
- **Bệnh nhân đo tự do (giờ bất kỳ, dồn dập, thiếu meal_context) là vùng ngoài phân phối train.** Đã kiểm `codebase/backend/health/schemas.py`: không có gì chặn việc này ngoài đời (`measured_at` do bệnh nhân tự nhập, `meal_context` optional, không giới hạn tần suất). Nhưng dữ liệu synthetic mô phỏng gần đúng lịch đo có cấu trúc của hệ thống nhắc lịch hiện tại (RANDOM chỉ 0,1%, 0 dòng thiếu meal_context). Model không crash ở vùng này (đã test) nhưng hành vi chưa được kiểm chứng — cần đưa vào shadow mode (Giai đoạn C) như một lát cắt riêng, không chỉ theo risk_group.
- An toàn: model này KHÔNG được gọi khi rule DANGER đã trả về true (Lớp 1 luôn thắng) — safety test độc lập cho việc này thuộc Giai đoạn C, chưa viết.
