from __future__ import annotations

from types import MappingProxyType
import json
import ast
from pathlib import Path
import threading
import time

import pytest

from travis.tui.builtin_themes import BUILTIN_THEMES, resolve_builtin_theme
from travis.tui.theme import REQUIRED_THEME_ROLES, ThemeContext, resolve_theme
from travis.tui.theme_controller import ThemeController
from travis.coding_agent.source_info import SourceInfo
from travis.coding_agent.settings_manager import SettingsManager
from travis.coding_agent.themes import Theme, ThemeRegistry
from tests._support_tui import CodingApp, FakeTerminal, InteractiveMode, faux_model


def _theme_record(name: str, *, source_path: str = "") -> Theme:
    definition = BUILTIN_THEMES[name]
    return Theme(
        name=name,
        colors=dict(definition["colors"]),
        vars=dict(definition["vars"]),
        source_path=source_path,
        source_info=SourceInfo(
            path=source_path or f"builtin:{name}",
            source="travis234-builtin" if not source_path else "test",
        ),
    )


def test_required_theme_surface_matches_pi_semantic_roles() -> None:
    assert len(REQUIRED_THEME_ROLES) == 51
    assert len(set(REQUIRED_THEME_ROLES)) == 51
    assert "thinkingMax" not in REQUIRED_THEME_ROLES
    assert {
        "accent",
        "thinkingText",
        "selectedBg",
        "userMessageBg",
        "toolPendingBg",
        "mdCodeBlockBorder",
        "toolDiffAdded",
        "syntaxKeyword",
        "thinkingXhigh",
        "bashMode",
    } <= set(REQUIRED_THEME_ROLES)


def test_all_six_builtin_themes_resolve_every_semantic_role() -> None:
    assert tuple(BUILTIN_THEMES) == (
        "Signal Glass",
        "Black Ice",
        "Neon Oni",
        "Blood Circuit",
        "Reactor Gold",
        "Polar Ghost",
    )
    for name, definition in BUILTIN_THEMES.items():
        theme, diagnostics = resolve_theme(
            name,
            definition["colors"],
            definition.get("vars", {}),
            color_mode="truecolor",
            fallback=None,
        )
        assert diagnostics == ()
        assert set(theme.colors) == set(REQUIRED_THEME_ROLES)
        assert isinstance(theme.colors, MappingProxyType)
        assert isinstance(theme.foreground_ansi, MappingProxyType)
        assert isinstance(theme.background_ansi, MappingProxyType)


def test_partial_theme_inherits_and_invalid_role_falls_back() -> None:
    fallback, _ = resolve_builtin_theme("Signal Glass", color_mode="truecolor")
    theme, diagnostics = resolve_theme(
        "partial",
        {"accent": "#ff00ff", "error": "not-a-color"},
        {},
        color_mode="truecolor",
        fallback=fallback,
    )

    assert theme.colors["accent"] == "#ff00ff"
    assert theme.colors["accent"] != fallback.colors["accent"]
    assert theme.colors["error"] == fallback.colors["error"]
    assert any(item.role == "error" for item in diagnostics)


def test_theme_variables_resolve_chains_and_report_missing_references_and_cycles() -> None:
    fallback, _ = resolve_builtin_theme("Signal Glass", color_mode="truecolor")
    theme, diagnostics = resolve_theme(
        "variables",
        {
            "accent": "brand",
            "success": "missing",
            "warning": "cycle_a",
        },
        {
            "brand": "brand_base",
            "brand_base": "#123456",
            "cycle_a": "cycle_b",
            "cycle_b": "cycle_a",
        },
        color_mode="truecolor",
        fallback=fallback,
    )

    assert theme.colors["accent"] == "#123456"
    assert theme.colors["success"] == fallback.colors["success"]
    assert theme.colors["warning"] == fallback.colors["warning"]
    assert {item.code for item in diagnostics} >= {"missing-variable", "variable-cycle"}


@pytest.mark.parametrize("invalid", ["#12345", "#gg0000", -1, 256, 1.5, True, None, [], {}])
def test_invalid_color_values_fall_back_locally(invalid: object) -> None:
    fallback, _ = resolve_builtin_theme("Signal Glass", color_mode="truecolor")
    theme, diagnostics = resolve_theme(
        "invalid",
        {"accent": invalid},
        {},
        color_mode="truecolor",
        fallback=fallback,
    )

    assert theme.colors["accent"] == fallback.colors["accent"]
    assert any(item.role == "accent" for item in diagnostics)


