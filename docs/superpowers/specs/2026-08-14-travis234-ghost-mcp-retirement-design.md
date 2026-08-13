# Travis234 Ghost MCP Add-on Retirement Design

**Date:** 2026-08-14
**Status:** Approved for planning; execution requires separate user approval
**Products:** Travis234, `travis234-mcp-adapter`, `travis234-ghost-mcp`
**Registry owner:** `htooayelwinict`

## Objective

Retire the optional `travis234-ghost-mcp` add-on from the Travis234 source tree
and public installation path without weakening or removing Travis234's general
MCP client support.

The retirement uses a forward release rather than rewriting Git history or
deleting valid Travis234 artifacts. The Ghost release is yanked on PyPI only
after clean replacement releases have passed their public-registry checks.
Existing user-owned data under `~/.travis234/ghost-mcp` is preserved.

## Decision Summary

1. Delete the Ghost add-on package, vendored Ghost source, Ghost-specific tests,
   evaluation harness, active user documentation, and superseded feature
   design, plan, and verification records from the current tree.
2. Keep `travis234-mcp-adapter` and its generic trusted packaged-server
   registration API. Remove Ghost-specific wording and replace Ghost-named test
   fixtures with neutral packaged-server fixtures.
3. Release Ghost-free `travis234` 2.4.6,
   `travis234-mcp-adapter` 0.1.3, npm launcher 2.4.6, and GHCR image 2.4.6.
4. Move npm `latest` and GHCR `production` only after the exact versioned
   replacements are verified.
5. Yank, but do not delete, `travis234-ghost-mcp` 0.1.0 on PyPI. Retaining the
   project and files reserves the name and keeps the action reversible.
6. Remove the locally installed add-on through the Travis234 package manager,
   while leaving `~/.travis234/ghost-mcp` untouched.
7. Integrate through local `main` without ever publishing the temporary
   `htooakalewis/mcp-addons` branch, then remove that branch locally and verify
   it is absent from `origin`.

## Verified Current State

The retirement design is based on read-only checks made on 2026-08-14:

- The repository is clean at `532bc69` before this retirement specification.
- Root Travis234, the npm launcher, and GHCR `production` are version 2.4.5.
- The general MCP adapter is published on PyPI at version 0.1.2.
- `travis234-ghost-mcp` has one PyPI release, 0.1.0, containing one macOS
  Apple Silicon wheel and one source distribution. Neither file is yanked.
- There is no standalone `travis234-ghost-mcp` package on npm.
- There is no standalone Ghost image in the `htooayelwinict` GHCR namespace.
- The GitHub repository has no GitHub Release object for the Ghost add-on.
- The scoped npm package is `@htooayelwinict/travis234`; its `latest` tag is
  2.4.5.
- `ghcr.io/htooayelwinict/travis234:2.4.5` and `:production` identify the
  current Travis234 release image.
- The root Python distribution, npm launcher, and GHCR image do not contain the
  Ghost executable or `travis234-ghost-mcp` package. The Ghost payload is
  isolated to the optional PyPI add-on.
- The active GitHub CLI identity has been switched and verified as
  `htooayelwinict`.
- `refs/heads/htooakalewis/mcp-addons` exists only as the branch checked out by
  this linked worktree. A read-only `git ls-remote` confirms that no branch with
  that name currently exists on `origin`.

## Goals

The retirement is complete when:

1. Current Travis234 source and active documentation no longer ship, build,
   test, advertise, or recommend `travis234-ghost-mcp`.
2. Travis234's normal MCP adapter, additive `--mcp` behavior, configured server
   support, output bounds, transports, cancellation, and lifecycle remain
   intact.
3. The clean replacement versions are available and selected by the public
   registry defaults.
4. An ordinary unpinned PyPI installation no longer selects the Ghost add-on.
5. The local Ghost add-on package is absent from `travis234 list`.
6. Existing data under `~/.travis234/ghost-mcp` remains in place.
7. Git history continues to contain the historical implementation commits, but
   the current tree contains only the focused regression contract and the
   retirement spec, plan, and verification record needed to enforce and explain
   the removal.
8. No local or remote branch named `htooakalewis/mcp-addons` remains.

## Non-goals

- Do not remove or redesign `travis234-mcp-adapter`.
- Do not turn Travis234 into an MCP server.
- Do not change normal MCP configuration paths or introduce migration aliases.
- Do not change agent-loop order, iteration budgets, or bounded parallel tool
  execution.
