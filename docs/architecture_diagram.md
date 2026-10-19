# Architecture Diagram — VDiab

Sơ đồ chi tiết cho kiến trúc; phần narrative/component detail đầy đủ ở [../ARCHITECTURE.md](../ARCHITECTURE.md). Nguồn: [PRD.md](PRD.md) **v0.4** — `MVP_Diabetes_Health_Management_System.md` là tài liệu nháp giai đoạn đầu, không còn đồng bộ với PRD hiện tại, không dùng làm nguồn phạm vi.

## 1. System Overview

![System overview diagram](diagrams/system_overview.png)

<details>
<summary>Mermaid source</summary>

```mermaid
flowchart TB
    subgraph Clients["React SPA — 1 app, UI theo vai trò"]
        PatientUI["Patient View"]
        DoctorUI["Doctor View"]
        AdminUI["Admin View"]
    end

    subgraph Backend["FastAPI Backend — modular monolith"]
        Auth["Auth và RBAC (JWT)<br/>Patient / Doctor / Admin"]
        HealthAPI["Health API + Rule Engine<br/>CRUD glucose(SMBG) · HbA1c (riêng) · thuốc · bữa ăn · vận động · triệu chứng<br/>validate + lưu lịch sử sửa<br/>ngưỡng cá nhân hoá → severity"]
        CarePlan["Care Plan Service<br/>mục tiêu/ngưỡng/kế hoạch đo/kế hoạch thuốc — versioned"]
        Appt["Appointment Service<br/>lịch tái khám"]
        Notify["Reminder / Notification<br/>cron + push/in-app"]
        Audit["Audit Log"]
        KB["Knowledge Source Mgmt<br/>Admin quản trị · bác sĩ phụ trách duyệt nguồn"]
        Agent["AI Agent (LangGraph)<br/>Consultation (F3) + Summary (F4) + Guardrail"]
        Tools["Tool Registry"]
    end

    subgraph Data["Data Layer"]
        PG[("PostgreSQL<br/>system of record")]
        Chroma[("ChromaDB<br/>guideline chunks — chỉ nguồn Đã duyệt")]
    end

    subgraph External["External Services"]
        LLM["OpenAI API"]
        LangSmith["LangSmith (AI tracing/log)"]
    end

    Clients -->|HTTPS REST + JWT| Auth
    Auth --> HealthAPI
    Auth --> CarePlan
    Auth --> Appt
    Auth --> Agent

    HealthAPI <--> PG
    HealthAPI -->|"severity: Cần chú ý / Nguy hiểm"| Notify
    CarePlan <--> PG
    Notify <--> PG
    Notify -.->|"push/in-app tới Patient + bác sĩ phụ trách<br/>(resolve qua doctor_patient_assignments — không broadcast)"| Clients
    Appt <--> PG
    Appt -->|nhắc trước lịch tái khám| Notify
    Appt -->|trigger tổng hợp trước tái khám| Agent

    KB <--> PG
    KB -->|ingest chunk khi trạng thái = Đã duyệt| Chroma

    Agent --> Tools
    Tools <--> PG
    Tools --> Chroma
    Agent <--> LLM
    Agent -.->|trace mọi câu hỏi/tool call| LangSmith
    Agent -.-> Clients

    HealthAPI -.->|log truy cập hồ sơ| Audit
    CarePlan -.->|log thay đổi kế hoạch/ngưỡng| Audit
    Agent -.->|log truy vấn AI| Audit
    Audit -.-> PG

    classDef ai fill:#1f6f5c,color:#fff,stroke:#15503f
    class Agent,Tools,LLM,LangSmith ai
```

</details>

## 2. Agent Flow (LangGraph)

Một graph duy nhất dùng chung cho AI Consultation (F3, Patient) và AI Summary (F4, Doctor); guardrail chạy cả đầu vào lẫn đầu ra, không chỉ chặn từ khoá mà còn ép buộc trích dẫn "đủ căn cứ". Câu hỏi có dấu hiệu nguy hiểm y tế cấp tính **không** chỉ bị từ chối — được chuyển sang luồng cảnh báo F2 (xem §4).

![Agent flow diagram](diagrams/agent_flow.png)

<details>
<summary>Mermaid source</summary>

