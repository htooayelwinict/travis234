import ast
import io
from pathlib import Path
import tokenize


def _module_parts_for_path(path: Path) -> tuple[str, ...]:
    parts = path.resolve().with_suffix("").parts
    try:
        appv22_index = len(parts) - 1 - parts[::-1].index("appv22")
    except ValueError:
        return ()

    module_parts = parts[appv22_index:]
    if module_parts[-1] == "__init__":
        module_parts = module_parts[:-1]
    return module_parts


def _resolve_import_from_module(path: Path, node: ast.ImportFrom) -> str | None:
    module_parts = node.module.split(".") if node.module else []
    if node.level == 0:
        return node.module

    path_module_parts = _module_parts_for_path(path)
    if not path_module_parts:
        return node.module

    package_parts = path_module_parts[:-1]
    if node.level > len(package_parts) + 1:
        return node.module

    base_parts = package_parts[: len(package_parts) - node.level + 1]
    return ".".join((*base_parts, *module_parts))


def _imported_modules(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            module = _resolve_import_from_module(path, node)
            if module:
                modules.add(module)
                modules.update(
                    f"{module}.{alias.name}" for alias in node.names if alias.name != "*"
                )
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


def _string_literals(path: Path) -> list[str]:
    tokens = tokenize.generate_tokens(io.StringIO(path.read_text(encoding="utf-8")).readline)
    literals: list[str] = []
    for token in tokens:
        if token.type != tokenize.STRING:
            continue
        try:
            value = ast.literal_eval(token.string)
        except (SyntaxError, ValueError):
            continue
        if isinstance(value, str):
            literals.append(value)
    return literals


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


def test_imported_modules_detects_absolute_extension_package_aliases(tmp_path):
    path = tmp_path / "probe.py"
    path.write_text(
        "\n".join(
            [
                "from appv22.extensions import file_management",
                "from appv22.extensions import base",
                "from extensions import file_management",
                "from extensions import registry",
            ]
        ),
        encoding="utf-8",
    )

    modules = _imported_modules(path)

    assert "appv22.extensions.file_management" in modules
    assert "extensions.file_management" in modules
    assert "appv22.extensions.base" in modules
    assert "extensions.registry" in modules


def test_imported_modules_detects_relative_extension_package_aliases(tmp_path):
    path = tmp_path / "appV2.2/appv22/runtime/probe.py"
    path.parent.mkdir(parents=True)
    path.write_text(
        "\n".join(
            [
                "from ..extensions import file_management",
                "from ..extensions import registry",
            ]
        ),
        encoding="utf-8",
    )

    modules = _imported_modules(path)

    assert "appv22.extensions.file_management" in modules
    assert "appv22.extensions.registry" in modules


def test_runtime_core_does_not_import_file_management_extensions():
    appv22_root = Path(__file__).resolve().parents[2] / "appV2.2/appv22"
    scanned_files = [
        *appv22_root.joinpath("runtime").rglob("*.py"),
        *(appv22_root.joinpath("core").rglob("*.py") if appv22_root.joinpath("core").exists() else []),
        appv22_root / "extensions/base.py",
        appv22_root / "extensions/registry.py",
    ]
    for path in scanned_files:
        for module in _imported_modules(path):
            assert not _is_file_management_extension_import(module)


def test_generic_providers_do_not_reference_file_management_domain():
    providers_root = Path(__file__).resolve().parents[2] / "appV2.2/appv22/providers"
    for path in providers_root.rglob("*.py"):
        for module in _imported_modules(path):
            assert not _is_file_management_extension_import(module)
        for literal in _string_literals(path):
            assert "appv22.extensions.file_management" not in literal
            assert "extensions.file_management" not in literal
            assert "file_management." not in literal
