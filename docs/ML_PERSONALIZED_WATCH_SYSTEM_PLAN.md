# Ngưỡng cảnh báo sớm cá nhân hóa — kế hoạch hệ thống

## 0. Trạng thái hiện tại

- **Giai đoạn A (mục 6) — xong, đã chạy và kiểm chứng.** `data/NHANES/final/build_watch_feature_table.py` sinh `data/ml/personalized_watch/{feature_table.csv, feature_dictionary.csv, split_manifest.json, build_report.json}`. 81.920 dòng (= 100.470 - 18.550 BASELINE_LEARNING), nhãn WATCH=25.459/NORMAL=35.659 khớp đúng số đã chẩn đoán ở mục 3. Test leakage/label ở `data/ml/personalized_watch/test_no_leakage.py`, 6/6 pass.
- **Giai đoạn B (bước 5-8) — xong, đã train + đánh giá + thử Lớp 3.** Đã thêm `pandas`/`scikit-learn` vào `requirements.txt`. `data/NHANES/final/train_watch_model.py` train Logistic Regression (v0) và HistGradientBoosting (v1, model chính), calibrate theo risk_group, chạy GroupKFold ổn định → `artifacts/personalized_watch/{v0_logreg,v1_hgb}/`. `data/NHANES/final/personalized_band.py` hiện thực Lớp 3 (invert model theo trục glucose).
  - **Đọc `artifacts/personalized_watch/v1_hgb/model_card.md` trước khi dùng số liệu** — PR-AUC ~0,9999 không phải bằng chứng hiệu quả lâm sàng: permutation importance xác nhận `glucose_mg_dl` một mình chiếm ~92% mức đóng góp (vì target_high/low cố định 70/180 cho mọi người), và **toàn bộ khối lâm sàng tĩnh đóng góp đúng bằng 0** — khớp chính xác với phát hiện ở mục 3.3/5 rằng rule hiện tại không dùng các cột đó.
  - **Phát hiện mới cho mục 4 (vẫn cần bàn):** thử Lớp 3 trên 20 bệnh nhân mẫu cho thấy cơ chế suy ra "dải cảnh báo sớm" chỉ ra số hợp lý (vd. bệnh nhân 119996: low=128, high=181 mg/dL) **khi baseline cá nhân của bệnh nhân nằm trong vùng an toàn** — nhưng với đa số mẫu (baseline median đã > 180, tức nhóm kiểm soát kém ở mục 3.3), model báo P(WATCH)=1.0 ngay tại baseline của chính họ, không có "dải an toàn" nào để suy ra. Đây là bằng chứng cụ thể, không phải suy đoán, cho câu hỏi mở "nhóm DANGER-nặng cần chính sách riêng" — dùng con số cố định target_high=180 cho mọi người khiến khái niệm "ngưỡng thắt chặt cá nhân" không định nghĩa được cho nhóm này.
- **Notebook tổng hợp để xem cùng nhau:** `data/NHANES/final/personalized_watch_analysis.ipynb` — gộp chẩn đoán dữ liệu (Phần 1) + Giai đoạn A (Phần 2) + Giai đoạn B (Phần 3), đã chạy sẵn (outputs đã lưu trong file, mở lên là thấy ngay không cần chạy lại). Kernel `p158-venv` trỏ đúng `.venv` của repo — mở bằng `jupyter lab` hoặc `jupyter notebook` từ repo root, chọn kernel "P-158 (.venv)".
  - **Bug đã sửa nhờ câu hỏi review:** `meal_context` từng bị gắn nhầm là "identifier" trong `build_watch_feature_table.py`, khiến model ban đầu train mà *không có* feature này dù được liệt kê là feature dùng được. Đã sửa, rebuild, train lại — số liệu ở mục B trên là sau khi sửa.
  - **Câu hỏi đã kiểm chứng: bệnh nhân đo tự do (giờ bất kỳ, dồn dập, thiếu meal_context) model có xử lý được không?** Đã kiểm `codebase/backend/health/schemas.py`: ngoài đời không có gì chặn việc này. Model có dùng thời gian thực (gap, rate, cửa sổ 24h/7d) và bối cảnh bữa ăn (chủ yếu qua baseline theo từng meal_context, bản thân cờ meal_context đóng góp gần như 0) — không giả định lịch đo cố định. Nhưng dữ liệu train mô phỏng gần đúng lịch đo có cấu trúc (RANDOM chỉ 0,1%, 0 dòng thiếu meal_context) nên hành vi ở vùng đo tự do thật **chưa được kiểm chứng bằng dữ liệu** — thêm vào việc cần làm ở Giai đoạn C: tách kết quả shadow mode theo mức độ bất thường của thời điểm đo.