```mermaid
flowchart TD
    START((Start)) --> Parse["receive_input<br/>resolve patient_id + role từ JWT"]
    Parse --> GuardIn{"safety_screen_in<br/>đổi liều/insulin? chẩn đoán? prompt injection?<br/>dấu hiệu nguy hiểm cấp tính?"}
    GuardIn -->|"Đổi liều / chẩn đoán / ngoài phạm vi / injection"| Decline["decline<br/>từ chối lịch sự, giải thích lý do, gợi ý hỏi bác sĩ"]
    GuardIn -->|"Dấu hiệu nguy hiểm cấp tính"| Escalate["escalate_to_alert_flow<br/>hướng dẫn hành động đã duyệt (F2) + gắn cờ dashboard bác sĩ"]
    Decline --> END1((End))
    Escalate --> END1
    GuardIn -->|Không| Plan["plan_tools<br/>LLM chọn tool cần gọi"]
    Plan --> NeedTool{Cần thêm dữ liệu?}
    NeedTool -->|Có| CallTool["call_tool<br/>get_health_records / calculate_health_metrics /<br/>retrieve_guideline (chỉ nguồn Đã duyệt, còn hiệu lực) /<br/>get_patient_profile / get_recent_alerts"]
    CallTool --> Plan
    NeedTool -->|Đủ| Synth["synthesize_response<br/>LLM chỉ dùng dữ liệu Tool trả về + đoạn trích guideline;<br/>nguồn mâu thuẫn → không tự chọn kết luận"]
    Synth --> GuardOut{"safety_screen_out<br/>còn từ khoá liều/chẩn đoán? mỗi khẳng định có<br/>≥1 đoạn nguồn trực tiếp hỗ trợ?"}
    GuardOut -->|Vi phạm / thiếu căn cứ| Decline
    GuardOut -->|Hợp lệ| Respond["respond<br/>trả lời + nguồn trích dẫn + khoảng thời gian dữ liệu<br/>(Summary: có thể tạo ai_review_flag)<br/>log LangSmith/Audit"]
    Respond --> END2((End))

    classDef guard fill:#a13a3a,color:#fff,stroke:#7a2b2b
    classDef ai fill:#1f6f5c,color:#fff,stroke:#15503f
    class GuardIn,GuardOut,Decline,Escalate guard
    class Plan,CallTool,Synth,Respond ai
```

</details>

## 3. Grounding Sequence — AI luôn lấy dữ liệu thật

Nguyên tắc trọng tâm của toàn kiến trúc: **LLM chỉ nhìn thấy dữ liệu do Tool trả về**, không truy vấn DB trực tiếp, không "nhớ" số liệu từ phiên trước — mọi câu trả lời có số liệu truy ngược được về một Tool call cụ thể.

```mermaid
sequenceDiagram
    participant U as Patient / Doctor
    participant Agent as AI Agent
    participant Tools as Tool Registry
    participant DB as PostgreSQL
    participant VS as ChromaDB
    participant LLM as LLM

    U->>Agent: "Tuần này đường huyết của tôi thay đổi thế nào?"
    Agent->>Agent: safety_screen_in (không phải đổi liều/chẩn đoán/dấu hiệu nguy hiểm)
    Agent->>Tools: get_health_records(patient_id, 7 ngày)
    Tools->>DB: SELECT ... FROM glucose_records WHERE ...
    DB-->>Tools: dữ liệu thật (rows)
    Tools-->>Agent: kết quả (JSON)
    Agent->>Tools: calculate_health_metrics(rows)
    Tools-->>Agent: trung bình, xu hướng, % dữ liệu thiếu
    opt Câu hỏi có yếu tố kiến thức
        Agent->>Tools: retrieve_guideline(query)
        Tools->>VS: similarity search (lọc knowledge_sources.status = Đã duyệt)
        VS-->>Tools: đoạn trích + tên tài liệu + vị trí
        Tools-->>Agent: citations[]
    end
    Agent->>LLM: prompt = câu hỏi + đúng kết quả tool trả về<br/>(không gửi toàn bộ DB, không gửi record thô)
    LLM-->>Agent: bản tóm tắt bằng ngôn ngữ tự nhiên
    Agent->>Agent: safety_screen_out (mỗi khẳng định có ≥1 đoạn nguồn trực tiếp? không lẫn ngôn ngữ chẩn đoán/liều?)
    Agent-->>U: trả lời + khoảng thời gian dữ liệu + citations + giới hạn nếu thiếu dữ liệu
```

## 4. Alert Flow (F2) — cảnh báo ngưỡng

