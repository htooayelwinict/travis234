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

_ENGINEERING_GUIDANCE = (
    "Operate as a senior software engineer responsible for the complete outcome. Understand the repository, its "
    "constraints, and the dependency graph before acting. Ground every material claim in repository contents or "
    "observed tool output. Treat child summaries as leads rather than proof: independently verify their material "
    "claims, exact test counts, and changed files before reporting. Never invent files, tests, command results, or "
    "verification."
)

_SUBAGENT_ORCHESTRATION_GUIDANCE = (
    "Use subagents when the user explicitly requests delegation. Otherwise, use them for two or more independent, "
    "bounded engineering workstreams only when project instructions do not restrict delegation. Give each child "
    "exact scope, constraints, expected evidence, and verification; do not delegate trivial, sequential, tightly "
    "coupled, shared-architecture, overlapping-edit, integration, or final-validation work. Start independent "
    "children concurrently with `spawn_subagent` and `wait=false`, continue useful parent work, collect every child "
    "with `wait_subagent`, and independently verify material claims before synthesizing the outcome. Honor an "
    "explicit user request not to use subagents."
)

def _execution_routing_guidance(tools: list[str]) -> str:
    guidance: list[str] = []
    if "bash" in tools:
        guidance.append("Run finite commands with `bash`.")
    if "process" in tools:
        guidance.append(
            "Use managed `bash` plus `process` for commands that remain running. "
            "Set `tty=true` only for terminal interaction; ordinary long-running commands should remain non-PTY."
        )
    if "tmux" in tools:
        guidance.append(
            "Use `tmux` for servers, watchers, REPLs, and work that must survive across turns. Capture evidence from "
            "long-lived work and stop resources that are no longer needed."
        )
    return " ".join(guidance)


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

    tools = options.selected_tools if options.selected_tools is not None else ["read", "bash", "tmux", "edit", "write"]
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
    orchestration_guidance = " ".join(
        part
        for part in (
            _ENGINEERING_GUIDANCE,
            _SUBAGENT_ORCHESTRATION_GUIDANCE
            if {"spawn_subagent", "wait_subagent"}.issubset(tools)
            else "",
            _execution_routing_guidance(tools),
        )
        if part
    )
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
        f"{orchestration_guidance}\n\n"
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
        "Travis234 documentation (consult only for Travis234 itself, its SDK, extensions, themes, skills, or TUI):",
    ]
    if readme_exists:
        lines.append(f"- Main documentation: {readme_path}")
    if docs_exists:
        lines.append(f"- Additional docs root: {docs_path}")
        lines.extend(f"- Installed documentation file: {path}" for path in installed_docs)
    if examples_exists:
        lines.append(f"- Examples root: {examples_path}")
    if docs_exists and examples_exists:
        lines.append(
            "- Resolve docs/... under the docs root and examples/... under the examples root, not cwd."
        )
    elif docs_exists:
        lines.append("- Resolve docs/... under the docs root, not cwd.")
    elif examples_exists:
        lines.append("- Resolve examples/... under the examples root, not cwd.")
    if docs_exists:
        lines.append(
            "- For Travis234 work, read the listed Markdown completely and follow its links; use only listed files "
            "and never assume an unlisted topic file exists."
        )
    elif examples_exists:
        lines.append("- For Travis234 SDK or extension work, consult the installed examples root.")
    return "\n".join(lines)


def _context_section(context_files: list[tuple[str, str]]) -> str:
    if not context_files:
        return ""
    section = "\n\n<project_context>\n\nProject-specific instructions and guidelines:\n\n"
    for file_path, content in context_files:
        section += f'<project_instructions path="{file_path}">\n{content}\n</project_instructions>\n\n'
    section += "</project_context>\n"
    return section