- **Giai đoạn C (bước 9-10) — xong.** `data/ml/personalized_watch/serving.py` là hàm suy luận an toàn duy nhất mọi nơi gọi (shadow mode ở đây, và Giai đoạn D sau này) phải đi qua — kiểm tra DANGER trước, ML chỉ được gọi khi không phải DANGER. `data/ml/personalized_watch/test_danger_precedence.py` — 8/8 pass, kể cả test trên toàn bộ 20.802 dòng DANGER thật trong dataset (dùng predictor giả sẽ raise lỗi nếu bị gọi, để chứng minh ML không bao giờ được gọi). `data/NHANES/final/run_shadow_mode.py` chạy pipeline an toàn trên toàn bộ 81.920 dòng → `data/ml/personalized_watch/shadow_mode_report.json`.
  - Agreement tổng thể rule vs ML (đã qua safety gate): 99,97% (81.920 dòng). Toàn bộ 20.802 dòng DANGER được gate đúng 100%, không dòng nào lọt qua ML (khớp confusion matrix). Trên non-DANGER: chỉ 8 ca WATCH bị ML bỏ sót, 18 ca NORMAL bị ML báo nhầm WATCH — trên tổng 61.118 dòng.
  - **Trả lời trực tiếp câu hỏi review (đo tự do có được xử lý không):** tách theo `timing_bucket` (gap <30 phút / 30 phút-3 giờ / >3 giờ / lần đo đầu) và theo `meal_context_group` (structured vs RANDOM/EVENT_RECOVERY) — agreement vẫn 99,96-100% ở mọi nhóm, **kể cả nhóm đo dồn dập** (<30 phút, n=2.311, agreement=1,0) và **nhóm bối cảnh bất thường** (n=918, agreement=1,0). Nhưng đây là các ca ĐÃ CÓ trong dữ liệu synthetic dù hiếm — không chứng minh được gì cho ca thật sự chưa từng xuất hiện (`meal_context` rỗng hoàn toàn, 0 ví dụ) đã nêu ở mục 1.7 trong notebook. Kết luận: an toàn cho các ca đo lệch nhịp nhưng vẫn có bối cảnh, chưa kiểm chứng được cho ca thiếu bối cảnh hoàn toàn.
  - **Bước 11 (model card + review bác sĩ) chưa làm** — đây là bước cần người thật (bác sĩ) xem các ca ML/rule lệch nhau, không phải việc có thể tự động hoá.
- **Lớp 2/3 đã đổi thiết kế: quantile regression per meal_context, thay cho invert classifier.** Quyết định sau khi thấy giới hạn ở mục 6.3 (Lớp 3 cũ không suy ra được band cho bệnh nhân baseline cao) và yêu cầu ngưỡng phải khác nhau theo `meal_context`, tự cập nhật khi có dữ liệu mới. `data/NHANES/final/train_threshold_regression.py` train hai `HistGradientBoostingRegressor(loss="quantile")` (quantile 0,10 và 0,90) trên **cùng** `feature_table.csv` đã có — không cần rebuild Giai đoạn A. Model chung (không phải 6 model riêng theo context) vì `RANDOM`/`EVENT_RECOVERY` quá ít dữ liệu để train riêng — `meal_context` là một feature, không phải khoá chia model.
  - **Đảo chiều leakage:** target giờ là `glucose_mg_dl` chính nó, nên `delta_glucose_mg_dl`, `rate_mg_dl_per_min`, `deviation_from_baseline_*` (đều tính từ giá trị hiện tại) phải bị loại khỏi feature — ngược hướng với danh sách leakage của classifier. Test riêng: `data/ml/personalized_watch/test_regression_no_leakage.py`, 4/4 pass.
  - **"Cập nhật với dữ liệu mới" = feature tự tính lại mỗi lần đo (miễn phí, đã có sẵn từ Giai đoạn A), không phải model tự train lại theo từng lần đo.** Model chỉ cần retrain định kỳ (vd. hàng tháng) trên dữ liệu tích lũy toàn bộ population — một job riêng, chưa xây.
  - **Kết quả (validation, n=16.002):** coverage 77,6% (mục tiêu lý thuyết 80% cho cặp quantile 10/90 — khá sát). **Band thật sự khác nhau rõ theo meal_context, đúng yêu cầu:** AFTER_MEAL rộng nhất (138–218 mg/dL, width 80 — hợp lý vì đường huyết sau ăn dao động nhiều), FASTING hẹp nhất (142–182, width 40 — hợp lý vì fasting kiểm soát chặt hơn). `RANDOM` (n=17) cho band rất rộng/bất ổn (60–204, width 143) — đúng như dự đoán, ngữ cảnh hiếm không đủ dữ liệu để ước lượng ổn định dù đã dùng model chung. Không còn hiện tượng "band rỗng" như Lớp 3 cũ ở mục 6.3 — model luôn ra được [min, max], kể cả khi con số đó nói thẳng "vùng an toàn của bạn đã sát DANGER".
  - Artifacts: `artifacts/personalized_watch/v2_quantile_regression/{min_model.joblib, max_model.joblib, metrics.json, feature_schema.json}`.
  - **Chưa làm:** cập nhật Lớp 1 diagram/quyết định mục 04 để phản ánh thiết kế mới (hiện mục 04 vẫn mô tả Lớp 2/3 kiểu cũ); an toàn hoá — quantile regression chưa có safety test kiểu `test_danger_precedence.py`; chưa quyết định có giữ classifier song song để bắt tín hiệu xu hướng/lặp lại hay không (câu hỏi mở ở mục 07 artifact).
