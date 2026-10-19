# Bàn giao dữ liệu cá nhân hóa để tiếp tục xây dựng ML

## 1. Mục đích tài liệu

Tài liệu này giúp một thành viên mới tiếp tục xây dựng mô hình ML cho chức năng cảnh báo **Cần theo dõi (WATCH)** mà không phải đọc lại toàn bộ lịch sử phát triển.

Phạm vi hiện tại chỉ gồm bệnh nhân đái tháo đường type 2 trong dữ liệu demo. Dữ liệu tiền đái tháo đường đã bị loại khỏi output cá nhân hóa và chưa được hỗ trợ trong pipeline này.

> Lưu ý an toàn: toàn bộ hồ sơ, định danh bác sĩ và phê duyệt trong dataset hiện tại là dữ liệu synthetic/demo. Không sử dụng trực tiếp để đưa ra quyết định điều trị cho bệnh nhân thật.

## 2. Trạng thái đã hoàn thành

Nhánh làm việc: `anh/canhanhoa`

Các mốc Git chính:

- `30c5c55`: thêm ngưỡng đái tháo đường cá nhân hóa và lớp phân tích WATCH.
- `fb36e8e`: thêm phiên bản kế hoạch chăm sóc sau mỗi lần tái khám.
- `7a10069`: merge và đồng bộ nhánh `anh/canhanhoa`.

Dataset hiện có:

- 489 bệnh nhân `TYPE_2`.
- 0 bệnh nhân tiền đái tháo đường trong output ngưỡng.
- 100.470 phép đo glucose synthetic trong 60 ngày.
- 18.550 bản ghi thuộc giai đoạn học baseline.
- 35.659 bản ghi `NORMAL`.
- 25.459 bản ghi `WATCH`.
- 20.802 bản ghi `DANGER`.
- 18.514 sự kiện WATCH được phát ra sau khi áp dụng cooldown.

Hệ thống đã tách rõ quyền quyết định:

- Bác sĩ kiểm soát ngưỡng `NORMAL` và `DANGER` sau mỗi lần khám.
- AI/rule engine chỉ phân tích mức `WATCH`, không sửa ngưỡng do bác sĩ đặt.
- `DANGER` luôn có độ ưu tiên cao hơn `WATCH`.
- WATCH chỉ gợi ý đo lại và ghi nhận bối cảnh; không tự động đổi thuốc hoặc liều insulin.

## 3. Logic cá nhân hóa hiện tại

### 3.1 Ngưỡng bác sĩ kiểm soát

Ngưỡng khởi tạo hiện dùng:

- Khoảng mục tiêu: 70–180 mg/dL.
- Nguy hiểm thấp: `< 54 mg/dL`.
- Nguy hiểm cao: `> 250 mg/dL`.
- Mức rất cao cần đánh giá lâm sàng: 300 mg/dL.

Các cut point glucose này được giữ theo chuẩn chung vì tài liệu nguồn không đủ căn cứ để tự tạo một cut point khác nhau cho từng người. Phần cá nhân hóa nằm ở hồ sơ rủi ro, mục tiêu thời gian trong khoảng và quá trình cập nhật có bác sĩ xác nhận.

Hai nhóm theo dõi:

- `STANDARD_TYPE_2`: TIR 70–180 tối thiểu 70%, TBR <70 tối đa 4%, TBR <54 tối đa 1%, TAR >180 tối đa 25%, TAR >250 tối đa 5%.
- `OLDER_OR_HIGH_RISK`: TIR tối thiểu 50%, TBR <70 tối đa 1%, TBR <54 tối đa 1%, TAR >180 tối đa 50%, TAR >250 tối đa 10%.

Đầu vào phân tầng gồm tuổi 65+, các cờ bệnh tim mạch đã ghi nhận và nhu cầu người chăm sóc. Insulin, sulfonylurea và meglitinide tạo cờ ưu tiên đánh giá hạ đường huyết nhưng không tự ý thay đổi cut point.

Nguồn được ghi trong dataset:

- ADA 2026 Section 6: DOI `10.2337/dc26-S006`.
- ADA 2026 Section 9: DOI `10.2337/dc26-S009`.
- Nguồn hỗ trợ: DOI `10.2337/dci19-0028`.

