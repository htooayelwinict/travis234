# OffSec README Landing Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an evidence-led Travis234 OffSec product lane to the main README landing page.

**Architecture:** Keep the existing coding-agent hero and quick start intact. Add a self-contained OffSec section immediately after the core feature cards, link it from the README navigation, and make its host-native and npx paths explicit. This is Markdown-only; it does not alter application runtime behavior.

**Tech Stack:** GitHub-flavored Markdown, Python one-off README validation.

## Global Constraints

- Modify only `README.md` for the landing copy; do not stage the existing untracked documentation files.
- Preserve the core Travis234 position as a coding agent and avoid autonomous-attack or outcome promises.
- Describe only shipped OffSec capabilities: host-native/PyPI use, published npx launcher, Kali image, persistent sessions, compaction, managed processes, skills, and provider flexibility.
- State that npx proxy services on Docker Desktop use `host.docker.internal`; host-native execution is preferred for VPN and local evidence.
- Use published names exactly: `travis234-offsec` and `@htooayelwinict/travis234-offsec`.

---

### Task 1: Add the OffSec product lane to the README

**Files:**
- Modify: `README.md:24-95`

**Interfaces:**
- Consumes: existing README anchor navigation, published OffSec package names, and the OffSec operator manual.
- Produces: `#travis234-offsec` navigation anchor, install commands, capability cards, operator-control statement, and documentation link.

- [ ] **Step 1: Add an OffSec navigation link**

Update the centered navigation block so the new section is discoverable:

```html
<a href="#travis234-offsec">OffSec</a> ·
```

Place it after `Quick start` and before `Architecture`.

- [ ] **Step 2: Add the Markdown landing section after the core feature table**

Use the following content, keeping the existing core quick-start section below it:

```markdown
## Travis234 OffSec

### A persistent AI terminal for investigation work

Travis234 OffSec is the Kali-ready product lane for DFIR, authorized labs,
security research, and private-environment investigation. It keeps the same
persistent session and compaction runtime while working where the evidence,
tools, and network routes already live.

| Work where the evidence is | Keep the investigation moving |
| --- | --- |
| Run host-native on Kali for VPN routes, private ranges, local artifacts, and installed tools. | Use managed PTYs, follow-up input, tmux, long-running processes, persistent sessions, and compaction for work that exceeds one context window. |

| Bring your model and provider | Sandbox when it fits |
| --- | --- |
| Use supported providers or an OpenAI-compatible proxy such as 9router. Model choice improves reasoning; Travis234 keeps process and session control dependable. | Launch a prebuilt unprivileged Kali image through npx when a disposable environment is useful. |

Install host-native on Kali:

```bash
uv tool install --python 3.13 travis234-offsec
travis234 --cwd ~/casework
```

Or launch the Kali sandbox:

```bash
npx @htooayelwinict/travis234-offsec --cwd ~/casework
```

For an OpenAI-compatible proxy in the sandbox, explicitly supply its dotenv
file. On Docker Desktop, use `host.docker.internal` rather than `localhost` in
the proxy base URL:

```bash
npx @htooayelwinict/travis234-offsec \
  --cwd ~/casework \
  --dotenv ~/.config/travis/9router.env
```

The operator supplies the workspace, network route, target scope, credentials,
and authorization. Use the host-native path when a VPN route or local evidence
must be visible directly. See the [OffSec operator manual](https://github.com/htooayelwinict/travis234/blob/offsec-agent/docs/offsec/manual.md).
```

- [ ] **Step 3: Validate README structure and release names**

Run:

```bash
python3 - <<'PY'
from pathlib import Path

readme = Path("README.md").read_text(encoding="utf-8")
for required in (
    'href="#travis234-offsec"',
    '## Travis234 OffSec',
    'travis234-offsec',
    '@htooayelwinict/travis234-offsec',
    'host.docker.internal',
    'docs/offsec/manual.md',
):
    assert required in readme, required
PY
git diff --check
git diff -- README.md
```

Expected: all assertions pass; diff is limited to the navigation and the new OffSec landing section.

- [ ] **Step 4: Commit only the README**

```bash
git add README.md
git commit -m "docs: add OffSec landing section"
```