- **Giai đoạn D — đã đưa quantile regression vào backend sống (chưa chạy thử với DB thật).** Soát schema thật (`codebase/backend/`) trước khi viết code: xác nhận `create_glucose_record()` không đánh giá gì cả, và phần lớn 48 input feature **không tồn tại** trong Postgres — demographics, comorbidity flags, `risk_group`, ngưỡng target/danger đều thiếu hoàn toàn; HbA1c/huyết áp có ở `ClinicalNote` (structured), còn LDL/HDL/triglyceride/glucose đói bị `migrate_nhanes_demo_data.py` làm phẳng thành text tự do, không query được.
  - Thêm 2 bảng mới vào `codebase/backend/patients/models.py`: **`PatientProfile`** (tuổi/giới/BMI/caregiver/4 cờ bệnh đồng mắc, nullable toàn bộ) và **`CarePlanTarget`** (ngưỡng target/danger + risk_group do bác sĩ duyệt, 1 dòng/bệnh nhân) — bảng thứ hai là điều kiện chặn thật sự: không có nó thì không có gì để Lớp 1 (DANGER) gate theo, bất kể dùng rule hay ML.
  - Thêm `predicted_min_mg_dl`/`predicted_max_mg_dl`/`threshold_status` (nullable) trực tiếp lên `GlucoseRecord` thay vì bảng assessment riêng — đơn giản hơn vì quan hệ 1-1 với mỗi lần đo, không cần theo dõi cooldown ở bước này.
  - `codebase/backend/health/threshold_features.py` — tính lại 48 feature từ query Postgres (rolling stats/baseline percentile/exceedance từ `GlucoseRecord`, labs từ `ClinicalNote` mới nhất, thuốc từ `Medication` để suy `medication_hypoglycemia_risk`). Field nào thực sự không có nguồn sống (LDL/HDL/triglyceride/glucose đói) → NaN + `_is_missing=1`, đúng như model đã được train để chịu được. `overall_data_quality_score` không có khái niệm tương đương ngoài đời và model không có cờ missing cho nó → impute hằng số 93,2 (trung bình tập train), ghi rõ đây là lựa chọn có chủ đích.
  - `codebase/backend/health/threshold_service.py::evaluate_glucose_reading` — bản sống của `serving.py`: kiểm DANGER từ `CarePlanTarget` trước, ML chỉ gọi khi không phải DANGER. Không có `CarePlanTarget` → trả `UNAVAILABLE`, không đoán.
  - Nối vào `create_glucose_record()`: bọc trong try/except riêng — bản ghi glucose luôn được lưu dù việc đánh giá threshold lỗi, không để phần ML mới (ít được kiểm chứng hơn) làm hỏng chức năng lưu số đo chính.
  - Test: `tests/test_backend/test_threshold_service.py` (5 test: abstain khi thiếu care plan, DANGER không gọi ML, boundary đúng semantics, WATCH/NORMAL có predicted min/max, record vẫn lưu được). Toàn bộ 351 test trong `tests/` vẫn pass — không có regression.
  - `scripts/backfill_care_plan_targets.py` — backfill `PatientProfile`/`CarePlanTarget` cho các bệnh nhân demo đã import, cùng quy ước id xác định (`uuid5`) với `migrate_nhanes_demo_data.py`. **Đã chạy thật và kiểm chứng end-to-end:** dựng `docker compose up -d db`, chạy `seed_admin` + `migrate_nhanes_demo_data` (489 bệnh nhân đã có sẵn từ trước, 0 dòng mới — xác nhận idempotent), sửa 2 lỗi thứ tự import lộ ra khi chạy thật (thiếu đăng ký `User`/`Appointment`/`GlucoseRecord`/`Medication` vào `SQLModel.metadata` trước khi `create_db_and_tables()` patch cột — cùng lỗi dạng migrate_nhanes_demo_data.py từng gặp), rồi backfill: **489/489 `PatientProfile` + 489/489 `CarePlanTarget` insert thành công**, chạy lại lần 2 cho ra 0 dòng mới (xác nhận idempotent).
  - **Test cuối cùng — tạo glucose record thật qua đúng `create_glucose_record()` trên Postgres thật** (không phải SQLite test fixture): bệnh nhân thật (187 bản ghi có sẵn), đọc 145 mg/dL (FASTING) → `threshold_status=WATCH`, `min=161.7`, `max=204.9` (145 thấp hơn min dự đoán — đúng); đọc 310 mg/dL → `threshold_status=DANGER`, `min`/`max`=None (ML không được gọi — đúng). Đã dọn 2 bản ghi test này khỏi DB sau khi xác nhận. `codebase.backend.main` (app FastAPI thật) cũng import và khởi tạo schema sạch, không riêng gì script backfill. Toàn bộ 351 test vẫn pass sau khi chạy live.
  - **Chưa làm:** doctor-facing UI/endpoint để tự sửa `CarePlanTarget` (hiện chỉ có backfill script, không có API); safety test dạng `test_danger_precedence.py` đối chiếu với *toàn bộ* dữ liệu DANGER thật trong Postgres (có 3 test tương đương trong `test_threshold_service.py` nhưng chỉ trên dữ liệu tự tạo trong test, chưa quét hết 20.802 dòng DANGER thật đã migrate); imputation cho demographics thiếu (age/bmi/sex khi `PatientProfile` rỗng — hiện tất cả 489 bệnh nhân demo đã có đủ vì backfill từ CSV, nhưng một bệnh nhân mới tạo qua UI thật sẽ có `PatientProfile` rỗng) chưa được model kiểm chứng vì không có cờ `_is_missing` tương ứng lúc train.