- Do not rewrite or force-push Git history.
- Do not delete Travis234 2.4.5 from PyPI, npm, or GHCR; those artifacts do not
  contain the optional Ghost payload.
- Do not delete the PyPI project or release files for
  `travis234-ghost-mcp`. PyPI deletion is permanent and would not recall copies
  already downloaded.
- Do not automatically delete recipes, models, vision environments, or other
  user-owned files under `~/.travis234/ghost-mcp`.
- Do not add a Travis runtime denylist or a Ghost-specific migration path.
  Previously installed third-party add-ons remain the operator's responsibility
  until explicitly removed.

## Approaches Considered

### 1. Forward retirement with a PyPI yank — selected

Remove the add-on from the current source tree, publish clean patch releases,
verify the new defaults, and then yank Ghost 0.1.0. This preserves registry and
Git history, provides a reversible emergency path, and avoids removing valid
Travis234 2.4.5 artifacts.

### 2. Hard purge

Delete the PyPI project and remove Travis234 2.4.5 from npm and GHCR. This is
rejected because PyPI deletion is irreversible, deletion cannot recall cached
copies, removing the project may create name-retention risk, and the npm/GHCR
artifacts do not contain Ghost.

### 3. Repository-only removal

Delete Ghost from the source tree without changing public registry state. This
is rejected because new users could continue installing the add-on through
PyPI and the public retirement would be incomplete.

## Repository Changes

### Regression contract first

Before deleting implementation files, add and run a focused retirement test
that fails against the current tree. The contract must prove that:

- the `packages/travis234-ghost-mcp` application tree is absent;
- root and adapter distributions do not depend on or contain the Ghost add-on;
- active README and adapter documentation do not advertise Ghost installation;
- Ghost-specific evaluation entry points are absent; and
- the generic packaged-server interface remains importable and covered by
  neutral behavior tests.

The test must distinguish active product material from the new retirement
specification, plan, and verification record, which intentionally name the
retired package.

### Remove Ghost-owned material

Delete:

- the complete `packages/travis234-ghost-mcp` directory, including the Python
  extension, Swift source snapshot, compiled-build inputs, recipes, vision
  sidecar, licenses, and package tests;
- `evals/bundled_ghost_mcp_smoke.py` and its root harness assertions;
- the active Ghost installation and usage section in the root README;
- the superseded bundled-Ghost design, implementation plan, and release
  verification record; and
- ignore rules that exist only for the removed Swift build tree, unless another
  tracked repository component independently requires them.

Git history remains the historical authority for the removed implementation,
upstream notices, and previous release evidence.

### Preserve generic MCP behavior

Retain the adapter's immutable `PackagedServer` descriptor, process-local
registry, configuration overlay, collision reporting, lifecycle integration,
and idempotent extension registration. These are generic MCP extension
capabilities rather than Ghost product code.

Adapter tests that currently use `ghost-os`, `ghost_mcp.py`, or the Ghost wheel
as fixtures must be converted to neutral names and minimal temporary executable
fixtures. Tests must continue covering:

- absolute executable paths constrained to a package root;
- immutable registration and conflicting duplicate rejection;
- idempotent identical registration;
- deterministic packaged/configured server merging;
- configured-name shadow reporting;
- bounded request timeouts and status output; and
- installation of the adapter without any Ghost distribution.

The adapter README may document the generic trusted extension interface, but it
must not name Ghost or tell users to install a retired package.

### Version and documentation updates

Align the root Python package, workspace package, npm launcher, runtime fallback
version, README badge, and distribution contracts at Travis234 2.4.6. Bump the
adapter to 0.1.3 so its Ghost-free metadata and documentation are available as
an immutable public release.

User-facing documentation continues to explain installation and use of the
general adapter and ordinary configured MCP servers. No Ghost replacement is
introduced as part of this retirement.

## Installed Users and Local Cleanup

Updating Travis234 cannot and must not silently uninstall a separately
installed extension. Existing users remove it explicitly:

```bash
travis234 remove travis234-ghost-mcp
```

For this workstation, execution will:

1. Confirm the package appears in `travis234 list` and resolve its exact
   package-owned install path without printing credentials or state contents.
