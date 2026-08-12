# Travis234 Bundled Ghost MCP Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship a self-contained `travis234-ghost-mcp` package that executes an embedded Travis-specific Ghost computer-use MCP server without a Ghost `mcp.json` entry or a separate Ghost installation.

**Architecture:** Restore the handed-off proxy-based MCP adapter, then add one process-local registry for immutable packaged stdio servers. A new macOS arm64 Python distribution carries a pinned, adapted Ghost Swift source snapshot, builds an embedded executable into its wheel, registers that executable in memory, and exposes bounded Travis TUI setup and doctor commands.

**Tech Stack:** Python 3.13, Travis234 extensions and package manager, `mcp>=2,<3`, pytest/pytest-asyncio, Swift 6.2+, Swift Testing, macOS Accessibility and ScreenCaptureKit, setuptools/wheel, npm launcher tests, and Docker release smoke tests.

## Global Constraints

- Product and CLI names are `Travis234` and `travis234`; the Python import package remains `travis`.
- The generic adapter remains optional and Python 3.13-only with `mcp>=2,<3`.
- The first Ghost add-on release supports macOS 14+ on Apple Silicon only.
- `travis234-ghost-mcp` starts at version `0.1.0`; the adapter receives only the smallest compatible patch increment.
- All mutable Ghost-owned data lives under `~/.travis234/ghost-mcp`; no `.ghost-os` path, fallback, or migration alias is permitted.
- Ghost is registered in memory and executed from its installed package; no Ghost `mcp.json` entry, runtime archive extraction, Homebrew installation, or Claude configuration is permitted.
- The optional multi-gigabyte vision model is not bundled and is downloaded only through an explicit setup action.
- Preserve MCP request bounds, credential redaction, cancellation, child cleanup, core agent-loop ordering, iteration budgets, and bounded parallel execution.
- Add a failing regression before every behavior change and run focused tests before each commit.
- Do not stage or commit `.disposable/ghost-os`, its `.git`, `.build`, caches, or generated release archives.
- Do not publish, tag, push, or mutate external package registries as part of this plan.
- Before completion, run the complete Python suites, Swift tests, npm launcher tests, root/adapter/add-on builds, clean installed-package checks, the release-container smoke, and the permissioned local TUI acceptance.

---

## File and Ownership Map

### Existing files restored or modified

- `packages/travis234-mcp-adapter/travis234_mcp_adapter/config.py`: existing file-based MCP configuration plus conversion/merge of trusted packaged descriptors.
- `packages/travis234-mcp-adapter/travis234_mcp_adapter/extension.py`: one idempotent adapter installation and session-owned packaged/configured server merge.
- `packages/travis234-mcp-adapter/travis234_mcp_adapter/proxy_tool.py`: connection-free status reports packaged servers and shadowed obsolete config.
- `packages/travis234-mcp-adapter/travis234_mcp_adapter/__init__.py`: exports only the supported packaged-server API.
- `packages/travis234-mcp-adapter/tests/test_config.py`: merge precedence and config-preservation regressions.
- `packages/travis234-mcp-adapter/tests/test_extension.py`: idempotence, lifecycle, and packaged-server integration regressions.
- `packages/travis234-mcp-adapter/tests/test_proxy_tool.py`: status and shadow-warning regressions.
- `packages/travis234-mcp-adapter/tests/test_distribution.py`: adapter version and public API distribution contract.
- `README.md`, `packages/travis234-mcp-adapter/README.md`: product-level install and adapter behavior.
- `pyproject.toml`, `package.json`, `packages/travis234-cli/package.json`: synchronized Travis234 patch version.
- `tests/test_distribution_contract.py`, `tests/test_pyproject_dependencies.py`: root release and optional-package contracts.

### New adapter file

- `packages/travis234-mcp-adapter/travis234_mcp_adapter/packaged_servers.py`: validates, stores, snapshots, and converts process-local immutable packaged stdio descriptors.
- `packages/travis234-mcp-adapter/tests/test_packaged_servers.py`: direct validation and registry tests.

### New Ghost add-on Python files

- `packages/travis234-ghost-mcp/pyproject.toml`: package metadata, adapter dependency, Travis extension resource, and included data.
- `packages/travis234-ghost-mcp/setup.py`: deterministic Swift release build and `macosx_14_0_arm64` non-pure wheel tagging.
- `packages/travis234-ghost-mcp/MANIFEST.in`: exact sdist allowlist for Swift source, assets, provenance, and notices.
- `packages/travis234-ghost-mcp/README.md`: install, setup, permissions, state, platform, and attribution.
- `packages/travis234-ghost-mcp/UPSTREAM.json`: pinned Ghost repository, commit, version, license, and adaptation list.
- `packages/travis234-ghost-mcp/LICENSE`: Travis234 add-on license.
- `packages/travis234-ghost-mcp/THIRD_PARTY_NOTICES.md`: Ghost, AXorcist, Commander, and swift-log notices.
- `packages/travis234-ghost-mcp/extensions/ghost_mcp.py`: extension loader that adds its payload root to `sys.path` and imports the add-on factory.
- `packages/travis234-ghost-mcp/travis234_ghost_mcp/__init__.py`: add-on version.
- `packages/travis234-ghost-mcp/travis234_ghost_mcp/host.py`: platform validation and package-relative executable/resource paths.
- `packages/travis234-ghost-mcp/travis234_ghost_mcp/commands.py`: bounded, sanitized setup/doctor subprocesses and custom-message handlers.
- `packages/travis234-ghost-mcp/travis234_ghost_mcp/extension.py`: packaged-server registration plus `/ghost-setup` and `/ghost-doctor` registration.
- `packages/travis234-ghost-mcp/travis234_ghost_mcp/bin/.gitkeep`: source-tree placeholder; the wheel build replaces it with the compiled `ghost` executable.
- `packages/travis234-ghost-mcp/travis234_ghost_mcp/assets/GHOST-MCP.md`: packaged server instructions.
- `packages/travis234-ghost-mcp/travis234_ghost_mcp/assets/recipes/*.json`: four pinned bundled recipes.
- `packages/travis234-ghost-mcp/travis234_ghost_mcp/assets/vision-sidecar/ghost-vision`: Travis state-root-aware launcher.
- `packages/travis234-ghost-mcp/travis234_ghost_mcp/assets/vision-sidecar/server.py`: Travis state-root-aware optional vision service.
- `packages/travis234-ghost-mcp/travis234_ghost_mcp/assets/vision-sidecar/requirements.txt`: pinned upstream vision requirements.

### Vendored and adapted Swift files

- `packages/travis234-ghost-mcp/vendor/ghost-os/Package.swift`
- `packages/travis234-ghost-mcp/vendor/ghost-os/Package.resolved`
- `packages/travis234-ghost-mcp/vendor/ghost-os/Sources/GhostOS/Actions/Actions.swift`
- `packages/travis234-ghost-mcp/vendor/ghost-os/Sources/GhostOS/Actions/FocusManager.swift`
- `packages/travis234-ghost-mcp/vendor/ghost-os/Sources/GhostOS/Common/LocatorBuilder.swift`
- `packages/travis234-ghost-mcp/vendor/ghost-os/Sources/GhostOS/Common/Logger.swift`
- `packages/travis234-ghost-mcp/vendor/ghost-os/Sources/GhostOS/Common/TravisPaths.swift`
- `packages/travis234-ghost-mcp/vendor/ghost-os/Sources/GhostOS/Host/TravisDoctor.swift`
- `packages/travis234-ghost-mcp/vendor/ghost-os/Sources/GhostOS/Host/TravisSetup.swift`
- `packages/travis234-ghost-mcp/vendor/ghost-os/Sources/GhostOS/Common/Types.swift`
- `packages/travis234-ghost-mcp/vendor/ghost-os/Sources/GhostOS/Learning/*.swift`
- `packages/travis234-ghost-mcp/vendor/ghost-os/Sources/GhostOS/MCP/*.swift`
- `packages/travis234-ghost-mcp/vendor/ghost-os/Sources/GhostOS/Perception/*.swift`
- `packages/travis234-ghost-mcp/vendor/ghost-os/Sources/GhostOS/Recipes/*.swift`
- `packages/travis234-ghost-mcp/vendor/ghost-os/Sources/GhostOS/Screenshot/ScreenCapture.swift`
- `packages/travis234-ghost-mcp/vendor/ghost-os/Sources/GhostOS/Vision/*.swift`
- `packages/travis234-ghost-mcp/vendor/ghost-os/Sources/ghost/main.swift`
- `packages/travis234-ghost-mcp/vendor/ghost-os/Tests/GhostOSTests/LocatorBuilderTests.swift`
- `packages/travis234-ghost-mcp/vendor/ghost-os/Tests/GhostOSTests/TravisPathsTests.swift`
- `packages/travis234-ghost-mcp/vendor/ghost-os/Tests/GhostOSTests/MCPToolsTests.swift`
- `packages/travis234-ghost-mcp/vendor/ghost-os/Tests/GhostOSTests/SetupContractTests.swift`