- **Giai đoạn E — xong, chạy thật trên Postgres thật.** Thêm `NotificationType.GLUCOSE_ALERT` vào `notifications/models.py`. Vì đây là Postgres native enum, `create_db_and_tables()`'s additive-column patcher **không** tự thêm giá trị enum mới vào type đã tồn tại (patcher chỉ thêm cột thiếu, không sửa enum) — trên DB đã tạo từ trước phải chạy tay `ALTER TYPE notification_type ADD VALUE 'GLUCOSE_ALERT'` một lần (DB tạo mới từ đầu qua `create_db_and_tables()` sẽ tự có đủ 5 giá trị, không cần bước này).
  - `notifications/service.py::create_glucose_alert_notification` — dùng lại đúng `get_or_create_notification` (idempotent theo `slot_key=f"glucose_alert:{glucose_record_id}"`) và `_push_zalo_reminder`'s pattern (`asyncio.run(ZaloBotNotifier().send_message(...))`, best-effort, không throw). Thêm giới hạn `MAX_GLUCOSE_ALERTS_PER_DAY=4` độc lập với logic cooldown-theo-lý-do cũ (quantile regression không còn "reason code" để cooldown theo).
  - Bệnh nhân chưa liên kết Zalo (`zalo_chat_id=None`) **vẫn được tạo Notification trong app**, chỉ bỏ qua bước đẩy Zalo — khớp đúng hành vi đã có của 3 loại reminder cũ (`scheduler.py`), trả lời câu hỏi mở còn lại ở mục 04.
  - Nối vào `create_glucose_record()`: chỉ gọi khi `threshold_status == "WATCH"` (DANGER/NORMAL không notify), bọc try/except riêng — lỗi notification không được rollback phần threshold evaluation đã commit trước đó.
  - Test: `tests/test_backend/test_glucose_alert.py` (5 test: WATCH tạo notification, DANGER không tạo, chạm giới hạn/ngày thì dừng, idempotent theo record id, bệnh nhân chưa liên kết Zalo vẫn có notification trong app) — pass. Toàn bộ `tests/` + `data/ml/personalized_watch/`: **374/374 pass**.
  - **Kiểm chứng thật trên Postgres thật:** đọc 245 mg/dL (band cá nhân dự đoán 171-237, danger_high=250) → `threshold_status=WATCH`, tạo đúng 1 `Notification` với nội dung "Chỉ số đo được là 245 mg/dL, cao hơn dải bình thường cá nhân của bạn (171-237 mg/dL)...". Bệnh nhân test không có `zalo_chat_id` nên đẩy Zalo bị bỏ qua đúng như thiết kế (không lỗi). Đã dọn dữ liệu test sau khi xác nhận.
  - **Chưa làm:** chưa test path có `zalo_chat_id` thật + `zalo_bot_enabled=True` (cần bot token thật, ngoài phạm vi môi trường này); doctor-facing UI để bật/tắt loại cảnh báo này cho từng bệnh nhân chưa có.