def test_theme_color_modes_generate_truecolor_256_and_plain_output() -> None:
    truecolor, _ = resolve_theme(
        "true",
        {role: "#336699" for role in REQUIRED_THEME_ROLES},
        {},
        color_mode="truecolor",
        fallback=None,
    )
    indexed, _ = resolve_theme(
        "indexed",
        {role: "#336699" for role in REQUIRED_THEME_ROLES},
        {},
        color_mode="256color",
        fallback=None,
    )
    plain, _ = resolve_theme(
        "plain",
        {role: "#336699" for role in REQUIRED_THEME_ROLES},
        {},
        color_mode="none",
        fallback=None,
    )

    assert truecolor.fg("accent", "x") == "\x1b[38;2;51;102;153mx\x1b[39m"
    assert truecolor.bg("userMessageBg", "x") == "\x1b[48;2;51;102;153mx\x1b[49m"
    assert indexed.fg("accent", "x").startswith("\x1b[38;5;")
    assert indexed.bg("userMessageBg", "x").startswith("\x1b[48;5;")
    assert plain.fg("accent", "x") == "x"
    assert plain.bg("userMessageBg", "x") == "x"
    assert plain.bold("x") == "x"


def test_xterm_index_is_preserved_and_foreground_background_reset_independently() -> None:
    colors = {role: 42 for role in REQUIRED_THEME_ROLES}
    theme, diagnostics = resolve_theme("indexed", colors, {}, color_mode="truecolor", fallback=None)

    assert diagnostics == ()
    assert theme.colors["accent"] == "42"
    assert theme.fg("accent", "x") == "\x1b[38;5;42mx\x1b[39m"
    assert theme.bg("userMessageBg", "x") == "\x1b[48;5;42mx\x1b[49m"
    assert theme.bold("x") == "\x1b[1mx\x1b[22m"
    assert theme.italic("x") == "\x1b[3mx\x1b[23m"
    assert theme.underline("x") == "\x1b[4mx\x1b[24m"
    assert theme.strikethrough("x") == "\x1b[9mx\x1b[29m"


def test_theme_context_changes_generation_only_when_palette_changes() -> None:
    first, _ = resolve_builtin_theme("Signal Glass", color_mode="truecolor")
    second, _ = resolve_builtin_theme("Blood Circuit", color_mode="truecolor")
    context = ThemeContext(first)

    assert context.generation == 0
    context.set_theme(first)
    assert context.generation == 0
    context.set_theme(second)
    assert context.generation == 1
    assert context.theme is second


def test_theme_context_can_restore_an_exact_preview_snapshot() -> None:
    first, _ = resolve_builtin_theme("Signal Glass", color_mode="truecolor")
    second, _ = resolve_builtin_theme("Blood Circuit", color_mode="truecolor")
    context = ThemeContext(first)
    snapshot = context.snapshot()

    context.set_theme(second)
    context.restore(snapshot)

    assert context.theme is first
    assert context.generation == 0


def test_theme_controller_observes_registry_changes_without_persisting() -> None:
    registry = ThemeRegistry()
    registry.register_many([_theme_record("Signal Glass"), _theme_record("Blood Circuit")])
    initial, _ = resolve_builtin_theme("Signal Glass", color_mode="truecolor")
    context = ThemeContext(initial)
    renders: list[str] = []
    settings_reads: list[str] = []
    controller = ThemeController(
        registry,
        lambda: settings_reads.append("read") or None,
        context,
        lambda: renders.append(context.theme.name),
        color_mode="truecolor",
    )

    controller.sync()
    registry.select("Blood Circuit")
    controller.sync()

    assert settings_reads == []
    assert registry.active_name == "Blood Circuit"
    assert context.theme.name == "Blood Circuit"
    assert renders == ["Blood Circuit"]


