from __future__ import annotations

import hashlib
import importlib.util
import os
from pathlib import Path
import subprocess
import sys

import pytest


ROOT = Path(__file__).parents[1]
HELPER = ROOT / "travis/resources/skills/orchestration/scripts/orchestrate.py"


def load_helper():
    spec = importlib.util.spec_from_file_location("travis234_orchestration_worktrees", HELPER)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def git(repo: Path, *arguments: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(repo), *arguments],
        check=check,
        capture_output=True,
        text=True,
        timeout=10,
    )


def initialize_repository(path: Path) -> Path:
    path.mkdir()
    git(path, "init", "-b", "main")
    git(path, "config", "user.name", "Travis234 Worktree Test")
    git(path, "config", "user.email", "worktree-test@travis234.invalid")
    (path / "README.md").write_text("initial\n", encoding="utf-8")
    git(path, "add", "README.md")
    git(path, "commit", "-m", "initial")
    return path


def registered_worktrees(repo: Path) -> str:
    return git(repo, "worktree", "list", "--porcelain").stdout


def test_repository_local_ignored_worktree_preserves_dirty_coordinator(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = load_helper()
    repo = initialize_repository(tmp_path / "repo")
    (repo / ".gitignore").write_text(".worktrees/\n", encoding="utf-8")
    git(repo, "add", ".gitignore")
    git(repo, "commit", "-m", "ignore local worktrees")
    original_ignore = (repo / ".gitignore").read_bytes()
    (repo / "README.md").write_text("dirty coordinator\n", encoding="utf-8")
    original_status = git(repo, "status", "--porcelain=v1", "--untracked-files=normal").stdout
    base_commit = git(repo, "rev-parse", "HEAD").stdout.strip()
    agent_dir = tmp_path / "agent"
    monkeypatch.setenv("TRAVIS234_CODING_AGENT_DIR", str(agent_dir))

    inspection = module.inspect_repository(repo, "HEAD")
    receipt = module.prepare_workspace(
        module.WorktreeRequest(
            repository=repo,
            workspace_mode="worktree",
            worktree_name="research-one",
            branch="research-one",
            base="HEAD",
        )
    )

    target = repo / ".worktrees" / "research-one"
    assert inspection.repository == repo.resolve()
    assert inspection.base_commit == base_commit
    assert inspection.dirty is True
    assert receipt == {
        "workspaceMode": "worktree",
        "repository": str(repo.resolve()),
        "workspace": str(target.resolve()),
        "worktree": str(target.resolve()),
        "branch": "research-one",
        "baseCommit": base_commit,
        "dirty": True,
        "uncommittedChangesTransferred": False,
        "automaticIntegration": False,
    }
    assert f"worktree {target.resolve()}" in registered_worktrees(repo)
    assert git(target, "rev-parse", "HEAD").stdout.strip() == base_commit
    assert git(repo, "status", "--porcelain=v1", "--untracked-files=normal").stdout == original_status
    assert (repo / ".gitignore").read_bytes() == original_ignore


@pytest.mark.parametrize("global_ignore", [False, True])
def test_untrusted_or_global_ignore_uses_private_agent_fallback_without_editing_repo(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    global_ignore: bool,
) -> None:
    module = load_helper()
    repo = initialize_repository(tmp_path / "repo")
    original_ignore_exists = (repo / ".gitignore").exists()
    if global_ignore:
        excludes = tmp_path / "global-ignore"
        excludes.write_text(".worktrees/\n", encoding="utf-8")
        git(repo, "config", "core.excludesFile", str(excludes))
    agent_dir = tmp_path / "agent"
    monkeypatch.setenv("TRAVIS234_CODING_AGENT_DIR", str(agent_dir))

    receipt = module.prepare_workspace(
        module.WorktreeRequest(
            repository=repo,
            workspace_mode="worktree",
            worktree_name="fallback-one",
            branch="fallback-one",
            base="main",
        )
    )

    repository_key = hashlib.sha256(str(repo.resolve()).encode("utf-8")).hexdigest()[:16]
    target = agent_dir / "orchestration" / "worktrees" / repository_key / "fallback-one"
    assert receipt["worktree"] == str(target.resolve())
    assert f"worktree {target.resolve()}" in registered_worktrees(repo)
    assert (repo / ".gitignore").exists() is original_ignore_exists


def test_current_workspace_selection_performs_no_git_mutation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = load_helper()
    repo = initialize_repository(tmp_path / "repo")
    (repo / "README.md").write_text("visible dirty state\n", encoding="utf-8")
    agent_dir = tmp_path / "agent"
    monkeypatch.setenv("TRAVIS234_CODING_AGENT_DIR", str(agent_dir))
    status_before = git(repo, "status", "--porcelain=v1", "--untracked-files=normal").stdout
    branches_before = git(repo, "branch", "--format=%(refname)").stdout
    worktrees_before = registered_worktrees(repo)
    head_before = git(repo, "rev-parse", "HEAD").stdout.strip()
    index_before = (repo / ".git" / "index").read_bytes()

    receipt = module.prepare_workspace(
        module.WorktreeRequest(repository=repo, workspace_mode="current")
    )

    assert receipt["workspaceMode"] == "current"
    assert receipt["workspace"] == str(repo.resolve())
    assert receipt["worktree"] is None
    assert receipt["baseCommit"] == head_before
    assert receipt["dirty"] is True
    assert receipt["uncommittedChangesTransferred"] is True
    assert git(repo, "status", "--porcelain=v1", "--untracked-files=normal").stdout == status_before
    assert git(repo, "branch", "--format=%(refname)").stdout == branches_before
    assert registered_worktrees(repo) == worktrees_before
    assert git(repo, "rev-parse", "HEAD").stdout.strip() == head_before
    assert (repo / ".git" / "index").read_bytes() == index_before


@pytest.mark.parametrize(
    ("field", "value", "expected_code"),
    [
        ("worktree_name", "../escape", "invalid_workspace"),
        ("branch", "bad branch", "invalid_workspace"),
        ("base", "missing-base", "invalid_base"),
    ],
)
def test_invalid_worktree_inputs_fail_before_mutation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    field: str,
    value: str,
    expected_code: str,
) -> None:
    module = load_helper()
    repo = initialize_repository(tmp_path / "repo")
    agent_dir = tmp_path / "agent"
    monkeypatch.setenv("TRAVIS234_CODING_AGENT_DIR", str(agent_dir))
    values = {
        "repository": repo,
        "workspace_mode": "worktree",
        "worktree_name": "valid-name",
        "branch": "valid-branch",
        "base": "main",
    }
    values[field] = value
    before = registered_worktrees(repo)

    with pytest.raises(module.HelperError) as raised:
        module.prepare_workspace(module.WorktreeRequest(**values))

    assert raised.value.code == expected_code
    assert registered_worktrees(repo) == before
    assert git(repo, "show-ref", "--verify", "--quiet", "refs/heads/valid-branch", check=False).returncode == 1


