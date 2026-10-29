# Epic 1 — Meal Intelligence: agent phân tích bữa ăn

**Ưu tiên:** P0 · **Độ lớn:** L · **Phụ thuộc:** — · **Trạng thái:** Gần xong — 1.1–1.6, 1.8 xong; 1.9 lịch 2026-10-29; 1.7 hoãn sau pilot (2026-10-29)

## Mục tiêu

Biến nhật ký bữa ăn từ text tự do thành dữ liệu có cấu trúc (món, khẩu phần, carb, kcal, GI) và
trả lời được câu hỏi lâm sàng: **món nào làm đường huyết sau ăn của bệnh nhân này tăng?**

## Hiện trạng (đã kiểm tra trong code)

- `MealRecord` (`codebase/backend/health/models.py:125`) chỉ có `meal_type`, `meal_time`,
  `description` (≤1000 ký tự), `note` (≤500).
- "Khẩu phần" ở frontend bị nối vào `note` dạng `"Khẩu phần: ... · ..."`
  (`codebase/frontend/components/pages/meals-page.tsx:106`) — không có trường riêng.
- **Ảnh bữa ăn không được upload** — chỉ giữ bằng `URL.createObjectURL` trong phiên trình duyệt.
- Agent chỉ đếm bữa để tính data completeness (`synthesize_summary.py:75-83, 268-282`);
  `metrics_tools.py` tính post-prandial over-target nhưng **không liên kết với bữa ăn cụ thể**.
- Đã có: `health/content_safety.py::is_unsafe_meal_description`, parser bữa ăn từ chat trong
  `agent/record_actions.py` (draft → confirm), tài liệu dinh dưỡng trong `data/knowledge_base/raw/`
  (`vade-chi-so-gi.md`, `medinet-che-do-dinh-duong-can-bang-dai-thao-duong.md`,
  `ada-standards-2026-nutrition-changes.md`, `bachmai-trung-tam-dinh-duong-lam-sang.md`).

## Phạm vi

**In:** phân tích text bữa ăn, bảng tra dinh dưỡng món Việt, tương quan bữa ăn ↔ glucose sau ăn,
hiển thị cho bệnh nhân và bác sĩ, nhận diện từ ảnh (draft).

