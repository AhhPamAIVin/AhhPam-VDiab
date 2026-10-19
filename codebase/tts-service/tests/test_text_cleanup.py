from app.text_cleanup import strip_markdown_for_speech


def test_strips_bold_and_italic():
    assert strip_markdown_for_speech("**Xin chào**, đây là *ví dụ*.") == "Xin chào, đây là ví dụ."


def test_strips_heading():
    assert strip_markdown_for_speech("### Lưu ý quan trọng\nNội dung.") == "Lưu ý quan trọng\nNội dung."


def test_strips_bullets_and_numbered_list():
    text = "- Uống thuốc đúng giờ\n- Đo đường huyết\n1. Bước một\n2. Bước hai"
    assert strip_markdown_for_speech(text) == "Uống thuốc đúng giờ\nĐo đường huyết\nBước một\nBước hai"


def test_strips_link_keeps_label():
    assert strip_markdown_for_speech("Xem thêm [hướng dẫn](https://example.com/a) tại đây.") == (
        "Xem thêm hướng dẫn tại đây."
    )


def test_strips_code_span_and_fence():
    assert strip_markdown_for_speech("Chạy lệnh `ls -la` để xem.") == "Chạy lệnh ls -la để xem."
    assert strip_markdown_for_speech("```\nprint(1)\n```") == "print(1)"


def test_table_pipes_become_commas():
    assert strip_markdown_for_speech("A | B | C") == "A , B , C"


def test_empty_and_whitespace_only():
    assert strip_markdown_for_speech("") == ""
    assert strip_markdown_for_speech("   \n\n  ") == ""