Tái tạo Giai đoạn A:

```bash
python3 data/NHANES/final/build_watch_feature_table.py \
  --assessments data/knowledge_structured/attention_engine/patient_attention_assessments.csv \
  --baselines data/knowledge_structured/attention_engine/patient_glucose_baselines.csv \
  --monitoring-profiles data/knowledge_structured/patient_cgm_monitoring_profiles.csv \
  --patient-profiles data/NHANES/deployment_dataset/patient_profiles.csv \
  --patient-clinical data/NHANES/deployment_dataset/patient_baseline_clinical.csv \
  --output-dir data/ml/personalized_watch
```

## 1. Tổng quan & bài toán

Sau buổi tái khám, bác sĩ đặt một ngưỡng nguy hiểm (thấp hoặc cao) cho từng bệnh nhân. Nếu glucose vượt ngưỡng đó, hệ thống báo bệnh nhân đang gặp nguy hiểm và cần tái khám hoặc đến viện. Mục tiêu ở đây là thêm một **ngưỡng thắt chặt hơn**, nằm phía trong ngưỡng nguy hiểm: khi glucose chạm ngưỡng này, bệnh nhân được nhắc hành động sớm để không đi tới ngưỡng nguy hiểm thật.

Mô hình hai lớp `DANGER` / `WATCH` cho ý tưởng này đã tồn tại sẵn trong repo dưới dạng rule engine (`data/NHANES/final/build_personalized_attention_dataset.py`) — nhưng `WATCH` hiện là một luật đa tín hiệu (7 lý do khác nhau), không phải một con số mg/dL cụ thể, và chỉ chạy offline để tạo dataset nghiên cứu, chưa chạy trong ứng dụng thật. Bài toán có ba phần:

1. Làm WATCH chính xác hơn — tận dụng thêm dữ liệu lâm sàng (HbA1c, bệnh đồng mắc...) mà rule hiện tại chưa dùng đến.
2. Suy ra một con số ngưỡng cụ thể cho từng bệnh nhân, đủ dễ hiểu để hiển thị cho bác sĩ/bệnh nhân — dù dữ liệu hiện tại không có sẵn nhãn "ngưỡng đúng" để train regression trực tiếp.
3. Báo trực tiếp cho bệnh nhân qua Zalo ngay khi một phép đo mới vượt ngưỡng WATCH.

Tài liệu này là bản chốt của quá trình thảo luận, hợp nhất chẩn đoán dữ liệu (`docs/ML_HANDOFF_PERSONALIZED_ATTENTION.md`) với việc soát lại `codebase/backend/` để bảo đảm kế hoạch khớp với hệ thống thật, không chỉ khớp với dataset nghiên cứu.

## 2. Dữ liệu hiện có

489 bệnh nhân TYPE_2, 100.470 phép đo glucose synthetic trong 60 ngày (~3,4 phép đo/ngày/người).

| Nhóm | File | Quy mô | Vai trò |
|---|---|---:|---|
| Chuỗi glucose | `data/simulator_60d/synthetic_glucose_measurements.csv` | 100.470 | Phép đo có thời gian & bối cảnh bữa ăn |
| WATCH-ready | `attention_engine/patient_attention_assessments.csv` | 100.470 | 1 đánh giá / phép đo — bảng chính cho ML |
| WATCH-ready | `attention_engine/patient_glucose_baselines.csv` | 2.142 | p10/median/p90 theo bệnh nhân × epoch × bối cảnh |
| WATCH-ready | `attention_engine/patient_attention_events.csv` | 18.514 | Cảnh báo thật phát ra sau cooldown 12h |
| Ngưỡng & care plan | `patient_cgm_monitoring_profiles.csv` | 489 | `risk_group`, mục tiêu TIR/TBR/TAR, ngưỡng DANGER/NORMAL |
| Ngưỡng & care plan | `patient_clinical_targets.csv` | 1.467 (3/bệnh nhân) | `CGM_DANGER_LOW`/`CGM_DANGER_HIGH`/`CGM_TARGET_RANGE`, doctor-approved |
| Hồ sơ lâm sàng | `NHANES/deployment_dataset/patient_profiles.csv` | 489 | Tuổi, giới tính, BMI, điểm chất lượng dữ liệu |
| Hồ sơ lâm sàng | `NHANES/deployment_dataset/patient_baseline_clinical.csv` | 489 | HbA1c, glucose đói, lipid, huyết áp, cờ bệnh đồng mắc |
| Longitudinal | `longitudinal_care/*.csv` | 489/bảng | Version care plan/target theo tái khám — chưa có biến thiên, xem mục 3 |