def test_existing_branch_occupied_path_and_registered_worktree_fail_before_add(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = load_helper()
    repo = initialize_repository(tmp_path / "repo")
    (repo / ".gitignore").write_text(".worktrees/\n", encoding="utf-8")
    git(repo, "add", ".gitignore")
    git(repo, "commit", "-m", "ignore worktrees")
    agent_dir = tmp_path / "agent"
    monkeypatch.setenv("TRAVIS234_CODING_AGENT_DIR", str(agent_dir))
    git(repo, "branch", "already-exists")

    with pytest.raises(module.HelperError, match="conflict") as branch_error:
        module.prepare_workspace(
            module.WorktreeRequest(
                repository=repo,
                workspace_mode="worktree",
                worktree_name="branch-conflict",
                branch="already-exists",
                base="main",
            )
        )
    assert branch_error.value.code == "workspace_conflict"

    occupied = repo / ".worktrees" / "occupied"
    occupied.mkdir(parents=True)
    with pytest.raises(module.HelperError) as path_error:
        module.prepare_workspace(
            module.WorktreeRequest(
                repository=repo,
                workspace_mode="worktree",
                worktree_name="occupied",
                branch="occupied-branch",
                base="main",
            )
        )
    assert path_error.value.code == "workspace_conflict"
    assert git(repo, "show-ref", "--verify", "--quiet", "refs/heads/occupied-branch", check=False).returncode == 1

    registered = repo / ".worktrees" / "registered"
    git(repo, "worktree", "add", "-b", "registered-branch", str(registered), "main")
    before = registered_worktrees(repo)
    with pytest.raises(module.HelperError) as registration_error:
        module.prepare_workspace(
            module.WorktreeRequest(
                repository=repo,
                workspace_mode="worktree",
                worktree_name="registered",
                branch="different-branch",
                base="main",
            )
        )
    assert registration_error.value.code == "workspace_conflict"
    assert registered_worktrees(repo) == before


def test_non_repository_and_detached_conflicting_current_base_fail_without_mutation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = load_helper()
    agent_dir = tmp_path / "agent"
    monkeypatch.setenv("TRAVIS234_CODING_AGENT_DIR", str(agent_dir))
    not_repo = tmp_path / "not-repo"
    not_repo.mkdir()
    with pytest.raises(module.HelperError) as repository_error:
        module.inspect_repository(not_repo, "HEAD")
    assert repository_error.value.code == "not_repository"

    repo = initialize_repository(tmp_path / "repo")
    first_commit = git(repo, "rev-parse", "HEAD").stdout.strip()
    (repo / "second.txt").write_text("second\n", encoding="utf-8")
    git(repo, "add", "second.txt")
    git(repo, "commit", "-m", "second")
    git(repo, "checkout", "--detach", first_commit)
    before = registered_worktrees(repo)
    with pytest.raises(module.HelperError) as detached_error:
        module.prepare_workspace(
            module.WorktreeRequest(
                repository=repo,
                workspace_mode="current",
                base="main",
            )
        )
    assert detached_error.value.code == "workspace_conflict"
    assert registered_worktrees(repo) == before
    assert git(repo, "rev-parse", "HEAD").stdout.strip() == first_commit


def test_partial_git_failure_reports_outcome_unknown_without_cleanup(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = load_helper()
    repo = initialize_repository(tmp_path / "repo")
    agent_dir = tmp_path / "agent"
    monkeypatch.setenv("TRAVIS234_CODING_AGENT_DIR", str(agent_dir))
    real_git = module.git

    def uncertain_git(repository, *arguments, check=True):
        if arguments[:2] == ("worktree", "add"):
            real_git(repository, "branch", "uncertain-branch", "main")
            raise subprocess.CalledProcessError(1, ["git", "worktree", "add"])
        return real_git(repository, *arguments, check=check)

    monkeypatch.setattr(module, "git", uncertain_git)
    with pytest.raises(module.HelperError) as raised:
        module.prepare_workspace(
            module.WorktreeRequest(
                repository=repo,
                workspace_mode="worktree",
                worktree_name="uncertain",
                branch="uncertain-branch",
                base="main",
            )
        )

    assert raised.value.code == "outcome_unknown"
    assert any("git worktree list --porcelain" in action for action in raised.value.next_actions)
    assert any("git show-ref" in action for action in raised.value.next_actions)
    assert git(repo, "show-ref", "--verify", "--quiet", "refs/heads/uncertain-branch").returncode == 0


def test_repository_local_worktree_parent_symlink_uses_fallback_without_escape(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = load_helper()
    repo = initialize_repository(tmp_path / "repo")
    (repo / ".gitignore").write_text(".worktrees/\n", encoding="utf-8")
    git(repo, "add", ".gitignore")
    git(repo, "commit", "-m", "ignore worktrees")
    outside = tmp_path / "outside"
    outside.mkdir()
    (repo / ".worktrees").symlink_to(outside, target_is_directory=True)
    monkeypatch.setenv("TRAVIS234_CODING_AGENT_DIR", str(tmp_path / "agent"))

    receipt = module.prepare_workspace(
        module.WorktreeRequest(
            repository=repo,
            workspace_mode="worktree",
            worktree_name="must-not-escape",
            branch="must-not-escape",
            base="main",
        )
    )

    assert str(tmp_path / "agent" / "orchestration" / "worktrees") in receipt["worktree"]
    assert not (outside / "must-not-escape").exists()
    assert git(
        repo,
        "show-ref",
        "--verify",
        "--quiet",
        "refs/heads/must-not-escape",
        check=False,
    ).returncode == 0
