from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from travis.coding_agent.package_manager import (
    DefaultPackageManager,
    parse_package_source,
)
from travis.coding_agent.settings_manager import SettingsManager


@pytest.mark.parametrize(
    ("source", "kind"),
    [
        ("./local-extension", "local"),
        ("git+https://example.test/repo.git@v1", "git"),
        ("travis-demo==1.2.0", "python"),
    ],
)
def test_package_source_kinds(source: str, kind: str, tmp_path: Path) -> None:
    parsed = parse_package_source(source, cwd=tmp_path)

    assert parsed.raw == source
    assert parsed.kind == kind
    if kind == "git":
        assert parsed.location == "https://example.test/repo.git"
        assert parsed.revision == "v1"


def test_project_package_mutations_require_resolved_trust(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    (source / "package.json").write_text(
        json.dumps({"name": "demo", "travis": {"extensions": []}}),
        encoding="utf-8",
    )
    manager = DefaultPackageManager(
        cwd=str(tmp_path / "repo"),
        agent_dir=str(tmp_path / "agent"),
        project_trusted=False,
    )

    with pytest.raises(RuntimeError, match="trusted"):
        manager.install(str(source), scope="project")
    with pytest.raises(RuntimeError, match="trusted"):
        manager.remove(str(source), scope="project")
    with pytest.raises(RuntimeError, match="trusted"):
        manager.update(scope="project")

    assert not (tmp_path / "repo" / ".travis234" / "packages").exists()


def test_local_install_is_atomic_persisted_and_resolved(tmp_path: Path) -> None:
    source = tmp_path / "source-package"
    skill = source / "skills" / "audit" / "SKILL.md"
    skill.parent.mkdir(parents=True)
    skill.write_text(
        "---\nname: audit\ndescription: Audit files\n---\nAudit carefully.",
        encoding="utf-8",
    )
    (source / "package.json").write_text(
        json.dumps(
            {
                "name": "audit-package",
                "version": "1.0.0",
                "travis": {"skills": ["skills/audit/SKILL.md"]},
            }
        ),
        encoding="utf-8",
    )
    settings = SettingsManager.in_memory()
    manager = DefaultPackageManager(
        cwd=str(tmp_path / "repo"),
        agent_dir=str(tmp_path / "agent"),
        settings_manager=settings,
        project_trusted=True,
    )

    installed = manager.install(str(source), scope="global")
    resolved = manager.resolve()

    assert Path(installed.install_path).is_dir()
    assert Path(installed.install_path) != source
    assert installed.version == "1.0.0"
    assert settings.global_settings["packages"] == [str(source)]
    assert [Path(resource.path).name for resource in resolved.skills] == ["SKILL.md"]
    assert resolved.skills[0].metadata["scope"] == "global"
    assert resolved.skills[0].metadata["origin"] == "package"
    assert manager.list_installed(scope="global") == [installed]
    assert not any(path.name.startswith(".tmp-") for path in Path(installed.install_path).parent.iterdir())


def test_failed_reinstall_preserves_previous_package(tmp_path: Path) -> None:
    source = tmp_path / "source-package"
    source.mkdir()
    manifest = source / "package.json"
    manifest.write_text(
        json.dumps({"name": "safe-package", "version": "1", "travis": {"extensions": []}}),
        encoding="utf-8",
    )
    manager = DefaultPackageManager(
        cwd=str(tmp_path / "repo"),
        agent_dir=str(tmp_path / "agent"),
        project_trusted=True,
    )
    installed = manager.install(str(source), scope="global")
    installed_manifest = Path(installed.install_path) / "package.json"

    manifest.write_text(
        json.dumps({"name": "safe-package", "version": "2", "travis": {"extensions": ["../escape.py"]}}),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="escapes package root"):
        manager.install(str(source), scope="global")

    assert json.loads(installed_manifest.read_text(encoding="utf-8"))["version"] == "1"


@pytest.mark.parametrize(
    ("source", "kind"),
    [
        ("git+https://example.test/repo.git", "git"),
        ("travis-demo==1.2.0", "python"),
    ],
)
def test_package_subprocesses_strip_runtime_credentials(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    source: str,
    kind: str,
) -> None:
    calls: list[tuple[list[str], dict[str, str]]] = []

    def fake_run(command, **kwargs):
        env = dict(kwargs["env"])
        calls.append((list(command), env))
        if kind == "git":
            destination = Path(command[-1])
        else:
            destination = Path(command[command.index("--target") + 1])
        destination.mkdir(parents=True, exist_ok=True)
        (destination / "package.json").write_text(
            json.dumps({"name": f"{kind}-package", "travis": {"extensions": []}}),
            encoding="utf-8",
        )
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr("travis.coding_agent.package_manager.subprocess.run", fake_run)
    monkeypatch.setenv("OPENAI_API_KEY", "provider-secret")
    monkeypatch.setenv("TRAVIS_COMPRESSION_API_KEY", "compression-secret")
    monkeypatch.setenv("GITHUB_TOKEN", "oauth-secret")
    monkeypatch.setenv("HTTPS_PROXY", "https://proxy.test")
    manager = DefaultPackageManager(
        cwd=str(tmp_path / "repo"),
        agent_dir=str(tmp_path / "agent"),
        project_trusted=True,
    )

    manager.install(
        source,
        scope="global",
        package_env={"PIP_INDEX_URL": "https://index-user:index-pass@example.test/simple"},
    )

    assert calls
    for _command, env in calls:
        assert "OPENAI_API_KEY" not in env
        assert "TRAVIS_COMPRESSION_API_KEY" not in env
        assert "GITHUB_TOKEN" not in env
        assert env["HTTPS_PROXY"] == "https://proxy.test"
        assert env["PIP_INDEX_URL"].startswith("https://index-user:")


def test_configured_missing_package_is_diagnostic_not_auto_install(tmp_path: Path) -> None:
    settings = SettingsManager.in_memory({"packages": ["travis-missing==9.9.9"]})
    manager = DefaultPackageManager(
        cwd=str(tmp_path / "repo"),
        agent_dir=str(tmp_path / "agent"),
        settings_manager=settings,
        project_trusted=True,
    )

    resolved = manager.resolve()

    assert resolved.extensions == []
    assert len(resolved.diagnostics) == 1
    assert "not installed" in resolved.diagnostics[0].message
    assert "travis-missing==9.9.9" in resolved.diagnostics[0].message
