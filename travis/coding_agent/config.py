"""Travis234 package metadata and user-state paths."""

from __future__ import annotations

from importlib import metadata, resources
import os
from pathlib import Path


PACKAGE_NAME = "travis234"
APP_NAME = "travis234"
APP_TITLE = "Travis234"
CONFIG_DIR_NAME = ".travis234"
ENV_AGENT_DIR = "TRAVIS234_CODING_AGENT_DIR"
ENV_SESSION_DIR = "TRAVIS234_CODING_AGENT_SESSION_DIR"
DEFAULT_SHARE_VIEWER_URL = "https://travis234.local/session/"

try:
    VERSION = metadata.version(PACKAGE_NAME)
except metadata.PackageNotFoundError:
    # Source checkouts can be imported before the editable package is installed.
    VERSION = "2.3.2"


def expand_tilde_path(path: str) -> str:
    return os.path.expanduser(path)


def _packaged_resource_path(*parts: str) -> str:
    resource = resources.files("travis").joinpath("resources", *parts)
    return str(Path(str(resource)).resolve())


def get_packaged_context_paths() -> tuple[str, ...]:
    """Return installed context resources exposed to a coding session."""

    return (
        _packaged_resource_path("README.md"),
        _packaged_resource_path("docs"),
        _packaged_resource_path("examples"),
    )


def get_share_viewer_url(gist_id: str) -> str:
    base_url = os.environ.get("TRAVIS234_SHARE_VIEWER_URL") or DEFAULT_SHARE_VIEWER_URL
    return f"{base_url}#{gist_id}"


def get_agent_dir() -> str:
    env_dir = os.environ.get(ENV_AGENT_DIR)
    if env_dir:
        return expand_tilde_path(env_dir)
    return str(Path.home() / CONFIG_DIR_NAME / "agent")


def get_custom_themes_dir() -> str:
    return str(Path(get_agent_dir()) / "themes")


def get_models_path() -> str:
    return str(Path(get_agent_dir()) / "models.json")


def get_auth_path() -> str:
    return str(Path(get_agent_dir()) / "auth.json")


def get_settings_path() -> str:
    return str(Path(get_agent_dir()) / "settings.json")


def get_tools_dir() -> str:
    return str(Path(get_agent_dir()) / "tools")


def get_bin_dir() -> str:
    return str(Path(get_agent_dir()) / "bin")


def get_prompts_dir() -> str:
    return str(Path(get_agent_dir()) / "prompts")


def get_sessions_dir() -> str:
    env_dir = os.environ.get(ENV_SESSION_DIR)
    if env_dir:
        return expand_tilde_path(env_dir)
    return str(Path(get_agent_dir()) / "sessions")


def get_debug_log_path() -> str:
    return str(Path(get_agent_dir()) / f"{APP_NAME}-debug.log")
