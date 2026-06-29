"""Edit diff/application helpers. Python port of Pi edit-diff.ts core."""

from __future__ import annotations

import difflib
import unicodedata
from dataclasses import dataclass


@dataclass
class AppliedEditsResult:
    base_content: str
    new_content: str


@dataclass
class DiffResult:
    diff: str
    first_changed_line: int | None = None


class EditToolError(ValueError):
    def __init__(self, message: str, details: dict) -> None:
        super().__init__(message)
        self.details = details


def detect_line_ending(content: str) -> str:
    crlf_idx = content.find("\r\n")
    lf_idx = content.find("\n")
    if lf_idx == -1 or crlf_idx == -1:
        return "\n"
    return "\r\n" if crlf_idx < lf_idx else "\n"


def normalize_to_lf(text: str) -> str:
    return text.replace("\r\n", "\n").replace("\r", "\n")


def restore_line_endings(text: str, ending: str) -> str:
    return text.replace("\n", "\r\n") if ending == "\r\n" else text


def strip_bom(content: str) -> tuple[str, str]:
    return ("\ufeff", content[1:]) if content.startswith("\ufeff") else ("", content)


def normalize_for_fuzzy_match(text: str) -> str:
    normalized = unicodedata.normalize("NFKC", text)
    return "\n".join(line.rstrip() for line in normalized.split("\n"))


def _count_occurrences(content: str, old_text: str) -> int:
    fuzzy_content = normalize_for_fuzzy_match(content)
    fuzzy_old_text = normalize_for_fuzzy_match(old_text)
    if fuzzy_old_text == "":
        return 0
    return fuzzy_content.count(fuzzy_old_text)


def _find_text(content: str, old_text: str) -> tuple[bool, int, int, bool, str]:
    exact_index = content.find(old_text)
    if exact_index != -1:
        return True, exact_index, len(old_text), False, content
    fuzzy_content = normalize_for_fuzzy_match(content)
    fuzzy_old_text = normalize_for_fuzzy_match(old_text)
    fuzzy_index = fuzzy_content.find(fuzzy_old_text)
    if fuzzy_index == -1:
        return False, -1, 0, False, content
    return True, fuzzy_index, len(fuzzy_old_text), True, fuzzy_content


def _not_found_error(path: str, edit_index: int, total_edits: int) -> ValueError:
    if total_edits == 1:
        return ValueError(
            f"Could not find the exact text in {path}. The old text must match exactly including all whitespace and newlines."
        )
    return ValueError(
        f"Could not find edits[{edit_index}] in {path}. The oldText must match exactly including all whitespace and newlines."
    )


def _duplicate_error(path: str, edit_index: int, total_edits: int, occurrences: int) -> ValueError:
    target = "oldText" if total_edits == 1 else f"edits[{edit_index}].oldText"
    message = (
        f"edit_failed: ambiguous_old_text\n"
        f"Found {occurrences} occurrences of {target} in {path}. "
        "Do not retry the same oldText. Read the current file content, then retry with a larger unique oldText block "
        "or combine nearby changes into one edit call."
    )
    details = {
        "code": "ambiguous_old_text",
        "path": path,
        "occurrences": occurrences,
        "edit_index": edit_index,
        "recovery": "read_current_file_then_retry_with_unique_context",
    }
    return EditToolError(message, details)


def _empty_old_text_error(path: str, edit_index: int, total_edits: int) -> ValueError:
    if total_edits == 1:
        return ValueError(f"oldText must not be empty in {path}.")
    return ValueError(f"edits[{edit_index}].oldText must not be empty in {path}.")


