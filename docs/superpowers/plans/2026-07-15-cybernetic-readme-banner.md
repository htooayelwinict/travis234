# Cybernetic README Banner Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the restrained Travis234 README masthead with an accessible hybrid Neural HUD SVG and textual terminal boot strip.

**Architecture:** Keep the existing local `docs/assets/travis234-banner.svg` asset boundary and rebuild its internal vector composition without remote resources or animation. Add a plain-text boot fallback directly below the image in `README.md`, and protect the contract with a focused brand test that parses the SVG as XML and validates the README integration.

**Tech Stack:** Markdown, static SVG 1.1-compatible XML, Python 3.13 `xml.etree.ElementTree`, pytest, ImageMagick or an available SVG renderer for visual QA, Python packaging, Docker.

## Global Constraints

- Preserve the 1400-by-420 responsive SVG canvas.
- Use only embedded SVG primitives; no remote fonts, scripts, images, animation, or tracking resources.
- Preserve informative SVG `<title>` and `<desc>` metadata and meaningful README image alt text.
- Keep meaningful boot status as selectable README text outside the image.
- Do not alter runtime behavior, versioning, extension semantics, compaction, session state, or the context envelope.
- Do not add raster assets or third-party runtime dependencies.
- Preserve every README section below the masthead, including the extension-flag documentation already added in the working tree.
- Do not stage or commit `appv231/`, `.env`, credentials, or temporary TUI artifacts.

---

### Task 1: Add the banner integration contract

**Files:**
- Modify: `tests/test_brand_contract.py`
- Test: `tests/test_brand_contract.py`

**Interfaces:**
- Consumes: repository-root `README.md` and `docs/assets/travis234-banner.svg`.
- Produces: `test_readme_uses_accessible_local_cybernetic_banner() -> None`, a release guard for the local SVG, accessibility metadata, safe static markup, cybernetic status copy, and textual fallback.

- [x] **Step 1: Write the failing test**

Add imports and the focused contract:

```python
from xml.etree import ElementTree


def test_readme_uses_accessible_local_cybernetic_banner() -> None:
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    banner_path = ROOT / "docs" / "assets" / "travis234-banner.svg"
    banner = banner_path.read_text(encoding="utf-8")
    root = ElementTree.fromstring(banner)
    namespace = {"svg": "http://www.w3.org/2000/svg"}

    assert 'src="docs/assets/travis234-banner.svg"' in readme
    assert 'alt="Travis234 cybernetic terminal coding agent"' in readme
    assert "TRAVIS234 // NEURAL TERMINAL ONLINE" in readme
    assert "[AGENT:READY]" in readme
    assert "[CONTEXT:COMPACT]" in readme
    assert "[TOOLS:BOUNDED]" in readme
    assert "[RUNTIME:PERSISTENT]" in readme
    assert root.attrib["viewBox"] == "0 0 1400 420"
    assert root.find("svg:title", namespace) is not None
    assert root.find("svg:desc", namespace) is not None
    assert root.find(".//svg:script", namespace) is None
    assert root.find(".//svg:animate", namespace) is None
    assert root.find(".//svg:animateTransform", namespace) is None
    assert "NEURAL TERMINAL" in banner
    assert "AGENT // READY" in banner
    assert "CONTEXT // COMPACT" in banner
    assert "https://" not in banner
    assert "http://" not in banner.replace("http://www.w3.org/2000/svg", "")
```

- [x] **Step 2: Run the test to verify it fails**

Run:

```bash
.venv/bin/python -m pytest tests/test_brand_contract.py::test_readme_uses_accessible_local_cybernetic_banner -q
```

Expected: `FAIL` because the current README does not contain the new alt text or boot sequence and the existing SVG does not contain the Neural HUD status labels.

### Task 2: Build the Neural HUD hero and boot strip

**Files:**
- Modify: `docs/assets/travis234-banner.svg`
- Modify: `README.md`
- Test: `tests/test_brand_contract.py`

**Interfaces:**
- Consumes: the exact markers enforced by Task 1 and the existing centered README masthead structure.
- Produces: a self-contained `1400x420` static SVG and a two-line selectable boot fallback.

- [x] **Step 1: Replace the SVG composition**

Rebuild `docs/assets/travis234-banner.svg` with these exact layers, back to front:

```text
1. rounded #050712 background with cyan/violet radial glows
2. 32 px circuit grid and 8 px low-opacity scanline patterns
3. clipped circuit paths terminating in luminous node circles
4. chamfered outer frame, corner targeting brackets, and top telemetry rail
5. left hexagonal neural-terminal core with terminal chevron and orbit nodes
6. central TRAVIS234 wordmark and NEURAL TERMINAL // CODING AGENT label
7. right telemetry panel containing AGENT // READY and CONTEXT // COMPACT
8. bottom status rail containing BOUNDED TOOLS, PERSISTENT SESSIONS,
   EXTENSION BUS, and PROVIDER NEUTRAL
```

