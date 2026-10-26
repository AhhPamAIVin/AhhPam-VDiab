# Trạng thái evaluation — VDiab

File này thay thế báo cáo "100% passed" chỉ dựa trên dataset trước đây. Việc
một case có mặt trong dataset là kiểm tra tính toàn vẹn (integrity), không
phải bằng chứng rằng ứng dụng đã chạy qua và pass.

Bằng chứng có thể tái tạo hiện tại nằm ở:

- [`latest.json`](latest.json): kết quả kiểm tra deterministic trên production code;
- [`pytest-summary.json`](pytest-summary.json): metadata kết quả test;
- [`coverage.xml`](coverage.xml): coverage line/branch dạng máy đọc được;
- [`../../docs/evaluation/EVALUATION_EVIDENCE.md`](../../docs/evaluation/EVALUATION_EVIDENCE.md): báo cáo tổng hợp (sinh lại bằng `make eval-report`).

Đo lại 2026-10-27, sau Epic 5 (đo đạc theo node, chạy song song, self-reported citation thay
`verify_citations`, fast-path planner, streaming, sửa nhãn intent + cổng HIGH_RISK, runner AI Summary;
chi tiết `docs/tasks/05-ai-performance-quality.md`). Bảng trong README dùng đúng các số này:

- deterministic intents **37/37** (trước 30/37): 7 nhãn golden `OUT_OF_SCOPE` sửa thành `HIGH_RISK`/`JAILBREAK`
  theo thiết kế, lý do ghi ở `intent_label_note` từng case; thêm cổng tất định cho 3 câu đổi liều trước đây lọt vào LLM.
  `eval/baseline.json` nâng lên 37;
- golden scenarios (`golden_scenarios_1793048049.json`, chấm cục bộ `--local`): intent **1.000**, citation **0.867**,
  key-facts **0.733**, refusal-pattern 0.500, forbidden-content 1.000, safety-disclaimer 0.833. Hai lần chạy khác
  trên gần cùng code: citation 0.933/0.933, key-facts 0.680/0.668 — LLM temperature 0.7 + LLM-judge nên dao động
  ±0.05. Citation được chấm theo "tiêu đề | đơn vị ban hành" (như eval retrieval); chấm lại lần chạy này bằng bộ
  chấm cũ chỉ so tiêu đề được 0.667 (10/15), bằng baseline — phần tăng đến từ việc chấm đúng theo đơn vị ban hành,
  còn việc bỏ `verify_citations` không làm giảm citation;
- trajectory **10/10** (`trajectory_local_20261027.json`), so khớp thứ tự node có tính đến nhánh chạy song song;
- agent E2E qua HTTP (`agent-e2e.json`): **30/38**; latency p50 5.9/6.7 s, **p95 10.3/11.8 s** (2 lần chạy; trước
  Epic 5: p95 14.1/17.2 s) — vẫn trên mục tiêu ≤ 10 s, phân tích theo node ở `docs/PERFORMANCE_REPORT.md`. 8 case fail
  đều vì thiếu nguồn bắt buộc (`required_sources`);
- AI Summary golden (`ai_summary_golden.json`, runner mới): **39/46 check PASS**; case: 2 PASS (TC-SUM-007 phân quyền,
  TC-SUM-008 không chẩn đoán/kê đơn), 3 FAIL, 2 NOT_SUPPORTED, 1 SKIPPED. FAIL thật: tóm tắt thiếu dữ liệu không có câu
  chuyển bác sĩ (TC-SUM-002); reflect với LLM thật không nêu ngưỡng 180 mg/dL cho mẫu cuối tuần (TC-SUM-005) và không
  đề xuất insight hạ đường huyết ở tháng 2 (TC-SUM-006) — hạ tầng create → update insight chạy đúng, kể cả trên
  Qdrant server. NOT_SUPPORTED: % tuân thủ thuốc (Epic 3), HbA1c trong tóm tắt. SKIPPED: TC-SUM-004 (sắp xếp ưu tiên
  nằm ở frontend);
- runner LangSmith cũ trả 403 — golden/trajectory chạy bằng `--local` (mặc định trong `make eval-*`).

Đo 2026-10-26, sau khi chuyển RAG sang Qdrant + BAAI/bge-m3 + chunking v2:

