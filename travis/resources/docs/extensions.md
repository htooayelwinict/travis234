# Travis234 extensions

Travis234 loads Python extension files from the global `~/.travis234/extensions/` directory and, for trusted projects, from `.travis234/extensions/`. Additional extension paths may be configured in settings.

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

The extension API also supports tools, shortcuts, event handlers, providers, widgets, and session actions through the `travis` registration object and command context. Inspect the installed Travis234 API or focused local tests before using an unfamiliar registration contract.