def apply_edits_to_normalized_content(normalized_content: str, edits: list[dict], path: str) -> AppliedEditsResult:
    normalized_edits = [
        {"oldText": normalize_to_lf(str(edit.get("oldText", ""))), "newText": normalize_to_lf(str(edit.get("newText", "")))}
        for edit in edits
    ]
    total_edits = len(normalized_edits)
    for index, edit in enumerate(normalized_edits):
        if edit["oldText"] == "":
            raise _empty_old_text_error(path, index, total_edits)

    initial_matches = [_find_text(normalized_content, edit["oldText"]) for edit in normalized_edits]
    base_content = normalize_for_fuzzy_match(normalized_content) if any(match[3] for match in initial_matches) else normalized_content

    matched = []
    for index, edit in enumerate(normalized_edits):
        found, match_index, match_length, _used_fuzzy, _content = _find_text(base_content, edit["oldText"])
        if not found:
            raise _not_found_error(path, index, total_edits)
        occurrences = _count_occurrences(base_content, edit["oldText"])
        if occurrences > 1:
            raise _duplicate_error(path, index, total_edits, occurrences)
        matched.append(
            {
                "edit_index": index,
                "match_index": match_index,
                "match_length": match_length,
                "new_text": edit["newText"],
            }
        )

    matched.sort(key=lambda item: item["match_index"])
    for index in range(1, len(matched)):
        previous = matched[index - 1]
        current = matched[index]
        if previous["match_index"] + previous["match_length"] > current["match_index"]:
            raise ValueError(
                f"edits[{previous['edit_index']}] and edits[{current['edit_index']}] overlap in {path}. "
                "Merge them into one edit or target disjoint regions."
            )

    new_content = base_content
    for edit in reversed(matched):
        start = edit["match_index"]
        end = start + edit["match_length"]
        new_content = new_content[:start] + edit["new_text"] + new_content[end:]

    if base_content == new_content:
        if total_edits == 1:
            raise ValueError(
                f"No changes made to {path}. The replacement produced identical content. This might indicate an issue with special characters or the text not existing as expected."
            )
        raise ValueError(f"No changes made to {path}. The replacements produced identical content.")
    return AppliedEditsResult(base_content=base_content, new_content=new_content)


def generate_unified_patch(path: str, old_content: str, new_content: str, context_lines: int = 4) -> str:
    return "".join(
        difflib.unified_diff(
            old_content.splitlines(keepends=True),
            new_content.splitlines(keepends=True),
            fromfile=path,
            tofile=path,
            n=context_lines,
        )
    )


def generate_diff_string(old_content: str, new_content: str, context_lines: int = 4) -> DiffResult:
    old_lines = old_content.split("\n")
    new_lines = new_content.split("\n")
    line_num_width = len(str(max(len(old_lines), len(new_lines))))
    output: list[str] = []
    old_line_num = 1
    new_line_num = 1
    first_changed_line: int | None = None

    matcher = difflib.SequenceMatcher(a=old_lines, b=new_lines)
    for tag, old_start, old_end, new_start, new_end in matcher.get_opcodes():
        if tag == "equal":
            lines = old_lines[old_start:old_end]
            if len(lines) > context_lines * 2:
                shown = [*lines[:context_lines], "...", *lines[-context_lines:]]
            else:
                shown = lines
            for line in shown:
                if line == "...":
                    output.append(f" {'':>{line_num_width}} ...")
                    skipped = len(lines) - (context_lines * 2)
                    old_line_num += max(0, skipped)
                    new_line_num += max(0, skipped)
                    continue
                output.append(f" {old_line_num:>{line_num_width}} {line}")
                old_line_num += 1
                new_line_num += 1
            continue
        if first_changed_line is None:
            first_changed_line = new_line_num
        if tag in ("replace", "delete"):
            for line in old_lines[old_start:old_end]:
                output.append(f"-{old_line_num:>{line_num_width}} {line}")
                old_line_num += 1
        if tag in ("replace", "insert"):
            for line in new_lines[new_start:new_end]:
                output.append(f"+{new_line_num:>{line_num_width}} {line}")
                new_line_num += 1

    return DiffResult(diff="\n".join(output), first_changed_line=first_changed_line)
