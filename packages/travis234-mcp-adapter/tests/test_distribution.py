from __future__ import annotations

import subprocess
import zipfile
from email.parser import Parser
from pathlib import Path

from travis.coding_agent.package_manager import DefaultPackageManager
from travis.coding_agent.resource_loader import DefaultResourceLoader
from travis.coding_agent.settings_manager import SettingsManager

PACKAGE_ROOT = Path(__file__).resolve().parents[1]


def test_readme_supported_surface_includes_resources_prompts_and_reconnect() -> None:
    readme = (PACKAGE_ROOT / "README.md").read_text(encoding="utf-8")

    assert "| MCP resources and prompts | Supported with bounds and untrusted-data labeling |" in readme
    assert "| Bounded explicit and automatic reconnect | Supported without request replay |" in readme
    assert "| MCP prompts and resource discovery | Not supported |" not in readme


def _build_adapter_wheel(output_dir: Path) -> Path:
    output_dir.mkdir(parents=True)
    subprocess.run(
        [
            "uv",
            "build",
            "--wheel",
            "--clear",
            "-o",
            str(output_dir),
            str(PACKAGE_ROOT),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    wheels = list(output_dir.glob("*.whl"))
    assert len(wheels) == 1
    return wheels[0]


def test_built_wheel_installs_and_loads_through_travis(
    tmp_path: Path,
    monkeypatch,
) -> None:
    wheel = _build_adapter_wheel(tmp_path / "dist")
    with zipfile.ZipFile(wheel) as archive:
        names = archive.namelist()
        metadata_name = next(name for name in names if name.endswith(".dist-info/METADATA"))
        metadata = Parser().parsestr(archive.read(metadata_name).decode("utf-8"))
    assert metadata["Name"] == "travis234-mcp-adapter"
    assert metadata["Version"] == "0.2.0"
    requirements = metadata.get_all("Requires-Dist", [])
    assert "mcp<3,>=2" in requirements
    assert not any("travis234-ghost-mcp" in item for item in requirements)
    assert any(name.endswith("/extensions/mcp_adapter.py") for name in names)
    assert "travis234_mcp_adapter/packaged_servers.py" in names
    monkeypatch.setattr(
        "travis.coding_agent.package_manager.importlib.util.find_spec",
        lambda name: None if name == "pip" else __import__(name).__spec__,
    )
    settings = SettingsManager.in_memory()
    manager = DefaultPackageManager(
        cwd=str(tmp_path / "repo"),
        agent_dir=str(tmp_path / "agent"),
        settings_manager=settings,
        project_trusted=True,
    )

    installed = manager.install(
        f"travis234-mcp-adapter @ {wheel.as_uri()}",
        scope="global",
    )
    resolved = manager.resolve()

    assert Path(installed.install_path).is_dir()
    assert [Path(item.path).name for item in resolved.extensions] == ["mcp_adapter.py"]

    loader = DefaultResourceLoader(
        cwd=str(tmp_path / "repo"),
        agent_dir=str(tmp_path / "agent"),
        settings_manager=settings,
        project_trusted=True,
    )
    loader.reload()
    runtime = loader.get_extensions()["runtime"]
    assert [item.definition.name for item in runtime.get_all_registered_tools()] == ["mcp"]
