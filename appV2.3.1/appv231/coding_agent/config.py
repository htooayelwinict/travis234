"""appv231 coding-agent package and user config paths."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any


def _normalize_path(path: str) -> str:
    return os.path.expanduser(path)


def _find_package_dir() -> Path:
    env_dir = os.environ.get("APPV231_PACKAGE_DIR")
    if env_dir:
        return Path(_normalize_path(env_dir)).resolve()
    current = Path(__file__).resolve()
    for directory in current.parents:
        if (directory / "package.json").exists():
            return directory
    return current.parents[2]


def get_package_dir() -> str:
    return str(_find_package_dir())


def get_themes_dir() -> str:
    package_dir = _find_package_dir()
    src_or_dist = "src" if (package_dir / "src").exists() else "dist"
    return str(package_dir / src_or_dist / "modes" / "interactive" / "theme")


def get_export_template_dir() -> str:
    package_dir = _find_package_dir()
    src_or_dist = "src" if (package_dir / "src").exists() else "dist"
    return str(package_dir / src_or_dist / "core" / "export-html")


def get_package_json_path() -> str:
    return str(_find_package_dir() / "package.json")


def get_readme_path() -> str:
    return str((_find_package_dir() / "README.md").resolve())


def get_docs_path() -> str:
    return str((_find_package_dir() / "docs").resolve())


def get_examples_path() -> str:
    return str((_find_package_dir() / "examples").resolve())


def get_changelog_path() -> str:
    return str((_find_package_dir() / "CHANGELOG.md").resolve())


def get_interactive_assets_dir() -> str:
    package_dir = _find_package_dir()
    src_or_dist = "src" if (package_dir / "src").exists() else "dist"
    return str(package_dir / src_or_dist / "modes" / "interactive" / "assets")


def get_bundled_interactive_asset_path(name: str) -> str:
    return str(Path(get_interactive_assets_dir()) / name)


def _read_package_json() -> dict[str, Any]:
    try:
        return json.loads(Path(get_package_json_path()).read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}


_PKG = _read_package_json()
_APPV231_CONFIG = _PKG.get("appv231Config") if isinstance(_PKG.get("appv231Config"), dict) else {}

PACKAGE_NAME = str(_PKG.get("name") or "@htooayelwinict/appv231")
APP_NAME = str(_APPV231_CONFIG.get("name") or "appv231")
APP_TITLE = APP_NAME
CONFIG_DIR_NAME = str(_APPV231_CONFIG.get("configDir") or ".appv231")
VERSION = str(_PKG.get("version") or "0.0.0")
ENV_AGENT_DIR = f"{APP_NAME.upper()}_CODING_AGENT_DIR"
ENV_SESSION_DIR = f"{APP_NAME.upper()}_CODING_AGENT_SESSION_DIR"
DEFAULT_SHARE_VIEWER_URL = "https://appv231.local/session/"


def expand_tilde_path(path: str) -> str:
    return _normalize_path(path)


def get_share_viewer_url(gist_id: str) -> str:
    base_url = os.environ.get("APPV231_SHARE_VIEWER_URL") or DEFAULT_SHARE_VIEWER_URL
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
    return str(Path(get_agent_dir()) / "sessions")


def get_debug_log_path() -> str:
    return str(Path(get_agent_dir()) / f"{APP_NAME}-debug.log")


getPackageDir = get_package_dir
getThemesDir = get_themes_dir
getExportTemplateDir = get_export_template_dir
getPackageJsonPath = get_package_json_path
getReadmePath = get_readme_path
getDocsPath = get_docs_path
getExamplesPath = get_examples_path
getChangelogPath = get_changelog_path
getInteractiveAssetsDir = get_interactive_assets_dir
getBundledInteractiveAssetPath = get_bundled_interactive_asset_path
expandTildePath = expand_tilde_path
getShareViewerUrl = get_share_viewer_url
getAgentDir = get_agent_dir
getCustomThemesDir = get_custom_themes_dir
getModelsPath = get_models_path
getAuthPath = get_auth_path
getSettingsPath = get_settings_path
getToolsDir = get_tools_dir
getBinDir = get_bin_dir
getPromptsDir = get_prompts_dir
getSessionsDir = get_sessions_dir
getDebugLogPath = get_debug_log_path
