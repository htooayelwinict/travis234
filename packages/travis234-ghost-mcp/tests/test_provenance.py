from __future__ import annotations

import json
import subprocess

from conftest import ASSET_ROOT, PACKAGE_ROOT, VENDOR_ROOT


def test_pinned_ghost_snapshot_is_auditable() -> None:
    upstream = json.loads(
        (PACKAGE_ROOT / "UPSTREAM.json").read_text(encoding="utf-8")
    )

    assert upstream == {
        "repository": "https://github.com/ghostwright/ghost-os",
        "commit": "991aa4831295aaff6beef04cc809d0f0b53dc024",
        "version": "2.2.1+6",
        "license": "MIT",
        "adaptations": [
            "Travis234-only state root",
            "package-relative resources",
            "non-interactive Travis setup and doctor",
            "no external MCP client configuration",
        ],
    }
    assert (PACKAGE_ROOT / "LICENSE").read_text(encoding="utf-8").startswith(
        "MIT License\n\nCopyright (c) 2026 Ghostwright"
    )
    assert (VENDOR_ROOT / "Package.swift").is_file()
    assert (VENDOR_ROOT / "Package.resolved").is_file()
    assert len(list((VENDOR_ROOT / "Sources").rglob("*.swift"))) == 26
    assert len(list((VENDOR_ROOT / "Tests").rglob("*.swift"))) == 1
    assert not (VENDOR_ROOT / ".git").exists()
    ignored_build = subprocess.run(
        ["git", "check-ignore", "-q", str(VENDOR_ROOT / ".build" / "probe")],
        cwd=PACKAGE_ROOT.parent.parent,
        check=False,
    )
    assert ignored_build.returncode == 0
    assert not list(VENDOR_ROOT.rglob("*.tar.gz"))


def test_pinned_runtime_assets_are_complete_and_narrow() -> None:
    assert (ASSET_ROOT / "GHOST-MCP.md").is_file()
    assert sorted(path.name for path in (ASSET_ROOT / "recipes").glob("*.json")) == [
        "arxiv-download.json",
        "finder-create-folder.json",
        "gmail-send.json",
        "slack-send.json",
    ]
    assert sorted(
        path.name for path in (ASSET_ROOT / "vision-sidecar").iterdir()
    ) == ["ghost-vision", "requirements.txt", "server.py"]


def test_third_party_notices_cover_resolved_dependencies() -> None:
    notices = (PACKAGE_ROOT / "THIRD_PARTY_NOTICES.md").read_text(
        encoding="utf-8"
    )

    for name in ("Ghost OS", "AXorcist", "Commander", "swift-log"):
        assert name in notices
    assert "MIT License" in notices
    assert "Apache License" in notices
    assert "Swift Logging API package" in notices
    assert (PACKAGE_ROOT / "licenses" / "AXorcist-LICENSE").read_text(
        encoding="utf-8"
    ).startswith("MIT License")
    assert (PACKAGE_ROOT / "licenses" / "Commander-LICENSE").read_text(
        encoding="utf-8"
    ).startswith("MIT License")
    assert (PACKAGE_ROOT / "licenses" / "swift-log-LICENSE.txt").read_text(
        encoding="utf-8"
    ).lstrip().startswith("Apache License")
