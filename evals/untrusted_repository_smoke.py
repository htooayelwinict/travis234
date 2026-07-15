"""Offline smoke proving that unknown project resources fail closed."""

from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
import json
from pathlib import Path
import tempfile

from travis.ai.providers.faux import create_faux_provider, faux_model, text_response_events
from travis.app import CodingApp
from travis.coding_agent.model_registry import ModelRegistry
from travis.coding_agent.project_trust import ProjectTrustContext
from travis.coding_agent.settings_manager import SettingsManager


@dataclass(frozen=True)
class UntrustedRepositorySmokeResult:
    exit_code: int
    project_trusted: bool
    extension_executed: bool
    global_extension_loaded: bool
    session_completed: bool


def run_untrusted_repository_smoke(root: str | Path) -> UntrustedRepositorySmokeResult:
    root_path = Path(root).expanduser().resolve()
    workspace = root_path / "unknown-project"
    agent_dir = root_path / "agent"
    config_dir = workspace / ".travis234"
    project_sentinel = root_path / "project-extension-executed"
    global_sentinel = root_path / "global-extension-loaded"
    (config_dir / "extensions").mkdir(parents=True, exist_ok=True)
    (config_dir / "skills" / "demo").mkdir(parents=True, exist_ok=True)
    (config_dir / "prompts").mkdir(parents=True, exist_ok=True)
    (config_dir / "themes").mkdir(parents=True, exist_ok=True)
    (agent_dir / "extensions").mkdir(parents=True, exist_ok=True)

    (config_dir / "settings.json").write_text(
        json.dumps({"extensions": [str(config_dir / "extensions")]}),
        encoding="utf-8",
    )
    (config_dir / "extensions" / "unsafe.py").write_text(
        "from pathlib import Path\n"
        f"Path({str(project_sentinel)!r}).write_text('executed', encoding='utf-8')\n"
        "def extension(travis):\n"
        "    return None\n",
        encoding="utf-8",
    )
    (config_dir / "skills" / "demo" / "SKILL.md").write_text(
        "---\nname: demo\ndescription: untrusted smoke skill\n---\nDo not load.\n",
        encoding="utf-8",
    )
    (config_dir / "prompts" / "demo.md").write_text(
        "---\ndescription: untrusted smoke prompt\n---\nDo not load.\n",
        encoding="utf-8",
    )
    (config_dir / "themes" / "demo.json").write_text(
        json.dumps({"name": "demo", "colors": {}, "vars": {}}),
        encoding="utf-8",
    )
    (config_dir / "SYSTEM.md").write_text("untrusted system prompt", encoding="utf-8")
    (config_dir / "APPEND_SYSTEM.md").write_text("untrusted append prompt", encoding="utf-8")
    (agent_dir / "extensions" / "safe.py").write_text(
        "from pathlib import Path\n"
        f"Path({str(global_sentinel)!r}).write_text('loaded', encoding='utf-8')\n"
        "def extension(travis):\n"
        "    return None\n",
        encoding="utf-8",
    )

    model = faux_model()
    registry = ModelRegistry.in_memory()
    registry.runtime.clear_providers()
    registry.runtime.set_provider(
        create_faux_provider(lambda active_model, _context: text_response_events(active_model, "smoke complete"))
    )
    settings = SettingsManager.create(str(workspace), str(agent_dir))
    app: CodingApp | None = None
    exit_code = 0
    session_completed = False
    project_trusted = False
    try:
        app = CodingApp(
            cwd=str(workspace),
            model=model,
            enable_tui=False,
            agent_dir=str(agent_dir),
            model_registry=registry,
            settings_manager=settings,
            project_trust_context=ProjectTrustContext(False, None),
        )
        app.run_turn("reply with smoke complete")
        project_trusted = app.session.resource_loader.project_trusted
        session_completed = any(
            getattr(message, "role", None) == "assistant" and "smoke complete" in str(message.content)
            for message in app.messages
        )
    except Exception:  # noqa: BLE001 - smoke converts failures into deterministic evidence.
        exit_code = 1
    finally:
        if app is not None:
            app.close()

    return UntrustedRepositorySmokeResult(
        exit_code=exit_code,
        project_trusted=project_trusted,
        extension_executed=project_sentinel.exists(),
        global_extension_loaded=global_sentinel.exists(),
        session_completed=session_completed,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", type=Path)
    args = parser.parse_args(argv)
    if args.workspace is not None:
        result = run_untrusted_repository_smoke(args.workspace)
    else:
        with tempfile.TemporaryDirectory(prefix="travis234-untrusted-smoke-") as temporary:
            result = run_untrusted_repository_smoke(temporary)
    print(json.dumps(asdict(result), sort_keys=True))
    return result.exit_code


if __name__ == "__main__":
    raise SystemExit(main())
