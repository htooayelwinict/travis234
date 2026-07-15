"""Prompt-template parsing and expansion primitives."""

from __future__ import annotations

import re
import shlex
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

from travis.coding_agent.resource_discovery import collect_resource_files
from travis.coding_agent.skills import ResourceDiagnostic, parse_frontmatter
from travis.coding_agent.source_info import SourceInfo, create_synthetic_source_info


@dataclass
class PromptTemplate:
    name: str
    description: str
    content: str
    source_info: SourceInfo
    file_path: str
    argument_hint: str | None = None


def load_prompt_templates(
    prompt_paths: list[str],
    *,
    cwd: str,
    metadata_by_path: dict[str, dict[str, object]] | None = None,
) -> dict[str, list[object]]:
    prompts: list[PromptTemplate] = []
    diagnostics: list[ResourceDiagnostic] = []
    seen_names: set[str] = set()
    for path_text in prompt_paths:
        path = _resolve_path(path_text, cwd)
        paths = collect_resource_files(path, "prompts") if path.is_dir() else [path]
        for prompt_file in paths:
            if not prompt_file.exists() or prompt_file.suffix != ".md":
                continue
            try:
                metadata, body = parse_frontmatter(prompt_file.read_text(encoding="utf-8"))
            except (OSError, ValueError) as error:
                diagnostics.append(ResourceDiagnostic("warning", str(error), str(prompt_file)))
                continue
            name = prompt_file.stem
            if name in seen_names:
                diagnostics.append(ResourceDiagnostic("collision", f'name "{name}" collision', str(prompt_file)))
                continue
            description = str(metadata.get("description") or "")
            if not description:
                first_line = next((line.strip() for line in body.splitlines() if line.strip()), "")
                description = first_line[:60] + ("..." if len(first_line) > 60 else "")
            argument_hint = metadata.get("argument-hint")
            prompts.append(
                PromptTemplate(
                    name=name,
                    description=description,
                    content=body,
                    source_info=_source_info_for_path(prompt_file, metadata_by_path),
                    file_path=str(prompt_file),
                    argument_hint=str(argument_hint) if argument_hint is not None else None,
                )
            )
            seen_names.add(name)
    return {"prompts": prompts, "diagnostics": diagnostics}


_PLACEHOLDER_PATTERN = re.compile(
    r"\$\{(\d+):-([^}]*)\}|\$\{@:(\d+)(?::(\d+))?\}|\$(ARGUMENTS|@|\d+)"
)


def substitute_prompt_arguments(content: str, arguments: Sequence[str]) -> str:
    """Apply Pi-compatible placeholders without recursively rewriting argument values."""

    values = list(arguments)

    def replace(match: re.Match[str]) -> str:
        default_number, default_value, slice_start, slice_length, simple = match.groups()
        if default_number is not None:
            index = int(default_number) - 1
            value = values[index] if 0 <= index < len(values) else ""
            return value or default_value
        if slice_start is not None:
            start = max(0, int(slice_start) - 1)
            selected = values[start:]
            if slice_length is not None:
                selected = selected[: int(slice_length)]
            return " ".join(selected)
        if simple in {"ARGUMENTS", "@"}:
            return " ".join(values)
        index = int(simple) - 1
        return values[index] if 0 <= index < len(values) else ""

    return _PLACEHOLDER_PATTERN.sub(replace, content)


def expand_prompt_template(text: str, templates: Sequence[PromptTemplate]) -> str:
    """Expand a leading slash template while preserving unmatched input."""

    if not text.startswith("/"):
        return text
    match = re.fullmatch(r"/([^\s]+)(?:\s+([\s\S]*))?", text)
    if match is None:
        return text
    command, raw_arguments = match.groups()
    template = next((item for item in templates if item.name == command), None)
    if template is None:
        return text
    try:
        arguments = shlex.split(raw_arguments) if raw_arguments else []
    except ValueError:
        return text
    return substitute_prompt_arguments(template.content, arguments)


def _resolve_path(path: str, cwd: str) -> Path:
    candidate = Path(path).expanduser()
    return (candidate if candidate.is_absolute() else Path(cwd) / candidate).resolve()


def _source_info_for_path(
    path: Path,
    metadata_by_path: dict[str, dict[str, object]] | None,
) -> SourceInfo:
    if metadata_by_path:
        for source_path, metadata in metadata_by_path.items():
            try:
                path.resolve().relative_to(Path(source_path).resolve())
            except ValueError:
                continue
            return create_synthetic_source_info(
                str(path),
                source=str(metadata.get("source", "local")),
                scope=str(metadata.get("scope", "temporary")),
                origin=str(metadata.get("origin", "top-level")),
                base_dir=metadata.get("baseDir") if isinstance(metadata.get("baseDir"), str) else None,
            )
    return create_synthetic_source_info(str(path), source="local", base_dir=str(path.parent))


__all__ = [
    "PromptTemplate",
    "expand_prompt_template",
    "load_prompt_templates",
    "substitute_prompt_arguments",
]