### 3.2 WATCH tự động

Engine hiện tại học baseline riêng theo:

- `patient_id`;
- `baseline_epoch_id`;
- `meal_context`.

Baseline dùng 14 ngày đầu, cần tối thiểu 5 điểm trong mỗi bối cảnh. Engine lưu các thống kê `p10`, `median`, `p90`, sau đó đánh giá các phép đo mới bằng baseline cá nhân, xu hướng gần đây, mức lặp lại và yếu tố rủi ro.

Thứ tự phân loại bắt buộc:

1. `DANGER` theo ngưỡng bác sĩ.
2. `WATCH` theo phân tích cá nhân hóa.
3. `NORMAL`.

Suggestion WATCH có cooldown 12 giờ cho mỗi bệnh nhân và mỗi lý do để tránh cảnh báo dồn dập.

### 3.3 Thay đổi sau tái khám

Mỗi lần khám được biểu diễn bằng clinical review event. Khi bác sĩ xác nhận thay đổi:

- tạo phiên bản care plan mới;
- đóng phiên bản cũ bằng `effective_to`;
- tạo phiên bản target tương ứng;
- mở baseline epoch mới để engine học lại theo trạng thái mới.

Nếu kết quả là `NO_THRESHOLD_CHANGE`, hệ thống tiếp tục dùng phiên bản hiện hành, không tự tạo ngưỡng mới. AI không được tạo hoặc phê duyệt thay đổi `NORMAL`/`DANGER`.

## 4. File quan trọng

### 4.1 Dữ liệu nguồn

| File | Vai trò |
|---|---|
| `data/NHANES/deployment_dataset/patient_profiles.csv` | Hồ sơ 489 bệnh nhân TYPE_2, tuổi, BMI, chất lượng dữ liệu và nhãn cohort. |
| `data/NHANES/deployment_dataset/patient_baseline_clinical.csv` | HbA1c, glucose đói, huyết áp, xét nghiệm và cờ bệnh đồng mắc. |
| `data/NHANES/deployment_dataset/patient_medications.csv` | Thuốc được báo cáo; không có đủ liều, lịch dùng và lịch sử hạ đường huyết. |
| `data/simulator_60d/synthetic_glucose_measurements.csv` | Chuỗi 100.470 phép đo glucose synthetic có thời gian và bối cảnh bữa ăn. |

### 4.2 Ngưỡng và care plan

| File | Vai trò |
|---|---|
| `data/knowledge_structured/patient_cgm_monitoring_profiles.csv` | Một hồ sơ ngưỡng và mục tiêu theo dõi cho mỗi bệnh nhân. |
| `data/knowledge_structured/patient_clinical_targets.csv` | Ba target cho mỗi bệnh nhân: khoảng bình thường, nguy hiểm thấp, nguy hiểm cao. |
| `data/knowledge_structured/patient_care_plans.csv` | Care plan khởi tạo và bác sĩ synthetic phê duyệt. |
| `data/knowledge_structured/patient_cgm_threshold_build_report.json` | Báo cáo số lượng, nguồn, phân tầng và invariant của bước tạo ngưỡng. |

### 4.3 Dữ liệu WATCH có thể dùng cho ML

| File | Số dòng | Vai trò |
|---|---:|---|
| `data/knowledge_structured/attention_engine/patient_glucose_baselines.csv` | 2.142 | Baseline theo bệnh nhân, epoch và meal context. |
| `data/knowledge_structured/attention_engine/patient_attention_assessments.csv` | 100.470 | Một assessment cho mỗi phép đo; đây là bảng chính để tạo tập ML. |
| `data/knowledge_structured/attention_engine/patient_attention_events.csv` | 18.514 | Sự kiện WATCH thực sự được phát sau cooldown. |
| `data/knowledge_structured/attention_engine/patient_attention_summary.csv` | 489 | Tổng hợp số trạng thái theo bệnh nhân. |
| `data/knowledge_structured/attention_engine/attention_engine_report.json` | 1 báo cáo | Phiên bản engine, phân bố nhãn và các ràng buộc an toàn. |