Không có "Emergency Detector" tách riêng — `severity` (Cần chú ý / Nguy hiểm) do Rule Engine gắn ngay khi lưu record (đồng bộ, không qua LLM) là đủ để Notification chạy đúng timeline. Đích nhận cảnh báo **luôn được resolve bằng query** tới `doctor_patient_assignments`, không phải danh sách tĩnh — chỉ bác sĩ phụ trách đúng bệnh nhân đó mới nhận được, không broadcast.

**Không có Caregiver, không có timer tự động liên hệ cấp cứu trong MVP.** PRD v0.4 xác nhận Caregiver không phải co-primary persona (chỉ xem xét lại sau pilot nếu có evidence) và "tự động liên hệ cấp cứu"/"cam kết giám sát 24/7" là Won't scope — hệ thống chỉ hiển thị cảnh báo cho Patient kèm hướng dẫn hành động **đã bác sĩ phê duyệt trước** (nội dung tĩnh, không phải phản hồi thời gian thực) và gắn cờ trên dashboard bác sĩ; không có cơ chế escort-to-hospital hay đếm ngược tự động gợi ý 115 như thiết kế v0.1.

```mermaid
sequenceDiagram
    participant P as Patient App
    participant HA as Health API + Rule Engine
    participant N as Notification
    participant DB as PostgreSQL
    participant Doc as Bác sĩ phụ trách

    P->>HA: Ghi nhận chỉ số (VD glucose < 70 hoặc > 300 mg/dL)
    HA->>HA: Rule Engine so ngưỡng cá nhân hoá → severity = Nguy hiểm
    HA->>N: trigger ngay (không chờ chu kỳ định kỳ)
    N-->>P: (≤ 2s) hiển thị cảnh báo + hướng dẫn hành động đã bác sĩ phê duyệt
    N->>DB: query doctor_patient_assignments(patient_id)
    DB-->>N: bác sĩ phụ trách
    N-->>Doc: gắn cờ trên dashboard (không cam kết phản hồi 24/7)
    Doc->>HA: xem alert + dữ liệu liên quan, ghi chú, cập nhật trạng thái xử lý
    HA->>N: đẩy ghi chú bác sĩ
    N-->>P: thông báo ghi chú/cập nhật
```

| t | Bước |
| --- | --- |
| 0s | Health API lưu bản ghi, Rule Engine gắn `severity` = Cần chú ý / Nguy hiểm |
| ≤ 2s | Notification hiển thị cảnh báo cho Patient App kèm hướng dẫn hành động đã bác sĩ phê duyệt |
| — | Notification query `doctor_patient_assignments` để resolve đúng bác sĩ phụ trách, gắn cờ trên dashboard (kênh gửi cụ thể — push/SMS/email — còn là open question trong PRD §9) |

## 5. Component Details

| Component | Technology | Purpose |
| --- | --- | --- |
| Frontend | React + Vite + Tailwind | 1 SPA, UI theo role (Patient/Doctor/Admin) |
| Backend | FastAPI (modular monolith) | Auth, Health API + Rule Engine, Care Plan, Appointment, Reminder/Notification, Audit Log, Knowledge Source Mgmt |
| Agent orchestration | LangGraph | Tool-calling loop + guardrail in/out, dùng chung Consultation + Summary |
| LLM | OpenAI API | Sinh câu trả lời/tóm tắt, chỉ dựa trên dữ liệu Tool trả về |
| Database | PostgreSQL | System of record: users, health records, care plans, alerts, ai_review_flags, notifications, appointments, audit logs |
| Vector Store | ChromaDB (embedded) | RAG trên guideline Đã duyệt (`data/knowledge_base/raw/`) cho F3 |
| Tracing | LangSmith | AI Logs + debug grounding/guardrail |
| Auth | JWT | RBAC theo role, scope kiểm tra ở API + Tool layer |

## 6. Feature → Diagram Traceability

| Feature (PRD) | Sơ đồ liên quan |
| --- | --- |
| F1 — Nhật ký sức khỏe theo kế hoạch | §1 System Overview (Health API + Rule Engine, Reminder) |
| F2 — Theo dõi xu hướng và cảnh báo cá nhân hóa | §4 Alert Flow |
| F3 — Trợ lý tư vấn lối sống có căn cứ | §2 Agent Flow, §3 Grounding Sequence |
| F4 — Theo dõi bệnh nhân giữa các kỳ tái khám | §1 System Overview (Care Plan/Appointment Service), §2 Agent Flow (nhánh Summary/`ai_review_flags`) |

Component detail đầy đủ theo từng Feature: xem bảng traceability trong [../ARCHITECTURE.md](../ARCHITECTURE.md).
