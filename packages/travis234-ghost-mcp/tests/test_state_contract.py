from __future__ import annotations

import pytest

from conftest import ASSET_ROOT, PACKAGE_ROOT, VENDOR_ROOT


@pytest.mark.parametrize(
    "forbidden",
    [
        ".ghost-os",
        "/opt/homebrew/share/ghost-os",
        "/usr/local/share/ghost-os",
        ".shadow/models",
        ".claude",
        "mcp.json",
    ],
)
def test_adapted_runtime_has_no_external_state_or_resource_fallback(
    forbidden: str,
) -> None:
    scanned = [
        *(VENDOR_ROOT / "Sources").rglob("*.swift"),
        *ASSET_ROOT.rglob("*.py"),
        ASSET_ROOT / "vision-sidecar" / "ghost-vision",
    ]
    offenders = [
        str(path.relative_to(PACKAGE_ROOT))
        for path in scanned
        if forbidden in path.read_text(encoding="utf-8")
    ]

    assert offenders == []


def test_vision_sidecar_has_one_fixed_model_path_and_no_override() -> None:
    server = (ASSET_ROOT / "vision-sidecar" / "server.py").read_text(
        encoding="utf-8"
    )

    assert 'Path.home() / ".travis234/ghost-mcp/models/ShowUI-2B"' in server
    assert "--model-path" not in server