### 4.4 Dữ liệu theo thời gian

| File | Vai trò |
|---|---|
| `data/knowledge_structured/longitudinal_care/patient_clinical_review_events.csv` | Event tái khám/bác sĩ đánh giá. |
| `data/knowledge_structured/longitudinal_care/patient_care_plan_versions.csv` | Các phiên bản care plan có hiệu lực theo thời gian. |
| `data/knowledge_structured/longitudinal_care/patient_target_versions.csv` | Các phiên bản target `NORMAL`/`DANGER`. |
| `data/knowledge_structured/longitudinal_care/patient_baseline_epochs.csv` | Khoảng học baseline tương ứng với từng phiên bản. |
| `data/knowledge_structured/longitudinal_care/patient_care_plan_change_log.csv` | Audit trail cho các thay đổi đã xác nhận. |

### 4.5 Script tái tạo

| Script | Chức năng |
|---|---|
| `data/NHANES/final/build_initial_diabetes_thresholds.py` | Khởi tạo ngưỡng và phân tầng TYPE_2. |
| `data/NHANES/final/build_longitudinal_care_plans.py` | Tạo version care plan/target từ review event. |
| `data/NHANES/final/build_personalized_attention_dataset.py` | Tạo baseline, assessment và WATCH event. |
| `data/NHANES/final/refresh_longitudinal_personalization.py` | Chạy lại toàn bộ phần longitudinal và attention theo thứ tự đúng. |
| `data/NHANES/final/run_recommendation_engine.py` | Recommendation engine khác; hiện output có 0 event và không phải nhãn chính cho bài toán WATCH ML. |

## 5. Cách tái tạo dataset hiện tại

Đứng tại thư mục gốc repository và chạy:

```powershell
py -3 data/NHANES/final/refresh_longitudinal_personalization.py --repo-root .
```

Nếu cần tạo lại ngưỡng khởi tạo trước:

```powershell
py -3 data/NHANES/final/build_initial_diabetes_thresholds.py `
  --profiles data/NHANES/deployment_dataset/patient_profiles.csv `
  --clinical data/NHANES/deployment_dataset/patient_baseline_clinical.csv `
  --assignments data/NHANES/deployment_dataset/patient_doctor_assignments.csv `
  --medications data/NHANES/deployment_dataset/patient_medications.csv `
  --output-dir data/knowledge_structured
```

Sau khi chạy phải kiểm tra:

- `patient_count = 489`;
- `prediabetes_patient_count_in_output = 0`;
- mỗi bệnh nhân chỉ có một care plan `ACTIVE` tại một thời điểm;
- `danger_low < target_low < target_high < danger_high`;
- không có target version mới nếu chưa có xác nhận bác sĩ;
- số assessment bằng số measurement thuộc cohort;
- mọi `DANGER` phải thắng `WATCH`.

## 6. Bài toán ML nên xây dựng

### 6.1 Phạm vi khuyến nghị cho phiên bản đầu

Không cho ML dự đoán hoặc sửa ngưỡng nguy hiểm. Giữ rule engine làm lớp an toàn trước ML.

Pipeline inference đề xuất:

```text
Phép đo mới
  -> tra care-plan version có hiệu lực
  -> kiểm tra DANGER bằng rule bác sĩ
  -> nếu DANGER: phát cảnh báo nguy hiểm, không gọi ML WATCH
  -> nếu không DANGER: ML ước lượng xác suất cần theo dõi
  -> áp dụng threshold xác suất + cooldown
  -> WATCH hoặc NORMAL
