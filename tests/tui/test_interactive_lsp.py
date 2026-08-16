from __future__ import annotations

from types import SimpleNamespace

from travis.tui.interactive_command_dispatcher import _is_lsp_status_command
from travis.tui.interactive_lsp import InteractiveLsp, format_lsp_status


class _Manager:
    def __init__(self) -> None:
        self.status_calls = 0

    def status(self) -> dict[str, object]:
        self.status_calls += 1
        return {
            "configured": 2,
            "active": 1,
            "configGeneration": 3,
            "servers": [
                {
                    "name": "python",
                    "running": True,
                    "generation": 4,
                    "positionEncoding": "utf-16",
                    "restartExhausted": False,
                }
            ],
            "limits": {
                "maxActiveServers": 3,
                "startupSeconds": 10,
                "requestSeconds": 20,
                "maxRestarts": 2,
                "restartWindowSeconds": 60,
            },
        }


class _Interactive(InteractiveLsp):
    def __init__(self, manager: _Manager | None) -> None:
        self.app = SimpleNamespace(
            session=SimpleNamespace(_language_services=manager)
        )
        self.history = SimpleNamespace(items=[], add=lambda item: self.history.items.append(item))
        self.status = SimpleNamespace(set_message=lambda value: setattr(self.status, "message", value))
        self.tui = SimpleNamespace(request_render=lambda: setattr(self.tui, "rendered", True))

    def _refresh_footer(self) -> None:
        self.footer_refreshed = True


def test_lsp_status_parser_is_exact() -> None:
    assert _is_lsp_status_command("/lsp status") is True
    assert _is_lsp_status_command("/lsp") is False
    assert _is_lsp_status_command("/lsp start") is False


def test_status_format_is_bounded_and_contains_no_command_paths() -> None:
    text = format_lsp_status(_Manager().status())

    assert "Configured: 2" in text
    assert "Active: 1/3" in text
    assert "python: running" in text
    assert "restarts 2/60s" in text
    assert "/usr/" not in text
    assert "initializationOptions" not in text


def test_lsp_status_reads_snapshot_without_starting_a_server() -> None:
    manager = _Manager()
    interactive = _Interactive(manager)

    interactive._run_lsp_status_command()

    assert manager.status_calls == 1
    assert len(interactive.history.items) == 1
    assert interactive.status.message == "Idle"
    assert interactive.tui.rendered is True


def test_lsp_status_reports_when_no_manager_is_composed() -> None:
    interactive = _Interactive(None)

    interactive._run_lsp_status_command()

    assert "not configured" in interactive.history.items[0].text.lower()
