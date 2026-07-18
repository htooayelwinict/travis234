# Packaged Skills and Extension Reference Design

**Status:** Approved for implementation and release

**Date:** 2026-07-18

## Objective

Make the installed Travis234 wheel self-sufficient for extension authoring and the two existing built-in skills without adding an extension-authoring skill, while removing the obsolete first-party Hypa extension.

## Scope

This change is limited to packaged resources, resource discovery, distribution metadata, CLI cleanup made necessary by removing Hypa, documentation, and their tests.

The change must not modify:

- the agent loop or tool coordinator;
- session persistence, JSONL structure, replacement ordering, or locking;
- context-envelope construction or compaction behavior;
- provider request translation, authentication, or model selection;
- iteration budgets or bounded parallel execution.

## Installed extension reference

`travis/resources/docs/extensions.md` remains the authoritative extension-authoring reference. Travis234 already lists that installed file in the system prompt when a user asks about Travis234 extensions, so the agent can read it directly without an extension-authoring skill.

The guide will document only behavior supported by the Python runtime and covered by source or tests. It will include:

- global, trusted-project, explicit-path, and package discovery;
- the `extension(travis)` module entry point and a minimal working extension;
- source ownership, stale API invalidation, reload, and session replacement;
- command, shortcut, flag, tool, provider, message-renderer, and event registration;
- command/event context properties and actions;
- TUI and non-interactive UI behavior;
- user-message delivery, steering, follow-up, and command-expansion boundaries;
- subagent and managed-process access exposed through extension contexts;
- lifecycle event names and the important transformation/blocking contracts;
- an agent-driven create, syntax-check, reload, diagnose, repair, and retry workflow;
- packaging, trust, diagnostics, security, and context-cost guidance;
- intentional Pi divergences and unsupported JavaScript/TypeScript behavior.

The document will clearly distinguish verified public behavior from internal implementation details. Examples will use the current Python API names and will not promise unsupported Pi-native objects.

## Built-in skill resources

The existing npm skills will be mirrored into the Python distribution at:

- `travis/resources/skills/subagent-delegation/SKILL.md`
- `travis/resources/skills/web-search/SKILL.md`

The packaged resource copies become native read-only defaults. The resource loader will add the packaged skills directory after configured, installed-package, global, and trusted-project skill paths. Because skill collision resolution is first-wins, user or project skills with the same name remain authoritative and the packaged copy becomes the collision loser.

`no_skills=True` must prevent the resource loader from adding packaged defaults. Existing configured, discovered, and explicit skill-path semantics remain unchanged.

Normal system-prompt cost is limited to each enabled skill's name, description, and installed path. Skill bodies remain lazy and enter the turn only when invoked through the existing skill mechanism.

The npm package must retain its own copies because its standalone tarball cannot import files from an independently installed Python wheel. Automated tests will require the npm and Python resource copies to be byte-identical so release artifacts cannot drift silently.

No startup files will be copied into `~/.travis234`. This avoids mutating user state, avoids stale seeded copies after upgrades, and ensures wheel upgrades immediately supply the newest default while preserving user overrides.

## Hypa removal

The following obsolete surface will be removed:

- `travis/resources/extensions/hypa/`;
- the `--install-extension hypa` CLI option and its resource-copy helper;
- Hypa-specific tests;
- root and npm README instructions for installing Hypa;
- the now-unused Python extension package-data pattern when no packaged Python extensions remain.

Unknown or removed `--install-extension` usage will be rejected by the normal argument parser because the option no longer exists. Travis234's general global/project extension loading and managed package commands remain unchanged.

## Error handling and precedence

- A missing packaged skill directory is treated as a distribution-contract failure in tests, not as a runtime reason to mutate state.
- Invalid packaged skill metadata is surfaced through the existing resource diagnostic path.
- User/project collisions preserve the current first-wins behavior and source-aware diagnostic.
- Removing Hypa must leave no runtime imports, CLI help, README instructions, package metadata, or tests that advertise it.

## Verification

Implementation follows test-first development:

1. Add failing distribution/resource tests proving both packaged skills exist, load by default, stay lazy, honor `--no-skills`, and lose to user overrides.
2. Add a failing mirror-integrity test proving Python and npm skill files are byte-identical.
3. Change existing CLI/documentation tests to require the absence of Hypa and confirm they fail before cleanup.
4. Implement the minimal resource-loader, package-data, resource, CLI, and documentation changes.
5. Run focused resource, CLI, installed-metadata, extension-guide, distribution, and npm tests.
6. Run the complete Python suite, npm launcher suite, wheel/sdist builds, repository hygiene/parity checks, and relevant container smoke test.

## Success criteria

- An installed native Travis234 session advertises the two packaged skills without writing to user state.
- Existing same-name user/project skills remain the selected versions.
- The agent can locate and fully read the installed extension guide when asked to create or repair an extension, without any dedicated authoring skill.
- Python and npm distributions ship identical definitions for both built-in skills.
- Hypa is absent from source, CLI help, docs, tests, wheel, and container artifacts.
- Protected runtime, session, provider, context-envelope, and compaction files remain untouched.

## Release

After every verification gate passes, align the Python and npm versions at `2.3.3`, commit the complete Travis234 change set while excluding the unrelated `appv231/` tree, push `main`, publish the wheel and source distribution to PyPI, publish the npm package, and dispatch the existing gated GHCR workflow for the `production` and `2.3.3` image tags. Verify each public artifact before reporting completion.
