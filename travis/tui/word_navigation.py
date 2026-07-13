"""Pure word-navigation helpers used by editor components."""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from typing import Any


PUNCTUATION_REGEX = re.compile(r"""[(){}\[\]<>.,;:'"!?+\-=*/\\|&%^$#@~`]""")


@dataclass(frozen=True)
class WordSegment:
    segment: str
    is_word_like: bool = False


def find_word_backward(text: str, cursor: int, options: Mapping[str, Any] | None = None) -> int:
    if cursor <= 0:
        return 0
    cursor = min(cursor, len(text))
    text_before_cursor = text[:cursor]
    segments = _segments(text_before_cursor, options)
    new_cursor = cursor
    is_atomic = _atomic_checker(options)

    while segments and not is_atomic(segments[-1].segment) and _is_whitespace(segments[-1].segment):
        new_cursor -= len(segments.pop().segment)

    if not segments:
        return new_cursor

    last = segments[-1]
    if is_atomic(last.segment):
        new_cursor -= len(last.segment)
    elif last.is_word_like:
        matches = list(PUNCTUATION_REGEX.finditer(last.segment))
        if not matches:
            new_cursor -= len(last.segment)
        else:
            last_match = matches[-1]
            new_cursor -= len(last.segment) - last_match.end()
    else:
        while segments and not is_atomic(segments[-1].segment) and not segments[-1].is_word_like and not _is_whitespace(
            segments[-1].segment
        ):
            new_cursor -= len(segments.pop().segment)

    return max(0, new_cursor)


def find_word_forward(text: str, cursor: int, options: Mapping[str, Any] | None = None) -> int:
    if cursor >= len(text):
        return len(text)
    cursor = max(0, cursor)
    segments = _segments(text[cursor:], options)
    new_cursor = cursor
    is_atomic = _atomic_checker(options)
    index = 0

    while index < len(segments) and not is_atomic(segments[index].segment) and _is_whitespace(segments[index].segment):
        new_cursor += len(segments[index].segment)
        index += 1

    if index >= len(segments):
        return new_cursor

    current = segments[index]
    if is_atomic(current.segment):
        new_cursor += len(current.segment)
    elif current.is_word_like:
        match = PUNCTUATION_REGEX.search(current.segment)
        new_cursor += match.start() if match else len(current.segment)
    else:
        while (
            index < len(segments)
            and not is_atomic(segments[index].segment)
            and not segments[index].is_word_like
            and not _is_whitespace(segments[index].segment)
        ):
            new_cursor += len(segments[index].segment)
            index += 1

    return min(len(text), new_cursor)


def _segments(text: str, options: Mapping[str, Any] | None) -> list[WordSegment]:
    segment_fn = options.get("segment") if isinstance(options, Mapping) else None
    if callable(segment_fn):
        return [_coerce_segment(item) for item in segment_fn(text)]
    return _default_segments(text)


def _default_segments(text: str) -> list[WordSegment]:
    segments: list[WordSegment] = []
    index = 0
    while index < len(text):
        char = text[index]
        if char.isspace():
            start = index
            while index < len(text) and text[index].isspace():
                index += 1
            segments.append(WordSegment(text[start:index], False))
            continue
        if _is_word_char(char):
            start = index
            while index < len(text) and _is_word_char(text[index]):
                index += 1
            segments.append(WordSegment(text[start:index], True))
            continue
        start = index
        while index < len(text) and not text[index].isspace() and not _is_word_char(text[index]):
            index += 1
        segments.append(WordSegment(text[start:index], False))
    return segments


def _coerce_segment(item: Any) -> WordSegment:
    if isinstance(item, WordSegment):
        return item
    if isinstance(item, str):
        return WordSegment(item, _is_word_segment(item))
    if isinstance(item, Mapping):
        return WordSegment(str(item.get("segment", "")), bool(item.get("isWordLike", item.get("is_word_like", False))))
    return WordSegment(str(getattr(item, "segment", "")), bool(getattr(item, "isWordLike", getattr(item, "is_word_like", False))))


def _atomic_checker(options: Mapping[str, Any] | None) -> Callable[[str], bool]:
    checker = options.get("isAtomicSegment") if isinstance(options, Mapping) else None
    if not callable(checker):
        checker = options.get("is_atomic_segment") if isinstance(options, Mapping) else None
    if callable(checker):
        return lambda segment: bool(checker(segment))
    return lambda _segment: False


def _is_word_char(char: str) -> bool:
    return char.isalnum() or char == "_"


def _is_word_segment(segment: str) -> bool:
    return any(_is_word_char(char) for char in segment)


def _is_whitespace(segment: str) -> bool:
    return bool(segment) and all(char.isspace() for char in segment)


findWordBackward = find_word_backward
findWordForward = find_word_forward