### New Ghost add-on tests and evaluation

- `packages/travis234-ghost-mcp/tests/conftest.py`: source import path and isolated-home helpers.
- `packages/travis234-ghost-mcp/tests/test_provenance.py`: pinned snapshot and notice allowlist.
- `packages/travis234-ghost-mcp/tests/test_state_contract.py`: forbidden-path and external-config mutation scan.
- `packages/travis234-ghost-mcp/tests/test_host.py`: platform and executable validation.
- `packages/travis234-ghost-mcp/tests/test_commands.py`: bounded setup/doctor invocation and safe environment.
- `packages/travis234-ghost-mcp/tests/test_extension.py`: in-memory server and TUI command registration.
- `packages/travis234-ghost-mcp/tests/test_distribution.py`: sdist/wheel contents, tags, executable mode, signature, and clean installation.
- `packages/travis234-ghost-mcp/tests/test_protocol.py`: real embedded-binary MCP catalog, call, cancellation, and cleanup.
- `evals/bundled_ghost_mcp_smoke.py`: isolated installed-wheel protocol smoke with bounded JSON evidence.
- `tests/test_eval_harness.py`: smoke harness contract.
- `docs/verification/main-bundled-ghost-mcp.md`: exact final automated and manual evidence.

---

### Task 1: Restore the handed-off proxy MCP adapter baseline

**Files:**
- Restore to `717a9d3`: all tracked paths changed by commits `c36a2db` through `a044c62`
- Preserve: `docs/superpowers/specs/2026-08-12-travis234-bundled-ghost-mcp-design.md`
- Preserve: `docs/superpowers/plans/2026-08-12-travis234-bundled-ghost-mcp.md`

**Interfaces:**
- Consumes: handed-off base commit `717a9d3` and the current linear redesign commits.
- Produces: adapter version 0.1.1 with one `mcp` proxy tool, additive root `--mcp`, and no native `mcp__server__tool` redesign.

- [ ] **Step 1: Record the rollback boundary**

Run:

```bash
git status --short
git log --oneline 717a9d3..HEAD
git diff --name-status 717a9d3..a044c62
```

Expected: only the committed redesign plus the approved bundled-Ghost spec/plan are present; `.disposable/ghost-os` is absent from status.

- [ ] **Step 2: Revert only the superseded redesign commits without discarding the new spec and plan**

Run the owned commits newest-first:

```bash
git revert --no-commit \
  a044c62 8bc87b4 c6860c1 a241d2e 8be312a 648eac2 d6d8953 a514ae1 \
  a10b758 9e69567 b4d973f 842573d 4df334d 2a47860 20ba6fb c36a2db
```

Expected: the application and adapter content match `717a9d3`; the approved bundled-Ghost spec and this plan remain.

- [ ] **Step 3: Verify the restored content exactly**

Run:

```bash
git diff --exit-code 717a9d3 -- \
  README.md pyproject.toml package.json packages/travis234-cli/package.json \
  travis tests packages/travis234-mcp-adapter evals/native_mcp_smoke.py \
  docs/superpowers/specs/2026-08-12-native-mcp-tool-registration-design.md \
  docs/superpowers/plans/2026-08-12-native-mcp-tool-registration.md \
  docs/verification/main-native-mcp-tool-registration.md
```

Expected: exit zero. New bundled-Ghost design/plan paths are deliberately outside the comparison.

- [ ] **Step 4: Run the restored focused suites**

Run:

```bash
.venv/bin/python -m pytest -q \
  tests/test_cli_runtime_controls.py \
  tests/test_coding_policy_and_extensions.py \
  tests/test_extension_host_runtime.py
(cd packages/travis234-mcp-adapter && ../../.venv/bin/python -m pytest -q)
```

Expected: all tests pass and the adapter distribution reports 0.1.1 with one proxy tool.

- [ ] **Step 5: Commit the rollback**

```bash
git add -u
git commit -m "revert: restore proxy MCP adapter baseline"
```

---

### Task 2: Add immutable packaged-server registration to the adapter

**Files:**
- Create: `packages/travis234-mcp-adapter/travis234_mcp_adapter/packaged_servers.py`
- Create: `packages/travis234-mcp-adapter/tests/test_packaged_servers.py`
- Modify: `packages/travis234-mcp-adapter/travis234_mcp_adapter/config.py`
- Modify: `packages/travis234-mcp-adapter/travis234_mcp_adapter/extension.py`
- Modify: `packages/travis234-mcp-adapter/travis234_mcp_adapter/proxy_tool.py`
- Modify: `packages/travis234-mcp-adapter/travis234_mcp_adapter/__init__.py`
- Modify: `packages/travis234-mcp-adapter/pyproject.toml`
- Modify: `packages/travis234-mcp-adapter/tests/test_config.py`
- Modify: `packages/travis234-mcp-adapter/tests/test_extension.py`
- Modify: `packages/travis234-mcp-adapter/tests/test_proxy_tool.py`
- Modify: `packages/travis234-mcp-adapter/tests/test_distribution.py`

**Interfaces:**
- Consumes: existing `ServerConfig`, `LoadedConfig`, `McpRuntime`, and proxy status behavior.
- Produces: `PackagedServer`, `register_packaged_server(server)`, `get_packaged_servers()`, and `merge_packaged_servers(config)`.
- `PackagedServer` signature: `PackagedServer(name: str, package_root: Path, command: Path, args: tuple[str, ...] = ("mcp",), request_timeout_ms: int | None = None)`.
- `merge_packaged_servers` returns `PackagedConfig(config: LoadedConfig, shadowed_configured_names: tuple[str, ...])` with packaged entries winning exact-name collisions.

- [ ] **Step 1: Write failing descriptor validation and immutability tests**

Add tests with these essential assertions:

```python
def test_packaged_server_requires_absolute_executable_inside_package(tmp_path: Path) -> None:
    root = tmp_path / "payload"
    root.mkdir()
    outside = tmp_path / "ghost"
    outside.write_text("binary", encoding="utf-8")
    outside.chmod(0o755)
    with pytest.raises(ValueError, match="inside package root"):
        PackagedServer(name="ghost-os", package_root=root, command=outside)


def test_registration_is_idempotent_but_rejects_substitution(ghost_descriptor) -> None:
    register_packaged_server(ghost_descriptor)
    register_packaged_server(ghost_descriptor)
    assert tuple(get_packaged_servers()) == ("ghost-os",)
    replacement = ghost_descriptor.command.parent / "other"
    replacement.write_text("replacement", encoding="utf-8")
    replacement.chmod(0o755)
    with pytest.raises(ValueError, match="already registered"):
        register_packaged_server(replace(ghost_descriptor, command=replacement))
```

Use an `autouse` monkeypatch fixture that replaces the module registry with an empty dictionary for every test instead of exposing a production reset API.

- [ ] **Step 2: Run the new direct tests and witness failure**

Run:

```bash
(cd packages/travis234-mcp-adapter && ../../.venv/bin/python -m pytest -q tests/test_packaged_servers.py)
```

Expected: collection fails because `packaged_servers.py` and its exported types do not exist.

- [ ] **Step 3: Implement the minimal frozen descriptor and process registry**

Implement this public shape:

```python
@dataclass(frozen=True)
class PackagedServer:
    name: str
    package_root: Path
    command: Path
    args: tuple[str, ...] = ("mcp",)
    request_timeout_ms: int | None = None

    def __post_init__(self) -> None:
        root = self.package_root.expanduser().resolve()
        command = self.command.expanduser().resolve()
        try:
            command.relative_to(root)
        except ValueError as error:
            raise ValueError("Packaged MCP server command must be inside package root") from error
        if not self.name.strip() or self.name != self.name.strip():
            raise ValueError("Packaged MCP server name must be non-empty and trimmed")
        if not command.is_file() or not os.access(command, os.X_OK):
            raise ValueError("Packaged MCP server command must be an executable file")
        if not isinstance(self.args, tuple) or not all(isinstance(value, str) for value in self.args):
            raise ValueError("Packaged MCP server args must be a tuple of strings")
        if self.request_timeout_ms is not None and (
            isinstance(self.request_timeout_ms, bool) or self.request_timeout_ms <= 0
        ):
            raise ValueError("Packaged MCP request timeout must be positive")
        object.__setattr__(self, "package_root", root)
        object.__setattr__(self, "command", command)


_REGISTRY: dict[str, PackagedServer] = {}


def register_packaged_server(server: PackagedServer) -> None:
    existing = _REGISTRY.get(server.name)
    if existing is None:
        _REGISTRY[server.name] = server
        return
    if existing != server:
        raise ValueError(f'Packaged MCP server "{server.name}" is already registered')


def get_packaged_servers() -> Mapping[str, PackagedServer]:
    return MappingProxyType(dict(sorted(_REGISTRY.items())))
```

Return a sorted `MappingProxyType` snapshot. Accept the same frozen descriptor twice as a no-op and reject a different descriptor under the same name. Do not add environment, URL, header, shell, or `PATH` fields.

- [ ] **Step 4: Add failing merge precedence and status tests**

Add focused tests equivalent to:

```python
def test_packaged_server_shadows_same_named_file_config_without_rewriting(config_tree, ghost_descriptor) -> None:
    configured = config_tree.write_global_travis("ghost-os", {"command": "/tmp/external-ghost"})
    loaded = load_config(config_tree.cwd, config_tree.home, False)
    register_packaged_server(ghost_descriptor)
    merged = merge_packaged_servers(loaded)
    assert merged.config.servers["ghost-os"].command == str(ghost_descriptor.command)
    assert merged.shadowed_configured_names == ("ghost-os",)
    assert configured.read_text(encoding="utf-8").find("external-ghost") >= 0
```

In the proxy status test, assert both `ghost-os: disconnected` and `ignored external configuration for packaged server: ghost-os`, with `shadowedConfiguredServers: ["ghost-os"]` in the adapter marker.

- [ ] **Step 5: Run merge/status tests and witness failure**

Run:

```bash
(cd packages/travis234-mcp-adapter && ../../.venv/bin/python -m pytest -q \
  tests/test_config.py tests/test_proxy_tool.py -k 'packaged or shadow')
```

Expected: failures show that file config is still the only server source and status has no shadow metadata.

- [ ] **Step 6: Merge packaged descriptors at session start**

Implement `PackagedConfig` and conversion to existing `ServerConfig` without pretending a config file exists:

```python
@dataclass(frozen=True)
class PackagedConfig:
    config: LoadedConfig
    shadowed_configured_names: tuple[str, ...]


def merge_packaged_servers(config: LoadedConfig) -> PackagedConfig:
    servers = dict(config.servers)
    shadowed = tuple(sorted(set(servers).intersection(get_packaged_servers())))
    for descriptor in get_packaged_servers().values():
        servers[descriptor.name] = ServerConfig(
            name=descriptor.name,
            source_path=descriptor.command,
            command=str(descriptor.command),
            args=descriptor.args,
            request_timeout_ms=descriptor.request_timeout_ms,
        )
    return PackagedConfig(
        config=replace(config, servers=MappingProxyType(servers)),
        shadowed_configured_names=shadowed,
    )
```

Add `shadowed_configured_names` to `ExtensionState`, apply the merge after `load_config`, and render the warning only in connection-free status. Preserve all ordinary config precedence and trust rules.

- [ ] **Step 7: Add and satisfy adapter installation idempotence regression**

Exercise two source-scoped APIs over one runner:

```python
def test_adapter_extension_is_idempotent_across_duplicate_distribution_paths(tmp_path: Path) -> None:
    runner = ExtensionRunner(cwd=str(tmp_path))
    extension(runner.create_extension_api("/one/extensions/mcp_adapter.py"))
    extension(runner.create_extension_api("/two/extensions/mcp_adapter.py"))
    assert [item.definition.name for item in runner.get_all_registered_tools()] == ["mcp"]
    assert len(runner._handlers["session_start"]) == 1
    assert len(runner._handlers["session_shutdown"]) == 1
```

Use a module-level `WeakKeyDictionary` keyed by the underlying `ExtensionRunner` identity (`getattr(travis, "_runner", travis)`) so duplicate dependency payloads reuse one `ExtensionState` without retaining disposed runners.

- [ ] **Step 8: Run and commit the complete adapter suite**

Set the adapter package version to 0.1.2 and update its distribution test in the same commit, because packaged-server registration is the public capability required by the add-on.

Run:

```bash
(cd packages/travis234-mcp-adapter && ../../.venv/bin/python -m pytest -q)
```

Expected: all adapter tests pass; configured stdio/HTTP behavior, proxy bounds, cancellation, and cleanup remain unchanged.

Commit:

```bash
git add packages/travis234-mcp-adapter
git commit -m "feat(mcp): register trusted packaged servers"
```

---

### Task 3: Import the pinned Ghost source, assets, provenance, and licenses

**Files:**
- Create: `packages/travis234-ghost-mcp/UPSTREAM.json`
- Create: `packages/travis234-ghost-mcp/LICENSE`
- Create: `packages/travis234-ghost-mcp/THIRD_PARTY_NOTICES.md`
- Create: pinned files under `packages/travis234-ghost-mcp/vendor/ghost-os/`
- Create: pinned files under `packages/travis234-ghost-mcp/travis234_ghost_mcp/assets/`
- Create: `packages/travis234-ghost-mcp/tests/conftest.py`
- Create: `packages/travis234-ghost-mcp/tests/test_provenance.py`

**Interfaces:**
- Consumes: disposable clone commit `991aa4831295aaff6beef04cc809d0f0b53dc024` (`v2.2.1-6-g991aa48`).
- Produces: auditable source snapshot with no nested repository/build products and all redistribution notices required by direct/transitive Swift dependencies.

- [ ] **Step 1: Write the failing provenance allowlist test**

```python
def test_pinned_ghost_snapshot_is_auditable() -> None:
    upstream = json.loads((PACKAGE_ROOT / "UPSTREAM.json").read_text(encoding="utf-8"))
    assert upstream["repository"] == "https://github.com/ghostwright/ghost-os"
    assert upstream["commit"] == "991aa4831295aaff6beef04cc809d0f0b53dc024"
    assert upstream["version"] == "2.2.1+6"
    assert upstream["license"] == "MIT"
    assert not any((PACKAGE_ROOT / "vendor/ghost-os").glob(".git/**"))
    assert not any((PACKAGE_ROOT / "vendor/ghost-os").glob(".build/**"))


def test_third_party_notices_cover_resolved_dependencies() -> None:
    notices = (PACKAGE_ROOT / "THIRD_PARTY_NOTICES.md").read_text(encoding="utf-8")
    for name in ("Ghost OS", "AXorcist", "Commander", "swift-log"):
        assert name in notices
```

- [ ] **Step 2: Run the provenance test and witness failure**

Run:

```bash
.venv/bin/python -m pytest -q packages/travis234-ghost-mcp/tests/test_provenance.py
```

Expected: failure because the add-on snapshot and provenance files do not exist.

- [ ] **Step 3: Import only the pinned allowlist**

Bring in `Package.swift`, `Package.resolved`, `Sources/`, `Tests/`, `GHOST-MCP.md`, the four `recipes/*.json` files, and the three `vision-sidecar` files from the disposable clone. Do not import `.git`, `.build`, GIF/SVG demos, scripts, contributor instructions, caches, or tarballs.

Write `UPSTREAM.json` with this exact core:

```json
{
  "repository": "https://github.com/ghostwright/ghost-os",
  "commit": "991aa4831295aaff6beef04cc809d0f0b53dc024",
  "version": "2.2.1+6",
  "license": "MIT",
  "adaptations": [
    "Travis234-only state root",
    "package-relative resources",
    "non-interactive Travis setup and doctor",
    "no external MCP client configuration"
  ]
}
```

Copy the upstream Ghost MIT text into the notice, plus AXorcist/Commander MIT and swift-log Apache-2.0/NOTICE attribution from the resolved checkouts. Mark every adapted Swift/sidecar file with a short header stating that Travis234 modified it from the pinned revision.

- [ ] **Step 4: Run source and provenance checks**

