# Quality report

## Latest verified local run

- Date: 2026-09-02 (Asia/Ho_Chi_Minh)
- Commit: `20f147edd40c06530c0fbfc0cbf851be6174c831`
- Worktree note: có thay đổi sẵn ở `codebase/frontend/next-env.d.ts`; kết quả không phải clean-checkout release evidence.
- Python: 3.14.5 (CI target: 3.11)
- Node/npm: 22.22.2 / 10.9.7

| Check | Command | Result | Status |
|---|---|---|---|
| Backend tests | `pytest tests/ -q --disable-warnings --tb=short` | 484 passed, 1 failed, 5020 warnings, 184.13s | Fail |
| Failure | same run | `test_non_danger_reading_gets_ml_prediction`: `ModuleNotFoundError: sklearn` loading joblib model | Environment/dependency gap; rerun required |
| Ruff | `ruff check codebase/ tests/` | 2 fixable findings: import order in `f5_engine.py`; unused `AIMessage` in test | Fail |
| Frontend unit | `npm test -- --run` | 28 passed, 1 file, 475ms | Pass |
| Frontend build | `npm run build` | compiled; 16 pages generated | Pass, but Next reported type validation skipped |
| Coverage | not rerun in this update | no new number | Unverified for current commit |
| Type check | not rerun | no new number | Unverified |

## Historical baseline (không thay thế latest result)

README ghi 2026-08-27: 462 passed/0 failed, line coverage 90.2%, branch 73.2%. Đây là historical artifact, không được trình bày như kết quả commit hiện tại nếu chưa chạy lại `make test-coverage` trong Python 3.11 đầy đủ dependency.

## Release gate

Release evidence chỉ đạt khi clean checkout trên Python 3.11 chạy: Ruff pass; pytest 0 failed; coverage artifact gắn commit; frontend test/build pass; type-check status rõ; eval source-of-truth được sinh lại và personal-data faithfulness không mâu thuẫn giữa README/report.
