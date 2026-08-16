from dataclasses import replace
from pathlib import Path
import sys

from travis.coding_agent.event_bus import create_event_bus
from travis.coding_agent.resource_extensions import (
    ExtensionLoadRequest,
    load_extension_runtime,
)


def extension_request(tmp_path: Path, *paths: Path) -> ExtensionLoadRequest:
    return ExtensionLoadRequest(
        cwd=str(tmp_path),
        event_bus=create_event_bus(),
        discovered_paths=tuple(str(path) for path in paths),
        additional_paths=(),
        factories=(),
        no_extensions=False,
        generation=1,
        apply_override=True,
        override=None,
    )


def test_extension_lease_disposes_only_after_last_release(tmp_path: Path) -> None:
    extension = tmp_path / "extensions" / "sample.py"
    extension.parent.mkdir()
    extension.write_text(
        "def extension(travis):\n"
        "    travis.register_command('sample', {'handler': lambda args, ctx: []})\n",
        encoding="utf-8",
    )
    lease = load_extension_runtime(extension_request(tmp_path, extension))
    retained = lease.retain()
    module_names = lease.module_names

    lease.release()
    assert retained.runtime.get_registered_command("sample") is not None
    assert all(name in sys.modules for name in module_names)
    retained.release()
    assert all(name not in sys.modules for name in module_names)


def test_bad_extension_is_diagnostic_while_valid_extension_loads(
    tmp_path: Path,
) -> None:
    root = tmp_path / "extensions"
    root.mkdir()
    (root / "bad.py").write_text(
        "raise RuntimeError('broken extension')\n", encoding="utf-8"
    )
    (root / "good.py").write_text(
        "def extension(travis):\n"
        "    travis.register_flag('safe', {'type': 'boolean'})\n",
        encoding="utf-8",
    )
    lease = load_extension_runtime(extension_request(tmp_path, root))
    try:
        assert "safe" in lease.runtime.get_flags()
        assert [Path(item["path"]).name for item in lease.result["errors"]] == [
            "bad.py"
        ]
        assert [
            Path(item["path"]).name for item in lease.result["extensions"]
        ] == ["good.py"]
    finally:
        lease.release()


def test_preloaded_runtime_does_not_reexecute_inline_factory(
    tmp_path: Path,
) -> None:
    calls: list[str] = []

    def factory(travis) -> None:
        calls.append("factory")
        travis.register_flag("profile", {"type": "string"})

    request = replace(
        extension_request(tmp_path), factories=(factory,), apply_override=False
    )
    preloaded = load_extension_runtime(request)
    adopted = load_extension_runtime(
        replace(request, generation=2, apply_override=True),
        preloaded=preloaded,
    )
    try:
        assert adopted is preloaded
        assert calls == ["factory"]
        assert "profile" in adopted.runtime.get_flags()
    finally:
        adopted.release()
