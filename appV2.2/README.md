# appv22

Pi-style coding agent with Hermes-style compaction.

appv22 directly ports and adapts implementation work from Pi and Hermes Agent.
See `NOTICE.md` for upstream attribution and `LICENSE` for the MIT license
terms preserved from those projects.

Install locally from a built wheel:

```bash
uv tool install dist/appv22-*.whl
```

Run:

```bash
appv22 --cwd . --dotenv .env
```

## Attribution

- Pi (`pi/`): coding-agent and TUI behavior, MIT licensed, copyright (c) 2025
  Mario Zechner.
- Hermes Agent (`hermes-agent/`): compaction/session recovery design, MIT
  licensed, copyright (c) 2025 Nous Research.