2. Ensure no package-owned Ghost child process is left running before removing
   its code.
3. Use the Travis234 package manager to remove the add-on.
4. Confirm it no longer appears in `travis234 list` and that the general MCP
   adapter still does.
5. Confirm only the existence, not the contents, of
   `~/.travis234/ghost-mcp`; leave that directory untouched.

This preserves the repository contract that user data stays under
`~/.travis234` and is not destructively removed without separate authorization.

## Git Integration and Temporary Branch Removal

The temporary worktree branch is local-only and must never be pushed. Source
integration happens before publication, but local branch deletion happens only
after publication, local uninstall, and final verification are complete:

1. Fetch `origin` and confirm local `main` still matches the expected upstream
   base before integration.
2. Integrate the retirement into the `main` branch checked out at
   `/Users/htooayelwin/orca/travis234` using a non-rewriting merge or
   fast-forward, as permitted by the actual commit graph.
3. Run the final source qualification from the integrated `main` commit and
   push only `main` to `origin` under the verified `htooayelwinict` identity.
4. Verify `origin/main` resolves to the exact qualified commit before any
   branch cleanup.
5. Keep the local temporary branch as a recovery reference while public
   publication and the local add-on uninstall run. Continue verifying that no
   corresponding remote ref exists.
6. After all release, yank, local-cleanup, and verification gates have passed,
   detach this linked worktree at the verified `origin/main` commit, confirm
   the temporary branch is an ancestor of `main`, and delete the exact local
   `htooakalewis/mcp-addons` branch.
7. If a remote `refs/heads/htooakalewis/mcp-addons` unexpectedly appears at any
   time, delete that exact remote ref. Do not use a wildcard or broad refspec.
8. Prove the final state with both `git branch --list` and
   `git ls-remote --heads origin refs/heads/htooakalewis/mcp-addons`; both must
   return no matching branch.

Branch deletion is the final destructive step. If integration, publication, or
upstream verification fails, retain the local branch for recovery while
continuing to guarantee that it is not pushed to the remote.

## Publication Sequence

Publication uses the `htooayelwinict` identities and existing release tooling.
Credentials must come from existing authenticated clients or untracked secret
configuration and must never appear in command output or tracked evidence.

### Gate 1: exact source qualification

1. Integrate the focused retirement implementation into local `main` through
   the repository's GitOps flow without rewriting history and without pushing
   the temporary worktree branch.
2. Run every focused and repository-level check described below from the exact
   candidate commit.
3. Build the root, adapter, and npm artifacts from that commit and record their
   hashes outside tracked credential material.
4. Build and smoke-test the no-cache release container.

No registry write occurs unless Gate 1 passes.

### Gate 2: immutable versioned replacements

1. Publish `travis234==2.4.6` and
   `travis234-mcp-adapter==0.1.3` to PyPI, then install those exact versions in
   a clean Python 3.13 environment.
2. Publish `ghcr.io/htooayelwinict/travis234:2.4.6`, pull it by its public
   digest, and run the repository container smoke against that digest.
3. Publish `@htooayelwinict/travis234@2.4.6` under a temporary non-default npm
   dist-tag, install the exact version, and verify its dry-run plus container
   launch path.

If any exact-version check fails, stop. Leave Ghost unyanked and leave npm
`latest` plus GHCR `production` on 2.4.5 while a new patch is prepared.

### Gate 3: move public defaults

1. Move npm `latest` to `@htooayelwinict/travis234@2.4.6` and verify it from
   registry metadata and a clean install.
2. Move GHCR `production` to the already verified 2.4.6 digest and prove that
   both tags resolve to the same manifest digest.
3. Run one final public-default launcher/container smoke.

Mutable tags can be moved back to 2.4.5 if a default-path check fails. Published
version numbers and image digests remain immutable.

### Gate 4: retire the Ghost release

After all clean public defaults pass, yank the complete
`travis234-ghost-mcp` 0.1.0 release through the PyPI project-management UI with
this reason:

> Retired; use Travis234's standard MCP adapter for supported MCP servers.

Confirm through the public PyPI JSON and Simple APIs that both the wheel and
source distribution are marked yanked with the retirement reason. Confirm that
an ordinary unpinned installation does not select 0.1.0.

