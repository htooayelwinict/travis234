"""Argparse integration for extension-registered CLI flags."""

from __future__ import annotations

import argparse
import re

from travis.coding_agent.extensions import ExtensionRunner


_FLAG_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


class ExtensionFlagSchemaError(ValueError):
    """Raised when registered extension flags cannot form an unambiguous CLI schema."""


class _StoreExtensionFlag(argparse.Action):
    def __init__(
        self,
        *args: object,
        flag_name: str,
        boolean: bool,
        **kwargs: object,
    ) -> None:
        self.flag_name = flag_name
        self.boolean = boolean
        kwargs["nargs"] = 0 if boolean else None
        super().__init__(*args, **kwargs)

    def __call__(
        self,
        parser: argparse.ArgumentParser,
        namespace: argparse.Namespace,
        values: object,
        option_string: str | None = None,
    ) -> None:
        del parser, option_string
        current = dict(getattr(namespace, self.dest, None) or {})
        current[self.flag_name] = True if self.boolean else str(values)
        setattr(namespace, self.dest, current)


def add_extension_flags(
    parser: argparse.ArgumentParser,
    runtime: ExtensionRunner,
) -> None:
    """Add a validated runtime's boolean and string flags to ``parser``."""

    conflicts = runtime.get_flag_conflicts()
    if conflicts:
        details = "; ".join(
            f'Extension flag "--{item.name}" from {item.conflicting_extension_path} '
            f"conflicts with {item.first_extension_path}"
            for item in conflicts
        )
        raise ExtensionFlagSchemaError(details)

    builtin_options = set(parser._option_string_actions)  # noqa: SLF001
    parser.set_defaults(extension_flag_values={})
    for name, flag in runtime.get_flags().items():
        option = f"--{name}"
        if not _FLAG_NAME.fullmatch(name):
            raise ExtensionFlagSchemaError(
                f'Extension flag "{option}" from {flag.extension_path} has an invalid name'
            )
        if flag.type not in {"boolean", "string"}:
            raise ExtensionFlagSchemaError(
                f'Extension flag "{option}" from {flag.extension_path} has invalid type {flag.type!r}'
            )
        if option in builtin_options:
            raise ExtensionFlagSchemaError(
                f'Extension flag "{option}" from {flag.extension_path} conflicts with a built-in option'
            )
        parser.add_argument(
            option,
            action=_StoreExtensionFlag,
            dest="extension_flag_values",
            flag_name=name,
            boolean=flag.type == "boolean",
            metavar="VALUE" if flag.type == "string" else None,
            help=flag.description or f"Registered by {flag.extension_path}",
        )