def test_theme_controller_preview_cancel_and_commit_are_transactional() -> None:
    registry = ThemeRegistry()
    registry.register_many([_theme_record("Signal Glass"), _theme_record("Blood Circuit")])
    initial, _ = resolve_builtin_theme("Signal Glass", color_mode="truecolor")
    context = ThemeContext(initial)
    renders: list[str] = []
    settings_writes: list[str] = []
    controller = ThemeController(
        registry,
        lambda: None,
        context,
        lambda: renders.append(context.theme.name),
        color_mode="truecolor",
    )
    original_snapshot = context.snapshot()

    assert controller.preview("Blood Circuit") is True
    assert context.theme.name == "Blood Circuit"
    assert registry.active_name == "Signal Glass"
    assert settings_writes == []
    controller.restore_preview()
    assert context.snapshot() == original_snapshot

    assert controller.preview("Blood Circuit") is True
    assert controller.commit_preview_result() == "Blood Circuit"
    assert context.snapshot() == original_snapshot
    assert registry.active_name == "Signal Glass"
    assert settings_writes == []
    assert renders == ["Blood Circuit", "Signal Glass", "Blood Circuit", "Signal Glass"]


def test_theme_controller_retains_last_valid_theme_when_active_source_breaks(tmp_path) -> None:
    source = tmp_path / "custom.json"
    definition = BUILTIN_THEMES["Neon Oni"]
    source.write_text(
        json.dumps({"name": "custom", "colors": dict(definition["colors"]), "vars": dict(definition["vars"])}),
        encoding="utf-8",
    )
    registry = ThemeRegistry()
    registry.register_many(
        [
            Theme(
                name="custom",
                colors=dict(definition["colors"]),
                vars=dict(definition["vars"]),
                source_path=str(source),
                source_info=SourceInfo(path=str(source), source="test"),
            )
        ]
    )
    fallback, _ = resolve_builtin_theme("Signal Glass", color_mode="truecolor")
    context = ThemeContext(fallback)
    controller = ThemeController(registry, lambda: None, context, lambda: None, color_mode="truecolor")
    controller.sync()
    valid_snapshot = context.snapshot()

    source.write_text("{ definitely invalid", encoding="utf-8")
    controller.sync()

    assert context.snapshot() == valid_snapshot
    assert any(item.code == "invalid-source" for item in controller.diagnostics)


def test_interactive_mode_registers_builtins_first_and_applies_persisted_theme(tmp_path) -> None:
    settings = SettingsManager.in_memory()
    settings.set_theme("Blood Circuit")
    app = CodingApp(
        cwd=str(tmp_path),
        model=faux_model(),
        terminal=FakeTerminal(columns=100, rows=30),
        enable_tui=True,
        settings_manager=settings,
    )

    mode = InteractiveMode(app, input_fn=lambda _prompt: "/exit")

    assert [theme.name for theme in mode.theme_registry.list()][:6] == list(BUILTIN_THEMES)
    assert mode.theme_registry.active_name == "Blood Circuit"
    assert mode.theme_context.theme.name == "Blood Circuit"
    app.close()


def test_interactive_mode_missing_persisted_theme_falls_back_without_writing_settings(tmp_path) -> None:
    settings = SettingsManager.in_memory()
    settings.settings["theme"] = "vanished"
    writes: list[str] = []
    settings.set_theme = writes.append  # type: ignore[method-assign]
    app = CodingApp(
        cwd=str(tmp_path),
        model=faux_model(),
        terminal=FakeTerminal(columns=100, rows=30),
        enable_tui=True,
        settings_manager=settings,
    )

    mode = InteractiveMode(app, input_fn=lambda _prompt: "/exit")

    assert mode.theme_registry.active_name == "Signal Glass"
    assert mode.theme_context.theme.name == "Signal Glass"
    assert writes == []
    app.close()