Run:

```bash
.venv/bin/python -m pytest -q packages/travis234-ghost-mcp/tests/test_provenance.py
git status --short
find packages/travis234-ghost-mcp/vendor/ghost-os -maxdepth 2 \
  \( -name .git -o -name .build -o -name '*.tar.gz' \) -print
```

Expected: tests pass; the final `find` prints nothing; the disposable clone remains absent from status.

- [ ] **Step 5: Run the unmodified pinned Swift tests and commit the import**

Run:

```bash
(cd packages/travis234-ghost-mcp/vendor/ghost-os && swift test)
```

Expected: the pinned locator tests pass before Travis adaptations.

Commit:

```bash
git add packages/travis234-ghost-mcp
git commit -m "chore(ghost-mcp): vendor pinned Ghost source"
```

---

### Task 4: Enforce the Travis234 state root and package-relative resources

**Files:**
- Create: `packages/travis234-ghost-mcp/vendor/ghost-os/Sources/GhostOS/Common/TravisPaths.swift`
- Create: `packages/travis234-ghost-mcp/vendor/ghost-os/Tests/GhostOSTests/TravisPathsTests.swift`
- Create: `packages/travis234-ghost-mcp/tests/test_state_contract.py`
- Modify: vendored `Common/Types.swift`, `Logger.swift`, `Recipes/RecipeStore.swift`, `MCP/MCPServer.swift`, `Vision/VisionBridge.swift`, `vision-sidecar/ghost-vision`, and `vision-sidecar/server.py`

**Interfaces:**
- Consumes: `CommandLine.arguments[0]`, the real user home, and the fixed package layout `bin/ghost` beside `assets/`.
- Produces: `TravisPaths(homeDirectory: URL, executableURL: URL)` with `stateRoot`, `recipesDirectory`, `logsDirectory`, `visionEnvironment`, `visionModelDirectory`, `packageRoot`, `instructionsFile`, and `visionSidecarDirectory`.

- [ ] **Step 1: Write failing Swift state/resource tests**

```swift
@Test("all mutable paths stay under the Travis234 state root")
func mutablePathsStayUnderStateRoot() throws {
    let paths = TravisPaths(
        homeDirectory: URL(fileURLWithPath: "/Users/tester"),
        executableURL: URL(fileURLWithPath: "/payload/travis234_ghost_mcp/bin/ghost")
    )
    #expect(paths.stateRoot.path == "/Users/tester/.travis234/ghost-mcp")
    for path in [paths.recipesDirectory, paths.logsDirectory, paths.visionEnvironment, paths.visionModelDirectory] {
        #expect(path.path.hasPrefix(paths.stateRoot.path + "/"))
    }
    #expect(paths.instructionsFile.path == "/payload/travis234_ghost_mcp/assets/GHOST-MCP.md")
}
```

Also test that `packageRoot` is exactly two parents above `bin/ghost`; no `PATH`, Homebrew, or current-working-directory fallback participates.

- [ ] **Step 2: Write the failing forbidden-path source scan**

```python
@pytest.mark.parametrize("forbidden", [".ghost-os", "/opt/homebrew/share/ghost-os", "/usr/local/share/ghost-os", ".shadow/models"])
def test_adapted_runtime_has_no_external_state_or_config_fallback(forbidden: str) -> None:
    scanned = [*VENDOR.rglob("*.swift"), *ASSETS.rglob("*.py"), ASSETS / "vision-sidecar/ghost-vision"]
    offenders = [str(path.relative_to(PACKAGE_ROOT)) for path in scanned if forbidden in path.read_text(encoding="utf-8")]
    assert offenders == []
```

- [ ] **Step 3: Run both regressions and witness failure**

Run:

```bash
(cd packages/travis234-ghost-mcp/vendor/ghost-os && swift test --filter TravisPathsTests)
.venv/bin/python -m pytest -q packages/travis234-ghost-mcp/tests/test_state_contract.py
```

Expected: missing `TravisPaths` and multiple upstream `.ghost-os`/Homebrew fallback failures.

- [ ] **Step 4: Add one path owner and route every mutable/resource path through it**

Implement the value object:

```swift
public struct TravisPaths: Sendable {
    public let homeDirectory: URL
    public let executableURL: URL

    public init(homeDirectory: URL = FileManager.default.homeDirectoryForCurrentUser,
                executableURL: URL = URL(fileURLWithPath: CommandLine.arguments[0]).standardizedFileURL) {
        self.homeDirectory = homeDirectory.standardizedFileURL
        self.executableURL = executableURL.standardizedFileURL
    }

    public var stateRoot: URL { homeDirectory.appending(path: ".travis234/ghost-mcp", directoryHint: .isDirectory) }
    public var recipesDirectory: URL { stateRoot.appending(path: "recipes", directoryHint: .isDirectory) }
    public var logsDirectory: URL { stateRoot.appending(path: "logs", directoryHint: .isDirectory) }
    public var visionEnvironment: URL { stateRoot.appending(path: "vision-venv", directoryHint: .isDirectory) }
    public var visionModelDirectory: URL { stateRoot.appending(path: "models/ShowUI-2B", directoryHint: .isDirectory) }
    public var packageRoot: URL { executableURL.deletingLastPathComponent().deletingLastPathComponent() }
    public var instructionsFile: URL { packageRoot.appending(path: "assets/GHOST-MCP.md") }
    public var visionSidecarDirectory: URL { packageRoot.appending(path: "assets/vision-sidecar", directoryHint: .isDirectory) }
}
```

Replace all direct recipe/log/model/venv strings and all Homebrew/share/legacy resource lookup arrays. Keep system Python executable discovery for explicit vision setup, but never treat Homebrew as a Ghost state or resource location.

- [ ] **Step 5: Adapt sidecar paths and verify zero forbidden strings**

The launcher must use only:

```bash
STATE_ROOT="$HOME/.travis234/ghost-mcp"
VENV_PYTHON="$STATE_ROOT/vision-venv/bin/python3"
SERVER_PY="$(cd "$(dirname "$0")" && pwd)/server.py"
```

The Python sidecar model path must be exactly `Path.home() / ".travis234/ghost-mcp/models/ShowUI-2B"`. Remove implicit legacy candidates and the explicit alternate model-path option so the add-on cannot establish another state location.

- [ ] **Step 6: Run state, Swift, and syntax checks**

Run:

```bash
(cd packages/travis234-ghost-mcp/vendor/ghost-os && swift test)
.venv/bin/python -m pytest -q packages/travis234-ghost-mcp/tests/test_state_contract.py
.venv/bin/python -m py_compile \
  packages/travis234-ghost-mcp/travis234_ghost_mcp/assets/vision-sidecar/server.py
```

Expected: all pass, and the source scan proves there is no external Ghost state/config fallback.

- [ ] **Step 7: Commit the state boundary**

```bash
git add packages/travis234-ghost-mcp
git commit -m "feat(ghost-mcp): isolate state under Travis234"
```

---

### Task 5: Replace upstream client configuration with bounded Travis setup and doctor flows

**Files:**
- Delete: `packages/travis234-ghost-mcp/vendor/ghost-os/Sources/ghost/SetupWizard.swift`
- Delete: `packages/travis234-ghost-mcp/vendor/ghost-os/Sources/ghost/Doctor.swift`
- Create: `packages/travis234-ghost-mcp/vendor/ghost-os/Sources/GhostOS/Host/TravisSetup.swift`
- Create: `packages/travis234-ghost-mcp/vendor/ghost-os/Sources/GhostOS/Host/TravisDoctor.swift`
- Create: `packages/travis234-ghost-mcp/vendor/ghost-os/Tests/GhostOSTests/SetupContractTests.swift`
- Modify: `packages/travis234-ghost-mcp/vendor/ghost-os/Sources/ghost/main.swift`

**Interfaces:**
- Consumes: `TravisPaths`, packaged recipes, macOS permission APIs, and explicit CLI commands.
- Produces: `ghost setup [--vision]`, `ghost doctor [--json]`, and bounded exit codes `0` healthy/complete, `2` permissions incomplete, `3` package/resource failure, `4` optional vision failure.

- [ ] **Step 1: Write failing setup mutation and command-line tests**

Add a testable filesystem boundary (`SetupFileSystem`) and assert:

```swift
@Test("setup writes only below the Travis state root")
func setupWritesOnlyBelowTravisState() throws {
    let recorder = RecordingSetupFileSystem()
    let paths = TravisPaths(homeDirectory: URL(fileURLWithPath: "/home/test"),
                            executableURL: URL(fileURLWithPath: "/pkg/bin/ghost"))
    _ = try TravisSetup(paths: paths, fileSystem: recorder, permissions: .allGranted).run(includeVision: false)
    #expect(recorder.writtenPaths.allSatisfy { $0.path.hasPrefix("/home/test/.travis234/ghost-mcp/") })
    #expect(recorder.writtenPaths.allSatisfy { !$0.path.contains(".claude") && !$0.path.contains("mcp.json") })
}
```

Test that bundled recipes copy only when the destination is absent, so setup never overwrites a user-edited recipe. Test that the default setup performs no network/venv operation and that only `--vision` enables the vision installer.

- [ ] **Step 2: Run setup tests and witness failure**

Run:

```bash
(cd packages/travis234-ghost-mcp/vendor/ghost-os && swift test --filter SetupContractTests)
```

Expected: failure because the upstream wizard still writes Claude configuration and has no injectable setup boundary.

- [ ] **Step 3: Implement non-interactive Travis setup**

Replace the upstream wizard with these responsibilities:

```swift
struct SetupReport: Codable, Sendable {
    let accessibilityGranted: Bool
    let screenRecordingGranted: Bool
    let inputMonitoringGranted: Bool
    let recipesInstalled: Int
    let visionReady: Bool
    let restartRequired: Bool
}

struct TravisSetup {
    func run(includeVision: Bool) throws -> SetupReport
}
```

The default run checks permissions, requests/opens the relevant macOS Privacy & Security panes once, installs missing recipes, prints an actionable report, and returns promptly without reading stdin. `--vision` explicitly creates `~/.travis234/ghost-mcp/vision-venv`, installs the pinned requirements, and downloads `mlx-community/ShowUI-2B-bf16-8bit` into the canonical model directory. Every subprocess uses an argument array, a deadline, and bounded output; no shell-composed path is allowed.

- [ ] **Step 4: Implement read-only doctor JSON**

`TravisDoctor.run()` must inspect:

```swift
struct DoctorReport: Codable, Sendable {
    let version: String
    let executable: String
    let architecture: String
    let accessibilityGranted: Bool
    let screenRecordingGranted: Bool
    let inputMonitoringGranted: Bool
    let recipeCount: Int
    let visionReady: Bool
    let stateRoot: String
}
```

`doctor --json` prints exactly one JSON object on stdout and diagnostics on stderr. It does not inspect Claude, Homebrew, or configured MCP clients and does not mutate state.

- [ ] **Step 5: Update CLI dispatch and help**

The command switch must be exact:

```swift
case "mcp": MCPServer().run()
case "setup": try runSetup(arguments: remaining)
case "doctor": try runDoctor(arguments: remaining)
case "version": print(GhostOS.version)
default: printTravisGhostHelpAndExit()
```

Reject unknown setup/doctor flags with a nonzero exit and bounded message. Remove all Claude Code-specific copy from CLI help and MCP instructions where it describes the host rather than the protocol.

- [ ] **Step 6: Run Swift tests and forbidden mutation scan**

Extend `test_state_contract.py` at this point with `.claude` and `mcp.json` in the forbidden runtime scan, because the upstream setup/doctor owners have now been removed.

Run:

```bash
(cd packages/travis234-ghost-mcp/vendor/ghost-os && swift test)
.venv/bin/python -m pytest -q packages/travis234-ghost-mcp/tests/test_state_contract.py
```

Expected: all tests pass; no `.claude`, `mcp.json`, `.ghost-os`, or Homebrew Ghost resource fallback exists in the adapted runtime.

- [ ] **Step 7: Commit setup and doctor**

```bash
git add packages/travis234-ghost-mcp
git commit -m "feat(ghost-mcp): add Travis setup and doctor"
```

---

### Task 6: Build a reproducible macOS arm64 add-on wheel

**Files:**
- Create: `packages/travis234-ghost-mcp/pyproject.toml`
- Create: `packages/travis234-ghost-mcp/setup.py`
- Create: `packages/travis234-ghost-mcp/MANIFEST.in`
- Create: `packages/travis234-ghost-mcp/travis234_ghost_mcp/__init__.py`
- Create: `packages/travis234-ghost-mcp/travis234_ghost_mcp/host.py`
- Create: `packages/travis234-ghost-mcp/travis234_ghost_mcp/bin/.gitkeep`
- Create: `packages/travis234-ghost-mcp/tests/test_host.py`
- Create: `packages/travis234-ghost-mcp/tests/test_distribution.py`

**Interfaces:**
- Consumes: vendored Swift package and package assets.
- Produces: `require_supported_host() -> None`, `package_root() -> Path`, `ghost_binary() -> Path`, an sdist containing reproducible sources, and a non-pure `py3-none-macosx_14_0_arm64` wheel containing `travis234_ghost_mcp/bin/ghost`.

- [ ] **Step 1: Write failing platform/path tests**

```python
def test_supported_host_requires_darwin_arm64_and_macos_14(monkeypatch) -> None:
    monkeypatch.setattr(sys, "platform", "linux")
    with pytest.raises(UnsupportedHostError, match="macOS 14.*Apple Silicon"):
        require_supported_host()


def test_ghost_binary_never_falls_back_to_path(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(host, "package_root", lambda: tmp_path)
    monkeypatch.setenv("PATH", str(tmp_path / "fake-bin"))
    with pytest.raises(PackageIntegrityError, match="embedded Ghost executable"):
        ghost_binary()
```

- [ ] **Step 2: Run host tests and witness failure**

Run:

```bash
.venv/bin/python -m pytest -q packages/travis234-ghost-mcp/tests/test_host.py
```

Expected: import failure because the Python package and host boundary do not exist.

- [ ] **Step 3: Implement strict host and package-relative binary resolution**

```python
def require_supported_host() -> None:
    version = tuple(int(part) for part in platform.mac_ver()[0].split(".")[:2] or ("0",))
    if sys.platform != "darwin" or platform.machine() != "arm64" or version < (14, 0):
        raise UnsupportedHostError("travis234-ghost-mcp requires macOS 14 or newer on Apple Silicon")


def ghost_binary() -> Path:
    require_supported_host()
    binary = (package_root() / "bin" / "ghost").resolve()
    binary.relative_to(package_root().resolve())
    if not binary.is_file() or not os.access(binary, os.X_OK):
        raise PackageIntegrityError("travis234-ghost-mcp is missing its embedded Ghost executable")
    return binary
```

Do not call `shutil.which`, inspect Homebrew, or download a binary.

- [ ] **Step 4: Write the failing wheel/sdist contract**

Build fixtures must assert:

```python
assert wheel.name.endswith("-py3-none-macosx_14_0_arm64.whl")
assert "travis234_ghost_mcp/bin/ghost" in wheel_names
assert "travis234_ghost_mcp/assets/GHOST-MCP.md" in wheel_names
assert "UPSTREAM.json" in sdist_names
assert any(name.endswith("vendor/ghost-os/Package.resolved") for name in sdist_names)
assert not any(".build/" in name or ".git/" in name for name in wheel_names + sdist_names)
```

After wheel extraction, assert the binary executable bit, `ghost version`, `codesign --verify --strict`, and `otool -L` contains only expected system/Swift libraries.

- [ ] **Step 5: Run distribution test and witness failure**

Run:

```bash
.venv/bin/python -m pytest -q packages/travis234-ghost-mcp/tests/test_distribution.py
```

Expected: failure because package metadata and the wheel build hook do not exist.

- [ ] **Step 6: Implement deterministic build commands**

Use `setuptools.command.build_py.build_py` and `wheel.bdist_wheel.bdist_wheel`:

```python
class BuildPy(build_py):
    def run(self) -> None:
        super().run()
        require_build_host()
        subprocess.run(
            ["swift", "build", "-c", "release", "--package-path", str(VENDOR_ROOT)],
            check=True,
            env=build_environment(),
        )
        destination = Path(self.build_lib) / "travis234_ghost_mcp/bin/ghost"
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(VENDOR_ROOT / ".build/release/ghost", destination)
        destination.chmod(0o755)
        subprocess.run(["/usr/bin/codesign", "--force", "--sign", "-", str(destination)], check=True)


class BdistWheel(bdist_wheel):
    def finalize_options(self) -> None:
        super().finalize_options()
        self.root_is_pure = False

    def get_tag(self) -> tuple[str, str, str]:
        return ("py3", "none", "macosx_14_0_arm64")
```