```

Bài toán ML v1 nên là phân loại nhị phân trên các dòng **sau giai đoạn baseline và không phải DANGER**:

- `y = 1`: `status == WATCH`;
- `y = 0`: `status == NORMAL`.

`BASELINE_LEARNING` không dùng làm nhãn huấn luyện. `DANGER` không đưa vào bài toán nhị phân WATCH vì đây là nhánh rule an toàn riêng.

Các nhãn hiện tại do rule engine sinh ra, vì vậy đây là **weak labels**, chưa phải ground truth lâm sàng. Mục tiêu đầu tiên của ML là học gần đúng logic hiện tại và chuẩn bị cho giai đoạn có nhãn bác sĩ, không được tuyên bố mô hình đã chứng minh hiệu quả y khoa.

### 6.2 Feature có thể dùng

Chỉ tạo feature từ dữ liệu có sẵn tại hoặc trước `measured_at`:

- glucose hiện tại;
- meal context và phút tính từ bữa ăn;
- độ lệch so với median/p10/p90 của baseline đúng epoch;
- số đo gần nhất, delta và tốc độ thay đổi;
- rolling mean, min, max, standard deviation trong 1 giờ, 3 giờ, 24 giờ;
- số lần vượt vùng mục tiêu trong 3 giờ, 24 giờ và 7 ngày;
- TIR/TBR/TAR quá khứ trong cửa sổ trượt;
- số lần WATCH gần đây và thời gian từ WATCH gần nhất;
- tuổi/nhóm rủi ro, BMI, bệnh tim mạch đã ghi nhận;
- cờ dùng insulin/sulfonylurea/meglitinide;
- care plan version và thời gian từ lúc epoch bắt đầu;
- mức đầy đủ/chất lượng dữ liệu và tỷ lệ CGM hoạt động.

Không dùng trực tiếp các cột sau làm feature vì gây label leakage:

- `status`;
- `attention_score` nếu score này trực tiếp tạo nhãn;
- `primary_reason_code`;
- `suggestion_code`, `suggestion_text`, `suggestion_status`;
- `event_state`;
- mọi review hoặc dữ liệu xuất hiện sau `measured_at`.

`patient_id`, `measurement_id`, `attention_assessment_id` chỉ dùng để join/audit, không dùng như feature dự đoán.

### 6.3 Chia train/validation/test

Không random từng dòng vì các phép đo của cùng bệnh nhân và cùng thời gian rất giống nhau, dẫn đến rò rỉ.

Khuyến nghị:

1. Giữ riêng một nhóm bệnh nhân làm test hoàn toàn chưa thấy.
2. Trong nhóm train, chia theo thời gian: dữ liệu cũ để train, dữ liệu mới hơn để validation.
3. Fit scaler/imputer/encoder chỉ trên train.
4. Không cho dữ liệu tương lai tham gia tính rolling feature của quá khứ.
5. Baseline của mỗi epoch chỉ được tính từ cửa sổ học baseline tương ứng.

Có thể dùng GroupKFold theo `patient_id` để đánh giá độ ổn định, nhưng test cuối vẫn nên là patient holdout kết hợp temporal holdout.

### 6.4 Baseline model

Nên bắt đầu bằng:

1. Rule engine hiện tại làm baseline tham chiếu.
2. Logistic Regression có class weight để dễ giải thích.
3. HistGradientBoosting/XGBoost/LightGBM nếu được phép thêm dependency.

Không cần deep learning ở bước đầu vì dataset synthetic còn nhỏ theo số bệnh nhân và nhãn là weak label.

### 6.5 Metric

Không chỉ dùng accuracy. Báo cáo tối thiểu:

- Precision, Recall và F1 cho lớp WATCH.
- PR-AUC và ROC-AUC.
- Confusion matrix tổng và theo nhóm rủi ro.
- False negative rate của WATCH.
- Số cảnh báo trên mỗi bệnh nhân/ngày sau cooldown.
- Calibration/Brier score của xác suất.
- Kết quả riêng cho `STANDARD_TYPE_2` và `OLDER_OR_HIGH_RISK`.
- Kết quả riêng theo meal context và medication risk.

Đồng thời phải có safety test độc lập chứng minh ML không thể ghi đè một assessment đã được rule phân loại `DANGER`.

## 7. Kế hoạch triển khai đề xuất

### Giai đoạn 1 — Tạo ML table

- Join assessment, measurement, baseline, monitoring profile và profile lâm sàng.
- Tạo feature theo thời gian bằng phép tính chỉ nhìn về quá khứ.
- Loại baseline-learning, danger và các cột leakage khỏi tập WATCH.
- Lưu data dictionary, lineage và checksum cho tập train.

Output đề xuất:

```text
data/ml/personalized_watch/
  train.parquet
  validation.parquet
  test.parquet
  feature_dictionary.csv
  split_manifest.json
  build_report.json
