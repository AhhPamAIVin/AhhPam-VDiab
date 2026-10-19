# Epic 1 — Meal Intelligence: agent phân tích bữa ăn

**Ưu tiên:** P0 · **Độ lớn:** L · **Phụ thuộc:** — · **Trạng thái:** Chưa bắt đầu

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
hiển thị cho bệnh nhân và bác sĩ, nhận diện từ ảnh (draft), ghi bằng giọng nói.

**Out:** gợi ý liều insulin theo carb (PRD Won't), thực đơn kê cho bệnh nhân, đếm calo chính xác
dạng app giảm cân, tích hợp CGM.

## Task

### 1.1 Tầng dữ liệu
- [ ] `MealRecord`: thêm `portion: str | None` (≤100) và `image_path: str | None` — nullable để
      `_add_missing_columns` tự thêm cột.
- [ ] Bảng mới `meal_analyses`: `id`, `meal_id` (FK unique), `patient_id`, `items` (JSON:
      `[{name, matched_food_id, quantity, unit, grams, carbs_g, kcal, gi_level, match_confidence}]`),
      `total_carbs_g`, `total_kcal`, `carb_load` (`low|medium|high|unknown`), `source`
      (`text|photo|voice`), `status` (`draft|confirmed|edited`), `model_version`, `nutrition_db_version`,
      `created_at`, `updated_at`.
- [ ] Upload ảnh: `POST /api/v1/health/meals/{id}/image` (multipart, ≤10MB, chỉ image/*), lưu vào
      volume (vd `data/uploads/meals/<patient_id>/<uuid>.jpg`); `GET` ảnh **có kiểm quyền** (ảnh là PHI,
      không serve static công khai). Thêm volume vào `docker-compose.yml`.
- [ ] Frontend `meals-page.tsx` + `lib/api.ts`: gửi `portion` riêng, upload ảnh sau khi tạo bữa ăn,
      bỏ cơ chế ảnh tạm.
- **AC:** tạo bữa ăn có ảnh → reload trang vẫn thấy ảnh; bệnh nhân A không xem được ảnh của B
  (test 403); DB cũ khởi động lại không lỗi và có cột mới.

### 1.2 Bảng tra dinh dưỡng món Việt
- [ ] `data/nutrition/vn_foods.csv`: `food_id, name_vi, aliases, default_unit, grams_per_unit,
      carbs_per_100g, kcal_per_100g, gi_level, source, source_page`. Nguồn chính: *Bảng thành phần thực
      phẩm Việt Nam* (Viện Dinh dưỡng Quốc gia); món chế biến (phở, cơm tấm, bánh mì, bún bò…) ghi rõ
      nguồn/công thức ước tính. Mục tiêu bản đầu: **150–300 món/thực phẩm phổ biến**.
- [ ] `codebase/models/agents/tools/nutrition_tools.py`: load CSV một lần/process, `lookup_food(name)`
      dùng chuẩn hóa bỏ dấu + fuzzy match; trả `match_confidence`; không match → `None` (không đoán).
- [ ] Bảng quy đổi đơn vị dân gian: bát, chén, đĩa, tô, cái, ổ, ly, cốc, muỗng → gram theo từng món.
- **AC:** test lookup cho ≥30 món mẫu (có/không dấu, viết tắt); món không có trong bảng trả `None`.

### 1.3 Meal analysis graph
- [ ] Graph riêng `build_meal_analysis_graph()` (cùng kiểu `build_reflect_graph()` trong
      `codebase/models/agents/graph.py`, không checkpointer), các node trong
      `codebase/models/agents/nodes/meal/`:
  1. `parse_meal_items` — LLM structured output: tách `description` + `portion` thành
     `[{name, quantity, unit}]`. **Không** sinh số dinh dưỡng.
  2. `lookup_nutrition` — deterministic, gọi `nutrition_tools`.
  3. `aggregate` — tính tổng, `carb_load` theo ngưỡng cấu hình (đặt trong config, có nguồn trích).
  4. `persist_analysis` — ghi `meal_analyses` với `status=draft`.
- [ ] Trigger: chạy nền sau `POST /health/meals` (FastAPI `BackgroundTasks`) và sau `apply_action`
      tạo bữa ăn trong `record_actions.py`; lỗi LLM không làm hỏng việc lưu bữa ăn.
- [ ] Endpoint: `GET /health/meals/{id}/analysis`, `POST /health/meals/{id}/analysis/rerun`,
      `PATCH /health/meals/{id}/analysis` (bệnh nhân sửa món/khẩu phần → `status=edited`, tính lại).
- **AC:** "1 bát phở bò, 1 ly trà đá" → 2 item, phở có carb từ bảng tra, trà đá ~0; món lạ →
  item có `carbs_g=null`, `carb_load` không bị tính sai; test với `mock_llm`.

### 1.4 Tương quan bữa ăn ↔ đường huyết
- [ ] `codebase/backend/health/meal_response.py`: ghép mỗi bữa với glucose `AFTER_MEAL` trong cửa sổ
      60–180 phút (cấu hình được) và glucose trước ăn gần nhất (≤60 phút trước, nếu có) → `delta`.
- [ ] Gộp theo món (`matched_food_id`) và theo `carb_load`: số lần, glucose sau ăn trung bình, % vượt
      180 mg/dL (`POST_PRANDIAL_TARGET_MG_DL` trong `metrics_tools.py`). **Chỉ hiển thị khi n ≥ 3.**
- [ ] Tool agent `get_meal_glucose_response(patient_id, days)`; thêm vào `fetch_data` cho intent liên
      quan dinh dưỡng và vào `synthesize_summary` (nhánh bác sĩ: mục "Bữa ăn liên quan tăng đường huyết").
- [ ] Ngôn ngữ mô tả tương quan, không khẳng định nhân quả ("thường đi kèm", không phải "gây ra").
- **AC:** dữ liệu seed có pattern rõ → món đó xuất hiện top; n < 3 không hiển thị; guardrail chặn câu
  khẳng định nhân quả/khuyên bỏ thuốc.

### 1.5 Giao diện
- [ ] Bệnh nhân (`meals-page.tsx`): card phân tích dưới mỗi bữa — danh sách món, tổng carb, badge
      carb_load/GI, mức tin cậy, nút "Sửa" và "Xác nhận"; nhãn rõ "ước tính, không thay thế chuyên gia dinh dưỡng".
- [ ] Bệnh nhân: mục "Món ảnh hưởng đường huyết của bạn" (từ 1.4).
- [ ] Bác sĩ (`patient-detail-page.tsx`): tab/section bữa ăn + bảng tương quan; đưa vào bản in báo cáo.
- **AC:** không dùng chỉ màu để biểu thị mức (PRD NFR); hiển thị tốt ở 360px.

### 1.6 Nhận diện món từ ảnh (sau 1.1–1.5)
- [ ] Node `recognize_from_photo` dùng vision model (`gpt-4o-mini` hỗ trợ ảnh) → danh sách món ứng viên
      → đi tiếp `lookup_nutrition`; **luôn là draft**, bệnh nhân phải xác nhận.
- [ ] Consent: ảnh gửi sang OpenAI — cần câu đồng ý rõ ràng lần đầu dùng; tắt được bằng config.
- **AC:** ảnh không phải đồ ăn → trả "không nhận diện được", không tạo item.

### 1.7 Ghi bữa ăn bằng giọng nói
- [ ] Nối STT (`codebase/stt-service`) → text → parser bữa ăn trong `record_actions.py` → confirm →
      trigger 1.3. Chủ yếu là việc frontend + kiểm tra parser với văn nói.
- **AC:** "trưa nay tôi ăn một đĩa cơm tấm sườn" tạo draft bữa trưa đúng giờ, có phân tích.

### 1.8 An toàn
- [ ] Bổ sung pattern vào `guardrail.py`: chặn gợi ý liều insulin/thuốc theo lượng carb, chặn "bỏ bữa
      để hạ đường", chặn chẩn đoán từ bữa ăn.
- [ ] Prompt của `parse_meal_items` không nhận chỉ dẫn từ nội dung bữa ăn (đã có case SEC-004
      indirect injection qua ghi chú bữa ăn trong `data/testset/`).
- **AC:** thêm ≥8 case vào eval guardrail; 100% bị chặn.

### 1.9 Eval
- [ ] `eval/meal_analysis/`: golden ≥50 mô tả bữa ăn tiếng Việt (có viết tắt, không dấu, nhiều món) với
      carb kỳ vọng. Metric: độ chính xác tách món, **MAE carb** (mục tiêu đề xuất ≤15 g/bữa — chốt lại
      với bác sĩ), tỷ lệ "không match" đúng.
- [ ] Target `make eval-meal-analysis` trong `Makefile`.

## Definition of Done
- Toàn bộ task 1.1–1.5, 1.8, 1.9 xong; 1.6–1.7 có thể tách release sau.
- Pytest xanh, eval meal đạt ngưỡng đã chốt, guardrail case mới 100%.
- README/ARCHITECTURE cập nhật luồng phân tích bữa ăn.

## Câu hỏi mở
- Ngưỡng `carb_load` low/medium/high theo nguồn nào (VADE/ADA)? — cần bác sĩ duyệt.
- Có cho bệnh nhân thấy số kcal không, hay chỉ carb? (tránh hành vi ăn kiêng cực đoan ở người già)
- Lưu ảnh bao lâu? (liên quan chính sách retention ở Epic 6)
