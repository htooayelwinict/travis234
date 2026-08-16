from __future__ import annotations

import json

import pytest

from travis.coding_agent.settings_manager import InMemorySettingsStorage, SettingsManager


def _settings_with_scopes(
    global_policy: object,
    project_policy: object,
    *,
    trusted: bool = True,
) -> SettingsManager:
    storage = InMemorySettingsStorage()
    storage.with_lock(
        "global",
        lambda _current: json.dumps({"toolPolicy": global_policy}),
    )
    storage.with_lock(
        "project",
        lambda _current: json.dumps({"toolPolicy": project_policy}),
    )
    return SettingsManager.from_storage(storage, {"projectTrusted": trusted})


def test_policy_settings_default_to_audit_read() -> None:
    settings = SettingsManager.in_memory()

    assert settings.get_tool_policy_settings() == {
        "mode": "audit",
        "autoAllowEffects": ["read"],
    }


def test_global_policy_normalizes_duplicate_effects_in_canonical_order() -> None:
    settings = SettingsManager.in_memory(
        {
            "toolPolicy": {
                "mode": "enforce",
                "autoAllowEffects": ["network", "read", "network", "write"],
            }
        }
    )

    assert settings.get_tool_policy_settings() == {
        "mode": "enforce",
        "autoAllowEffects": ["read", "write", "network"],
    }


def test_trusted_project_can_raise_mode_and_intersects_auto_allow() -> None:
    settings = _settings_with_scopes(
        {"mode": "audit", "autoAllowEffects": ["read", "write", "execute"]},
        {"mode": "enforce", "autoAllowEffects": ["read", "network"]},
    )

    assert settings.get_tool_policy_settings() == {
        "mode": "enforce",
        "autoAllowEffects": ["read"],
    }


def test_project_cannot_lower_enforce_or_widen_global_allow_set() -> None:
    settings = _settings_with_scopes(
        {"mode": "enforce", "autoAllowEffects": ["read"]},
        {"mode": "audit", "autoAllowEffects": ["read", "write", "network"]},
    )

    assert settings.get_tool_policy_settings() == {
        "mode": "enforce",
        "autoAllowEffects": ["read"],
    }


def test_project_cannot_disable_a_global_policy() -> None:
    settings = _settings_with_scopes(
        {"mode": "audit", "autoAllowEffects": ["read"]},
        {"mode": "disabled", "autoAllowEffects": ["read"]},
    )

    assert settings.get_tool_policy_settings()["mode"] == "audit"


def test_untrusted_project_policy_is_ignored() -> None:
    settings = _settings_with_scopes(
        {"mode": "audit", "autoAllowEffects": ["read", "write"]},
        {"mode": "enforce", "autoAllowEffects": []},
        trusted=False,
    )

    assert settings.get_tool_policy_settings() == {
        "mode": "audit",
        "autoAllowEffects": ["read", "write"],
    }


@pytest.mark.parametrize(
    ("policy", "expected"),
    [
        ({"mode": "surprise", "autoAllowEffects": ["read"]}, {"mode": "audit", "autoAllowEffects": ["read"]}),
        ({"mode": "audit", "autoAllowEffects": "read"}, {"mode": "audit", "autoAllowEffects": []}),
        ({"mode": "audit", "autoAllowEffects": ["read", "unknown"]}, {"mode": "audit", "autoAllowEffects": []}),
    ],
)
def test_malformed_global_policy_records_error_and_fails_safe(
    policy: object,
    expected: dict[str, object],
) -> None:
    settings = SettingsManager.in_memory({"toolPolicy": policy})

    assert settings.get_tool_policy_settings() == expected
    errors = settings.drain_errors()
    assert len(errors) == 1
    assert errors[0]["scope"] == "global"
    assert "toolPolicy" in str(errors[0]["error"])


def test_malformed_trusted_project_policy_records_error_without_widening() -> None:
    settings = _settings_with_scopes(
        {"mode": "audit", "autoAllowEffects": ["read"]},
        {"mode": "enforce", "autoAllowEffects": ["write", 7]},
    )

    assert settings.get_tool_policy_settings() == {
        "mode": "enforce",
        "autoAllowEffects": [],
    }
    errors = settings.drain_errors()
    assert len(errors) == 1
    assert errors[0]["scope"] == "project"
