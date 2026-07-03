# <div align="center">ALLTHEBEST</div>

<div align="center">

![Banner](https://capsule-render.vercel.app/api?type=waving&height=320&color=0:0f0c29,30:302b63,70:24243e,100:4a148c&text=ALLTHEBEST&fontSize=72&fontAlignY=34&desc=Pi%20%C3%97%20Hermes%20Python%20Agent%20Runtime&descAlignY=56&animation=fadeIn&fontColor=ffffff&stroke=00d4ff&strokeWidth=2)

[![Python](https://img.shields.io/badge/Python-3.13+-1f2937?style=for-the-badge&logo=python&logoColor=00d4ff)](https://www.python.org/)
[![uv](https://img.shields.io/badge/uv-0.6+-1f2937?style=for-the-badge&logo=astral&logoColor=a855f7)](https://docs.astral.sh/uv/)
[![Pydantic](https://img.shields.io/badge/Pydantic-2.x-1f2937?style=for-the-badge&logo=pydantic&logoColor=0ea5e9)](https://docs.pydantic.dev/)
[![LangGraph](https://img.shields.io/badge/LangGraph-0.6+-1f2937?style=for-the-badge&logo=langchain&logoColor=22c55e)](https://langchain-ai.github.io/langgraph/)
[![Tests](https://img.shields.io/badge/Tests-723%20passing-1f2937?style=for-the-badge&logo=pytest&logoColor=f59e0b)](https://docs.pytest.org/)
[![License](https://img.shields.io/badge/License-MIT-1f2937?style=for-the-badge&logo=opensourceinitiative&logoColor=white)](LICENSE)

**A self-contained Python port of the Pi coding agent, fused with Hermes-style context compaction and runtime guardrails.**

**Status:** `appV2.2` is sealed at tag `v2.2`. Active next-version work now lives in `appV2.3.1/appv231/` on the `next/appv231.1` branch.

</div>

---

## What is this?

`allthebest` is an agent-runtime research workspace. Its active next-version component, **`appv231`** (`appV2.3.1/appv231/`), is branched from the sealed `appV2.2` Python rewrite that combines two influential agent designs:

- **Pi** — the interactive coding-agent loop, differential TUI, and multi-provider LLM abstraction.
- **Hermes** — deterministic + LLM-based context compaction, tool-loop guardrails, and overflow/output-cap recovery.

The result is a terminal-native coding assistant that can read files, run bash commands, edit code, and carry on long multi-turn sessions while keeping context windows under control.

> This repo also houses reference copies of the upstream [`hermes-agent/`](hermes-agent/) and [`pi/`](pi/) codebases for comparison and porting. They are **not** runtime dependencies of `appv231`.

---

## Why It Exists

Most agent runtimes either couple tightly to a single framework or hide their boundaries behind opaque abstractions. `appv231` continues the `appv22` line to:

- Keep the runtime **self-contained** and auditable in pure Python.
- Port proven patterns from Pi and Hermes **without importing their source**.
- Maintain strict architectural boundaries with a regression test that forbids cross-repo imports.
- Run **offline tests** via a faux provider so CI stays fast and deterministic.

---

## Core Features

| Capability | Description |
|---|---|
| **Interactive Coding Agent** | Read, write, edit (diff), bash, grep, find, and ls tools driven by an LLM worker. |
| **Differential TUI** | Live event-driven terminal rendering of assistant messages, tool calls, and status. |
| **Hermes-Style Compaction** | Dual-pass context compression: deterministic pruning + LLM summarization. |
| **Overflow & Output-Cap Recovery** | Detects provider context errors, shrinks context, and resumes. |
| **Tool-Loop Guardrails** | Hard-stop thresholds for repeated failed or non-progressing tool calls. |
| **Multi-Provider LLM Registry** | OpenRouter, OpenAI-compatible, and faux/offline providers with model pattern matching. |
| **Thinking Levels** | Per-request reasoning control via `--thinking` and scoped model cycling. |
| **Session Persistence** | JSONL session store with resume/fork/branch support. |
| **Offline Test Suite** | 723 pytest cases run against the faux provider without network calls. |

---

## Architecture Snapshot

```text
appV2.3.1/appv231/
├── ai/                 # Multi-provider LLM registry, streaming, env config
│   ├── models.py
│   ├── providers/appv2_env.py
│   └── providers/faux.py
├── agent/              # Stateful agent loop, iteration budgets, guardrails
│   ├── agent.py
│   ├── agent_loop.py
│   └── tool_guardrails.py
├── coding_agent/       # Session orchestration, coding tools, prompts
│   ├── agent_session.py
│   ├── tools/
│   └── system_prompt.py
├── compaction/         # Hermes-style dual-pass context compression
│   ├── compressor.py
│   └── timing.py
├── tui/                # Differential terminal UI and interactive loop
│   ├── interactive_mode.py
│   ├── tui.py
│   └── interactive.py
├── app.py              # Composition root: CodingApp
└── cli.py              # argparse entry point
```

The data flow is intentionally simple:

```text
User Input → Agent Loop → LLM Worker → Tool Calls → Tool Results → ... → Assistant Reply
                 ↑_______________________________________________|
                                    (context compaction runs between turns)
```

---

## Quickstart

### Install the sandboxed app globally

This is the recommended user-side install path for `appv231`.

Run directly with npm once the package is published:

```bash
npx @htooayelwinict/appv231 --cwd .
```

Or install the npm launcher globally:

```bash
npm install -g @htooayelwinict/appv231
appv231 --cwd .
```

The npm launcher pulls and runs `ghcr.io/htooayelwinict/appv231:production`.

Repo-local installer:

```bash
npm run install:appv231
```

The installer pulls `ghcr.io/htooayelwinict/appv231:production`, installs the global `appv231-sandbox` command, and verifies the command with a dry run. After installation, run from any directory:

```bash
appv231-sandbox --cwd .
```

Publish the production image first with the `appv231 release image` GitHub Actions workflow, or use the local development fallback below until that image exists.

If your npm global prefix is not writable:

```bash
APPV231_NPM_PREFIX="$HOME/.local" npm run install:appv231
```

Use a specific release image:

```bash
APPV231_IMAGE=ghcr.io/htooayelwinict/appv231:2.3.1 npm run install:appv231
```

Development fallback, build the image locally from this checkout instead of pulling:

```bash
APPV231_IMAGE=appv231:local APPV231_BUILD_LOCAL=1 npm run install:appv231
```

Playwright is not installed in the base appv231 package or sandbox image. For browser automation development, install the optional extra from `appV2.3.1`:

```bash
python -m pip install ".[browser]"
```

### 1. Environment

Requires **Python 3.13** (see `.python-version`).

```bash
# Copy the environment template
cp .env.example .env
```

Edit `.env` and set at least:

```text
APPV231_WORKER_LLM_ENABLED=true
APPV231_WORKER_LLM_API_KEY=sk-or-v1-...
APPV231_WORKER_LLM_BASE_URL=https://openrouter.ai/api/v1
```

Optional:

```text
APPV231_WORKER_LLM_MODEL=qwen/qwen3-coder-next
APPV231_WORKER_LLM_PROVIDER_SORT=latency
APPV231_WORKER_LLM_MAX_TOKENS=8192
```

### 2. Install

```bash
uv sync
```

### 3. Run the Interactive TUI

```bash
uv run python appV2.3.1/scripts/appv231_tui.py --cwd ./your-project
```

Or via the npm wrapper:

```bash
npm run tui -- --cwd ./your-project
```

When `--dotenv` is omitted, the CLI uses the nearest `.env` in the working directory (`--cwd`) or its parents. Use `--dotenv path/to/.env` to force a specific file.

### 4. Run Tests

```bash
PYTHONPATH=appV2.3.1 .venv/bin/python -m pytest appV2.3.1/tests -q
```

Expected: **723 passing**.

---

## Usage Examples

### One-shot prompt

```bash
uv run python appV2.3.1/scripts/appv231_tui.py --tui --cwd ./my-project "refactor src/utils.py"
```

### Plain REPL loop

```bash
uv run python appV2.3.1/scripts/appv231_tui.py --plain --cwd ./my-project
```

### Override model and thinking level

```bash
uv run python appV2.3.1/scripts/appv231_tui.py \
  --model openrouter/moonshotai/kimi-k2.6 \
  --thinking medium \
  --cwd ./my-project
```

### Export a session to HTML

```bash
uv run python appV2.3.1/scripts/appv231_tui.py \
  --export session.jsonl \
  output.html
```

### Enable tool-loop hard-stop guardrails for debugging

```bash
uv run python appV2.3.1/scripts/appv231_tui.py \
  --tool-loop-hard-stop \
  --cwd ./my-project
```

---

## Example Runtime Invocation

```python
from appv231.app import CodingApp
from appv231.ai.env_config import load_model_config
from appv231.ai.models import get_default_model_for_provider
from appv231.ai.register_builtins import register_builtin_providers
from appv231.ai.types import Model

register_builtin_providers(dotenv_path=".env")
config = load_model_config("APPV231_WORKER_LLM", ".env")
model_id = config.model or get_default_model_for_provider("openrouter") or "moonshotai/kimi-k2.6"
model = Model(
    id=model_id,
    name=model_id,
    api="openai-completions",
    provider="openrouter",
    base_url=config.base_url,
    reasoning=False,
    context_window=128000,
    max_tokens=config.max_tokens or 8192,
)

app = CodingApp(cwd="./my-project", model=model, enable_tui=False)
app.run_turn("List the 5 largest Python files")
```

---

## Testing & Quality

- **Framework:** pytest
- **Total tests:** 723 passing
- **Offline coverage:** faux provider enables full test runs without API keys
- **Coupling guard:** `appV2.3.1/tests/test_no_appv21_coupling.py` ensures `appv231` never imports from `pi/` or `hermes-agent/`

Run the suite:

```bash
PYTHONPATH=appV2.3.1 .venv/bin/python -m pytest appV2.3.1/tests -q
```

Run with coverage:

```bash
PYTHONPATH=appV2.3.1 .venv/bin/python -m pytest appV2.3.1/tests --cov=appV2.3.1/appv231 --cov-report=term-missing
```

---

## Project Layout

```text
allthebest/
├── appV2.2/                    # Sealed Python baseline (appv22), tag v2.2
│   ├── appv22/                 # Source package
│   ├── scripts/appv22_tui.py   # Main runtime launcher
│   └── tests/                  # 583 pytest cases
├── appV2.3.1/                  # Active next-version app (appv231)
│   ├── appv231/                # Source package
│   ├── scripts/appv231_tui.py  # Main runtime launcher
│   └── tests/                  # Current appV2.3.1 suite
├── docs/                       # Local/reference documentation and reports
├── hermes-agent/               # Reference: upstream Hermes Agent (untracked)
├── pi/                         # Reference: upstream Pi monorepo (untracked)
├── .env.example                # Environment template
├── pyproject.toml              # Python project metadata
├── package.json                # npm wrapper scripts
├── uv.lock                     # uv lockfile
└── README.md                   # You are here
```

---

## Design Notes

- **`appv231` is intentionally descriptive at the boundaries.** The decompressor/planner/worker pipeline from earlier iterations has been superseded by the agent loop.
- **The agent loop is bounded.** Iteration budgets, tool-loop hard stops, and explicit step types prevent runaway behavior.
- **Compaction is first-class.** Context is compressed preflight, post-response, and on overflow, not just as a last resort.
- **Provider errors are recoverable.** Context-overflow and output-cap errors trigger shrink-and-resume rather than crashing the session.
- **No upstream source coupling.** `appv231` does not import TypeScript code from `pi/` or Python code from `hermes-agent/`.

---

## Release Boundary

`appV2.2` is the sealed stable line for the Python Pi/Hermes runtime in this repository. `appV2.3.1` is the active appv231 next-version line.

Allowed changes in this line:

- Bug fixes
- Security fixes
- Test hardening
- Documentation corrections

Not allowed in this line:

- New advanced agent features
- Runtime architecture rewrites
- New version experiments

Start those in `appV2.3.1` so `appV2.2` remains a known-good baseline.

## Documentation

- [`hermes-agent/README.md`](hermes-agent/README.md) — Hermes Agent user guide.
- [`pi/README.md`](pi/README.md) — Pi agent harness docs.

---

## Roadmap / Status

- [x] Self-contained Python port of Pi coding-agent loop
- [x] Hermes dual-pass context compaction integration
- [x] Differential TUI with live event rendering
- [x] Overflow and output-cap recovery
- [x] Tool-loop guardrails
- [x] 723 offline pytest tests
- [x] Seal `appV2.2` as the stable baseline
- [x] Create `appV2.3.1/appv231` as the next version line
- [ ] Move advanced features into `appV2.3.1`

---

<div align="center">

**Built for fast iteration, clear boundaries, and zero hand-wavy runtime behavior.**

<sub>Reference upstream code lives in <code>hermes-agent/</code> and <code>pi/</code>; the active runtime is <code>appV2.3.1/appv231/</code>. The sealed baseline remains <code>appV2.2/appv22/</code>.</sub>

</div>
