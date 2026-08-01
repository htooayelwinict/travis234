# OffSec npx dotenv forwarding design

## Goal

Let the published OffSec npm launcher load an explicitly chosen dotenv file so
OpenAI-compatible proxies such as 9router can provide their API key and base URL
to the containerized CLI.

## Contract

`--dotenv PATH` is a launcher option, used before `--`:

```bash
npx @htooayelwinict/travis234-offsec \
  --cwd ~/agent-work \
  --dotenv ~/.config/travis/9router.env
```

The launcher resolves and validates the path on the host, then supplies it to
Docker with `--env-file PATH`. It does not mount the source file into the
container and does not copy its contents into `~/.travis234` or the workspace.
The Python CLI receives the resulting environment and uses its existing provider
and base-URL configuration paths.

Without `--dotenv`, the launcher continues to forward no host provider
credentials. `--image`, `TRAVIS234_IMAGE`, `TRAVIS234_SANDBOX_IMAGE`, and
`TRAVIS234_SANDBOX_HOME` retain their current behavior.

## Implementation boundaries

Only `packages/travis234-cli` changes:

- Parse `--dotenv PATH` and `--dotenv=PATH` as launcher flags.
- Resolve the dotenv path relative to the host working directory and fail before
  Docker starts when it is missing or not a regular file.
- Add Docker's `--env-file` only when the user supplied the flag.
- Do not forward the dotenv flag to the container CLI, because the file is not
  present there.
- Update launcher help and package documentation.

The Python runtime, provider transports, agent-loop ordering, compaction, and
bounded subagent execution are out of scope.

## Tests and release

Add failing launcher tests first for parsing, validation, explicit Docker
`--env-file` construction, and the unchanged no-dotenv default. Run npm launcher
tests, focused distribution tests, package dry-run, and a relevant container
smoke check. Publish a new aligned OffSec prerelease image and npm package after
the code is verified; the host-native PyPI package already supports `--dotenv`
and does not require a runtime change.
