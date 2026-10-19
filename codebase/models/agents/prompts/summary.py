"""Summary-intent prompt template.

OWNER: AI Summary workstream. Do not edit from other workstreams — this file
has exactly one owner so parallel work never collides here.

synthesize_summary.py (same workstream) does the actual number-crunching:
trends/attention_points/limitations are built deterministically from
tools/metrics_tools.calculate_health_metrics's output in Python, *not* by
asking the LLM to invent them — see that file's docstring for why. This
template's only job is to turn those already-correct bullet points into
readable prose, in the right register for the audience. The LLM is
instructed not to add numbers of its own on top of what it's given.

Patient-facing vs doctor-facing tone branches on state["user_role"] — see
design doc §4.1: patient = plain language + 2-3 suggested doctor questions;
doctor = denser, keeps units, explicit "observation vs inference" separation,
leads with data completeness.
"""

from __future__ import annotations

SUMMARY_TEMPLATE = """Nhiệm vụ: Viết một đoạn tóm tắt sức khỏe bằng tiếng Việt dành cho {audience_label}, \
dựa CHỈ trên các số liệu và nhận định đã được tính toán sẵn bên dưới.

QUY TẮC BẮT BUỘC:
- Không tự thêm bất kỳ con số nào ngoài các số liệu đã cho.
- Không suy diễn nguyên nhân-kết quả trừ khi phần "Xu hướng đã tính toán" đã tự gắn nhãn là suy luận.
- Nếu phần "Xu hướng đã tính toán" trống, hãy nói rõ là chưa đủ dữ liệu, không tự bịa ra một xu hướng.
- Luôn cố gắng nêu bật ÍT NHẤT MỘT nhận định/điểm đáng chú ý cụ thể (ví dụ: một xu hướng, một điểm bất thường, \
một mối liên hệ đã được gắn nhãn "suy luận") thay vì chỉ liệt kê lại số liệu trung bình một cách chung chung — \
xem ví dụ bên dưới. Chỉ được phép nói "chưa có gì đáng chú ý" khi phần "Xu hướng đã tính toán" VÀ "Điểm cần lưu ý" \
bên dưới đều thực sự trống — không hạ thấp một nhận định đã có sẵn trong dữ liệu xuống thành lời văn chung chung.
- Chỉ trả về đoạn văn/gạch đầu dòng tóm tắt bằng tiếng Việt (không markdown tiêu đề, không JSON).

Bệnh nhân: {patient_name}
Khoảng thời gian xem xét: {date_range_str}
Người đọc: {role_label}

Độ đầy đủ dữ liệu trong khoảng thời gian này:
{completeness_str}

Thuốc đang dùng:
{medications_str}

Xu hướng đã tính toán (giữ nguyên số liệu, chỉ diễn giải lại bằng lời văn):
{trends_str}

Điểm cần lưu ý:
{attention_str}

Giới hạn của dữ liệu/phân tích:
{limitations_str}

Hướng dẫn văn phong cho {role_label}:
{role_instructions}

{example_output}
(Ví dụ trên chỉ minh hoạ VĂN PHONG và CẤU TRÚC — đặc biệt là cách nêu bật một nhận định cụ thể thay vì chỉ liệt kê \
số liệu. Số liệu trong ví dụ là số liệu MINH HOẠ, không liên quan đến bệnh nhân hiện tại — TUYỆT ĐỐI không copy \
hoặc tái sử dụng bất kỳ con số nào từ ví dụ vào bài viết thật bên dưới.)

Viết đoạn tóm tắt (3-6 câu hoặc vài gạch đầu dòng ngắn, bằng tiếng Việt, dựa đúng vào số liệu thật ở trên):
"""

# design doc §4.1 — tone branch. Falls back to the patient framing for any
# role not explicitly listed (the /summary endpoint only ever reaches this
# node for PATIENT/DOCTOR per require_roles, so this is a defensive default,
# not an expected path).
ROLE_LABELS: dict[str, str] = {
    "patient": "bệnh nhân",
    "doctor": "bác sĩ điều trị",
}

AUDIENCE_LABELS: dict[str, str] = {
    "patient": "bệnh nhân (không chuyên về y khoa)",
    "doctor": "bác sĩ điều trị (chuyên môn y khoa)",
}

# Few-shot anchor, one per role (tone differs too much for a single shared
# example — see ROLE_INSTRUCTIONS). Purpose is narrow: show the model the
# SHAPE of a good summary (concise, grounded, and — critically — always
# surfacing at least one concrete insight rather than a bland restatement of
# an average) without it ever being tempted to reuse the illustrative numbers
# themselves. Both variants deliberately model a weekend-vs-weekday pattern
# *and* a separate safety-relevant point (a low-glucose count) in the same
# short paragraph, since a real summary often has more than one thing worth
# calling out and shouldn't stop at the first.
EXAMPLE_OUTPUTS: dict[str, str] = {
    "patient": (
        "Ví dụ về một đoạn tóm tắt tốt (số liệu trong ví dụ là MINH HOẠ, không phải số liệu thật — xem ghi chú "
        "bên dưới):\n"
        '"Trong 7 ngày qua, đường huyết trung bình của bạn là 145 mg/dL. Mình nhận thấy một điểm đáng chú ý: '
        "đường huyết sau bữa tối vào cuối tuần thường cao hơn ngày thường khoảng 30 mg/dL — có thể liên quan đến "
        "thói quen ăn uống hoặc vận động khác vào cuối tuần. Ngoài ra, có 2 lần đo dưới 70 mg/dL trong tuần, đây "
        'là dấu hiệu hạ đường huyết cần theo dõi thêm. Hãy trao đổi với bác sĩ về xu hướng cuối tuần này trong '
        'lần tái khám tới."'
    ),
    "doctor": (
        "Ví dụ về một đoạn tóm tắt tốt (số liệu trong ví dụ là MINH HOẠ, không phải số liệu thật — xem ghi chú "
        "bên dưới):\n"
        '"Độ đầy đủ dữ liệu: 18/21 lần đo đường huyết kỳ vọng (85.7%). [Quan sát] Đường huyết trung bình toàn kỳ '
        "145 mg/dL (n=18, khoảng 68-210 mg/dL). [Suy luận] Đường huyết cuối tuần cao hơn trung bình 30 mg/dL so "
        "với ngày thường (cuối tuần: 165 mg/dL, n=6; ngày thường: 135 mg/dL, n=12) — gợi ý một mô hình theo ngày "
        "trong tuần, cần khai thác thêm để xác nhận nguyên nhân. Ghi nhận 2 lần đo dưới 70 mg/dL trong kỳ, cần "
        'theo dõi nguy cơ hạ đường huyết."'
    ),
}

ROLE_INSTRUCTIONS: dict[str, str] = {
    "patient": (
        "Dùng ngôn ngữ đơn giản, gần gũi, tránh thuật ngữ y khoa khó hiểu. Không chẩn đoán, không đề nghị "
        "thay đổi thuốc hoặc liều lượng. Nếu có điểm bất thường, khuyến khích trao đổi với bác sĩ điều trị."
    ),
    "doctor": (
        "Dùng văn phong súc tích, giữ nguyên đơn vị đo lường (mg/dL, phút, %). Phân biệt rõ ràng giữa "
        "'quan sát' (số liệu đo được trực tiếp từ dữ liệu) và 'suy luận' (diễn giải/gợi ý rút ra từ số liệu, "
        "chưa xác nhận lâm sàng). Nêu độ đầy đủ dữ liệu ngay từ đầu đoạn tóm tắt."
    ),
}
