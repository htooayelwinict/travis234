from __future__ import annotations

import re
from pathlib import Path


ROOT = Path(__file__).parents[1]
ALLOWED_ATTRIBUTION_FILES = {ROOT / "LICENSE", ROOT / "NOTICE.md"}
SKIPPED_TREES = {ROOT / ".git", ROOT / ".worktrees", ROOT / "docs" / "superpowers"}
FORBIDDEN_RUNTIME_PATTERNS = (
    re.compile(r"appv(?:2|21|22|23|231)", re.IGNORECASE),
    re.compile(r"\bpi(?:-style)?\b", re.IGNORECASE),
    re.compile(r"\bhermes(?:-style| agent)?\b", re.IGNORECASE),
    re.compile(r"(?:^|/)\.pi(?:/|$)", re.IGNORECASE),
)


def _runtime_text_files() -> list[Path]:
    suffixes = {".py", ".js", ".json", ".md", ".toml", ".yml", ".yaml"}
    files: list[Path] = []
    for path in ROOT.rglob("*"):
        if not path.is_file() or path.suffix.lower() not in suffixes:
            continue
        if path in ALLOWED_ATTRIBUTION_FILES:
            continue
        if any(tree == path or tree in path.parents for tree in SKIPPED_TREES):
            continue
        files.append(path)
    return files


def test_focused_repository_layout() -> None:
    assert (ROOT / "travis" / "__init__.py").is_file()
    assert not (ROOT / "appV2.3.1").exists()
    assert not (ROOT / "appv231").exists()


def test_runtime_text_has_no_former_product_labels() -> None:
    failures: list[str] = []
    for path in _runtime_text_files():
        text = path.read_text(encoding="utf-8", errors="ignore")
        for pattern in FORBIDDEN_RUNTIME_PATTERNS:
            if pattern.search(text):
                failures.append(f"{path.relative_to(ROOT)}: {pattern.pattern}")
    assert failures == []


def test_only_travis234_state_contract_is_documented() -> None:
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert "~/.travis234/agent/AGENTS.md" in readme
    assert "~/.travis234/agent/skills/" in readme
    assert "~/.travis234/agent/sessions/" in readme
    assert "/travis-home/agent/sessions/" in readme