```

### Giai đoạn 2 — Train và đánh giá offline

- Train Logistic Regression baseline.
- Train một tree-based model để so sánh.
- Tune threshold xác suất theo giới hạn số cảnh báo và recall WATCH.
- Đánh giá theo patient holdout, nhóm rủi ro và thời gian.
- Lưu model card và báo cáo sai lệch.

Output đề xuất:

```text
artifacts/personalized_watch/<model_version>/
  model.*
  preprocessing.*
  feature_schema.json
  metrics.json
  model_card.md
  training_manifest.json
```

### Giai đoạn 3 — Shadow mode

- Chạy ML song song với rule engine nhưng chưa dùng ML để phát cảnh báo thật.
- So sánh quyết định ML với WATCH hiện tại.
- Ghi nhận false positive, false negative và số cảnh báo/ngày.
- Thu review của bác sĩ để tạo nhãn chất lượng cao hơn.

### Giai đoạn 4 — Human validation và production gate

- Bác sĩ duyệt định nghĩa WATCH, feature và ngưỡng xác suất.
- Kiểm thử drift, missing data, calibration và subgroup performance.
- Có rollback về rule engine.
- Chỉ bật production sau khi hoàn tất đánh giá lâm sàng, bảo mật và quyền riêng tư.

## 8. Acceptance criteria cho ML v1

- Pipeline tái lập được từ Git commit và manifest cố định.
- Không có bệnh nhân tiền đái tháo đường trong train/validation/test.
- Không có cùng `patient_id` ở cả train và test patient-holdout.
- Không có feature nhìn thấy dữ liệu tương lai.
- Không dùng cột sinh nhãn làm feature.
- ML chỉ chạy sau khi rule DANGER trả về false.
- ML không ghi hoặc sửa care plan/target version.
- Prediction chứa `model_version`, `care_plan_version`, `baseline_epoch_id`, feature timestamp và evidence có thể audit.
- Có kiểm tra hiệu năng theo nhóm rủi ro và thuốc nguy cơ hạ đường huyết.
- Có cơ chế abstain/fallback khi thiếu baseline, schema sai hoặc dữ liệu không đủ.

## 9. Rủi ro và giới hạn cần nói rõ

- Glucose 60 ngày và các event hiện tại là synthetic.
- Nhãn WATCH hiện do rule tạo, không phải nhãn bác sĩ xác nhận.
- Medication chỉ là dữ liệu kê đơn tự báo cáo; thiếu liều, lịch dùng và tình trạng còn sử dụng.
- Chưa có outcome thật để chứng minh WATCH giúp giảm biến cố.
- Cut point không thực sự khác nhau theo từng bệnh nhân; phần cá nhân hóa hiện chủ yếu là risk profile, time-in-range goal, baseline, context và version theo tái khám.
- Mô hình mới phải hỗ trợ drift khi care plan đổi và baseline epoch được reset.
- Không được dùng model để tự thay đổi thuốc, insulin hoặc ngưỡng nguy hiểm.

## 10. Việc đầu tiên người nhận nên làm

1. Checkout nhánh `anh/canhanhoa` và xác nhận commit đầu là `7a10069` hoặc commit mới hơn chứa tài liệu này.
2. Chạy `refresh_longitudinal_personalization.py` và đối chiếu report.
3. Viết script tạo ML table có temporal feature và patient-level split.
4. Viết test chống leakage trước khi train model.
5. Train Logistic Regression baseline và xuất metrics theo subgroup.
6. Trình model card cùng các giới hạn synthetic/weak-label trước khi tích hợp.

## 11. Quy ước không được phá vỡ

- Doctor-owned: `NORMAL`, `DANGER`, care plan và target versions.
- AI-owned: điểm rủi ro WATCH và suggestion theo dõi không điều trị.
- Rule-owned: DANGER precedence và production safety gate.
- Mọi thay đổi theo lần khám phải có event, version, effective date và audit trail.
- Không dùng output demo như y lệnh lâm sàng.

