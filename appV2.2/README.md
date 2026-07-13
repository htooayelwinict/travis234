# appv22

Pi-style coding agent with Hermes-style compaction.

appv22 directly ports and adapts implementation work from Pi and Hermes Agent.
See `NOTICE.md` for upstream attribution and `LICENSE` for the MIT license
terms preserved from those projects.

## Status

`appv22` is sealed as the stable `appV2.2` baseline. Keep this line limited to bug fixes, security fixes, test hardening, and documentation corrections. Put new advanced agent work in the next version line.

## Requirements

- Python 3.13
- `uv` for local development from the repository root
- Optional provider credentials in `.env` for live LLM runs

## Run from the repository

```bash
uv run python appV2.2/scripts/appv22_tui.py --dotenv .env --cwd .
```

Or use the root npm wrapper:

```bash
npm run tui -- --dotenv .env --cwd .
```

## Install locally from a wheel

```bash
uv tool install dist/appv22-*.whl
```

Then run:

```bash
appv22 --cwd . --dotenv .env
```

## Test

From the repository root:

```bash
PYTHONPATH=appV2.2 .venv/bin/python -m pytest appV2.2/tests -q
```

Expected result for the sealed baseline: `583 passed`.

## Environment

Copy the root template and set the worker provider values needed for live model calls:

```bash
cp .env.example .env
```

Minimum live-run settings:

```text
APPV2_WORKER_LLM_ENABLED=true
APPV2_WORKER_LLM_API_KEY=...
APPV2_WORKER_LLM_BASE_URL=https://openrouter.ai/api/v1
```

## Attribution

- Pi (`pi/`): coding-agent and TUI behavior, MIT licensed, copyright (c) 2025
  Mario Zechner.
- Hermes Agent (`hermes-agent/`): compaction/session recovery design, MIT
  licensed, copyright (c) 2025 Nous Research.
