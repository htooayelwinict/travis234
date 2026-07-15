# Travis234 extensions

Travis234 loads Python extension files from the global `~/.travis234/agent/extensions/` directory and, for trusted projects, from `.travis234/extensions/`. Additional extension paths may be configured in settings.

An extension module exports a callable named `extension`:

```python
def extension(travis):
    def handle_check(args, ctx):
        return ctx.send_message(
            {
                "customType": "live-check",
                "content": f"CHECK_OK:{args}",
                "display": True,
            }
        )

    travis.register_command(
        "live-check",
        {
            "description": "Run the live check",
            "handler": handle_check,
        },
    )
```

Command handlers receive the command argument string and a fresh extension context. Do not retain a context across a session replacement or reload; old contexts become stale intentionally.

Use `/reload` after changing an extension. Reload creates a fresh extension runtime, re-reads configured paths, and replaces prior registrations. Syntax-check a project extension before reloading with `python -m py_compile .travis234/extensions/<name>.py`.

The extension API also supports tools, shortcuts, event handlers, providers, widgets, and session actions through the `travis` registration object and command context. Its lifecycle manifest covers the 33 pinned Pi events, including trust, resource discovery, session/model/thinking changes, provider boundaries, turns, messages, tools, input, and user shell activity. Duplicate commands remain addressable in source order as `name`, `name:1`, and `name:2`.

Travis234 targets Pi's resource behavior through a Python-native boundary; JavaScript extension files are not executed. Skills and prompt templates use safe YAML frontmatter. Invalid YAML, invalid skill names, and collisions become diagnostics. Directory discovery merges `.gitignore`, `.ignore`, and `.fdignore`, while explicitly configured individual files are still honored.

Prompt templates expand only at the start of a user turn and support shell quoting, `$ARGUMENTS`, `$@`, positional values, defaults, and slices. Enabled skill commands use `/skill:<name>` and inject the selected skill body with its relative-reference base. Themes are replaced on `/reload`; the active name survives when still present, otherwise the TUI reports its fallback. Extension UI contexts expose `setTheme(name)`.

Resource packages may come from a local directory, `git+` URL with an optional exact revision, or Python requirement. Use `travis234 install`, `remove`, `update`, `list`, and `config`; `--local` selects trusted project scope. The TUI equivalents are `/install`, `/remove`, `/update`, and `/packages`. Mutations require confirmation in the TUI, installs are transactional, startup only diagnoses missing configured packages, and Git/pip subprocesses receive a credential-sanitized environment.
