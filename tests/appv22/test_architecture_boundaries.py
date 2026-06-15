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


def test_runtime_core_does_not_import_extensions_package():
    runtime_files = (Path(__file__).resolve().parents[2] / "appV2.2/appv22/runtime").rglob(
        "*.py"
    )
    for path in runtime_files:
        for module in _imported_modules(path):
            assert module != "appv22.extensions"
            assert not module.startswith("appv22.extensions.")