Toàn bộ dữ liệu trên là **synthetic**, không phải hồ sơ bệnh nhân thật. Đây là dataset nghiên cứu dùng để huấn luyện — ứng dụng thật (`codebase/backend/`) chạy trên Postgres và hiện chỉ import số đo thô qua `scripts/migrate_nhanes_demo_data.py`, không import các trạng thái WATCH/DANGER đã tính sẵn.

## 3. Phân tích

**3.1 — Dữ liệu tự đo rời rạc, không phải CGM liên tục.** Khoảng cách đo trung vị 345 phút (~5,75 giờ), ~3,4 phép đo/ngày, gắn bối cảnh bữa ăn. Feature "rolling 1 giờ" gần như luôn chỉ có 1 điểm — phải tính rolling theo *số phiên đo*, không theo giờ cố định. TIR/TBR/TAR tính từ dữ liệu này là % trên số lần đo, không phải % thời gian thật.

**3.2 — Ngưỡng DANGER: đã có, đã duyệt, nhưng chưa cá nhân hóa.** Mỗi bệnh nhân đã có dòng target riêng, trạng thái `ACTIVE`, do bác sĩ duyệt (127 bác sĩ khác nhau, 0 dòng thiếu `approved_by_doctor_id`) — nhưng giá trị giống hệt nhau cho cả 489 người: danger_low = 54, danger_high = 250, normal range = 70–180, mốc cần đánh giá lâm sàng = 300 mg/dL. Schema đã sẵn sàng cho cá nhân hóa (mỗi bệnh nhân có dòng target riêng); chỉ là giá trị gán vào chưa phân hóa.

**3.3 — Lệch mạnh theo bệnh nhân, tương quan rõ với chỉ số lâm sàng.** Tương quan tỉ lệ DANGER với HbA1c: r = 0,79; với glucose đói: r = 0,89. 65/489 bệnh nhân (13,3%) có >50% bản ghi là DANGER. 88/489 (18%) có tỉ lệ WATCH = 100% trong phần không-DANGER. HbA1c/glucose đói tương quan mạnh nhưng hoàn toàn không được attention engine dùng — tín hiệu thật đang bị bỏ qua.

**3.4 — Baseline cá nhân hóa còn mỏng, nhãn có hai cấp độ.** 22,7% baseline (486/2.142 tổ hợp) không đủ điều kiện dùng; trung bình 8,66 điểm/baseline (tối thiểu 5). Nhãn WATCH có 2 cấp: assessment-level (25.459, trước cooldown) và event-level (18.514, sau cooldown — 27,3% bị nén lại). Cần chọn một cấp để train, không trộn lẫn.

**3.5 — Chưa có dữ liệu biến thiên theo thời gian tái khám.** `care_plan_version` chỉ có 1 giá trị trong toàn bộ 100.470 dòng; `patient_care_plan_change_log.csv` rỗng. Hành vi "model chịu được drift khi đổi care plan" hiện chỉ đúng về logic, chưa được dữ liệu thật kiểm chứng.

**3.6 — Chưa có đánh giá WATCH/DANGER nào chạy sống.** Đã soát `codebase/backend/health/service.py`: `create_glucose_record()` chỉ lưu bản ghi, không gọi rule engine nào. Toàn bộ logic DANGER/WATCH mới tồn tại ở dạng script offline. Đây là khoảng trống phải lấp trước khi bất kỳ cảnh báo nào — rule-based hay ML — có thể chạy trên dữ liệu thật.

## 4. Đề xuất hệ thống

Kiến trúc 4 lớp, giữ nguyên nguyên tắc bác sĩ toàn quyền trên DANGER/NORMAL:

```text
Phép đo glucose mới
  → Lớp 1: rule DANGER cố định do bác sĩ duyệt — luôn thắng, chặn ML
  → Lớp 2: ML ước lượng P(WATCH) — HistGradientBoosting chính, Logistic Regression đối chiếu
       input = feature time-series (3.1) + feature lâm sàng tĩnh (mục 5)
  → P(WATCH) qua ngưỡng vận hành + cooldown 12h → WATCH hoặc NORMAL
  → Lớp 3: suy ra dải cảnh báo sớm cá nhân (số mg/dL cụ thể) bằng cách
       invert model Lớp 2 theo trục glucose, giữ nguyên context/trend hiện tại
  → Lớp 4: nếu WATCH và đã liên kết Zalo (User.zalo_chat_id) → gửi tin qua ZaloBotNotifier
```