The sanitized build environment retains only compiler-required variables and never prints the parent environment. `MANIFEST.in` includes the exact vendored source/assets/notices and excludes `.build`, `.git`, caches, logs, archives, and generated binaries.

- [ ] **Step 7: Add exact package metadata**

Use:

```toml
[project]
name = "travis234-ghost-mcp"
version = "0.1.0"
requires-python = ">=3.13,<3.14"
dependencies = ["travis234-mcp-adapter>=0.1.2,<0.2"]

[tool.setuptools.data-files]
"extensions" = ["extensions/ghost_mcp.py"]
"share/travis234-ghost-mcp" = ["UPSTREAM.json", "THIRD_PARTY_NOTICES.md"]
```

Include package data for `bin/ghost`, instructions, recipes, and vision-sidecar sources. Include `LICENSE` through the standard wheel license-files mechanism and place `UPSTREAM.json` plus third-party notices in the wheel's `share/travis234-ghost-mcp` data directory. The source tree keeps only `.gitkeep` under `bin`; built binaries stay ignored.

- [ ] **Step 8: Build, validate, and commit package mechanics**

Run:

```bash
.venv/bin/python -m pytest -q \
  packages/travis234-ghost-mcp/tests/test_host.py \
  packages/travis234-ghost-mcp/tests/test_distribution.py
rm -rf packages/travis234-ghost-mcp/dist packages/travis234-ghost-mcp/build
.venv/bin/python -m build packages/travis234-ghost-mcp
.venv/bin/python -m twine check packages/travis234-ghost-mcp/dist/*
```

Use explicit validated package-local build/dist paths for cleanup; do not delete a workspace root or user directory.

Commit:

```bash
git add packages/travis234-ghost-mcp
git commit -m "build(ghost-mcp): package embedded macOS server"
```

---

### Task 7: Register bundled Ghost automatically and expose TUI setup commands

**Files:**
- Create: `packages/travis234-ghost-mcp/extensions/ghost_mcp.py`
- Create: `packages/travis234-ghost-mcp/travis234_ghost_mcp/commands.py`
- Create: `packages/travis234-ghost-mcp/travis234_ghost_mcp/extension.py`
- Create: `packages/travis234-ghost-mcp/tests/test_commands.py`
- Create: `packages/travis234-ghost-mcp/tests/test_extension.py`
- Modify: `packages/travis234-ghost-mcp/pyproject.toml`
- Modify: `packages/travis234-mcp-adapter/tests/test_distribution.py`

**Interfaces:**
- Consumes: `register_packaged_server`, `PackagedServer`, `ghost_binary`, and Travis extension command APIs.
- Produces: `ghost_server_descriptor() -> PackagedServer`, `run_ghost_command(mode: Literal["setup", "doctor"], include_vision: bool = False) -> CommandResult`, and `extension(travis) -> None`.

- [ ] **Step 1: Write failing command safety tests**

```python
def test_doctor_uses_argument_array_safe_environment_and_bound(tmp_path: Path, monkeypatch) -> None:
    seen = {}
    def fake_run(argv, **kwargs):
        seen.update(argv=argv, kwargs=kwargs)
        return subprocess.CompletedProcess(argv, 0, stdout='{"version":"2.2.1"}', stderr="")
    monkeypatch.setattr(subprocess, "run", fake_run)
    result = run_ghost_command("doctor")
    assert seen["argv"][-2:] == ["doctor", "--json"]
    assert set(seen["kwargs"]["env"]) <= {"HOME", "PATH", "TMPDIR", "LANG", "LC_ALL", "TERM_PROGRAM"}
    assert seen["kwargs"]["timeout"] == 30
    assert len(result.text.encode("utf-8")) <= 16_384
```

Test that setup accepts only empty arguments or exact `vision`, uses a 120-second non-vision deadline, and rejects all other input without spawning. Vision uses an explicit 1,800-second deadline and is never selected by default.

- [ ] **Step 2: Write failing extension registration tests**

```python
def test_extension_registers_embedded_server_and_commands_without_config_io(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(extension_module, "ghost_binary", lambda: executable_fixture(tmp_path))
    runner = ExtensionRunner(cwd=str(tmp_path))
    extension_module.extension(runner)
    descriptor = get_packaged_servers()["ghost-os"]
    assert descriptor.command == executable_fixture(tmp_path).resolve()
    assert descriptor.args == ("mcp",)
    assert runner.get_registered_command("ghost-setup") is not None
    assert runner.get_registered_command("ghost-doctor") is not None
    assert not list(tmp_path.rglob("mcp.json"))
```

- [ ] **Step 3: Run command/extension tests and witness failure**

Run:

```bash
.venv/bin/python -m pytest -q \
  packages/travis234-ghost-mcp/tests/test_commands.py \
  packages/travis234-ghost-mcp/tests/test_extension.py
```

Expected: imports fail because command and extension owners do not exist.

- [ ] **Step 4: Implement bounded command execution and TUI messages**

Define:

```python
@dataclass(frozen=True)
class CommandResult:
    ok: bool
    text: str
    exit_code: int


def setup_handler(args: str, ctx) -> object:
    include_vision = parse_setup_argument(args)
    result = run_ghost_command("setup", include_vision=include_vision)
    return ctx.send_message({
        "customType": "ghost-mcp-status",
        "content": result.text,
        "display": True,
        "details": {"operation": "setup", "ok": result.ok},
    })
```

Use the same custom message shape for doctor. Decode output with replacement, combine only bounded diagnostics, never echo the child environment, and turn timeout/missing-permission/package errors into concise user text.

- [ ] **Step 5: Implement automatic packaged registration**

```python
def ghost_server_descriptor() -> PackagedServer:
    root = package_root()
    return PackagedServer(
        name="ghost-os",
        package_root=root,
        command=ghost_binary(),
        args=("mcp",),
        request_timeout_ms=1_800_000,
    )


def extension(travis) -> None:
    register_packaged_server(ghost_server_descriptor())
    travis.register_command("ghost-setup", {"description": "Set up bundled Ghost permissions", "handler": setup_handler})
    travis.register_command("ghost-doctor", {"description": "Diagnose bundled Ghost MCP", "handler": doctor_handler})
```

The loader inserts only its own payload root into `sys.path`. It does not read/write config, extract assets, look up Ghost on `PATH`, or connect during extension loading.

- [ ] **Step 6: Prove adapter dependency duplication is harmless**

Extend the adapter wheel installation test to install both the standalone adapter wheel and an add-on fixture that depends on it. Load all resolved extensions and assert exactly one `mcp` tool, one adapter session lifecycle, and one pair of Ghost commands.

- [ ] **Step 7: Run focused integrated tests and commit**

Run:

```bash
(cd packages/travis234-mcp-adapter && ../../.venv/bin/python -m pytest -q tests/test_distribution.py tests/test_extension.py)
.venv/bin/python -m pytest -q packages/travis234-ghost-mcp/tests
```

Expected: all pass with no user/config mutations.

Commit:

```bash
git add packages/travis234-mcp-adapter packages/travis234-ghost-mcp
git commit -m "feat(ghost-mcp): auto-register bundled server"
```

---

### Task 8: Prove the real embedded Ghost MCP protocol and lifecycle

**Files:**
- Create: `packages/travis234-ghost-mcp/vendor/ghost-os/Tests/GhostOSTests/MCPToolsTests.swift`
- Create: `packages/travis234-ghost-mcp/tests/test_protocol.py`
- Create: `evals/bundled_ghost_mcp_smoke.py`
- Modify: `tests/test_eval_harness.py`

**Interfaces:**
- Consumes: built embedded binary, adapter proxy `mcp`, and isolated `HOME`.
- Produces: deterministic evidence of initialize, exact 29-tool discovery, representative bounded call, timeout/cancellation, child shutdown, and zero Ghost configuration.

- [ ] **Step 1: Write failing Swift catalog identity test**

```swift
@Test("bundled MCP catalog contains the pinned 29 tools")
func catalogIsPinned() throws {
    let tools = MCPTools.definitions()
    #expect(tools.count == 29)
    let names = Set(tools.compactMap { $0["name"] as? String })
    #expect(names.contains("ghost_context"))
    #expect(names.contains("ghost_screenshot"))
    #expect(names.contains("ghost_click"))
    #expect(names.contains("ghost_type"))
    #expect(names.contains("ghost_recipes"))
    #expect(names.contains("ghost_learn_start"))
}
```

