# Performance report

> Trạng thái: chưa có benchmark runtime đủ điều kiện. Không suy p95/p99 từ unit test hoặc lý thuyết scalability.

| Workload | Dataset/users | Requests | p50 | p95 | p99 | Error rate | Status |
|---|---:|---:|---:|---:|---:|---:|---|
| Health/auth/CRUD | Chưa đo | — | — | — | — | — | Unverified |
| Doctor dashboard | Chưa đo | — | — | — | — | — | Unverified |
| AI Consultation | Chưa đo | — | — | — | — | — | Unverified; tách provider latency |
| AI Summary | Chưa đo | — | — | — | — | — | Unverified; tách retrieval/LLM |

## Protocol tối thiểu

Ghi CPU/RAM/OS, Python, database topology, commit, dataset size, concurrency và warm-up. Chạy workload lặp lại; xuất raw result; đo API thường tách khỏi AI, và AI tách DB/retrieval/provider nếu có trace. Báo cả kết quả xấu, timeout và retry. Capacity chỉ được công bố trong phạm vi workload đã đo.

Target đề xuất để thảo luận, chưa phải SLO đã cam kết: CRUD p95 ≤1s; dashboard p95 ≤2s; AI p95 ≤10s; error rate <1% trong workload pilot. Nếu AI p95 >10s, ghi actual, nguyên nhân dự kiến, hành động và tiêu chí rerun; không loại bỏ sample chậm.