Use system font stacks only. Keep primary text at `#f4f8ff` and secondary text no darker than `#9fb4d8` against the dark background. Use cyan `#38f8d4`, blue `#4ba3ff`, violet `#a86cff`, and pink `#ff4fd8` as accents. Filters may provide restrained glow, but every label must remain legible without the filter.

- [x] **Step 2: Add the README boot fallback**

Change the opening image alt text and insert the centered boot strip before the badge row:

````markdown
<p align="center">
  <img src="docs/assets/travis234-banner.svg" alt="Travis234 cybernetic terminal coding agent" width="100%" />
</p>

```text
TRAVIS234 // NEURAL TERMINAL ONLINE
[AGENT:READY] [CONTEXT:COMPACT] [TOOLS:BOUNDED] [RUNTIME:PERSISTENT]
```
````

Do not change the badge row, product description, navigation, or any later technical documentation.

- [x] **Step 3: Run the focused contract**

Run:

```bash
.venv/bin/python -m pytest tests/test_brand_contract.py::test_readme_uses_accessible_local_cybernetic_banner -q
```

Expected: `1 passed`.

- [x] **Step 4: Run the complete brand contract**

Run:

```bash
.venv/bin/python -m pytest tests/test_brand_contract.py -q
```

Expected: all brand-contract tests pass with no former-product or state-path regressions.

### Task 3: Render and inspect the visual result

**Files:**
- Verify: `docs/assets/travis234-banner.svg`
- Generated outside repository: `/tmp/travis234-banner.png`

**Interfaces:**
- Consumes: the Task 2 SVG.
- Produces: visual evidence that the complete 1400-by-420 canvas renders without clipping or layout defects; no generated image enters Git.

- [x] **Step 1: Parse the SVG independently**

Run:

```bash
.venv/bin/python -c 'from xml.etree import ElementTree; ElementTree.parse("docs/assets/travis234-banner.svg"); print("svg_xml=ok")'
```

Expected: `svg_xml=ok`.

- [x] **Step 2: Render to a temporary PNG**

Use the first available local renderer:

```bash
magick -background none docs/assets/travis234-banner.svg /tmp/travis234-banner.png
```

If ImageMagick is unavailable, use `rsvg-convert` or macOS Quick Look without adding dependencies to the repository. Expected: a 1400-by-420 PNG outside the checkout.

- [x] **Step 3: Visually inspect the rendered canvas**

Inspect `/tmp/travis234-banner.png` at original detail. Confirm:

- the full wordmark and all telemetry labels are inside the frame;
- the core icon is recognizable and balanced against the right console;
- glow does not obscure text;
- cyan, blue, violet, and pink accents remain distinct;
- the composition remains readable when scaled to typical README width.

If any item fails, adjust only the SVG geometry or color values and repeat Tasks 2 Step 3 through Task 3 Step 3.

### Task 4: Verify and publish the integrated Travis234 change

**Files:**
- Verify: all intentional working-tree files
- Exclude: `appv231/`, `.env`, temporary TUI roots, `/tmp/travis234-banner.png`

**Interfaces:**
- Consumes: the already-complete extension-flag implementation, its README documentation, and the approved banner.
- Produces: one verified main-branch history whose remote tip matches the local commit.

- [x] **Step 1: Run source and documentation gates**

Run:

```bash
.venv/bin/python -m pytest -q
.venv/bin/python scripts/verify_acceptance.py --parity-json
.venv/bin/python scripts/check_repository_hygiene.py
.venv/bin/python -m compileall -q travis tests evals scripts
git diff --check
```

Expected: the full Python suite passes; 89 parity contracts resolve as 78 Pi and 11 Hermes with zero invalid contracts; every hygiene counter is zero; compileall and whitespace checks exit zero.

- [x] **Step 2: Run launcher and package gates**

Run:

```bash
npm --prefix packages/travis234-cli test
npm --prefix packages/travis234-cli run pack:dry-run
.venv/bin/python -m build
```

Expected: 20 npm tests pass; the npm dry-run contains exactly five public files; Python wheel and sdist build successfully.

- [x] **Step 3: Inspect Python archives**

List the wheel and sdist entries and verify neither contains `appv231/`, Pi/Hermes oracle trees, `.env`, or `docs/superpowers/`. Expected forbidden count: zero for both archives.

- [x] **Step 4: Build and smoke the release container**

Run:

```bash
docker build --no-cache -f Dockerfile.release -t travis234:extension-flags-local .
.venv/bin/python -m evals.container_smoke --image travis234:extension-flags-local
```

Expected: no-cache build succeeds and the installed container smoke exits zero, including dynamic extension-flag help.

- [x] **Step 5: Stage only intentional Travis234 files**

Use explicit `git add` paths for the extension implementation, its tests/docs, the banner asset, README, and both approved spec/plan files. Inspect `git diff --cached --name-only` and confirm it contains no `appv231/`, `.env`, or temporary artifact.

- [x] **Step 6: Commit and push main**

Run:

```bash
git commit -m "feat: add production-safe extension CLI flags"
git push origin main
```

Expected: the push is fast-forward and `git rev-parse HEAD` equals `git rev-parse origin/main`. The only remaining untracked repository entry may be the intentionally excluded `appv231/` oracle clone.
