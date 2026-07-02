"""Pi TUI keybinding registry."""

from __future__ import annotations

from copy import deepcopy

from appv231.tui.keys import matches_key


TUI_KEYBINDINGS: dict[str, dict[str, object]] = {
    "tui.editor.cursorUp": {"defaultKeys": "up", "description": "Move cursor up"},
    "tui.editor.cursorDown": {"defaultKeys": "down", "description": "Move cursor down"},
    "tui.editor.cursorLeft": {"defaultKeys": ["left", "ctrl+b"], "description": "Move cursor left"},
    "tui.editor.cursorRight": {"defaultKeys": ["right", "ctrl+f"], "description": "Move cursor right"},
    "tui.editor.cursorWordLeft": {
        "defaultKeys": ["alt+left", "ctrl+left", "alt+b"],
        "description": "Move cursor word left",
    },
    "tui.editor.cursorWordRight": {
        "defaultKeys": ["alt+right", "ctrl+right", "alt+f"],
        "description": "Move cursor word right",
    },
    "tui.editor.cursorLineStart": {"defaultKeys": ["home", "ctrl+a"], "description": "Move to line start"},
    "tui.editor.cursorLineEnd": {"defaultKeys": ["end", "ctrl+e"], "description": "Move to line end"},
    "tui.editor.jumpForward": {"defaultKeys": "ctrl+]", "description": "Jump forward to character"},
    "tui.editor.jumpBackward": {"defaultKeys": "ctrl+alt+]", "description": "Jump backward to character"},
    "tui.editor.pageUp": {"defaultKeys": "pageUp", "description": "Page up"},
    "tui.editor.pageDown": {"defaultKeys": "pageDown", "description": "Page down"},
    "tui.editor.deleteCharBackward": {"defaultKeys": "backspace", "description": "Delete character backward"},
    "tui.editor.deleteCharForward": {
        "defaultKeys": ["delete", "ctrl+d"],
        "description": "Delete character forward",
    },
    "tui.editor.deleteWordBackward": {
        "defaultKeys": ["ctrl+w", "alt+backspace"],
        "description": "Delete word backward",
    },
    "tui.editor.deleteWordForward": {
        "defaultKeys": ["alt+d", "alt+delete"],
        "description": "Delete word forward",
    },
    "tui.editor.deleteToLineStart": {"defaultKeys": "ctrl+u", "description": "Delete to line start"},
    "tui.editor.deleteToLineEnd": {"defaultKeys": "ctrl+k", "description": "Delete to line end"},
    "tui.editor.yank": {"defaultKeys": "ctrl+y", "description": "Yank"},
    "tui.editor.yankPop": {"defaultKeys": "alt+y", "description": "Yank pop"},
    "tui.editor.undo": {"defaultKeys": "ctrl+-", "description": "Undo"},
    "tui.input.newLine": {"defaultKeys": "shift+enter", "description": "Insert newline"},
    "tui.input.submit": {"defaultKeys": "enter", "description": "Submit input"},
    "tui.input.tab": {"defaultKeys": "tab", "description": "Tab / autocomplete"},
    "tui.input.copy": {"defaultKeys": "ctrl+c", "description": "Copy selection"},
    "tui.select.up": {"defaultKeys": "up", "description": "Move selection up"},
    "tui.select.down": {"defaultKeys": "down", "description": "Move selection down"},
    "tui.select.pageUp": {"defaultKeys": "pageUp", "description": "Selection page up"},
    "tui.select.pageDown": {"defaultKeys": "pageDown", "description": "Selection page down"},
    "tui.select.confirm": {"defaultKeys": "enter", "description": "Confirm selection"},
    "tui.select.cancel": {"defaultKeys": ["escape", "ctrl+c"], "description": "Cancel selection"},
}


def _normalize_keys(keys: object) -> list[str]:
    if keys is None:
        return []
    key_list = keys if isinstance(keys, list) else [keys]
    seen: set[str] = set()
    result: list[str] = []
    for key in key_list:
        key_id = str(key)
        if key_id not in seen:
            seen.add(key_id)
            result.append(key_id)
    return result


class KeybindingsManager:
    def __init__(
        self,
        definitions: dict[str, dict[str, object]] | None = None,
        user_bindings: dict[str, object] | None = None,
    ) -> None:
        self.definitions = definitions or TUI_KEYBINDINGS
        self.user_bindings = dict(user_bindings or {})
        self._keys_by_id: dict[str, list[str]] = {}
        self._conflicts: list[dict[str, object]] = []
        self._rebuild()

    def _rebuild(self) -> None:
        self._keys_by_id.clear()
        self._conflicts = []
        user_claims: dict[str, list[str]] = {}
        for keybinding, keys in self.user_bindings.items():
            if keybinding not in self.definitions:
                continue
            for key in _normalize_keys(keys):
                user_claims.setdefault(key, [])
                if keybinding not in user_claims[key]:
                    user_claims[key].append(keybinding)
        for key, keybindings in user_claims.items():
            if len(keybindings) > 1:
                self._conflicts.append({"key": key, "keybindings": list(keybindings)})
        for keybinding, definition in self.definitions.items():
            keys = (
                _normalize_keys(self.user_bindings[keybinding])
                if keybinding in self.user_bindings
                else _normalize_keys(definition.get("defaultKeys"))
            )
            self._keys_by_id[keybinding] = keys

    def matches(self, data: str, keybinding: str) -> bool:
        return any(matches_key(data, key) for key in self._keys_by_id.get(keybinding, []))

    def get_keys(self, keybinding: str) -> list[str]:
        return list(self._keys_by_id.get(keybinding, []))

    getKeys = get_keys

    def get_definition(self, keybinding: str) -> dict[str, object] | None:
        return self.definitions.get(keybinding)

    getDefinition = get_definition

    def get_conflicts(self) -> list[dict[str, object]]:
        return deepcopy(self._conflicts)

    getConflicts = get_conflicts

    def set_user_bindings(self, user_bindings: dict[str, object]) -> None:
        self.user_bindings = dict(user_bindings)
        self._rebuild()

    setUserBindings = set_user_bindings

    def get_user_bindings(self) -> dict[str, object]:
        return dict(self.user_bindings)

    getUserBindings = get_user_bindings

    def get_resolved_bindings(self) -> dict[str, object]:
        resolved: dict[str, object] = {}
        for keybinding in self.definitions:
            keys = self._keys_by_id.get(keybinding, [])
            resolved[keybinding] = keys[0] if len(keys) == 1 else list(keys)
        return resolved

    getResolvedBindings = get_resolved_bindings


_global_keybindings: KeybindingsManager | None = None


def set_keybindings(keybindings: KeybindingsManager) -> None:
    global _global_keybindings
    _global_keybindings = keybindings


def get_keybindings() -> KeybindingsManager:
    global _global_keybindings
    if _global_keybindings is None:
        _global_keybindings = KeybindingsManager(TUI_KEYBINDINGS)
    return _global_keybindings


setKeybindings = set_keybindings
getKeybindings = get_keybindings