Also assert every tool has an object input schema and non-empty description.

- [ ] **Step 2: Write failing installed-binary protocol test**

Build/install fixtures must start with an empty isolated home and assert:

```python
await runner.async_emit({"type": "session_start"})
status = await mcp.execute("status", {}, None, None, None)
catalog = await mcp.execute("list", {"server": "ghost-os"}, None, None, None)
assert "ghost-os: disconnected" in status.content[0].text
assert 'MCP tools on "ghost-os" (29)' in catalog.content[0].text
assert "ghost_context" in catalog.content[0].text
assert not list(home.rglob("mcp.json"))
assert not (home / ".ghost-os").exists()
```

The test then calls a protocol-safe tool such as `ghost_recipes`, shuts down twice, and uses `psutil` to prove the child PID is gone.

- [ ] **Step 3: Run focused protocol tests and witness failure**

Run:

```bash
(cd packages/travis234-ghost-mcp/vendor/ghost-os && swift test --filter MCPToolsTests)
.venv/bin/python -m pytest -q packages/travis234-ghost-mcp/tests/test_protocol.py
```

Expected: failures until the pinned catalog assertions and built package lifecycle fixture are complete.

- [ ] **Step 4: Add the minimum catalog visibility and integration fixture**

Keep tool definitions immutable and use the existing read-only `MCPTools.definitions()` API. In Python, build the wheel once per test session, install adapter/add-on wheels with the real `DefaultPackageManager`, load extensions through `DefaultResourceLoader`, emit session start, and call the registered proxy definition. Never use the disposable clone executable.

- [ ] **Step 5: Add cancellation and bounded failure regressions**

Start a request that waits, cancel through an `AbortSignal`, and assert `CancelledError` plus child cleanup at session shutdown. Run screenshot/context without permission only when the host lacks it and assert the returned text names `/ghost-setup` and remains below the adapter's inline bound; do not weaken the test when permissions are already granted.

- [ ] **Step 6: Write the isolated smoke harness contract first**

Add to `tests/test_eval_harness.py`:

```python
def test_bundled_ghost_smoke_reports_protocol_without_config(tmp_path: Path) -> None:
    result = run_bundled_ghost_smoke(tmp_path)
    assert result["server"] == "ghost-os"
    assert result["tool_count"] == 29
    assert result["configured"] is False
    assert result["child_reaped"] is True
    assert result["legacy_state_created"] is False
```

Then implement `run_bundled_ghost_smoke(workspace: Path) -> dict[str, object]` using installed wheels and bounded JSON-only output. It must never inspect the user's real home or print desktop content.

- [ ] **Step 7: Run protocol/eval suites and commit**

Run:

```bash
(cd packages/travis234-ghost-mcp/vendor/ghost-os && swift test)
.venv/bin/python -m pytest -q \
  packages/travis234-ghost-mcp/tests/test_protocol.py \
  tests/test_eval_harness.py -k bundled_ghost
.venv/bin/python -m evals.bundled_ghost_mcp_smoke
```

Expected: 29 tools, no config, no legacy state, and clean child shutdown.

Commit:

```bash
git add packages/travis234-ghost-mcp evals/bundled_ghost_mcp_smoke.py tests/test_eval_harness.py
git commit -m "test(ghost-mcp): prove embedded protocol lifecycle"
```

---

### Task 9: Finalize versions, user documentation, and distribution contracts

**Files:**
- Modify: `pyproject.toml`
- Modify: `package.json`
- Modify: `packages/travis234-cli/package.json`
- Modify: `README.md`
- Modify: `packages/travis234-mcp-adapter/README.md`
- Modify: `packages/travis234-mcp-adapter/tests/test_distribution.py`
- Create: `packages/travis234-ghost-mcp/README.md`
- Modify: `packages/travis234-ghost-mcp/pyproject.toml`
- Modify: `tests/test_distribution_contract.py`
- Modify: `tests/test_pyproject_dependencies.py`

**Interfaces:**
- Consumes: verified package/runtime behavior from Tasks 1-8.
- Produces: Travis234 2.4.5, adapter 0.1.2, Ghost add-on 0.1.0, exact install/setup docs, and synchronized release metadata.

- [ ] **Step 1: Write failing synchronized-version and optional-package tests**

Add assertions:

```python
def test_release_versions_include_bundled_ghost_addon() -> None:
    assert root_project_version() == "2.4.5"
    assert root_npm_version() == "2.4.5"
    assert launcher_npm_version() == "2.4.5"
    assert adapter_version() == "0.1.2"
    assert ghost_addon_version() == "0.1.0"


def test_root_distribution_does_not_depend_on_macos_ghost_addon() -> None:
    assert "travis234-ghost-mcp" not in root_requires_dist()
```

The add-on distribution test must assert its adapter range is `travis234-mcp-adapter<0.2,>=0.1.2` and that the root Linux-compatible wheel contains no Ghost binary/source.

- [ ] **Step 2: Run contract tests and witness failure**

Run:

```bash
.venv/bin/python -m pytest -q \
  tests/test_distribution_contract.py \
  tests/test_pyproject_dependencies.py \
  packages/travis234-mcp-adapter/tests/test_distribution.py \
  packages/travis234-ghost-mcp/tests/test_distribution.py
```

Expected: adapter/root versions and documentation contracts are not yet synchronized.

- [ ] **Step 3: Apply exact versions and metadata**

Set root Python/npm/launcher versions to 2.4.5 and verify the already-released task boundaries remain adapter 0.1.2 and add-on 0.1.0. Do not add the adapter or add-on to root dependencies. Keep the adapter's `mcp>=2,<3` range and the add-on's adapter range exact.

- [ ] **Step 4: Write user documentation with executable commands**

The root and add-on README must show:

```bash
travis234 install travis234-ghost-mcp
travis234 --mcp
```

Document `/ghost-doctor`, `/ghost-setup`, and explicit `/ghost-setup vision`. State clearly that:

- Ghost is embedded and executed in place;
- no separate clone, Homebrew install, archive extraction, or Ghost `mcp.json` entry is required;
- platform support is macOS 14+ Apple Silicon;
- state is only `~/.travis234/ghost-mcp`;
- Accessibility is required, Screen Recording is needed for screenshots, and Input Monitoring is needed only for learning;
- visual grounding downloads a large model only after explicit authorization;
- `travis234 remove travis234-ghost-mcp` removes package code but intentionally retains user recipes/models under `~/.travis234/ghost-mcp`;
- Ghost OS is MIT-licensed and pinned to the recorded upstream commit.

The adapter README must describe packaged-server registration as a trusted extension API, not a user config format, and retain all ordinary `mcp.json` documentation for unrelated servers.

- [ ] **Step 5: Run documentation/package contracts**

Run:

```bash
.venv/bin/python -m pytest -q \
  tests/test_distribution_contract.py \
  tests/test_pyproject_dependencies.py \
  packages/travis234-mcp-adapter/tests/test_distribution.py \
  packages/travis234-ghost-mcp/tests/test_distribution.py
npm --prefix packages/travis234-cli test
npm --prefix packages/travis234-cli run pack:dry-run
```

Expected: all pass and dry-run package contents contain no Ghost payload.

- [ ] **Step 6: Commit release metadata and docs**

```bash
git add \
  README.md pyproject.toml package.json packages/travis234-cli/package.json \
  packages/travis234-mcp-adapter packages/travis234-ghost-mcp \
  tests/test_distribution_contract.py tests/test_pyproject_dependencies.py
git commit -m "docs: release bundled Ghost MCP add-on"
```

---

### Task 10: Run complete release qualification and local TUI acceptance

**Files:**
- Create: `docs/verification/main-bundled-ghost-mcp.md`
- Modify only for a discovered, regression-tested defect: files owned by Tasks 1-9

**Interfaces:**
- Consumes: the exact committed source tree and locally built distributions.
- Produces: reproducible verification evidence, installed-wheel manual acceptance, and a clean worktree ready for final review.

- [ ] **Step 1: Invoke the completion verification workflow**

Before making any passing/complete claim, read and follow `superpowers:verification-before-completion`. If any command fails, use `superpowers:systematic-debugging`; write a failing regression before fixing code.

- [ ] **Step 2: Run every automated test suite from a clean build state**

Run:

```bash
.venv/bin/python -m pytest -q
(cd packages/travis234-mcp-adapter && ../../.venv/bin/python -m pytest -q)
.venv/bin/python -m pytest -q packages/travis234-ghost-mcp/tests
(cd packages/travis234-ghost-mcp/vendor/ghost-os && swift test)
npm --prefix packages/travis234-cli test
npm --prefix packages/travis234-cli run pack:dry-run
```

Record exact pass counts and durations. Any skip must name the external prerequisite and must not be represented as a pass.

- [ ] **Step 3: Build and validate every package**

Create one explicit temporary directory with `mktemp -d`, record its path in a task-specific variable, and build:

```bash
.venv/bin/python -m build
.venv/bin/python -m build packages/travis234-mcp-adapter
.venv/bin/python -m build packages/travis234-ghost-mcp
.venv/bin/python -m twine check \
  dist/* \
  packages/travis234-mcp-adapter/dist/* \
  packages/travis234-ghost-mcp/dist/*
```

Inspect root/adapter wheels as platform-neutral and the add-on wheel as `macosx_14_0_arm64`. Confirm the add-on wheel contains the signed executable, exact assets, provenance, and notices, while tracked status contains no built binary/archive.

- [ ] **Step 4: Perform a clean real package-manager installation**

With isolated `HOME` and `TRAVIS234_CODING_AGENT_DIR` beneath the validated temporary directory, define exact task-specific paths:

```bash
GHOST_MCP_TMP="$(mktemp -d /tmp/travis234-ghost-mcp-qualification.XXXXXX)"
GHOST_MCP_HOME="$GHOST_MCP_TMP/home"
GHOST_MCP_AGENT_DIR="$GHOST_MCP_HOME/.travis234/agent"
GHOST_MCP_WORKSPACE="$GHOST_MCP_TMP/workspace"
GHOST_MCP_VENV="$GHOST_MCP_TMP/runtime-venv"
GHOST_MCP_AUTH_SOURCE="$HOME/.travis234/agent/auth.json"
GHOST_MCP_ROOT_WHEEL="$PWD/dist/travis234-2.4.5-py3-none-any.whl"
GHOST_MCP_WHEEL="$PWD/packages/travis234-ghost-mcp/dist/travis234_ghost_mcp-0.1.0-py3-none-macosx_14_0_arm64.whl"
mkdir -p "$GHOST_MCP_HOME" "$GHOST_MCP_AGENT_DIR" "$GHOST_MCP_WORKSPACE"
python3.13 -m venv "$GHOST_MCP_VENV"
"$GHOST_MCP_VENV/bin/python" -m pip install "$GHOST_MCP_ROOT_WHEEL"
"$GHOST_MCP_VENV/bin/python" -m pip check
install -m 600 "$GHOST_MCP_AUTH_SOURCE" "$GHOST_MCP_AGENT_DIR/auth.json"
HOME="$GHOST_MCP_HOME" TRAVIS234_CODING_AGENT_DIR="$GHOST_MCP_AGENT_DIR" \
  PIP_NO_INDEX=1 PIP_FIND_LINKS="$PWD/packages/travis234-mcp-adapter/dist" \
  "$GHOST_MCP_VENV/bin/travis234" install "travis234-ghost-mcp @ file://$GHOST_MCP_WHEEL"
HOME="$GHOST_MCP_HOME" TRAVIS234_CODING_AGENT_DIR="$GHOST_MCP_AGENT_DIR" \
  "$GHOST_MCP_VENV/bin/travis234" list
```

The auth copy is only for the manual provider-backed TUI; never print or include it in evidence, and delete the validated temporary tree after qualification. Assert the resolved extensions contain `ghost_mcp.py` and `mcp_adapter.py`, but runtime registration contains exactly one `mcp` tool and one Ghost command pair. Run the bundled smoke and verify no MCP config, Claude config, Homebrew file, `.ghost-os`, orphan child, or spill file is created.

- [ ] **Step 5: Build and run the unprivileged release-container smoke**

Run:

```bash
docker build --no-cache -f Dockerfile.release -t travis234:bundled-ghost-mcp .
.venv/bin/python evals/container_smoke.py --image travis234:bundled-ghost-mcp
```

Expected: the generic Linux Travis image and all existing print/JSON/RPC/TUI/package/trust/process smokes pass. Separately exercise the add-on host guard from source and assert it reports `macOS 14 or newer on Apple Silicon` without creating state or altering ordinary MCP behavior; do not attempt to run the macOS wheel in Linux.

- [ ] **Step 6: Run the real installed-wheel TUI setup checks**

In one real PTY using only the isolated installed distributions:

1. Start `HOME="$GHOST_MCP_HOME" TRAVIS234_CODING_AGENT_DIR="$GHOST_MCP_AGENT_DIR" "$GHOST_MCP_VENV/bin/travis234" --cwd "$GHOST_MCP_WORKSPACE" --provider openrouter --model xiaomi/mimo-v2.5-pro --no-tools --mcp`.
2. Run `/ghost-doctor` and capture only bounded permission/status metadata.
3. Run `/ghost-setup` if Accessibility or Screen Recording is missing, grant the requested macOS permissions, and restart the installed Travis process when instructed.
4. Call the `mcp` status operation and verify `ghost-os` is present with no config file.
5. List the Ghost server and verify exactly 29 tools.

Do not print authentication material or unrelated desktop accessibility content in the verification record.

- [ ] **Step 7: Run the required computer-use acceptance prompt**

Submit this prompt to Travis in the same installed TUI session:

```text
Using only the bundled ghost-os MCP computer-use server, open a browser, open YouTube, open Rick Astley's Never Gonna Give You Up official video, and play it. Do not use shell tools. Verify the final URL, page title, and playback/audio state before reporting success.
```

Expected: Travis calls the existing `mcp` proxy to discover/call the embedded Ghost server, reaches `https://www.youtube.com/watch?v=dQw4w9WgXcQ`, observes the official-video title, starts playback, and verifies a playback/audio indicator. If the browser exposes a different canonical query suffix, accept it only when the video ID remains exactly `dQw4w9WgXcQ`.

- [ ] **Step 8: Verify cleanup and repository hygiene**

Exit with `/exit`, then assert:

```bash
pgrep -f 'travis234_ghost_mcp/.*/ghost mcp'
find "$GHOST_MCP_HOME" \( -path '*/.ghost-os/*' -o -name mcp.json -o -name 'travis234-mcp-*.txt' \) -print
git status --short
git diff --check
git ls-files .disposable packages/travis234-ghost-mcp/travis234_ghost_mcp/bin/ghost
```

Expected: `pgrep`, `find`, and `git ls-files` print nothing; status contains only the verification document before its commit; the disposable clone and built binary remain untracked/un-staged.

After recording the bounded results, remove the isolated tree containing the copied auth file only after validating its exact prefix:

```bash
case "$GHOST_MCP_TMP" in
  /tmp/travis234-ghost-mcp-qualification.*) find "$GHOST_MCP_TMP" -depth -delete ;;
  *) echo "Refusing to delete unexpected qualification path" >&2; exit 1 ;;
esac
test ! -e "$GHOST_MCP_TMP"
```

Record that this disposable qualification tree was deleted and cannot be recovered; do not close the browser/video state created by the requested acceptance task.

- [ ] **Step 9: Write exact verification evidence**

Create `docs/verification/main-bundled-ghost-mcp.md` with:

- commit under test;
- OS/architecture/Swift/Python versions;
- exact automated commands, pass counts, and build artifact names;
- package-manager install and zero-config evidence;
- 29-tool protocol and child-cleanup evidence;
- container image tag and smoke result;
- permission state and setup actions;
- TUI prompt, exact final URL/title/playback result;
- explicit evidence that no `.ghost-os`, MCP config, external client config, tracked binary, credential, spill, or orphan process remained.

Do not paste desktop trees, provider tokens, environment dumps, or unbounded tool results.

- [ ] **Step 10: Commit verification and perform final review**

```bash
git add docs/verification/main-bundled-ghost-mcp.md
git commit -m "docs: verify bundled Ghost MCP release"
git status --short
git log --oneline 717a9d3..HEAD
```

Expected: clean status. Invoke `superpowers:requesting-code-review`, address only evidence-backed findings with regression-first fixes, repeat every affected focused/full gate, then use `superpowers:finishing-a-development-branch` for the integration decision. Do not publish or push without separate user authorization.
