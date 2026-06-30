"""System prompt construction. Port of pi/packages/coding-agent/src/core/system-prompt.ts."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date as _date
from typing import Optional

from appv23.coding_agent.config import get_docs_path, get_examples_path, get_readme_path
from appv23.coding_agent.resource_loader import Skill, format_skills_for_prompt


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
    "You are an expert coding assistant operating inside appv23, a coding agent harness. "
    "You help users by reading files, executing commands, editing code, and writing new files."
)


_TASK_COMPLETION_GUIDANCE = (
    "# Finishing the job\n"
    "When the user asks you to build, run, verify, summarize, report, review, document, "
    "or write something, the deliverable is the completed artifact backed by real tool "
    "output, not a description of one. If the user names a file path for a summary, "
    "report, checklist, notes, document, or other written result, that file path is "
    "the deliverable. If the target file does not exist, create it with write instead "
    "of treating it as source content to read. Use edit for precise updates to existing "
    "files. If a tool failure blocks the real path, report the blocker directly instead "
    "of inventing a result."
)


def _get_readme_path() -> str:
    return get_readme_path()


def _get_docs_path() -> str:
    return get_docs_path()


def _get_examples_path() -> str:
    return get_examples_path()


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

    readme_path = _get_readme_path()
    docs_path = _get_docs_path()
    examples_path = _get_examples_path()
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

    prompt = (
        f"{_PREAMBLE}\n\n"
        f"{_TASK_COMPLETION_GUIDANCE}\n\n"
        f"Available tools:\n{tools_list}\n\n"
        "In addition to the tools above, you may have access to other custom tools depending on the project.\n\n"
        f"Guidelines:\n{guidelines_text}\n\n"
        "Pi documentation (read only when the user asks about pi itself, its SDK, extensions, themes, skills, or TUI):\n"
        f"- Main documentation: {readme_path}\n"
        f"- Additional docs: {docs_path}\n"
        f"- Examples: {examples_path} (extensions, custom tools, SDK)\n"
        "- When reading pi docs or examples, resolve docs/... under Additional docs and examples/... under Examples, not the current working directory\n"
        "- When asked about: extensions (docs/extensions.md, examples/extensions/), themes (docs/themes.md), skills (docs/skills.md), prompt templates (docs/prompt-templates.md), TUI components (docs/tui.md), keybindings (docs/keybindings.md), SDK integrations (docs/sdk.md), custom providers (docs/custom-provider.md), adding models (docs/models.md), pi packages (docs/packages.md)\n"
        "- When working on pi topics, read the docs and examples, and follow .md cross-references before implementing\n"
        "- Always read pi .md files completely and follow links to related docs (e.g., tui.md for TUI API details)"
    )
    prompt += append_section
    prompt += _context_section(options.context_files)
    if has_read and options.skills:
        prompt += format_skills_for_prompt(options.skills)
    prompt += f"\nCurrent date: {today}"
    prompt += f"\nCurrent working directory: {prompt_cwd}"
    return prompt


def _context_section(context_files: list[tuple[str, str]]) -> str:
    if not context_files:
        return ""
    section = "\n\n<project_context>\n\nProject-specific instructions and guidelines:\n\n"
    for file_path, content in context_files:
        section += f'<project_instructions path="{file_path}">\n{content}\n</project_instructions>\n\n'
    section += "</project_context>\n"
    return section
