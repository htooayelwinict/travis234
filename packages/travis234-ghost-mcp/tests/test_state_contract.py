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
        *PACKAGE_ROOT.joinpath("travis234_ghost_mcp").glob("*.py"),
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


def test_permission_failures_use_shared_travis_setup_guidance() -> None:
    sources = {
        "Perception/Perception.swift": 3,
        "Perception/Annotate.swift": 2,
        "Vision/VisionPerception.swift": 2,
        "Learning/LearningTypes.swift": 2,
    }

    for relative_path, expected_uses in sources.items():
        source = (VENDOR_ROOT / "Sources/GhostOS" / relative_path).read_text(
            encoding="utf-8"
        )
        assert source.count("TravisPermissionGuidance.") == expected_uses
