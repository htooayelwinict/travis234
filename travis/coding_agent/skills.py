"""Skill parsing, validation, and discovery."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

import yaml

from travis.coding_agent.resource_discovery import collect_resource_files
from travis.coding_agent.source_info import SourceInfo, create_synthetic_source_info

SKILL_NAME_PATTERN = re.compile(r"(?!-)(?!.*--)[a-z0-9]+(?:-[a-z0-9]+)*")


@dataclass
class ResourceDiagnostic:
    type: str
    message: str
    path: str
    collision: dict[str, object] | None = None


@dataclass
class Skill:
    name: str
    description: str
    file_path: str
    base_dir: str
    source_info: SourceInfo
    disable_model_invocation: bool = False
    allowed_tools: tuple[str, ...] = ()


def parse_frontmatter(raw: str) -> tuple[dict[str, object], str]:
    if not raw.startswith("---"):
        return {}, raw
    lines = raw.splitlines()
    if not lines or lines[0].strip() != "---":
        return {}, raw
    end_index = next(
        (index for index, line in enumerate(lines[1:], start=1) if line.strip() == "---"),
        None,
    )
    if end_index is None:
        return {}, raw
    frontmatter_text = "\n".join(lines[1:end_index])
    try:
        loaded = yaml.safe_load(frontmatter_text)
    except yaml.YAMLError as error:
        raise ValueError(f"Malformed YAML frontmatter: {error}") from error
    if loaded is None:
        metadata: dict[str, object] = {}
    elif isinstance(loaded, dict):
        metadata = {str(key): value for key, value in loaded.items()}
    else:
        raise ValueError("YAML frontmatter must be a mapping")
    body = "\n".join(lines[end_index + 1 :]).lstrip("\r\n").rstrip("\n")
    return metadata, body


def validate_skill_metadata(name: str, description: str) -> tuple[str, ...]:
    errors: list[str] = []
    if len(name) > 64 or not SKILL_NAME_PATTERN.fullmatch(name):
        errors.append("name must match the Pi skill-name contract")
    if not description.strip():
        errors.append("description is required")
    if len(description) > 1_024:
        errors.append("description must be at most 1024 characters")
    return tuple(errors)


def load_skills(
    skill_paths: list[str],
    *,
    cwd: str,
    metadata_by_path: dict[str, dict[str, object]] | None = None,
) -> dict[str, list[object]]:
    skills_by_name: dict[str, Skill] = {}
    diagnostics: list[ResourceDiagnostic] = []
    seen_real_paths: set[str] = set()
    for path_text in skill_paths:
        path = _resolve_path(path_text, cwd)
        if not path.exists():
            diagnostics.append(ResourceDiagnostic("warning", "skill path does not exist", str(path)))
            continue
        paths = collect_resource_files(path, "skills") if path.is_dir() else [path]
        for skill_file in paths:
            try:
                metadata, _body = parse_frontmatter(skill_file.read_text(encoding="utf-8"))
            except (OSError, ValueError) as error:
                diagnostics.append(ResourceDiagnostic("warning", str(error), str(skill_file)))
                continue
            name = str(metadata.get("name") or skill_file.parent.name)
            description = str(metadata.get("description") or "")
            validation_errors = validate_skill_metadata(name, description)
            diagnostics.extend(
                ResourceDiagnostic("warning", message, str(skill_file))
                for message in validation_errors
            )
            if validation_errors:
                continue
            real_path = str(skill_file.resolve())
            if real_path in seen_real_paths:
                continue
            existing = skills_by_name.get(name)
            if existing is not None:
                diagnostics.append(
                    ResourceDiagnostic(
                        "collision",
                        f'name "{name}" collision',
                        str(skill_file),
                        {
                            "resourceType": "skill",
                            "name": name,
                            "winnerPath": existing.file_path,
                            "loserPath": str(skill_file),
                        },
                    )
                )
                continue
            skill = Skill(
                name=name,
                description=description,
                file_path=str(skill_file),
                base_dir=str(skill_file.parent),
                source_info=_source_info_for_path(skill_file, metadata_by_path),
                disable_model_invocation=metadata.get("disable-model-invocation") is True,
                allowed_tools=_parse_allowed_tools(metadata.get("allowed-tools")),
            )
            skills_by_name[name] = skill
            seen_real_paths.add(real_path)
    return {"skills": list(skills_by_name.values()), "diagnostics": diagnostics}


def format_skills_for_prompt(skills: list[Skill]) -> str:
    visible_skills = [skill for skill in skills if not skill.disable_model_invocation]
    if not visible_skills:
        return ""
    lines = [
        "",
        "",
        "The following skills provide specialized instructions for specific tasks.",
        "Use the read tool to load a skill's file when the task matches its description.",
        "When a skill file references a relative path, resolve it against the skill directory (parent of SKILL.md / dirname of the path) and use that absolute path in tool commands.",
        "",
        "<available_skills>",
    ]
    for skill in visible_skills:
        lines.extend(
            [
                "  <skill>",
                f"    <name>{_escape_xml(skill.name)}</name>",
                f"    <description>{_escape_xml(skill.description)}</description>",
                f"    <location>{_escape_xml(skill.file_path)}</location>",
                "  </skill>",
            ]
        )
    lines.append("</available_skills>")
    return "\n".join(lines)


def format_skill_invocation(skill: Skill, additional_instructions: str = "") -> str:
    """Read and wrap one explicitly selected skill for a user turn."""

    raw = Path(skill.file_path).read_text(encoding="utf-8")
    _metadata, body = parse_frontmatter(raw)
    skill_block = (
        f'<skill name="{skill.name}" location="{skill.file_path}">\n'
        f"References are relative to {skill.base_dir}.\n\n"
        f"{body.strip()}\n"
        "</skill>"
    )
    instructions = additional_instructions.strip()
    return f"{skill_block}\n\n{instructions}" if instructions else skill_block


def skill_command_names(skills: Sequence[Skill]) -> tuple[str, ...]:
    return tuple(f"skill:{skill.name}" for skill in skills)


def _parse_allowed_tools(value: object) -> tuple[str, ...]:
    raw_tools = value.replace(",", " ").split() if isinstance(value, str) else value if isinstance(value, list) else []
    result: list[str] = []
    for item in raw_tools:
        if isinstance(item, str) and item.strip() and item.strip() not in result:
            result.append(item.strip())
    return tuple(result)


def _resolve_path(path: str, cwd: str) -> Path:
    candidate = Path(path).expanduser()
    return (candidate if candidate.is_absolute() else Path(cwd) / candidate).resolve()


def _source_info_for_path(
    path: Path,
    metadata_by_path: dict[str, dict[str, object]] | None,
) -> SourceInfo:
    metadata = None
    if metadata_by_path:
        for source_path, source_metadata in metadata_by_path.items():
            try:
                path.resolve().relative_to(Path(source_path).resolve())
                metadata = source_metadata
                break
            except ValueError:
                continue
    if metadata:
        return create_synthetic_source_info(
            str(path),
            source=str(metadata.get("source", "local")),
            scope=str(metadata.get("scope", "temporary")),
            origin=str(metadata.get("origin", "top-level")),
            base_dir=metadata.get("baseDir") if isinstance(metadata.get("baseDir"), str) else None,
        )
    return create_synthetic_source_info(str(path), source="local", base_dir=str(path.parent))


def _escape_xml(text: str) -> str:
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&apos;")
    )


__all__ = [
    "ResourceDiagnostic",
    "SKILL_NAME_PATTERN",
    "Skill",
    "format_skill_invocation",
    "format_skills_for_prompt",
    "load_skills",
    "parse_frontmatter",
    "skill_command_names",
    "validate_skill_metadata",
]
