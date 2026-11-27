from worklib.logutil import sanitize_log_text, split_log_buffer, wrap_log_line


def test_split_cr_progress():
    lines, pending = split_log_buffer("10%\r20%\r30%\n完了\n")
    assert lines == ["10%", "20%", "30%", "完了"]
    assert pending == ""


def test_sanitize_long_line():
    out = sanitize_log_text("x" * 500, max_len=100)
    assert " …\n" in out


def test_wrap_log_line():
    parts = wrap_log_line("abcdefghij", max_len=4)
    assert parts[0] == "abcd …"