Yanking is intentionally not deletion. An exact `==0.1.0` or `===0.1.0` pin
may still install a yanked release, previously downloaded copies remain outside
registry control, and the project page remains visible. This is the accepted
trade-off for reversibility, downstream stability, and name retention.

### Gate 5: local uninstall and branch teardown

1. Remove the locally installed add-on through `travis234 remove`, confirm the
   adapter remains installed, and verify the Ghost state directory is retained
   without inspecting its contents.
2. Confirm all public registry and `origin/main` checks still pass.
3. Detach the linked worktree, prove the temporary branch is contained in
   `main`, delete the exact local branch, and prove the exact remote ref remains
   absent.

Gate 5 is last so the local branch remains available if any earlier release or
retirement action needs a forward correction.

## Failure and Recovery

- **Source or test failure:** make no registry changes; repair through a new
  failing regression and rerun all affected gates.
- **Partial PyPI replacement publication:** do not overwrite an uploaded
  version. Fix forward with the next patch version and keep Ghost unyanked.
- **npm exact-version failure:** keep `latest` on 2.4.5 and publish a new patch;
  do not reuse an unpublished version.
- **GHCR exact-image failure:** do not move `production`; publish a corrected
  digest under a new patch tag.
- **Default-tag failure:** move npm `latest` or GHCR `production` back to the
  verified 2.4.5 artifact.
- **Unexpected post-yank regression:** PyPI owners may unyank 0.1.0 while a
  corrected retirement is prepared. Do not delete the release.
- **Local uninstall failure:** leave user state untouched, report the exact
  bounded package-manager error, and do not manually delete broad package or
  state directories.
- **Branch cleanup precondition failure:** do not force-delete unmerged work.
  Keep the branch local, ensure the remote ref remains absent, repair the
  integration, and repeat the ancestry check before deletion.

## Verification Strategy

### Focused checks

- Run the new retirement contract red against the pre-removal tree, then green
  after removal.
- Run the complete adapter suite with neutral packaged-server fixtures.
- Run affected root distribution, dependency, evaluation-harness, extension,
  and documentation tests.
- Search tracked active product paths for Ghost package names, commands,
  runtime imports, vendored source, and install instructions. Retirement
  records and the focused regression contract are the only allowed current-tree
  references.

### Repository release gates

Before implementation is reported complete, run and record:

- the complete root Python test suite;
- the complete `travis234-mcp-adapter` Python test suite;
- npm launcher tests;
- npm dry-run package build;
- root and adapter sdist/wheel builds;
- metadata and distribution checks for every Python artifact;
- clean Python 3.13 installs of the exact built artifacts;
- the no-cache `Dockerfile.release` build; and
- the relevant unprivileged container smoke checks.

There is no Ghost package or Swift suite to build after removal.

### Public registry checks

- PyPI reports Travis234 2.4.6 and adapter 0.1.3, and clean installation does
  not acquire `travis234-ghost-mcp`.
- npm reports `@htooayelwinict/travis234` `latest` as 2.4.6.
- GHCR reports `2.4.6` and `production` at the same verified manifest digest.
- PyPI marks every Ghost 0.1.0 file yanked with the bounded retirement reason.
- No standalone Ghost npm or GHCR artifact exists.
- No GitHub credential, npm token, PyPI token, `.env` value, or authentication
  response is captured in logs or verification records.
- `origin/main` points to the qualified retirement commit and no remote
  `htooakalewis/mcp-addons` ref exists.

### Local checks

- `travis234 list` no longer includes `travis234-ghost-mcp`.
- `travis234-mcp-adapter` remains installed and its generic MCP proxy can start
  without a Ghost server.
- No package-owned Ghost process remains.
- `~/.travis234/ghost-mcp` is preserved without inspecting or exposing its
  contents.
- No local branch named `htooakalewis/mcp-addons` remains after integration and
  verification.

## Completion Criteria

The work may be reported complete only when:

1. The current repository tree contains no active Ghost add-on implementation,
   advertisement, build, or test dependency.
2. Generic MCP behavior and all required repository release gates pass.
3. The Ghost-free patch releases are public and selected by npm/GHCR defaults.
4. Ghost 0.1.0 is publicly marked yanked, not deleted.
5. The local add-on is uninstalled and local user data is preserved.
6. A final verification record contains commands, bounded results, artifact
   versions, and public digests without credentials.
7. `htooakalewis/mcp-addons` is absent as both a local and remote branch.