**Vì sao classification, không phải regression trực tiếp:** dữ liệu hiện tại không có nhãn "ngưỡng đúng" nào để train regression — chỉ có nhãn WATCH/NORMAL từ rule. HistGradientBoosting xử lý tốt feature hỗn hợp numeric/categorical, không cần deep learning với 489 bệnh nhân; Logistic Regression chạy song song làm baseline dễ giải thích.

**Con số ở Lớp 3** được suy ra chứ không train riêng: giữ nguyên context/trend/feature lâm sàng hiện tại của một bệnh nhân, quét giá trị glucose giả định qua model Lớp 2 để tìm điểm P(WATCH) cắt ngưỡng vận hành (vd. 0,5) — đó là con số mg/dL cụ thể, cá nhân hóa, không cần nhãn regression không tồn tại.

**Còn cần bàn cùng nhau, chưa tự chốt:**

- Con số Lớp 3 hiển thị trực tiếp cho bệnh nhân, hay chỉ bác sĩ xem trước?
- Ngưỡng vận hành P(WATCH) cố định chung hay tune riêng theo nhóm 65 bệnh nhân DANGER-nặng?
- Đưa toàn bộ khối lâm sàng vào ngay v1, hay bắt đầu hẹp rồi mở rộng?
- DANGER có cần kênh báo riêng (Zalo/gọi điện) hay dùng chung Lớp 4?
- Bệnh nhân chưa liên kết Zalo — vẫn tạo `Notification` trong app để hiển thị, hay bỏ qua tới khi họ liên kết?

## 5. Kết hợp hồ sơ lâm sàng tĩnh với chuỗi thời gian glucose

Nguyên lý: **fusion sớm qua feature engineering**, không phải kiến trúc hai nhánh riêng. Với 489 bệnh nhân và nhãn weak-label, một kiến trúc hai nhánh kiểu deep learning sẽ phức tạp hơn mức dữ liệu cho phép — cả hai nguồn được nén thành cột số trên cùng một dòng, rồi đưa vào một model bảng duy nhất.

| Nhóm feature | Nguồn | Join key | Ví dụ cột | Encode |
|---|---|---|---|---|
| Thời gian — trạng thái hiện tại | `patient_attention_assessments.csv` | grain gốc | glucose hiện tại, delta, tốc độ | Scale cùng scaler với feature tĩnh |
| Thời gian — baseline cá nhân | `patient_glucose_baselines.csv` | (patient_id, baseline_epoch_id, meal_context) | độ lệch so với p10/median/p90 | Numeric, scale trên train |
| Tĩnh — nhân khẩu & chất lượng | `patient_profiles.csv` | patient_id | tuổi, BMI, caregiver_required_flag, completeness | Numeric scale; completeness + cờ missing |
| Tĩnh — xét nghiệm lâm sàng | `patient_baseline_clinical.csv` | patient_id (snapshot, chưa version hoá) | HbA1c, glucose đói, lipid, huyết áp, cờ tim mạch | Numeric scale + `<field>_is_missing` |
| Tĩnh — hồ sơ theo dõi | `patient_cgm_monitoring_profiles.csv` | (patient_id, care_plan_id) — FK có sẵn trên dòng assessment | risk_group, medication_hypoglycemia_risk, mục tiêu TIR/TBR/TAR | One-hot / categorical gốc |

**Không phải leakage:** `risk_group`/`medication_hypoglycemia_risk` đã là input của rule engine hôm nay (`build_personalized_attention_dataset.py` dòng 198 & 252) nhưng chỉ cộng thêm `attention_score`/modifier, không đổi ranh giới WATCH/NORMAL — dùng làm feature ML là an toàn. HbA1c/lipid/huyết áp/bệnh đồng mắc thì chưa hề được engine đụng tới — tín hiệu chưa khai thác, không phải leakage.

**Rủi ro mới:** thêm ~15-20 cột tĩnh theo bệnh nhân trên chỉ 489 người khiến tree model dễ "thuộc mặt" từng bệnh nhân qua tổ hợp field tĩnh thay vì học tín hiệu lâm sàng thật. Bắt buộc kiểm tra: AUC patient-holdout không được lệch xa AUC train; nếu lệch mạnh, chạy permutation importance để xem model có đang dựa gần như hoàn toàn vào khối tĩnh hay không.

**Khoảng trống hiệu lực theo thời gian:** `patient_baseline_clinical.csv` hiện chỉ có 1 snapshot/bệnh nhân, không có cột ngày — join thẳng theo `patient_id` an toàn cho v1, nhưng nên viết join như thể có version (giống `effective_profile()`/`effective_epoch()` engine đang dùng cho care plan) để không vỡ khi có lần xét nghiệm thứ hai.

