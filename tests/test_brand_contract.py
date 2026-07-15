from __future__ import annotations

import re
from pathlib import Path


CONTRACT_FILE = Path(__file__).resolve()
ROOT = CONTRACT_FILE.parents[1]
ALLOWED_ATTRIBUTION_FILES = {ROOT / "LICENSE", ROOT / "NOTICE.md"}
THIRD_PARTY_PRODUCT_LABEL_FILES = {
    ROOT / "travis" / "coding_agent" / "export_html_assets" / "vendor" / "highlight.min.js",
}
EXTERNAL_RESOURCE_COMPATIBILITY_FILES = {
    ROOT / "travis" / "coding_agent" / "project_trust.py",
}
ACTIVE_TEXT_ROOTS = (
    ROOT / "travis",
    ROOT / "evals",
    ROOT / "packages" / "travis234-cli" / "bin",
)
ACTIVE_ROOT_FILES = (
    ROOT / "Dockerfile",
    ROOT / "Dockerfile.release",
    ROOT / "README.md",
    ROOT / "package.json",
    ROOT / "pyproject.toml",
)
FORBIDDEN_STATE_PATTERNS = (
    re.compile(
        r"(?:(?<![\w.])\.(?:agents|allthebest|appv(?:2|21|22|23|231))|/(?:agent-home|allthebest-home|appv(?:2|21|22|23|231)-home))(?:/|\b)",
        re.IGNORECASE,
    ),
)
FORMER_APP_PATTERNS = (
    re.compile(r"appv(?:2|21|22|23|231)", re.IGNORECASE),
    re.compile(r"\bv231\b", re.IGNORECASE),
    re.compile(r"\ballthebest\b", re.IGNORECASE),
    *FORBIDDEN_STATE_PATTERNS,
)
PRODUCT_LINEAGE_PATTERNS = (
    re.compile(r"(?<!math\.)(?<!raspberry )\bpi\b", re.IGNORECASE),
    re.compile(r"\bhermes(?:-style| agent)?\b", re.IGNORECASE),
    re.compile(r"(?:^|/)\.pi(?:/|$)", re.IGNORECASE),
)
FORBIDDEN_RUNTIME_PATTERNS = FORMER_APP_PATTERNS + PRODUCT_LINEAGE_PATTERNS


def _runtime_text_files() -> list[Path]:
    files = [path for root in ACTIVE_TEXT_ROOTS for path in root.rglob("*") if path.is_file()]
    files.extend(path for path in ACTIVE_ROOT_FILES if path.is_file())
    return sorted(path for path in files if "__pycache__" not in path.parts)


def _read_tracked_text(path: Path) -> str | None:
    if path.is_symlink() or not path.is_file():
        return None
    data = path.read_bytes()
    if b"\0" in data:
        return None
    return data.decode("utf-8", errors="ignore")


def _matches_forbidden_runtime_pattern(value: str) -> bool:
    return any(pattern.search(value) for pattern in FORBIDDEN_RUNTIME_PATTERNS)


def _content_patterns(path: Path) -> tuple[re.Pattern[str], ...]:
    if path in THIRD_PARTY_PRODUCT_LABEL_FILES or path in EXTERNAL_RESOURCE_COMPATIBILITY_FILES:
        return FORMER_APP_PATTERNS[:3]
    return FORMER_APP_PATTERNS


def test_runtime_scope_is_filesystem_based_and_excludes_reference_oracles() -> None:
    files = set(_runtime_text_files())
    assert CONTRACT_FILE not in files
    assert ROOT / "travis" / "cli.py" in files
    assert ROOT / "packages" / "travis234-cli" / "bin" / "travis234.js" in files
    assert not any((ROOT / "pi") in path.parents for path in files)
    assert not any((ROOT / "hermes-agent") in path.parents for path in files)
    assert not any((ROOT / "appv231") in path.parents for path in files)


def test_cli_and_tui_use_the_travis234_product_name() -> None:
    cli_text = (ROOT / "travis" / "cli.py").read_text(encoding="utf-8")
    session_commands = (ROOT / "travis" / "tui" / "interactive_session_commands.py").read_text(
        encoding="utf-8"
    )

    assert "Run the Travis234 terminal coding agent" in cli_text
    assert '"Travis234 TUI\\n"' in session_commands
    assert "travis travis+travis" not in cli_text + session_commands


def test_runtime_patterns_cover_paths_without_generic_pi_false_positives() -> None:
    forbidden_paths = (
        ".github/workflows/appv231-release-image.yml",
        "Dockerfile.appv231.release",
        "commands/pi-agent",
        "env/appv23",
        "images/appv22/runtime",
        "scripts/install-appv231-sandbox.sh",
        "/pi-home/agent/sessions/",
        "/allthebest-home/agent/sessions/",
        "/appv231-home/agent/sessions/",
        "~/.agents/skills/",
        "~/.allthebest/agent/sessions/",
        "~/.appv231/agent/sessions/",
    )
    allowed_text = (
        "/travis-home/agent/sessions/",
        "~/.travis234/agent/sessions/",
        "src/math.py",
        "ratio = math.pi",
        "Raspberry Pi",
    )
    assert all(_matches_forbidden_runtime_pattern(path) for path in forbidden_paths)
    assert not any(_matches_forbidden_runtime_pattern(text) for text in allowed_text)


def test_state_patterns_cover_shell_and_composed_home_paths() -> None:
    legacy_paths = (
        "$HOME/.agents/skills/",
        "${HOME}/.agents/skills/",
        ".agents/skills/",
        'Path.home() / ".agents" / "skills"',
    )
    assert all(_matches_forbidden_runtime_pattern(path) for path in legacy_paths)


def test_vendored_highlighter_allows_language_pi_but_not_former_app_names() -> None:
    vendor = next(iter(THIRD_PARTY_PRODUCT_LABEL_FILES))
    patterns = _content_patterns(vendor)
    assert not any(pattern.search("builtins: pi, sin, cos") for pattern in patterns)
    assert any(pattern.search("appv231") for pattern in patterns)


def test_focused_repository_layout() -> None:
    assert (ROOT / "travis" / "__init__.py").is_file()
    assert not (ROOT / "appV2.3.1").exists()
    assert (ROOT / "appv231").is_dir()
    assert (ROOT / "PI_HERMES_TRAVIS_CROSS_CHECK_REPORT.md").is_file()


def test_runtime_text_has_no_former_product_labels() -> None:
    failures: list[str] = []
    for path in _runtime_text_files():
        relative_path = path.relative_to(ROOT).as_posix()
        for pattern in FORBIDDEN_RUNTIME_PATTERNS:
            if pattern.search(relative_path):
                failures.append(f"{relative_path} [path]: {pattern.pattern}")
        text = _read_tracked_text(path)
        if text is None:
            continue
        for pattern in _content_patterns(path):
            if pattern.search(text):
                failures.append(f"{relative_path} [content]: {pattern.pattern}")
    assert failures == []


def test_only_travis234_state_contract_is_documented() -> None:
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert "~/.travis234/agent/AGENTS.md" in readme
    assert "~/.travis234/agent/skills/" in readme
    assert "~/.travis234/agent/sessions/" in readme
    assert "/travis-home/agent/sessions/" in readme
    assert not any(pattern.search(readme) for pattern in FORBIDDEN_STATE_PATTERNS)
