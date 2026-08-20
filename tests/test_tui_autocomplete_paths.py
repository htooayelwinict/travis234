from __future__ import annotations

from pathlib import Path

from travis.tui.components.autocomplete import CombinedAutocompleteProvider


def test_file_suggestions_preserve_relative_and_absolute_prefix_shapes(tmp_path: Path) -> None:
    source = tmp_path / "src"
    source.mkdir()
    (source / "app.py").write_text("pass\n", encoding="utf-8")
    provider = CombinedAutocompleteProvider(base_path=str(tmp_path))

    assert provider._get_file_suggestions("./s") == [
        {"value": "./src/", "label": "src/"}
    ]
    assert provider._get_file_suggestions("src/a") == [
        {"value": "src/app.py", "label": "app.py"}
    ]
    assert provider._get_file_suggestions(f"{tmp_path}/s") == [
        {"value": f"{tmp_path}/src/", "label": "src/"}
    ]
    assert provider._get_file_suggestions("missing/path/") == []