## 6. Từng bước xây dựng

A-C là pipeline ML ngoại tuyến (không đụng backend sống); D-E là phần đưa vào ứng dụng thật.

### Giai đoạn A — Feature table & split

1. Viết script build feature table: join `patient_attention_assessments.csv` + `patient_glucose_baselines.csv` + `patient_cgm_monitoring_profiles.csv` + `patient_profiles.csv` + `patient_baseline_clinical.csv` → `data/ml/personalized_watch/build_feature_table.py` → `feature_table.parquet`.
2. Tính feature chỉ nhìn quá khứ: glucose hiện tại, delta/tốc độ, độ lệch so với p10/median/p90, số lần vượt target trong N phiên đo gần nhất, TIR/TBR/TAR cửa sổ trượt theo số lần đo.
3. Loại `BASELINE_LEARNING` khỏi tập train; giữ `DANGER` riêng cho test an toàn ở giai đoạn C. Loại cột leakage: `status`, `attention_score`, `primary_reason_code`, `risk_modifiers`, `suggestion_*`, `event_state`. Test: `tests/test_no_leakage.py`.
4. Chia patient-holdout (20%, stratify theo risk_group và tỉ lệ DANGER) + temporal split trong phần train. `split_manifest.json`.

### Giai đoạn B — Train & suy ra ngưỡng số

5. Train Logistic Regression baseline (`class_weight="balanced"`) → `artifacts/personalized_watch/v0_logreg/`.
6. Train HistGradientBoosting làm model chính, so PR-AUC/recall/FN rate với baseline, báo cáo theo risk_group và nhóm DANGER-nặng → `artifacts/personalized_watch/v1_hgb/metrics.json`.
7. Calibrate xác suất theo risk_group (Platt/Isotonic).
8. Suy ra dải cảnh báo sớm cá nhân (Lớp 3): quét glucose giả định qua model để tìm điểm P(WATCH) cắt ngưỡng vận hành → `personalized_band.py`.

### Giai đoạn C — An toàn & shadow mode

9. Safety test độc lập: input đã bị DANGER chặn → ML không bao giờ được gọi/ghi đè → `tests/test_danger_precedence.py`.
10. Shadow mode trên toàn bộ 100.470 dòng: log song song ML vs rule, chưa phát cảnh báo thật, báo cáo lệch theo nhóm rủi ro và bệnh nhân DANGER-nặng.
11. Model card + review bác sĩ trên các ca lệch nhiều nhất → `artifacts/personalized_watch/v1_hgb/model_card.md`.

### Giai đoạn D — Đưa đánh giá WATCH/DANGER vào luồng sống

12. Port logic rule engine vào `health/service.py`: hàm `evaluate_glucose_status()` dùng lại luật của `build_personalized_attention_dataset.py` (sau này thay bằng model Lớp 2), gọi ngay sau khi lưu bản ghi mới → `codebase/backend/health/attention.py`.
13. Thêm bảng lưu assessment/event trong Postgres — tương đương `patient_attention_assessments`/`patient_attention_events` bản offline, kèm cooldown 12h/lý do cho dữ liệu sống.

### Giai đoạn E — Báo Zalo khi vượt WATCH

14. Thêm `NotificationType.GLUCOSE_ALERT` vào `notifications/models.py` (event-driven, khác 4 loại hiện có vốn theo lịch cố định).
15. Gọi thẳng `ZaloBotNotifier` ngay sau bước 12 (không qua cron tick) — tái dùng `ZaloBotNotifier().send_message(chat_id, text)` như `scheduler.py` đang làm, chỉ gửi nếu `User.zalo_chat_id is not None`. Không cần bảng consent mới — hạ tầng liên kết Zalo (`ZaloLinkCode`, `zalo_link_listener.py`) đã có sẵn, xem `docs/ZALO_NOTIFICATIONS.md`.
16. Idempotency & audit trail: dùng lại pattern `get_or_create_notification` đã có, đổi `slot_key` thành khoá theo `attention_event_id`.
17. Thêm giới hạn tổng số tin/ngày/bệnh nhân, độc lập với cooldown theo lý do.
18. Test đường biên: chưa liên kết Zalo → không gửi, không crash luồng lưu glucose chính → `tests/test_backend/test_glucose_alert.py`.

## 7. Nguồn

`docs/ML_HANDOFF_PERSONALIZED_ATTENTION.md` · `data/knowledge_structured/attention_engine/` · `data/simulator_60d/` · `data/NHANES/deployment_dataset/` · `data/NHANES/final/build_personalized_attention_dataset.py` · `codebase/backend/health/service.py` · `codebase/backend/notifications/` · `docs/ZALO_NOTIFICATIONS.md`
