"""System prompt construction."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date as _date
from pathlib import Path
from typing import Optional

from travis.coding_agent.config import get_packaged_context_paths
from travis.coding_agent.resource_loader import Skill, format_skills_for_prompt


@dataclass
class BuildSystemPromptOptions:
    cwd: str
    custom_prompt: str | None = None
    selected_tools: Optional[list[str]] = None
    tool_snippets: dict[str, str] = field(default_factory=dict)
    prompt_guidelines: list[str] = field(default_factory=list)
    append_system_prompt: str | None = None
    context_files: list[tuple[str, str]] = field(default_factory=list)  # (path, content)
    skills: list[Skill] = field(default_factory=list)


_PREAMBLE = (
    "You are an expert coding assistant operating inside Travis234, a coding agent harness. "
    "You help users by reading files, executing commands, editing code, and writing new files."
)


def build_system_prompt(options: BuildSystemPromptOptions) -> str:
    prompt_cwd = options.cwd.replace("\\", "/")
    today = _date.today().strftime("%Y-%m-%d")
    append_section = f"\n\n{options.append_system_prompt}" if options.append_system_prompt else ""

    if options.custom_prompt:
        prompt = options.custom_prompt + append_section
        prompt += _context_section(options.context_files)
        custom_prompt_has_read = options.selected_tools is None or "read" in options.selected_tools
        if custom_prompt_has_read and options.skills:
            prompt += format_skills_for_prompt(options.skills)
        prompt += f"\nCurrent date: {today}"
        prompt += f"\nCurrent working directory: {prompt_cwd}"
        return prompt

    tools = options.selected_tools if options.selected_tools is not None else ["read", "bash", "edit", "write"]
    visible_tools = [name for name in tools if options.tool_snippets.get(name)]
    if visible_tools:
        tools_list = "\n".join(f"- {name}: {options.tool_snippets[name]}" for name in visible_tools)
    else:
        tools_list = "(none)"

    guidelines: list[str] = []
    seen: set[str] = set()

    def add(guideline: str) -> None:
        normalized = guideline.strip()
        if normalized and normalized not in seen:
            seen.add(normalized)
            guidelines.append(normalized)

    has_bash = "bash" in tools
    has_grep = "grep" in tools
    has_find = "find" in tools
    has_ls = "ls" in tools
    has_read = "read" in tools
    if has_bash and not has_grep and not has_find and not has_ls:
        add("Use bash for file operations like ls, rg, find")
    for guideline in options.prompt_guidelines:
        add(guideline)
    add("Be concise in your responses")
    add("Show file paths clearly when working with files")

    guidelines_text = "\n".join(f"- {g}" for g in guidelines)

    documentation_section = _documentation_section()
    prompt = (
        f"{_PREAMBLE}\n\n"
        f"Available tools:\n{tools_list}\n\n"
        "In addition to the tools above, you may have access to other custom tools depending on the project.\n\n"
        f"Guidelines:\n{guidelines_text}"
        f"{documentation_section}"
    )
    prompt += append_section
    prompt += _context_section(options.context_files)
    if has_read and options.skills:
        prompt += format_skills_for_prompt(options.skills)
    prompt += f"\nCurrent date: {today}"
    prompt += f"\nCurrent working directory: {prompt_cwd}"
    return prompt


def _documentation_section() -> str:
    readme_path, docs_path, examples_path = get_packaged_context_paths()
    readme_exists = Path(readme_path).is_file()
    docs_root = Path(docs_path)
    docs_exists = docs_root.is_dir()
    installed_docs = sorted(
        path for path in docs_root.rglob("*.md") if path.is_file()
    ) if docs_exists else []
    examples_exists = Path(examples_path).is_dir()
    if not (readme_exists or docs_exists or examples_exists):
        return ""

    lines = [
        "",
        "",
        "Travis234 documentation (read only when the user asks about Travis234 itself, its SDK, extensions, "
        "themes, skills, or TUI):",
    ]
    if readme_exists:
        lines.append(f"- Main documentation: {readme_path}")
    if docs_exists:
        lines.append(f"- Additional docs: {docs_path}")
        lines.extend(f"- Installed documentation file: {path}" for path in installed_docs)
    if examples_exists:
        lines.append(f"- Examples: {examples_path} (extensions, custom tools, SDK)")
    if docs_exists and examples_exists:
        lines.append(
            "- Resolve docs/... under Additional docs and examples/... under Examples, not the current working directory"
        )
    if docs_exists:
        lines.extend(
            [
                "- Use only the installed documentation files listed above; never assume an unlisted topic file exists",
                "- When working on Travis234 topics, read the available documentation and follow .md cross-references "
                "before implementing",
                "- Always read Travis234 .md files completely and follow links to related docs",
            ]
        )
    elif examples_exists:
        lines.append("- Use the installed examples when working on Travis234 extensions, custom tools, or SDK integrations")
    return "\n".join(lines)


def _context_section(context_files: list[tuple[str, str]]) -> str:
    if not context_files:
        return ""
    section = "\n\n<project_context>\n\nProject-specific instructions and guidelines:\n\n"
    for file_path, content in context_files:
        section += f'<project_instructions path="{file_path}">\n{content}\n</project_instructions>\n\n'
    section += "</project_context>\n"
    return section
