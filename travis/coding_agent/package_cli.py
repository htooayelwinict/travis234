"""Package command-line dispatch that runs before agent startup."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Sequence

from travis.coding_agent.package_manager import DefaultPackageManager, configured_package_source
from travis.coding_agent.project_trust import ProjectTrustStore
from travis.coding_agent.settings_manager import SettingsManager

PACKAGE_COMMANDS = frozenset({"install", "remove", "update", "list", "config"})


def is_package_cli_invocation(argv: Sequence[str]) -> bool:
    return bool(argv) and argv[0] in PACKAGE_COMMANDS


def run_package_cli(argv: Sequence[str], *, agent_dir: str) -> int:
    parser = _build_parser()
    args = parser.parse_args(list(argv))
    cwd = Path(args.cwd).expanduser().resolve()
    if not cwd.is_dir():
        print(f"Error: working directory is not a directory: {cwd}", file=sys.stderr)
        return 1
    scope = "project" if args.local else "global"
    try:
        project_trusted = _resolve_package_trust(
            cwd=cwd,
            agent_dir=agent_dir,
            scope=scope,
            override=args.project_trust_override,
        )
        settings = SettingsManager.create(
            str(cwd),
            agent_dir,
            {"projectTrusted": project_trusted},
        )
        manager = DefaultPackageManager(
            cwd=str(cwd),
            agent_dir=agent_dir,
            settings_manager=settings,
            project_trusted=project_trusted,
            offline=args.offline,
        )
        if args.package_command == "install":
            installed = manager.install(args.source, scope=scope)
            print(f"Installed {installed.source.raw}: {installed.install_path}")
            return 0
        if args.package_command == "remove":
            if not manager.remove(args.source, scope=scope):
                raise KeyError(f"Installed package not found: {args.source}")
            print(f"Removed {args.source}")
            return 0
        if args.package_command == "update":
            updated = manager.update(args.source, scope=scope)
            suffix = "" if len(updated) == 1 else "s"
            print(f"Updated {len(updated)} package{suffix}")
            return 0
        if args.package_command == "list":
            installed = manager.list_installed(scope=scope)
            if args.json:
                print(
                    json.dumps(
                        [
                            {
                                "source": item.source.raw,
                                "scope": item.scope,
                                "path": item.install_path,
                                "version": item.version,
                            }
                            for item in installed
                        ],
                        indent=2,
                    )
                )
            elif not installed:
                print("No installed packages")
            else:
                for item in installed:
                    version = f" ({item.version})" if item.version else ""
                    print(f"{item.source.raw}{version} [{item.scope}] {item.install_path}")
            return 0
        return _run_config_command(args, settings, scope)
    except (OSError, RuntimeError, ValueError, KeyError) as error:
        message = error.args[0] if isinstance(error, KeyError) and error.args else str(error)
        print(f"Error: {message}", file=sys.stderr)
        return 1


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="travis234", description="Manage Travis234 resource packages")
    subparsers = parser.add_subparsers(dest="package_command", required=True)
    install = subparsers.add_parser("install", help="Install a local, Git, or Python package")
    install.add_argument("source")
    remove = subparsers.add_parser("remove", help="Remove an installed package")
    remove.add_argument("source")
    update = subparsers.add_parser("update", help="Update installed packages from their exact sources")
    update.add_argument("source", nargs="?")
    list_parser = subparsers.add_parser("list", help="List installed packages")
    list_parser.add_argument("--json", action="store_true")
    config = subparsers.add_parser("config", help="List or edit configured package sources")
    config_actions = config.add_mutually_exclusive_group()
    config_actions.add_argument("--add", metavar="SOURCE")
    config_actions.add_argument("--remove", metavar="SOURCE")

    for command_parser in (install, remove, update, list_parser, config):
        command_parser.add_argument("--cwd", default=".", help="Project working directory")
        command_parser.add_argument(
            "--offline",
            action="store_true",
            help="Allow local package operations but block network package acquisition",
        )
        command_parser.add_argument(
            "--local",
            action="store_true",
            help="Use trusted project scope instead of global scope",
        )
        trust = command_parser.add_mutually_exclusive_group()
        trust.add_argument(
            "-a",
            "--approve",
            dest="project_trust_override",
            action="store_const",
            const=True,
            default=None,
            help="Trust project package configuration for this process",
        )
        trust.add_argument(
            "-na",
            "--no-approve",
            dest="project_trust_override",
            action="store_const",
            const=False,
            help="Reject project package configuration",
        )
    return parser


def _resolve_package_trust(
    *,
    cwd: Path,
    agent_dir: str,
    scope: str,
    override: bool | None,
) -> bool:
    if scope != "project":
        return False
    if override is True:
        return True
    if override is False:
        raise RuntimeError("Project package operations require a trusted project")
    saved = ProjectTrustStore(agent_dir).get(cwd)
    if saved is not None:
        if saved:
            return True
        raise RuntimeError("Project package operations require a trusted project")
    global_settings = SettingsManager.create(str(cwd), agent_dir, {"projectTrusted": False})
    default_trust = global_settings.get_default_project_trust()
    if default_trust == "always":
        return True
    raise RuntimeError(
        "Project package operations require a trusted project; use --approve or save a trust decision"
    )


def _run_config_command(args: argparse.Namespace, settings: SettingsManager, scope: str) -> int:
    source_settings = settings.global_settings if scope == "global" else settings.project_settings
    current = list(source_settings.get("packages", []))
    if args.add:
        if args.add not in current:
            current.append(args.add)
    elif args.remove:
        current = [entry for entry in current if configured_package_source(entry) != args.remove]
    else:
        for entry in current:
            source = configured_package_source(entry)
            if source is not None:
                print(source)
        return 0
    if scope == "global":
        settings.set_packages(current)
    else:
        settings.set_project_packages(current)
    print(f"Configured {len(current)} {scope} package source(s)")
    return 0


__all__ = ["PACKAGE_COMMANDS", "is_package_cli_invocation", "run_package_cli"]