def test_interactive_mode_honors_no_color_without_changing_theme_selection(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("NO_COLOR", "1")
    settings = SettingsManager.in_memory()
    settings.set_theme("Neon Oni")
    app = CodingApp(
        cwd=str(tmp_path),
        model=faux_model(),
        terminal=FakeTerminal(columns=100, rows=30),
        enable_tui=True,
        settings_manager=settings,
    )

    mode = InteractiveMode(app, input_fn=lambda _prompt: "/exit")

    assert mode.theme_registry.active_name == "Neon Oni"
    assert mode.theme_context.theme.color_mode == "none"
    assert mode.theme_context.theme.fg("accent", "plain") == "plain"
    app.close()


def test_theme_kind_select_uses_overlay_preview_and_restores_before_return(tmp_path) -> None:
    settings = SettingsManager.in_memory()
    app = CodingApp(
        cwd=str(tmp_path),
        model=faux_model(),
        terminal=FakeTerminal(columns=100, rows=30),
        enable_tui=True,
        settings_manager=settings,
    )
    mode = InteractiveMode(app)
    mode.init()
    original = mode.theme_context.snapshot()
    original_registry_name = mode.theme_registry.active_name
    original_message_count = len(app.messages)

    saw_overlay: list[bool] = []

    def choose_next() -> None:
        deadline = time.monotonic() + 2
        while not app.tui.has_overlay() and time.monotonic() < deadline:
            time.sleep(0.005)
        saw_overlay.append(app.tui.has_overlay())
        assert app.tui.terminal.input_handler is not None
        app.tui.terminal.input_handler("\x1b[B")
        app.tui.terminal.input_handler("\r")

    sender = threading.Thread(target=choose_next)
    sender.start()
    selected = mode.prompt_extension_select("Theme", list(BUILTIN_THEMES), kind="theme")
    sender.join(timeout=2)

    assert saw_overlay == [True]
    assert selected == "Black Ice"
    assert mode.theme_context.snapshot() == original
    assert mode.theme_registry.active_name == original_registry_name
    assert settings.get_theme() is None
    assert len(app.messages) == original_message_count
    assert app.tui.has_overlay() is False
    mode.footer_data_provider.dispose()
    app.tui.stop()
    app.close()


def test_theme_operations_leave_agent_context_and_compaction_envelope_unchanged(tmp_path) -> None:
    settings = SettingsManager.in_memory()
    app = CodingApp(
        cwd=str(tmp_path),
        model=faux_model(),
        terminal=FakeTerminal(columns=100, rows=30),
        enable_tui=True,
        settings_manager=settings,
    )
    mode = InteractiveMode(app, input_fn=lambda _prompt: "/exit")
    before = (
        repr(app.messages),
        len(app.messages),
        app.compaction.compressor.threshold_tokens,
        app.compaction.compressor.compression_count,
        app.session.session_id,
        app.session.session_path,
    )

    assert mode.theme_controller.preview("Neon Oni") is True
    mode.theme_controller.restore_preview()
    mode.theme_registry.select("Blood Circuit")
    mode.theme_controller.sync()

    after = (
        repr(app.messages),
        len(app.messages),
        app.compaction.compressor.threshold_tokens,
        app.compaction.compressor.compression_count,
        app.session.session_id,
        app.session.session_path,
    )
    assert after == before
    app.close()


def test_theme_core_has_no_agent_runtime_context_or_compaction_imports() -> None:
    forbidden = ("travis.agent", "travis.ai", "travis.compaction", "travis.coding_agent")
    root = Path(__file__).resolve().parents[1]
    for relative in (
        "travis/tui/theme.py",
        "travis/tui/builtin_themes.py",
        "travis/tui/theme_controller.py",
    ):
        tree = ast.parse((root / relative).read_text(encoding="utf-8"), filename=relative)
        imports = [
            alias.name
            for node in ast.walk(tree)
            if isinstance(node, (ast.Import, ast.ImportFrom))
            for alias in node.names
            if isinstance(node, ast.Import) or node.module is not None
        ]
        if any(isinstance(node, ast.ImportFrom) and node.module for node in ast.walk(tree)):
            imports.extend(
                node.module
                for node in ast.walk(tree)
                if isinstance(node, ast.ImportFrom) and node.module is not None
            )
        assert not any(name.startswith(forbidden) for name in imports), (relative, imports)


def test_tui_package_exports_semantic_theme_surface() -> None:
    from travis.tui import (
        BUILTIN_THEMES as exported_builtins,
        REQUIRED_THEME_ROLES as exported_roles,
        ResolvedTheme,
        ThemeContext as ExportedThemeContext,
        ThemeController as ExportedThemeController,
        resolve_theme as exported_resolve_theme,
    )

    assert exported_builtins is BUILTIN_THEMES
    assert exported_roles is REQUIRED_THEME_ROLES
    assert ResolvedTheme.__name__ == "ResolvedTheme"
    assert ExportedThemeContext is ThemeContext
    assert ExportedThemeController is ThemeController
    assert exported_resolve_theme is resolve_theme