**Out:** gợi ý liều insulin theo carb (PRD Won't), thực đơn kê cho bệnh nhân, đếm calo chính xác
dạng app giảm cân, tích hợp CGM, ghi bữa ăn bằng giọng nói trên production (1.7 — hoãn sau pilot).

## Task

### 1.1 Tầng dữ liệu
> **Quyết định 2026-10-27: không lưu ảnh bữa ăn.** Ảnh chỉ dùng để AI điền sẵn form, rồi bị bỏ; DB chỉ giữ
> dữ liệu dạng chữ bệnh nhân đã xác nhận. Lý do: không giữ PHI dạng ảnh trên server (hết câu hỏi retention
> ảnh ở Epic 6), không cần volume/dọn file; các tính năng sau (1.4, Epic 2, Epic 4) chỉ cần món + khẩu
> phần. Cái giá: không phân tích lại được từ ảnh, bác sĩ không xem được ảnh, eval nhận diện ảnh phải dùng bộ
> ảnh riêng. Upload/xem ảnh theo thiết kế cũ đã bỏ; phần nhận diện kéo lên làm cùng 1.1 (xem 1.6).

- [x] `MealRecord`: thêm `portion: str | None` (≤100) — nullable để `_add_missing_columns` tự thêm cột.
      ~~`image_path`~~ thay bằng `input_source` (`text|photo|voice`, NULL với bữa ăn trước Epic 1).
      *Denylist chất cấm chạy cả trên `portion` (không thêm lời gọi LLM). Bữa ăn cũ vẫn giữ khẩu phần trong
      `note` dạng "Khẩu phần: …" — không migrate.*
- [x] Bảng mới `meal_analyses`: `id`, `meal_id` (FK unique), `patient_id`, `items` (JSON:
      `[{name, matched_food_id, quantity, unit, grams, carbs_g, kcal, gi_level, match_confidence}]`),
      `total_carbs_g`, `total_kcal`, `carb_load` (`low|medium|high|unknown`), `source`
      (`text|photo|voice`), `status` (`draft|confirmed|edited`), `model_version`, `nutrition_db_version`,
      `created_at`, `updated_at`.
      *Model `MealAnalysis` trong `health/models.py`; `source` dùng chung enum `MealInputSource` với
      `MealRecord.input_source`. Chưa có endpoint — thuộc 1.3.*
- [x] ~~Upload ảnh `POST /health/meals/{id}/image` + `GET` có kiểm quyền + volume~~ — bỏ theo quyết định trên.
- [x] Frontend `meals-page.tsx` + `lib/api.ts`: gửi `portion` riêng, bỏ cơ chế ảnh tạm.
      *Ảnh chỉ còn là preview cục bộ (object URL) trong `components/meal-photo-prefill.tsx`.*
- **AC:** ~~reload vẫn thấy ảnh / test 403 ảnh~~ (không còn ảnh để xem); DB cũ khởi động lại không lỗi và có
  cột mới. *Test: `tests/test_backend/test_meal_photo.py::test_old_database_gains_new_meal_columns`.*

### 1.2 Bảng tra dinh dưỡng món Việt
- [x] `data/nutrition/vn_foods.csv`: `food_id, name_vi, aliases, default_unit, grams_per_unit,
      carbs_per_100g, kcal_per_100g, gi_level, source, source_page`. Nguồn chính: *Bảng thành phần thực
      phẩm Việt Nam* (Viện Dinh dưỡng Quốc gia); món chế biến (phở, cơm tấm, bánh mì, bún bò…) ghi rõ
      nguồn/công thức ước tính. Mục tiêu bản đầu: **150–300 món/thực phẩm phổ biến**.
      *Đặt ở `codebase/models/agents/tools/nutrition_data/vn_foods.csv` (không phải `data/`): `.dockerignore`
      loại `data/` và volume `/app/data` sẽ che file trong image. **214 món/thực phẩm**, thêm cột `gi_source`.
      **Trung thực về nguồn:** mức GI có nguồn (`VADE` = bảng GI của VADE trong kho tri thức, `BYT-5481` =
      phân loại trái cây định tính); **toàn bộ carb/kcal là ước tính (`source=ESTIMATE`) chưa đối chiếu Bảng
      TPTP VN** — `NUTRITION_DB_VERSION = vn_foods-2026.10-unverified`, lưu kèm mỗi phân tích, UI ghi "ước tính".
      Việc đối chiếu số liệu với Bảng TPTP VN nằm trong task ngày 29/10 (1.9).*
- [x] `codebase/models/agents/tools/nutrition_tools.py`: load CSV một lần/process, `lookup_food(name)`
      dùng chuẩn hóa bỏ dấu + fuzzy match; trả `match_confidence`; không match → `None` (không đoán).
      *Thứ tự: khớp đúng (1.0) → khớp không dấu chỉ khi người dùng gõ không dấu (0.95; gõ "bò" có dấu không
      bao giờ rơi thành "bơ") → tên món nằm trong câu, tên một từ chỉ tính khi là từ đầu ("nước mắm" không
      thành "nước") (0.8) → fuzzy ≥ 0.85. Tên trùng giữa hai món làm load lỗi ngay.*
- [x] Bảng quy đổi đơn vị dân gian: bát, chén, đĩa, tô, cái, ổ, ly, cốc, muỗng → gram theo từng món.
      *`vn_food_units.csv` theo từng món + đồng nghĩa (chén=bát, cốc=ly, thìa=muỗng, trái=quả, chiếc=cái,
      suất=phần) + đơn vị khối lượng (g, kg, lạng, ml, lít). Đơn vị lạ với món đó → dùng khẩu phần mặc định
      và đánh dấu `portion_estimated`.*
- **AC:** test lookup cho ≥30 món mẫu (có/không dấu, viết tắt); món không có trong bảng trả `None`.
  *`tests/test_models/test_nutrition_tools.py` (38 món có dấu, 7 không dấu, 3 viết tắt, 9 trường hợp `None`).*

### 1.3 Meal analysis graph
- [x] Graph riêng `build_meal_analysis_graph()` (cùng kiểu `build_reflect_graph()` trong
      `codebase/models/agents/graph.py`, không checkpointer), các node trong
      `codebase/models/agents/nodes/meal/`:
  1. `parse_meal_items` — LLM structured output: tách `description` + `portion` thành
     `[{name, quantity, unit}]`. **Không** sinh số dinh dưỡng.
  2. `lookup_nutrition` — deterministic, gọi `nutrition_tools`.
  3. `aggregate` — tính tổng, `carb_load` theo ngưỡng cấu hình (đặt trong config, có nguồn trích).
  4. `persist_analysis` — ghi `meal_analyses` với `status=draft`.
  *`parse_meal_items` tách bằng luật trước (`nodes/meal/parsing.py`); chỉ gọi LLM khi còn tên món bảng tra
  không biết; LLM lỗi → giữ kết quả luật. `carb_load`: < 30 g thấp, 30–<60 vừa, ≥ 60 cao
  (`MEAL_CARB_LOW_MAX_G`/`MEAL_CARB_HIGH_MIN_G`, theo khoảng 45–60 g/bữa trong giáo dục đếm carb của ADA —
  **chờ bác sĩ duyệt**); có món không tra được → `unknown`, trừ khi phần đã biết đã ≥ ngưỡng cao.*
- [x] Trigger: chạy nền sau `POST /health/meals` (FastAPI `BackgroundTasks`) và sau `apply_action`
      tạo bữa ăn trong `record_actions.py`; lỗi LLM không làm hỏng việc lưu bữa ăn.
      *Trigger chat nằm ở `agent/router.py::handle_record_action` (cùng chỗ refresh summary).*
- [x] Endpoint: `GET /health/meals/{id}/analysis`, `POST /health/meals/{id}/analysis/rerun`,
      `PATCH /health/meals/{id}/analysis` (bệnh nhân sửa món/khẩu phần → `status=edited`, tính lại).
      *Thêm `POST .../analysis/confirm` và `analysis` đi kèm mỗi bữa trong `GET /health/meals`. PATCH không gọi
      LLM; rerun dùng chung rate limit `/agent/*`.*
- **AC:** "1 bát phở bò, 1 ly trà đá" → 2 item, phở có carb từ bảng tra, trà đá ~0; món lạ →
  item có `carbs_g=null`, `carb_load` không bị tính sai; test với `mock_llm`.
  *`tests/test_backend/test_meal_analysis.py` (20 case, kể cả LLM bịa số dinh dưỡng bị bỏ qua).*

### 1.4 Tương quan bữa ăn ↔ đường huyết
- [x] `codebase/backend/health/meal_response.py`: ghép mỗi bữa với glucose `AFTER_MEAL` trong cửa sổ
      60–180 phút (cấu hình được) và glucose trước ăn gần nhất (≤60 phút trước, nếu có) → `delta`.
      *Nhiều lần đo sau ăn → lấy lần gần mốc 2 giờ nhất; "trước ăn" = `BEFORE_MEAL` hoặc `FASTING`.*
- [x] Gộp theo món (`matched_food_id`) và theo `carb_load`: số lần, glucose sau ăn trung bình, % vượt
      180 mg/dL (`POST_PRANDIAL_TARGET_MG_DL` trong `metrics_tools.py`). **Chỉ hiển thị khi n ≥ 3.**
      *`GET /health/meals/glucose-response?days=30` (7–90).*
- [x] Tool agent `get_meal_glucose_response(patient_id, days)`; thêm vào `fetch_data` cho intent liên
      quan dinh dưỡng và vào `synthesize_summary` (nhánh bác sĩ: mục "Bữa ăn liên quan tăng đường huyết").
      *`tools/meal_tools.py`; QA chỉ nhận khối này khi câu hỏi có từ khóa ăn uống (luật tất định), SUMMARY
      luôn nhận. Mỗi bữa trong `records` có thêm `carb_estimate` để agent trả lời "bữa trưa bao nhiêu carb".*
- [x] Ngôn ngữ mô tả tương quan, không khẳng định nhân quả ("thường đi kèm", không phải "gây ra").
- **AC:** dữ liệu seed có pattern rõ → món đó xuất hiện top; n < 3 không hiển thị; guardrail chặn câu
  khẳng định nhân quả/khuyên bỏ thuốc.
  *`tests/test_backend/test_meal_response.py` (18 case); guardrail ở 1.8.*

### 1.5 Giao diện
- [x] Bệnh nhân (`meals-page.tsx`): card phân tích dưới mỗi bữa — danh sách món, tổng carb, badge
      carb_load/GI, mức tin cậy, nút "Sửa" và "Xác nhận"; nhãn rõ "ước tính, không thay thế chuyên gia dinh dưỡng".
      *`components/meal-analysis-card.tsx` (thêm "Phân tích lại"). Mức tin cậy hiện dạng chữ: "bạn ghi: …" khi
      tên được chuẩn hóa, "khẩu phần theo mặc định", "chưa có trong bảng tra". **Bệnh nhân chỉ thấy carb, không
      thấy kcal** (tránh ăn kiêng cực đoan — xem câu hỏi mở); bác sĩ thấy cả kcal.*
- [x] Bệnh nhân: mục "Món ảnh hưởng đường huyết của bạn" (từ 1.4).
      *`components/meal-glucose-response.tsx`, dùng chung với bác sĩ.*
- [x] Bác sĩ (`patient-detail-page.tsx`): tab/section bữa ăn + bảng tương quan; đưa vào bản in báo cáo.
      *Tab "Bữa ăn" (`components/patient-meals-panel.tsx`) + mục "Bữa ăn liên quan tăng đường huyết" trong
      báo cáo in.*
- **AC:** không dùng chỉ màu để biểu thị mức (PRD NFR); hiển thị tốt ở 360px.
  *Mỗi mức carb có chữ + icon riêng; layout `flex-wrap`, không bảng ngang trên mobile. **Chưa kiểm trên
  trình duyệt thật** — typecheck/ESLint sạch.*

### 1.6 Nhận diện món từ ảnh (làm cùng 1.1 — ảnh không được lưu)
- [x] Node `recognize_from_photo` dùng vision model (`gpt-4o-mini` hỗ trợ ảnh) → danh sách món ứng viên
      → đi tiếp `lookup_nutrition`; **luôn là draft**, bệnh nhân phải xác nhận.
      *`codebase/models/agents/nodes/meal/recognize_from_photo.py` — hàm async thuần (không đọc HTTP/chat
      state) để agent đa chức năng dùng lại làm tool/node. LLM chỉ trả `[{name, quantity, unit}]`; code
      kiểm từng món, món sai dạng bị loại chứ không sửa, số dinh dưỡng từ model bị bỏ. Ảnh gửi `detail=low`,
      không log, không gửi Langfuse (`get_llm(trace=False)`). Endpoint `POST /health/meals/photo-analysis`
      (chỉ bệnh nhân, dùng chung rate limit `/agent/*`) trả draft, **không lưu gì** — bữa ăn chỉ được tạo
      khi bệnh nhân bấm lưu form (`input_source=photo`). Nối `lookup_nutrition` khi có 1.2/1.3.
      Đã gọi thật qua proxy hiện tại với ảnh không phải đồ ăn: 2.4s, `is_food=false` đúng định dạng;
      chưa đo độ chính xác nhận diện món Việt (cần bộ ảnh — xem 1.9).*
- [x] Consent: ảnh gửi sang OpenAI — cần câu đồng ý rõ ràng lần đầu dùng; tắt được bằng config.
      *`User.meal_photo_ai_consent` (bệnh nhân tự bật/tắt qua `PATCH /auth/me`, hỏi ở lần chọn ảnh đầu tiên,
      có nút tắt trên trang bữa ăn); `MEAL_PHOTO_ANALYSIS_ENABLED=false` tắt cho mọi người (503).*
- **AC:** ảnh không phải đồ ăn → trả "không nhận diện được", không tạo item.
  *Test: `tests/test_backend/test_meal_photo.py`, `tests/test_models/test_meal_photo_recognition.py`.*

### 1.7 Ghi bữa ăn bằng giọng nói — **hoãn sau pilot (quyết định 2026-10-29)**
*Lý do: `codebase/stt-service` cần máy GPU NVIDIA riêng (~16 GB VRAM, `docker-compose.stt.yml`), không chạy cùng
máy backend — chi phí hạ tầng không tương xứng cho pilot. Production không cấu hình `NEXT_PUBLIC_STT_WS_URL` nên
nút nói tự ẩn (`voice-input-button.tsx`); nhập liệu giọng nói nói chung không có trên production. Nơi có STT,
câu nói đã đi vào chat như văn bản gõ và agent ghi bữa ăn qua `draft_records` (Epic 8), nên phần còn lại của
task chỉ là kiểm parser với văn nói + gắn `MealInputSource.VOICE`. Hướng sau pilot không cần GPU: API chuyển
giọng nói của nhà cung cấp LLM — gửi giọng bệnh nhân ra ngoài nên cần consent như ảnh (1.6).*
- [ ] Nối STT (`codebase/stt-service`) → text → parser bữa ăn trong `record_actions.py` → confirm →
      trigger 1.3. Chủ yếu là việc frontend + kiểm tra parser với văn nói.
      *Đã sẵn: bữa ăn xác nhận từ chat đã kích hoạt phân tích (1.3); `MealInputSource.VOICE` đã có.*
- **AC:** "trưa nay tôi ăn một đĩa cơm tấm sườn" tạo draft bữa trưa đúng giờ, có phân tích.

### 1.8 An toàn
- [x] Bổ sung pattern vào `guardrail.py`: chặn gợi ý liều insulin/thuốc theo lượng carb, chặn "bỏ bữa
      để hạ đường", chặn chẩn đoán từ bữa ăn.
      *Kiểm theo từng câu, câu có phủ định ("không nên bỏ bữa…", "đừng tự ý ngừng thuốc") được cho qua.
      Lỗi mới: `carb_based_dose_advice`, `meal_skipping_advice`, `medication_stop_advice`; khẳng định nhân quả
      cá nhân ("phở là nguyên nhân làm đường huyết của bạn tăng") vào `causal_overreach`; thêm 7 cụm chẩn đoán
      (kháng insulin, tiền đái tháo đường…).*
- [x] Prompt của `parse_meal_items` không nhận chỉ dẫn từ nội dung bữa ăn (đã có case SEC-004
      indirect injection qua ghi chú bữa ăn trong `data/testset/`).
      *Prompt coi nội dung bữa ăn là dữ liệu; và dù model làm theo, mọi số dinh dưỡng đều từ bảng tra (test
      `test_llm_nutrition_numbers_are_ignored`).*
- **AC:** thêm ≥8 case vào eval guardrail; 100% bị chặn.
  *`eval/data/guardrail_meal_cases.json`: 10 case phải chặn + 6 case an toàn đối chứng, dùng chung cho
  `tests/test_backend/test_meal_guardrails.py` (16/16) và `eval/respond_agent/guardrail_judge.py`.*

### 1.9 Eval — **phần văn bản xong 2026-10-29; đối chiếu bảng TPTP và bộ ảnh còn mở**
- [x] `eval/meal_analysis/`: golden ≥50 mô tả bữa ăn tiếng Việt (có viết tắt, không dấu, nhiều món) với
      carb kỳ vọng. Metric: độ chính xác tách món, **MAE carb** (mục tiêu đề xuất ≤15 g/bữa — chốt lại
      với bác sĩ), tỷ lệ "không match" đúng.
      *`eval/data/meal_analysis_cases.json` (62 case: 53 có dấu, 6 không dấu, 4 viết tắt, 24 nhiều món, 5 văn
      nói, 3 đơn vị khối lượng, 2 khẩu phần ở ô riêng, 7 món không có trong bảng) + `eval/meal_analysis/text_analysis.py`.
      Nhãn = món + lượng bệnh nhân nói; carb kỳ vọng tính từ nhãn **qua chính bảng tra** → đo lỗi tách món/khớp
      tên/khẩu phần, **không** đo độ đúng số liệu của bảng (việc đó là mục đối chiếu TPTP bên dưới).
      Lần chạy 2026-10-29 (`eval/results/meal_analysis_text-20261029-1013.json`, `gpt-4o-mini` qua proxy):
      luồng production đúng 61/62 case; recall/precision món 0.988/0.988; khẩu phần đúng 100%; 7/7 món lạ
      để trống carb, 0 khớp nhầm; **MAE carb 0.05 g/bữa** (55 case đủ món trong bảng, sai lớn nhất 2.7 g); carb_load
      khớp 100%; LLM chỉ được gọi ở 12.9% case, p95 2.1 s. Chỉ dùng luật (LLM tắt): 60/62, MAE 1.6 g, sai lớn
      nhất 85.8 g ("tối qua ăn 2 chén cơm với…" — câu văn nói, luật không tách được) → bước LLM có ích đúng chỗ.
      Lỗi tìm thấy: "bm trứng" khớp thành "bánh mì không" (alias `bm` + luật "từ đầu câu"), không gọi LLM vì
      tên đã khớp → đã thêm alias `bm trứng/bm ốp la/bm thịt/bm pate/bm chả` (+ test). Chạy lại sau khi sửa
      (`eval/results/meal_analysis_text-20261029-1044.json`): luồng production **62/62**, recall/precision 1.00. **Giới hạn:** bộ case do agent viết khi đã thấy bảng tra, nên lệch về các tên
      có trong bảng; câu thật của bệnh nhân sẽ khó hơn — nên bổ sung câu thật (ẩn danh) khi có pilot.*
- [x] Target `make eval-meal-analysis` trong `Makefile`.
- [ ] Đối chiếu carb/kcal trong `vn_foods.csv` với *Bảng thành phần thực phẩm Việt Nam* (Viện Dinh dưỡng),
      điền `source`/`source_page`, bỏ hậu tố `-unverified` khỏi `NUTRITION_DB_VERSION`.
      *Chưa làm (2026-10-29): cần bản Bảng TPTP VN (sách/PDF của Viện Dinh dưỡng) để tra từng dòng và ghi số
      trang — không điền số từ trí nhớ/nguồn thứ cấp. Đến khi xong, UI giữ nhãn "ước tính" và khóa luận ghi số
      carb là ước tính chưa đối chiếu.*
- [ ] Eval nhận diện món từ ảnh (`recognize_from_photo`, 1.6): bộ ảnh món Việt
      tự chụp/có giấy phép (không dùng ảnh bệnh nhân — hệ thống không lưu ảnh), nhãn
      `[{name, quantity, unit}]` kỳ vọng + vài ảnh không phải đồ ăn. Metric: precision/recall tên món,
      tỷ lệ khẩu phần đúng đơn vị, tỷ lệ ảnh không phải đồ ăn bị trả `is_food=false`, latency p50/p95 qua proxy.
      Target `make eval-meal-photo`.
      *Script + target đã có (`eval/meal_analysis/photo.py`, `make eval-meal-photo`); **chưa có bộ ảnh** — cần
      ảnh tự chụp/có giấy phép đặt ở `eval/data/meal_photos/` + `labels.json` (định dạng trong docstring).
      Chưa chạy, chưa có số.*

## Definition of Done
- Toàn bộ task 1.1–1.5, 1.8, 1.9 xong; 1.7 hoãn sau pilot (1.6 đã làm cùng 1.1).
- Pytest xanh, eval meal đạt ngưỡng đã chốt, guardrail case mới 100%.
- README/ARCHITECTURE cập nhật luồng phân tích bữa ăn.

## Câu hỏi mở
- Ngưỡng `carb_load` low/medium/high theo nguồn nào (VADE/ADA)? — cần bác sĩ duyệt.
  *Tạm dùng < 30 / 30–<60 / ≥ 60 g mỗi bữa (theo khoảng 45–60 g/bữa của ADA), chỉnh bằng config.*
- Có cho bệnh nhân thấy số kcal không, hay chỉ carb? (tránh hành vi ăn kiêng cực đoan ở người già)
  *Tạm thời: bệnh nhân chỉ thấy carb; kcal vẫn lưu và bác sĩ thấy.*
- ~~Lưu ảnh bao lâu?~~ — không lưu ảnh (quyết định 2026-10-27, xem 1.1).
