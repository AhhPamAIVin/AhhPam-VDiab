# VDiab — Golden Dataset & Evaluation Framework

Bộ dữ liệu chuẩn (**Golden Dataset**) được xây dựng bám sát theo **PRD v0.4** và **AI Summary & Respond System Design Doc**, phục vụ kiểm thử tự động, đánh giá chất lượng và kiểm định an toàn y khoa trước khi pilot.

---

## 1. Ma trận độ phủ (Coverage Matrix)

Tổng cộng **76 test cases** bao phủ toàn diện 4 trụ cột tính năng chính của hệ thống VDiab:

| Nhóm kịch bản | File dữ liệu | Số ca | Tiêu chí PRD / Thiết kế | Trọng tâm kiểm thử |
|---|---|:---:|---|---|
| **1. AI Consultation Benchmark** | [`data/ai_scenarios_30.json`](file:///d:/Minh/Coding/AI/VinUni/Build%20Phase/P-158/eval/data/ai_scenarios_30.json) | **30** | PRD §7 (Bộ 30 tình huống chuẩn) | • 10 ca Đủ nguồn (Citing QĐ 5481, Dược thư 2022, ADA 2026)<br>• 5 ca Thiếu nguồn (Từ chối mẹo dân gian, thuốc nam)<br>• 5 ca Cấp cứu (Hạ ĐH nặng, DKA, nhồi máu cơ tim, đột quỵ)<br>• 5 ca Yêu cầu đổi điều trị (Chặn kê đơn, chỉnh liều)<br>• 5 ca Mâu thuẫn nguồn (BYT vs ADA, xử trí Acarbose) |
| **2. Guardrails & Jailbreak Defense** | [`data/ai_scenarios_30.json`](file:///d:/Minh/Coding/AI/VinUni/Build%20Phase/P-158/eval/data/ai_scenarios_30.json) | **8** | System Design §6, §11 | Prompt Injection, Roleplay Dr. Alex, Translation Bypass, Indirect Injection trong ghi chú món ăn, Base64/Suffix attack |
| **3. Rule Engine & Alerting** | [`data/rule_engine_cases.json`](file:///d:/Minh/Coding/AI/VinUni/Build%20Phase/P-158/eval/data/rule_engine_cases.json) | **15** | PRD §6 (F2), QĐ 5481 (Bảng 4, Bảng 5) | Phân loại Normal/Attention/Danger; Quy đổi mg/dL ↔ mmol/L; Ngưỡng cá nhân hóa người cao tuổi; Độc lập hoàn toàn với LLM (Zero-downtime) |
| **4. Input Validation & Data Quality** | [`data/data_validation_cases.json`](file:///d:/Minh/Coding/AI/VinUni/Build%20Phase/P-158/eval/data/data_validation_cases.json) | **15** | PRD §6 (F1 AC) | Chặn thời gian tương lai, chặn giá trị sinh lý phi thực tế (<0, >1000 mg/dL), phát hiện trùng lặp, tách biệt HbA1c, chống sửa tên/liều thuốc |
| **5. AI Summary & Reflection Memory** | [`data/ai_summary_cases.json`](file:///d:/Minh/Coding/AI/VinUni/Build%20Phase/P-158/eval/data/ai_summary_cases.json) | **8** | PRD §6 (F4), System Design §7, §10 | Tóm tắt 7/30 ngày không suy diễn quan hệ nhân quả sai lệch; Xử lý thiếu dữ liệu; Reflection insight (EVAL-REFLECT-001/002); Doctor triage ≤ 2 phút |

---

## 2. Cấu trúc thư mục

```
eval/
├── golden_dataset.json            # Master manifest kết nối toàn bộ datasets
├── run_eval.py                   # Runner tự động kiểm tra tính toàn vẹn & chạy benchmark
├── generate_evidence.py          # Sinh docs/evaluation/EVALUATION_EVIDENCE.md từ results/
├── run_test_evidence.py          # Chạy pytest+coverage, ghi results/pytest-summary.json
├── README.md                     # Tài liệu hướng dẫn & ma trận kiểm thử
├── data/
│   ├── ai_scenarios_30.json      # 38 kịch bản AI Consultation & Bảo mật
│   ├── rule_engine_cases.json    # 15 kịch bản phân loại ngưỡng Rule Engine
│   ├── data_validation_cases.json# 15 kịch bản xác thực dữ liệu đầu vào
│   └── ai_summary_cases.json     # 8 kịch bản Tóm tắt, Phân luồng & Bộ nhớ Insight
├── respond_agent/                # chất lượng RAG/agent (gọi LLM thật); --local = chấm cục bộ, không cần LangSmith
│   ├── retrieval_recall.py       # Retrieval recall của RAG (exact-hit@k + Ragas context-recall)
│   ├── guardrail_judge.py        # So sánh guardrail.py với một LLM judge an toàn độc lập
│   ├── trajectory.py             # Đánh giá routing/trajectory của agent
│   ├── golden_scenarios.py       # 30 kịch bản trong data/ai_scenarios_30.json, chạy qua graph thật
│   ├── ai_summary_golden.py      # 8 case data/ai_summary_cases.json qua summary graph, reflect(), cổng phân quyền
│   └── personal_data_faithfulness.py  # LLM có báo cáo đúng dữ liệu thật của bệnh nhân seed hay không
├── watch_classifier/              # CI gate cho classifier WATCH cá nhân hóa (pytest/sklearn thuần)
│   └── gate.py
└── results/
    └── report.md                 # Báo cáo đánh giá tổng hợp
```

Xem thêm [`../docs/AI_Eval_Metrics_Plan.md`](../docs/AI_Eval_Metrics_Plan.md) cho phần
`respond_agent/`/`watch_classifier/` — thiết kế, kết quả thật, và các finding đã phát hiện
(bao gồm một regression sau khi refactor `plan_response`, §2.4).

---

## 3. Hướng dẫn chạy kiểm thử

### 3.1. Chạy đánh giá toàn bộ Golden Dataset
```bash
python eval/run_eval.py
```

The runner now keeps dataset integrity separate from executable evaluation.
For release readiness, use:

```bash
python eval/run_eval.py --strict --output eval/results/latest.json
```

Slow retrieval, real-agent, RAGAS, coverage, traceability, and clinician-review
instructions are documented in [`../docs/evaluation/README.md`](../docs/evaluation/README.md).

Các runner gọi LLM thật (xem `Makefile`, mục `eval-*`) mặc định chấm cục bộ (`--local`) và ghi JSON
vào `eval/results/`. `make eval-ai-summary-golden` ghi mỗi kỳ vọng của `ai_summary_cases.json` là PASS,
FAIL hoặc NOT_SUPPORTED (sản phẩm chưa có tính năng tạo ra output đó — không bao giờ tính là pass);
TC-SUM-004 (thứ tự ưu tiên trên dashboard bác sĩ) là SKIPPED vì logic nằm ở frontend.

### 3.2. Chạy cùng hệ thống Backend Unit & Integration Tests
```bash
pytest tests/ -v
```

---

## 4. Mục tiêu & Tiêu chuẩn nghiệm thu (PRD Metrics Target)

1. **F1 Data Completeness:** $\ge 80\%$ bản ghi đo theo kế hoạch có đầy đủ giá trị, đơn vị, thời gian, ngữ cảnh.
2. **F2 Alert Accuracy:** $100\%$ giá trị đạt ngưỡng được Rule Engine phát hiện chính xác, không bỏ sót ca nguy hiểm.
3. **F3 AI Grounding & Citation:** $\ge 90\%$ nội dung tư vấn được bác sĩ duyệt phù hợp, $100\%$ khẳng định y khoa có trích dẫn tài liệu hợp lệ.
4. **F4 Medical Safety Boundary:** $100\%$ câu hỏi yêu cầu đổi thuốc/liều hoặc tình huống cấp cứu được xử lý đúng luồng (chặn hoặc chuyển cấp cứu).
5. **F5 Doctor Triage:** Bác sĩ xác định bệnh nhân cần ưu tiên trong $\le 2\text{ phút}$.
