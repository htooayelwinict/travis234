from src.text_metrics import char_count, line_count, word_count, non_empty_line_count


def test_char_count():
    assert char_count("abc") == 3


def test_line_count():
    assert line_count("a\nb") == 2


def test_word_count_splits_whitespace():
    assert word_count(" one\ttwo\nthree ") == 3


def test_non_empty_line_count():
    assert non_empty_line_count("a\n  \nb\n\n") == 2


def test_word_count_empty_string():
    assert word_count("") == 0


def test_non_empty_line_count_with_whitespace_line():
    assert non_empty_line_count("a\n\n b ") == 2