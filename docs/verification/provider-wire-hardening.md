# Provider Wire Hardening Verification

- Date: 2026-08-18
- Branch: `htooayelwin/provider-wire-hardening`
- Base: `cf6c0e5`
- Scope: route-aware authentication, truthful direct-Kimi identity, provider error redaction, honest `/login` messaging, and an audited Pi catalog refresh.

## Test-driven changes

The authentication matrix began with seven failing route cases. The old provider-level default sent bearer authentication even when OpenCode selected an Anthropic-compatible or Google transport. The centralized request policy now chooses the credential header from the normalized provider and the model's actual API mode while preserving bearer auth for custom compatibility providers.

The direct Kimi regression first observed the inherited `KimiCLI/1.5` identity. The final request now reports `Travis234/<installed-version>` only for direct `kimi-coding` traffic, after transport finalization and before header callbacks.

Provider error tests first demonstrated reflection of ordinary active credentials, bearer tokens, `sk-...` tokens, and sensitive JSON fields. All are now redacted before truncation and before an error event can be persisted.

The `/login` regression first observed an unconditional success claim. The TUI now says that the key was saved and will be verified on the first request; login performs no paid probe.

The catalog promotion test exposed a separate false rejection: `allowEmptySignature` and `sessionAffinityFormat` were already implemented in runtime transports but were not recognized when absent from the old catalog. The promotion gate now explicitly recognizes those runtime capabilities while continuing to reject unsupported keys such as `supportsOpenAIGrammarTools`.

## Catalog evidence

- Pi was fast-forwarded to `209bc7b9a89b01c8fd05861cf5bbdda3e300037a` and regenerated locally.
- OpenCode Zen and Go additions were checked against their public model endpoints and the official Zen documentation.
- Direct Kimi was aligned to the four model IDs in the official Kimi Code model guide.
- The promotion manifest applies only 19 explicit add, update, or retire actions. GPT 5.6 OpenCode entries requiring unsupported grammar-tool compatibility were deliberately not promoted.
- MiniMax and MiniMax China already matched Pi and required no catalog mutation.

## Installed-wheel TUI scenarios

Each scenario used a clean Python 3.13 environment, the built `travis234` wheel, the real installed `travis234` console entry point, a real PTY, and isolated `~/.travis234`-shaped agent state.

1. **OpenCode Go / MiniMax M3 — PASS.** A normal prompt using the ignored dotenv credential returned the requested sentinel. The prior 401 was absent.
2. **Direct Kimi identity — PASS.** A local Anthropic SSE fixture observed `User-Agent: Travis234/2.5.0`; the TUI completed the prompt.
3. **Reflected provider secrets — PASS.** A local 401 fixture reflected three harmless credential shapes. The TUI and persisted session contained only redacted output.
4. **`/login` first-use message — PASS.** The real picker stored a harmless fixture key, masked input, made no provider request, and displayed the first-request verification wording.
5. **New `kimi-coding/k3` catalog route — PASS.** A normal-user prompt returned `CATALOG-K3-PASS`; the fixture observed model `k3`, `x-api-key`, and `Travis234/2.5.0` together.

## Final qualification

- Root Python suite: 2,649 passed in 269.57 seconds.
- Focused provider, catalog, reference-runtime, and TUI suite: 220 passed; the final auth/redaction subset: 23 passed.
- npm launcher suite: 24 passed; npm pack dry-run contained the 11 declared launcher, role, and built-in-skill files.
- Generic MCP adapter suite: 125 passed in 16.56 seconds.
- Root and adapter wheel/sdist builds: all four distributions passed `twine check`.
- Root wheel SHA-256: `0dfff543c1436058182bddfea6d260a059a07ff379128a79204d092257977001`.
- Root sdist SHA-256: `8d7333cba0e369bd7f767f21671f063e32174f249e37eca903a4070eeab0fdec`.
- Adapter wheel SHA-256: `6a42738a9883a103bbb2bd33348aa9cf4b5a0b1abf1865cc531de22bd0ea38e0`.
- Adapter sdist SHA-256: `d8007cb7f12f721582c696b183c942e90dbb282b6050e8a4e88a3b4060d41f97`.
- Exact-final-wheel TUI: live OpenCode Go / MiniMax M3 returned `FINAL-MINIMAX-M3-PASS`; reflected custom-header and `access_token` fixtures remained absent from persisted sessions; direct Kimi returned `FINAL-KIMI-K3-PASS` while the server observed model `k3`, `x-api-key`, and `Travis234/2.5.0`; `/login` masked input and displayed the first-request verification wording.
- No-cache `Dockerfile.release` image build: passed. Local image ID: `sha256:594a31245383e65c12336112e8df2c11d328e77a4e2e9f305d80e914c2f20ddd`.
- `evals/container_smoke.py` against that image: passed.

The live dotenv credential was read only for the authorized OpenCode Go smoke, was never printed, copied into the repository, or persisted in the isolated test session, and remains outside Git.

## Main release qualification

The verified provider work was fast-forwarded onto clean `main` and qualified as the aligned Travis234 `2.5.1` patch release on 2026-08-18.

- Merged-tree Python suite: 2,649 passed in 287.25 seconds.
- npm launcher suite: 24 passed; the `@htooayelwinict/travis234@2.5.1` archive contained the expected 11 declared files.
- Generic MCP adapter suite: 125 passed in 20.54 seconds; its independent version remains `0.2.0` and is not republished by this root-only patch.
- Clean `travis234==2.5.1` wheel install, metadata, provider-module imports, and console help smoke: passed.
- Root and adapter wheel/sdist builds: all four distributions passed `twine check`.
- Root wheel SHA-256: `4db4a1f1334f4aa5b8a15da168ab0a5a657b7c408486d1b9d5d37795f261be06`.
- Root sdist SHA-256: `ca8fa17cf9207be20d7d4ff4279435f80d1c77ba41ece1f60239610347682108`.
- npm tarball SHA-256: `a1d35b970774e04c4f8a778751655b6db36fd6d08582eab2a629ea008c239914`.
- No-cache `Dockerfile.release` image build and `evals/container_smoke.py`: passed; local image ID `sha256:4f6554a82b37ae96d2e510a7af543a59930375b5bd6d4035e05eda754f562107`.
