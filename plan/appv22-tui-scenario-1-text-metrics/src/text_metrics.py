def char_count(text: str) -> int:
    return len(text)


def line_count(text: str) -> int:
    if text == "":
        return 0
    return text.count("\n") + 1


def word_count(text: str) -> int:
    return len(text.split())


def non_empty_line_count(text: str) -> int:
    if text == "":
        return 0
    lines = text.splitlines()
    return sum(1 for line in lines if line.strip())