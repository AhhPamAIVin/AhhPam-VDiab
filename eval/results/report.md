# Trạng thái evaluation — VDiab

File này thay thế báo cáo "100% passed" chỉ dựa trên dataset trước đây. Việc
một case có mặt trong dataset là kiểm tra tính toàn vẹn (integrity), không
phải bằng chứng rằng ứng dụng đã chạy qua và pass.

Bằng chứng có thể tái tạo hiện tại nằm ở:

- [`latest.json`](latest.json): kết quả kiểm tra deterministic trên production code;
- [`pytest-summary.json`](pytest-summary.json): metadata kết quả test;
- [`coverage.xml`](coverage.xml): coverage line/branch dạng máy đọc được;
- [`../../docs/evaluation/EVALUATION_EVIDENCE.md`](../../docs/evaluation/EVALUATION_EVIDENCE.md): báo cáo tổng hợp (sinh lại bằng `make eval-report`).

Baseline xác minh gần nhất (2026-08-27, sau khi refactor `plan_response` — xem
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
