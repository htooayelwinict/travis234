from __future__ import annotations

from appv231.ai.models import get_provider_auth_status
from appv231.ai.providers.faux import faux_model
from appv231.ai.stream import ApiProvider, get_api_provider
from appv231.app import CodingApp
from appv231.coding_agent import create_agent_session_services
from appv231.coding_agent.provider_control_plane import ProviderControlPlane


def _provider(api: str) -> ApiProvider:
    def unavailable(*_args, **_kwargs):
        raise RuntimeError("not invoked")

    return ApiProvider(api=api, stream=unavailable, stream_simple=unavailable)


def test_app_and_session_share_one_control_plane(tmp_path) -> None:
    control = ProviderControlPlane.in_memory()
    app = CodingApp(cwd=str(tmp_path), model=faux_model(), provider_control_plane=control)

    assert app.provider_control_plane is control
    assert app.session.provider_control_plane is control
    assert app.session.model_registry is control.models
    assert app.session.auth_storage is control.auth


def test_sdk_services_and_session_share_one_control_plane(tmp_path) -> None:
    control = ProviderControlPlane.in_memory()
    services = create_agent_session_services(
        {
            "cwd": str(tmp_path),
            "agentDir": str(tmp_path / "agent"),
            "providerControlPlane": control,
        }
    )

    assert services["providerControlPlane"] is control
    assert services["modelRegistry"] is control.models
    assert services["authStorage"] is control.auth


def test_two_in_memory_control_planes_do_not_leak_registrations() -> None:
    left = ProviderControlPlane.in_memory()
    right = ProviderControlPlane.in_memory()
    left.api_providers.register(_provider("private"), source_id="test")

    assert left.api_providers.get("private") is not None
    assert right.api_providers.get("private") is None


def test_api_provider_registration_close_restores_previous_entry() -> None:
    control = ProviderControlPlane.in_memory()
    base = control.api_providers.register(_provider("same"), source_id="base")
    override_provider = _provider("same")
    override = control.api_providers.register(override_provider, source_id="plugin")

    assert control.api_providers.get("same") is override_provider
    override.close()
    assert control.api_providers.get("same") is not None
    override.close()
    base.close()
    assert control.api_providers.get("same") is None


def test_repeated_refresh_keeps_one_fallback_resolver() -> None:
    control = ProviderControlPlane.in_memory()
    for _ in range(20):
        control.refresh()
    control.auth.get_api_key("custom")
    assert control.fallback_resolution_count("custom") == 1


def test_extension_registration_close_restores_all_owned_surfaces() -> None:
    control = ProviderControlPlane.in_memory()

    def config(api: str, model_id: str):
        return {
            "provider": "same",
            "baseUrl": "https://same.example.test",
            "apiKey": "test-key",
            "api": api,
            "streamSimple": _provider(api).stream_simple,
            "models": [
                {
                    "id": model_id,
                    "name": model_id,
                    "reasoning": False,
                    "input": ["text"],
                    "cost": {},
                    "contextWindow": 32000,
                    "maxTokens": 4096,
                }
            ],
        }

    base = control.register_extension("base", config("base-api", "base"))
    override = control.register_extension("plugin", config("plug-api", "plugin"))

    assert control.api_providers.get("plug-api") is not None
    assert control.models.find("same", "plugin") is not None

    override.close()
    assert control.api_providers.get("plug-api") is None
    assert control.api_providers.get("base-api") is not None
    assert control.models.find("same", "base") is not None

    override.close()
    base.close()
    assert control.api_providers.get("base-api") is None


def test_extension_auth_registration_does_not_mutate_default_global_registry() -> None:
    control = ProviderControlPlane.in_memory()
    assert get_provider_auth_status("isolated") == {"configured": False}
    registration = control.register_extension(
        "plugin",
        {
            "provider": "isolated",
            "baseUrl": "https://isolated.example.test",
            "apiKey": "test-key",
            "api": "isolated-api",
            "streamSimple": _provider("isolated-api").stream_simple,
            "models": [{"id": "model", "name": "Model"}],
        },
    )

    assert control.models.get_provider_auth_status("isolated")["configured"] is True
    assert get_provider_auth_status("isolated") == {"configured": False}
    try:
        get_api_provider("isolated-api")
    except KeyError:
        pass
    else:
        raise AssertionError("isolated registration leaked into default API registry")

    registration.close()
