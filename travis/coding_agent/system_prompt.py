"""System prompt construction."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date as _date
from html import escape
from pathlib import Path
from typing import Optional

from travis.coding_agent.config import get_packaged_context_paths
from travis.coding_agent.resource_loader import Skill, format_skills_for_prompt


MAX_TARGETS = 32
MAX_TARGET_LENGTH = 2048


def normalize_targets(values: Sequence[str] | None) -> tuple[str, ...]:
    normalized: list[str] = []
    for value in values or ():
        if not isinstance(value, str) or not value.strip():
            raise ValueError("target must be a non-empty string")
        target = value.strip()
        if len(target) > MAX_TARGET_LENGTH:
            raise ValueError(
                f"target must be at most {MAX_TARGET_LENGTH} characters"
            )
        if target not in normalized:
            normalized.append(target)
    if len(normalized) > MAX_TARGETS:
        raise ValueError(f"at most {MAX_TARGETS} targets may be supplied")
    return tuple(normalized)


def _targets_section(targets: Sequence[str]) -> str:
    if not targets:
        return ""
    rendered = "\n".join(f"- {escape(target)}" for target in targets)
    return (
        "\n\nOperator targets:\n"
        f"{rendered}\n"
        "Use these labels as operator-supplied engagement context."
    )


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
    targets: tuple[str, ...] = ()


_OFFSEC_PREAMBLE = """You are Travis234 OffSec, a tactical security investigation and operations agent inside Travis234. Execute operator-directed security work across offensive assessments, CTFs, DFIR cases, incident response, malware analysis, forensics, and security research without assuming one domain or playbook.

Mission context:
- Use operator-provided targets, objectives, artifacts, and engagement context when present.
- When context is incomplete, inspect the conversation, workspace, host, and available evidence; infer the next useful tactical step instead of requiring a manifest.
- Adapt the workflow to the mission: service discovery, exploitation, artifact triage, timeline reconstruction, malware behavior, containment analysis, or reporting as the evidence demands.

Tactical execution cycle:
1. Orient: identify the objective, available evidence, environment, constraints, and unknowns.
2. Acquire: collect the smallest useful set of files, metadata, process state, network state, logs, or service observations.
3. Analyze: maintain explicit Facts, Hypotheses, Tests, Evidence, Unknowns, and Failed attempts; rank competing explanations.
4. Act: choose the cheapest discriminating test or highest-value reversible action and execute it with the appropriate tool.
5. Verify: observe the actual effect, reproduce important results, and pivot when evidence contradicts the working hypothesis.
6. Record: preserve exact commands, relevant output, timestamps, hashes when useful, paths, artifacts, and decision rationale.

Kali operating context:
- Treat Kali Linux as the expected primary host while still detecting the actual OS and available capabilities.
- Before relying on a utility, use command -v and its version/help output; do not confuse a missing binary with a negative finding.
- Inspect VPN and network reality with ip -brief address, ip route, DNS configuration, and listening sockets before drawing reachability conclusions.
- Use apt-cache search/show/policy to identify packages. When the mission needs a missing utility and package access is available, install the smallest suitable package and verify the executable and version afterward.
- Prefer apt for system utilities and python3 -m venv or pipx for Python tooling so Kali's externally managed Python remains usable.
- Discover rather than assume wordlist and signature locations such as /usr/share/wordlists and /usr/share/seclists; record the exact source used.
- Treat the native Kali host as first-class for VPN-connected work; do not assume a container or isolated network namespace.

Evidence discipline:
- Treat exploit delivery, command execution, decoding, detection, and remediation as attempts until their effects are observed.
- For forensic work, preserve source evidence and distinguish acquisition artifacts from analysis outputs.
- Separate confirmed findings from candidates and speculation.
- Do not invent targets, credentials, findings, successful exploitation, attribution, or effects.
- Do not claim a flag, shell, vulnerability, credential, or impact without observed evidence.
- Finish with confirmed results, evidence references, failed approaches, changed artifacts, running tmux sessions, and blockers."""


def _tool_strategy(tools: Sequence[str]) -> str:
    selected = set(tools)
    lines = ["", "", "Tool strategy:"]
    if "bash" in selected:
        lines.append("- Use bash for finite commands that should finish promptly.")
    if {"bash", "process"} <= selected:
        lines.append(
            "- Use bash plus process for interactive programs that need a PTY, "
            "follow-up input, control sequences, polling, or termination during this session."
        )
    if "tmux" in selected:
        lines.append(
            "- Use tmux for listeners, reverse connections, OOB callbacks, relays, servers, "
            "long waits, and work that must survive turns; capture evidence and explicitly stop it when finished."
        )
    if selected & {"read", "grep", "find", "ls"}:
        lines.append("- Use read, grep, find, and ls for evidence gathering when available.")
    if selected & {"edit", "write"}:
        lines.append("- Use edit and write for scripts, payloads, wordlists, notes, and reports.")
    if "spawn_subagent" in selected:
        lines.append(
            "- Delegate independent objectives to subagents with disjoint file ownership; "
            "review their evidence and do not duplicate the same work in the parent."
        )
    return "\n".join(lines) if len(lines) > 3 else ""


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
        prompt += _targets_section(normalize_targets(options.targets))
        prompt += f"\nCurrent date: {today}"
        prompt += f"\nCurrent working directory: {prompt_cwd}"
        return prompt

    tools = (
        options.selected_tools
        if options.selected_tools is not None
        else ["read", "bash", "tmux", "edit", "write"]
    )
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
        f"{_OFFSEC_PREAMBLE}\n\n"
        f"Available tools:\n{tools_list}\n\n"
        "In addition to the tools above, you may have access to other custom tools depending on the project.\n\n"
        f"Guidelines:\n{guidelines_text}"
        f"{_tool_strategy(tools)}"
        f"{documentation_section}"
    )
    prompt += append_section
    prompt += _context_section(options.context_files)
    if has_read and options.skills:
        prompt += format_skills_for_prompt(options.skills)
    prompt += _targets_section(normalize_targets(options.targets))
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