- 603 test pass, 0 fail;
- F2 rule engine giờ chạy luồng ghi thật (`create_glucose_record`: quy đổi đơn vị, đánh giá ngưỡng, tạo cảnh báo) trên DB in-memory: **12/15**. 3 case fail (TC-RULE-008/012/014) cần mục tiêu theo bối cảnh bữa ăn mà care plan hiện chưa có. Eval này cũng phát hiện lỗi chỉ số mmol/L bị so như mg/dL (báo động NGUY HIỂM giả), đã sửa;
- retrieval recall 53 fact card (k=6): exact-hit **0.830**, trước đó 0.679 với Chroma + OpenAI embedding (cùng bộ câu hỏi) — `retrieval_recall_bge-m3.json`;
- retrieval tier 2, 24 câu hỏi trên tài liệu dài (nhãn theo file nguồn + cụm bằng chứng, không phụ thuộc chunk_id): hit@1 0.375, hit@3 0.917, hit@10 1.000, MRR 0.642;
- retrieval theo nguồn, 15 kịch bản AI: case-hit 0.733, source-recall 0.857, MRR 0.787 (trước đó bộ chấm chỉ so tiêu đề nên tính nhầm các bài đúng nguồn VADE / BV Nội tiết TW là trượt);
- các mục còn lại bên dưới chưa đo lại (golden scenarios, guardrail judge, trajectory, faithfulness, coverage, E2E).

Baseline xác minh trước đó (2026-08-27, sau khi refactor `plan_response` — xem
`docs/AI_Eval_Metrics_Plan.md` §2.4 để biết toàn bộ kết quả chạy lại và những
gì đã thay đổi; §2.4 cuối và §2.5 ghi lại 3 việc làm thêm cùng ngày: fix
regression personal-data faithfulness, chạy `run_agent_e2e.py` lần đầu để có
số liệu p95 thật, và fix một assertion chết trong chính script đó):

- 462 test pass, 0 fail;
- 90.2% line coverage, 73.2% branch coverage;
- validation schema: 9/9 case thực thi pass, 6 case cần mapping API/sản phẩm khác;
- deterministic intents: 30/37 pass so với golden label hiện tại (tăng từ 26/37 trước khi refactor);
- F2 rule engine cảnh báo production: chưa triển khai (15 case không được tính pass);
- retrieval recall (`eval/respond_agent/retrieval_recall.py`): exact-hit rate 0.679, Ragas context-recall 0.679;
- guardrail so với LLM judge độc lập (`eval/respond_agent/guardrail_judge.py`): 1.000 guardrail-vs-expected, 0.812 judge-vs-expected;
- agent trajectory/routing (`eval/respond_agent/trajectory.py`): 10/10 (1.000);
- golden scenarios (`eval/respond_agent/golden_scenarios.py`, 30 case chạy graph thật): intent match 0.767, citation requirement met 0.467, key-facts coverage 0.686 — xem §2.4 để biết vì sao các số này giảm so với lần chạy trước khi refactor và vì sao đây không phải lỗ hổng an toàn mới;
- personal-data faithfulness (`eval/respond_agent/personal_data_faithfulness.py`): **4/4 — regression đã fix** (root cause: `plan_response.py` phân loại sai câu hỏi "bác sĩ điều trị của tôi là ai" thành cần tra cứu kiến thức thay vì dữ liệu cá nhân; đã sửa prompt rule 4a, xem §2.4);
- agent E2E qua HTTP thật (`eval/run_agent_e2e.py`, chạy lần đầu 2026-08-27 nhắm vào backend dev cục bộ + token thật): 25/38 pass — 13 fail còn lại đều là citation lệch nguồn đã biết (§2.2 finding #3), không phải nội dung sai/lỗi HTTP; **p95 latency 13.6–16.1s, avg ~6s trên 2 lần chạy — vượt mục tiêu PRD "p95 phản hồi AI ≤ 10 giây"**, xem §2.5;
- WATCH classifier gate (`eval/watch_classifier/`): 46/46 pytest + 28/28 threshold check pass;
- RAGAS runner độc lập (`eval/run_ragas.py`): chưa chạy — cần server/token đang chạy mà môi trường này không có;
- AI Summary / F4 doctor-prioritization golden dataset (`eval/data/ai_summary_cases.json`, 8 case): **có schema, chưa có runner thực thi qua code thật** — cùng dạng gap "dataset-only, chưa được tính là bằng chứng" như F2 rule engine ở trên;
- clinician review: chưa chạy, cần người review thật.

Chạy `python eval/generate_evidence.py` sau khi có artifact mới thay vì tự sửa
điểm số bằng tay — script này giờ cũng đọc kết quả từ `eval/respond_agent/` và
`eval/watch_classifier/` (mục §3.1/§3.2 trong `EVALUATION_EVIDENCE.md`).
