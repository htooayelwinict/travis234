import ast
from pathlib import Path


def _imported_modules(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.add(node.module)
    return modules


def _is_file_management_extension_import(module: str) -> bool:
    return module in {
        "appv22.extensions.file_management",
        "extensions.file_management",
    } or module.startswith(
        (
            "appv22.extensions.file_management.",
            "extensions.file_management.",
        )
    )


def test_runtime_extension_boundary_allows_generic_extension_contracts():
    assert not _is_file_management_extension_import("appv22.extensions")
    assert not _is_file_management_extension_import("appv22.extensions.base")
    assert not _is_file_management_extension_import("appv22.extensions.registry")
    assert not _is_file_management_extension_import("extensions")
    assert not _is_file_management_extension_import("extensions.base")
    assert not _is_file_management_extension_import("extensions.registry")
    assert _is_file_management_extension_import("appv22.extensions.file_management")
    assert _is_file_management_extension_import("appv22.extensions.file_management.tools")
    assert _is_file_management_extension_import("extensions.file_management")
    assert _is_file_management_extension_import("extensions.file_management.tools")


def test_runtime_core_does_not_import_file_management_extensions():
    appv22_root = Path(__file__).resolve().parents[2] / "appV2.2/appv22"
    scanned_files = [
        *appv22_root.joinpath("runtime").rglob("*.py"),
        appv22_root / "extensions/base.py",
        appv22_root / "extensions/registry.py",
    ]
    for path in scanned_files:
        for module in _imported_modules(path):
            assert not _is_file_management_extension_import(module)
