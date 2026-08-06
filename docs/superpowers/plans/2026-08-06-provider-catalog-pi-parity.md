# Provider Catalog Pi-Parity Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Retire direct Anthropic Opus 4.1 safely and add deterministic, promotion-gated Pi catalog maintenance without runtime discovery or bulk catalog replacement.

**Architecture:** Extend the existing pure catalog-generation module with validation, deterministic comparison, and scoped promotion operations. Extend the existing maintenance script with explicit read-only check and promotion-apply modes, then use a checked-in evidence manifest to remove only the two approved direct Anthropic records.

**Tech Stack:** Python 3.13 standard library, JSON, dataclasses, pytest, existing checked-in `builtin_models.json` catalog.

## Global Constraints

- Product and CLI remain `Travis234` and `travis234`; Python imports remain under `travis`.
- Treat the repository root as the only active application tree.
- Preserve user data under `~/.travis234`; introduce no alternate state path or migration alias.
- Keep credentials, environment contents, and arbitrary request headers out of reports, fixtures, tracked files, and command output.
- Add a failing regression before each catalog bug fix.
- Keep `builtin_providers()` static; do not add `refresh_models` callbacks or normal-startup network access.
- Pi is the parity baseline, but official documentation and verified endpoint behavior are authoritative.
- Catalog comparison is informational; catalog mutation requires explicit provider/model promotions.
- Retire Opus 4.1 only from direct Anthropic. Preserve Bedrock, Cloudflare AI Gateway, and OpenCode variants.
- Preserve existing OpenRouter safe-output overrides and refresh behavior.
- Do not modify agent-loop ordering, iteration budgeting, bounded parallel execution, compaction, session persistence, TUI state, or tool scheduling.
- Do not stage, edit, remove, or reformat either user-owned untracked document.
- Before completion, run focused Python tests, the full Python suite, npm launcher tests, package builds/checks, and the relevant release-container smoke.

---

## File structure

- Modify `travis/ai/catalog_generation.py`: catalog validation, stable drift records, comparison, promotion validation, and scoped application. Retain `apply_openrouter_capabilities()` unchanged except exports/import organization.
- Modify `scripts/sync_builtin_model_catalog.py`: preserve existing OpenRouter refresh mode; add explicit Pi check/apply modes and atomic writes.
- Create `scripts/catalog_promotions/2026-08-06-anthropic-opus-4-1-retirement.json`: reviewed retirement scope, Pi commit, reason, and official evidence.
- Modify `travis/ai/builtin_models.json`: remove exactly two direct Anthropic records through the promotion tool.
- Modify `tests/test_catalog_generation.py`: pure comparison, validation, gating, safety-override, and retirement regressions.
- Create `tests/test_sync_builtin_model_catalog.py`: script-level read-only, malformed-input, scoped-apply, and atomic-failure tests.

Do not create a runtime cache, provider refresh callback, new model schema, or full Pi snapshot fixture.

## Execution preflight

- [ ] **Record the immutable comparison base before Task 1**

```bash
git rev-parse HEAD > /tmp/travis234-provider-catalog-plan-base
git status --short --branch
```

Expected: the base file contains the current 40-character commit, and status shows only the two pre-existing user-owned untracked documents.

### Task 1: Add deterministic Pi catalog comparison

**Files:**

- Modify: `tests/test_catalog_generation.py:1-10`
- Modify: `travis/ai/catalog_generation.py:1-76`

**Interfaces:**

- Produces: `CatalogDrift`, `validate_catalog()`, `compare_pi_catalogs()`, and `catalog_drift_to_dict()`.
- Consumers: the maintenance script and promotion tests in later tasks.

- [ ] **Step 1: Add failing comparison and validation tests**

Extend imports in `tests/test_catalog_generation.py`:

```python
from travis.ai.catalog_generation import (
    CatalogDrift,
    apply_openrouter_capabilities,
    catalog_drift_to_dict,
    compare_pi_catalogs,
    validate_catalog,
)
```

Then add:

```python
def _catalog_record(provider: str, model_id: str, **overrides) -> dict:
    record = {
        "id": model_id,
        "name": model_id,
        "api": "openai-completions",
        "provider": provider,
        "baseUrl": f"https://{provider}.invalid/v1",
        "reasoning": False,
        "input": ["text"],
        "cost": {"input": 0, "output": 0, "cacheRead": 0, "cacheWrite": 0},
        "contextWindow": 32_000,
        "maxTokens": 4_096,
    }
    record.update(overrides)
    return record


def test_compare_pi_catalogs_is_stable_and_classifies_drift() -> None:
    current = {
        "direct": {
            "same": _catalog_record("direct", "same", contextWindow=32_000),
            "removed": _catalog_record("direct", "removed"),
        },
        "travis-only": {"local": _catalog_record("travis-only", "local")},
    }
    pi = {
        "direct": {
            "same": _catalog_record("direct", "same", contextWindow=64_000),
            "added": _catalog_record(
                "direct",
                "added",
                compat={"futureCompatFlag": True},
            ),
        },
        "pi-only": {"remote": _catalog_record("pi-only", "remote")},
    }

    first = compare_pi_catalogs(current, pi)
    second = compare_pi_catalogs(current, pi)

    assert first == second
    assert first == tuple(sorted(first, key=lambda item: item.sort_key()))
    assert {item.kind for item in first} == {
        "provider_missing_from_pi",
        "provider_missing_from_travis",
        "model_missing_from_pi",
        "model_missing_from_travis",
        "field_difference",
        "unsupported_compatibility",
    }
    assert any(
        item.kind == "field_difference"
        and item.provider == "direct"
        and item.model == "same"
        and item.field == "contextWindow"
        for item in first
    )
    assert any(
        item.kind == "unsupported_compatibility"
        and item.field == "compat.futureCompatFlag"
        for item in first
    )
    assert [catalog_drift_to_dict(item) for item in first] == [
        catalog_drift_to_dict(item) for item in second
    ]


@pytest.mark.parametrize(
    "payload",
    [
        [],
        {"provider": []},
        {"provider": {"model": []}},
        {"provider": {"model": {"id": "different"}}},
        {
            "provider": {
                "model": {
                    "id": "model",
                    "provider": "provider",
                    "api": [],
                }
            }
        },
    ],
)
def test_validate_catalog_rejects_malformed_shapes(payload) -> None:
    with pytest.raises(ValueError, match="catalog"):
        validate_catalog(payload)
```

- [ ] **Step 2: Run the new tests and confirm the interfaces are absent**

```bash
.venv/bin/python -m pytest \
  tests/test_catalog_generation.py::test_compare_pi_catalogs_is_stable_and_classifies_drift \
  tests/test_catalog_generation.py::test_validate_catalog_rejects_malformed_shapes \
  -q
```

Expected: collection fails because the new catalog comparison interfaces do not exist.

- [ ] **Step 3: Add the drift type, validation, and supported-field constants**

Add these definitions above `apply_openrouter_capabilities()` in `travis/ai/catalog_generation.py`:

```python
from dataclasses import dataclass
import json
from typing import Any, Literal


CatalogDriftKind = Literal[
    "provider_missing_from_travis",
    "provider_missing_from_pi",
    "model_missing_from_travis",
    "model_missing_from_pi",
    "field_difference",
    "unsupported_api",
    "unsupported_compatibility",
]

COMPARABLE_MODEL_FIELDS = (
    "id",
    "name",
    "api",
    "provider",
    "baseUrl",
    "reasoning",
    "thinkingLevelMap",
    "input",
    "cost",
    "contextWindow",
    "maxTokens",
    "compat",
)


@dataclass(frozen=True)
class CatalogDrift:
    kind: CatalogDriftKind
    provider: str
    model: str | None = None
    field: str | None = None
    current: Any = None
    reference: Any = None

    def sort_key(self) -> tuple[str, str, str, str, str, str]:
        return (
            self.provider,
            self.model or "",
            self.kind,
            self.field or "",
            _stable_json(self.current),
            _stable_json(self.reference),
        )


def _stable_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def validate_catalog(payload: Any) -> dict[str, dict[str, dict[str, Any]]]:
    if not isinstance(payload, dict):
        raise ValueError("catalog must contain a provider object")
    for provider, models in payload.items():
        if not isinstance(provider, str) or not isinstance(models, dict):
            raise ValueError("catalog provider entries must contain model objects")
        for model_id, record in models.items():
            if not isinstance(model_id, str) or not isinstance(record, dict):
                raise ValueError("catalog model entries must be objects")
            if record.get("id") != model_id:
                raise ValueError(
                    f"catalog record id mismatch for {provider}/{model_id}"
                )
            if record.get("provider") != provider:
                raise ValueError(
                    f"catalog record provider mismatch for {provider}/{model_id}"
                )
            if not isinstance(record.get("api"), str) or not record["api"]:
                raise ValueError(f"catalog record api is invalid for {provider}/{model_id}")
            for numeric_field in ("contextWindow", "maxTokens"):
                value = record.get(numeric_field)
                if value is not None and (
                    not isinstance(value, int) or isinstance(value, bool) or value < 0
                ):
                    raise ValueError(
                        f"catalog record {numeric_field} is invalid for "
                        f"{provider}/{model_id}"
                    )
            if "input" in record and not isinstance(record["input"], list):
                raise ValueError(f"catalog record input is invalid for {provider}/{model_id}")
            if "cost" in record and not isinstance(record["cost"], dict):
                raise ValueError(f"catalog record cost is invalid for {provider}/{model_id}")
            if "compat" in record and not isinstance(record["compat"], dict):
                raise ValueError(f"catalog record compat is invalid for {provider}/{model_id}")
    return payload
```

- [ ] **Step 4: Implement deterministic comparison without headers or secrets**

Implement:

```python
def compare_pi_catalogs(
    current: dict[str, Any],
    reference: dict[str, Any],
) -> tuple[CatalogDrift, ...]:
    current = validate_catalog(current)
    reference = validate_catalog(reference)
    current_apis = {
        str(record.get("api"))
        for models in current.values()
        for record in models.values()
        if record.get("api")
    }
    current_compat = {
        str(key)
        for models in current.values()
        for record in models.values()
        for key in (
            record.get("compat", {}).keys()
            if isinstance(record.get("compat"), dict)
            else ()
        )
    }
    drift: list[CatalogDrift] = []

    for provider in sorted(set(current) | set(reference)):
        if provider not in current:
            drift.append(CatalogDrift("provider_missing_from_travis", provider))
            continue
        if provider not in reference:
            drift.append(CatalogDrift("provider_missing_from_pi", provider))
            continue
        current_models = current[provider]
        reference_models = reference[provider]
        for model_id in sorted(set(current_models) | set(reference_models)):
            if model_id not in current_models:
                drift.append(
                    CatalogDrift("model_missing_from_travis", provider, model_id)
                )
                reference_record = reference_models[model_id]
            elif model_id not in reference_models:
                drift.append(CatalogDrift("model_missing_from_pi", provider, model_id))
                continue
            else:
                reference_record = reference_models[model_id]

            reference_api = reference_record.get("api")
            if reference_api not in current_apis:
                drift.append(
                    CatalogDrift(
                        "unsupported_api",
                        provider,
                        model_id,
                        "api",
                        reference=reference_api,
                    )
                )
            reference_compat = reference_record.get("compat")
            if isinstance(reference_compat, dict):
                for key in sorted(set(reference_compat) - current_compat):
                    drift.append(
                        CatalogDrift(
                            "unsupported_compatibility",
                            provider,
                            model_id,
                            f"compat.{key}",
                            reference=reference_compat[key],
                        )
                    )
            if model_id not in current_models:
                continue
            current_record = current_models[model_id]
            for field in COMPARABLE_MODEL_FIELDS:
                if current_record.get(field) != reference_record.get(field):
                    drift.append(
                        CatalogDrift(
                            "field_difference",
                            provider,
                            model_id,
                            field,
                            current_record.get(field),
                            reference_record.get(field),
                        )
                    )

    return tuple(sorted(drift, key=CatalogDrift.sort_key))


def catalog_drift_to_dict(item: CatalogDrift) -> dict[str, Any]:
    return {
        "kind": item.kind,
        "provider": item.provider,
        "model": item.model,
        "field": item.field,
        "current": item.current,
        "reference": item.reference,
    }
```

Do not compare or emit `headers`, environment values, or fields outside `COMPARABLE_MODEL_FIELDS`.

- [ ] **Step 5: Export and run the comparison tests**

Add the new public names to `__all__`, retaining `apply_openrouter_capabilities`, then run:

```bash
.venv/bin/python -m pytest tests/test_catalog_generation.py -q
```

Expected: all existing OpenRouter tests and new comparison tests pass.

- [ ] **Step 6: Commit the read-only comparison layer**

```bash
git add travis/ai/catalog_generation.py tests/test_catalog_generation.py
git commit -m "feat: add deterministic Pi catalog comparison"
```

### Task 2: Add explicit promotion validation and scoped application

**Files:**

- Modify: `tests/test_catalog_generation.py`
- Modify: `travis/ai/catalog_generation.py`

**Interfaces:**

- Consumes: validated current/Pi catalogs from Task 1.
- Produces: `CatalogPromotion`, `PromotionSet`, `load_promotion_set()`, and `apply_pi_promotions()`.

- [ ] **Step 1: Add failing promotion-gate tests**

Extend imports with `apply_pi_promotions` and `load_promotion_set`, then add:

```python
def test_apply_pi_promotions_changes_only_explicit_scope() -> None:
    current = {
        "direct": {
            "keep": _catalog_record("direct", "keep", contextWindow=32_000),
            "retire": _catalog_record("direct", "retire"),
        }
    }
    pi = {
        "direct": {
            "keep": _catalog_record("direct", "keep", contextWindow=64_000),
            "add": _catalog_record("direct", "add"),
        }
    }
    promotions = load_promotion_set(
        {
            "piCommit": "bde81c84405514c8b0f57c34405c152fb129c0ce",
            "promotions": [
                {
                    "action": "add",
                    "provider": "direct",
                    "model": "add",
                    "reason": "supported current model",
                    "evidence": "https://provider.invalid/models",
                },
                {
                    "action": "retire",
                    "provider": "direct",
                    "model": "retire",
                    "reason": "retired upstream",
                    "evidence": "https://provider.invalid/deprecations",
                },
            ],
        }
    )

    updated, changed = apply_pi_promotions(current, pi, promotions)

    assert changed == ("add:direct/add", "retire:direct/retire")
    assert updated["direct"]["keep"] == current["direct"]["keep"]
    assert updated["direct"]["add"] == pi["direct"]["add"]
    assert "retire" not in updated["direct"]


def test_apply_pi_promotions_rejects_unavailable_or_unsupported_updates() -> None:
    current = {"direct": {"keep": _catalog_record("direct", "keep")}}
    pi = {
        "direct": {
            "future": _catalog_record(
                "direct",
                "future",
                api="future-api",
                compat={"futureCompatFlag": True},
            )
        }
    }
    promotions = load_promotion_set(
        {
            "piCommit": "bde81c84405514c8b0f57c34405c152fb129c0ce",
            "promotions": [
                {
                    "action": "add",
                    "provider": "direct",
                    "model": "future",
                    "reason": "not yet supported",
                    "evidence": "https://provider.invalid/models",
                }
            ],
        }
    )

    with pytest.raises(ValueError, match="unsupported api"):
        apply_pi_promotions(current, pi, promotions)


def test_apply_pi_promotions_rejects_unregistered_provider() -> None:
    current = {"direct": {"keep": _catalog_record("direct", "keep")}}
    pi = {"new-provider": {"m": _catalog_record("new-provider", "m")}}
    promotions = load_promotion_set(
        {
            "piCommit": "bde81c84405514c8b0f57c34405c152fb129c0ce",
            "promotions": [
                {
                    "action": "add",
                    "provider": "new-provider",
                    "model": "m",
                    "reason": "provider is not registered in Travis234",
                    "evidence": "https://provider.invalid/models",
                }
            ],
        }
    )

    with pytest.raises(ValueError, match="unsupported provider"):
        apply_pi_promotions(current, pi, promotions)


@pytest.mark.parametrize(
    "payload",
    [
        {"piCommit": "", "promotions": []},
        {"piCommit": "abc", "promotions": []},
        {
            "piCommit": "b" * 40,
            "promotions": [
                {
                    "action": "update",
                    "provider": "direct",
                    "model": "m",
                    "reason": "",
                    "evidence": "http://not-secure.invalid",
                }
            ],
        },
    ],
)
def test_load_promotion_set_requires_auditable_evidence(payload) -> None:
    with pytest.raises(ValueError, match="promotion"):
        load_promotion_set(payload)
```

- [ ] **Step 2: Run the promotion tests and verify the interfaces are absent**

```bash
.venv/bin/python -m pytest \
  tests/test_catalog_generation.py::test_apply_pi_promotions_changes_only_explicit_scope \
  tests/test_catalog_generation.py::test_apply_pi_promotions_rejects_unavailable_or_unsupported_updates \
  tests/test_catalog_generation.py::test_apply_pi_promotions_rejects_unregistered_provider \
  tests/test_catalog_generation.py::test_load_promotion_set_requires_auditable_evidence \
  -q
```

Expected: collection fails because the promotion interfaces do not exist.

- [ ] **Step 3: Add immutable promotion types and manifest validation**

Implement in `travis/ai/catalog_generation.py`:

```python
@dataclass(frozen=True)
class CatalogPromotion:
    action: Literal["add", "update", "retire"]
    provider: str
    model: str
    reason: str
    evidence: str


@dataclass(frozen=True)
class PromotionSet:
    pi_commit: str
    promotions: tuple[CatalogPromotion, ...]


def load_promotion_set(payload: Any) -> PromotionSet:
    if not isinstance(payload, dict):
        raise ValueError("promotion manifest must contain an object")
    pi_commit = payload.get("piCommit")
    if (
        not isinstance(pi_commit, str)
        or len(pi_commit) != 40
        or any(char not in "0123456789abcdef" for char in pi_commit.lower())
    ):
        raise ValueError("promotion manifest requires a full Pi commit")
    raw_promotions = payload.get("promotions")
    if not isinstance(raw_promotions, list) or not raw_promotions:
        raise ValueError("promotion manifest requires at least one promotion")
    promotions: list[CatalogPromotion] = []
    seen: set[tuple[str, str]] = set()
    for raw in raw_promotions:
        if not isinstance(raw, dict):
            raise ValueError("promotion entries must be objects")
        action = raw.get("action")
        provider = raw.get("provider")
        model = raw.get("model")
        reason = raw.get("reason")
        evidence = raw.get("evidence")
        if action not in {"add", "update", "retire"}:
            raise ValueError("promotion action must be add, update, or retire")
        if not all(isinstance(value, str) and value.strip() for value in (provider, model, reason)):
            raise ValueError("promotion provider, model, and reason are required")
        if not isinstance(evidence, str) or not evidence.startswith("https://"):
            raise ValueError("promotion evidence must be an https URL")
        key = (provider, model)
        if key in seen:
            raise ValueError(f"duplicate promotion scope: {provider}/{model}")
        seen.add(key)
        promotions.append(CatalogPromotion(action, provider, model, reason, evidence))
    return PromotionSet(pi_commit.lower(), tuple(promotions))
```

- [ ] **Step 4: Implement scoped promotion application**

Use `copy.deepcopy` so neither input is mutated:

```python
def apply_pi_promotions(
    current: dict[str, Any],
    reference: dict[str, Any],
    promotion_set: PromotionSet,
) -> tuple[dict[str, Any], tuple[str, ...]]:
    import copy

    current = validate_catalog(current)
    reference = validate_catalog(reference)
    current_apis = {
        str(record.get("api"))
        for models in current.values()
        for record in models.values()
        if record.get("api")
    }
    current_compat = {
        str(key)
        for models in current.values()
        for record in models.values()
        for key in (
            record.get("compat", {}).keys()
            if isinstance(record.get("compat"), dict)
            else ()
        )
    }
    updated = copy.deepcopy(current)
    changed: list[str] = []
    for promotion in promotion_set.promotions:
        if promotion.provider not in current:
            raise ValueError(f"unsupported provider: {promotion.provider}")
        provider_models = updated[promotion.provider]
        if promotion.action == "retire":
            if promotion.model not in provider_models:
                raise ValueError(
                    f"retirement target is absent: {promotion.provider}/{promotion.model}"
                )
            del provider_models[promotion.model]
        else:
            reference_record = reference.get(promotion.provider, {}).get(promotion.model)
            if not isinstance(reference_record, dict):
                raise ValueError(
                    f"Pi promotion target is absent: {promotion.provider}/{promotion.model}"
                )
            if reference_record.get("api") not in current_apis:
                raise ValueError(
                    f"unsupported api for {promotion.provider}/{promotion.model}: "
                    f"{reference_record.get('api')}"
                )
            compat = reference_record.get("compat")
            unknown_compat = (
                sorted(set(compat) - current_compat) if isinstance(compat, dict) else []
            )
            if unknown_compat:
                raise ValueError(
                    f"unsupported compatibility for {promotion.provider}/{promotion.model}: "
                    f"{unknown_compat}"
                )
            exists = promotion.model in provider_models
            if promotion.action == "add" and exists:
                raise ValueError("add promotion target already exists")
            if promotion.action == "update" and not exists:
                raise ValueError("update promotion target is absent")
            provider_models[promotion.model] = copy.deepcopy(reference_record)
        changed.append(
            f"{promotion.action}:{promotion.provider}/{promotion.model}"
        )
    validate_catalog(updated)
    return updated, tuple(sorted(changed))
```

Do not allow wildcard providers/models or implicit additions/removals.

- [ ] **Step 5: Export and run the promotion tests**

Add the new types and functions to `__all__`, then run:

```bash
.venv/bin/python -m pytest tests/test_catalog_generation.py -q
```

Expected: all catalog-generation tests pass, including unchanged OpenRouter safety tests.

- [ ] **Step 6: Commit the promotion gate**

```bash
git add travis/ai/catalog_generation.py tests/test_catalog_generation.py
git commit -m "feat: gate Pi catalog promotions explicitly"
```

### Task 3: Add read-only check and atomic apply script modes

**Files:**

- Create: `tests/test_sync_builtin_model_catalog.py`
- Modify: `scripts/sync_builtin_model_catalog.py:1-72`

**Interfaces:**

- Consumes: `compare_pi_catalogs()`, `catalog_drift_to_dict()`, `load_promotion_set()`, and `apply_pi_promotions()` from Tasks 1-2.
- Produces: `main(argv: list[str] | None = None) -> int`, `--pi-catalog`, `--check`, and `--promotions` behavior while retaining the existing OpenRouter refresh path.

- [ ] **Step 1: Add failing script-mode tests**

Create `tests/test_sync_builtin_model_catalog.py`:

```python
from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts import sync_builtin_model_catalog


def _record(provider: str, model_id: str, *, context_window: int = 32_000) -> dict:
    return {
        "id": model_id,
        "name": model_id,
        "api": "openai-completions",
        "provider": provider,
        "baseUrl": f"https://{provider}.invalid/v1",
        "reasoning": False,
        "input": ["text"],
        "cost": {"input": 0, "output": 0, "cacheRead": 0, "cacheWrite": 0},
        "contextWindow": context_window,
        "maxTokens": 4_096,
    }


def _write_json(path: Path, payload: object) -> None:
    path.write_text(json.dumps(payload), encoding="utf-8")


def test_pi_check_mode_reports_drift_without_writing(tmp_path: Path, capsys) -> None:
    catalog_path = tmp_path / "catalog.json"
    pi_path = tmp_path / "pi.json"
    current = {"direct": {"m": _record("direct", "m")}}
    reference = {"direct": {"m": _record("direct", "m", context_window=64_000)}}
    _write_json(catalog_path, current)
    _write_json(pi_path, reference)
    before = catalog_path.read_bytes()

    result = sync_builtin_model_catalog.main(
        [
            "--catalog",
            str(catalog_path),
            "--pi-catalog",
            str(pi_path),
            "--check",
        ]
    )

    report = json.loads(capsys.readouterr().out)
    assert result == 0
    assert catalog_path.read_bytes() == before
    assert report[0]["kind"] == "field_difference"
    assert report[0]["field"] == "contextWindow"


def test_pi_apply_mode_changes_only_manifest_scope(tmp_path: Path) -> None:
    catalog_path = tmp_path / "catalog.json"
    pi_path = tmp_path / "pi.json"
    promotions_path = tmp_path / "promotions.json"
    current = {
        "direct": {
            "keep": _record("direct", "keep"),
            "retire": _record("direct", "retire"),
        }
    }
    reference = {
        "direct": {"keep": _record("direct", "keep", context_window=64_000)}
    }
    promotions = {
        "piCommit": "bde81c84405514c8b0f57c34405c152fb129c0ce",
        "promotions": [
            {
                "action": "retire",
                "provider": "direct",
                "model": "retire",
                "reason": "retired upstream",
                "evidence": "https://provider.invalid/deprecations",
            }
        ],
    }
    _write_json(catalog_path, current)
    _write_json(pi_path, reference)
    _write_json(promotions_path, promotions)

    result = sync_builtin_model_catalog.main(
        [
            "--catalog",
            str(catalog_path),
            "--pi-catalog",
            str(pi_path),
            "--promotions",
            str(promotions_path),
        ]
    )

    updated = json.loads(catalog_path.read_text(encoding="utf-8"))
    assert result == 0
    assert updated["direct"]["keep"] == current["direct"]["keep"]
    assert "retire" not in updated["direct"]


def test_pi_apply_failure_leaves_catalog_bytes_unchanged(tmp_path: Path) -> None:
    catalog_path = tmp_path / "catalog.json"
    pi_path = tmp_path / "pi.json"
    promotions_path = tmp_path / "promotions.json"
    _write_json(catalog_path, {"direct": {"m": _record("direct", "m")}})
    _write_json(pi_path, [])
    _write_json(
        promotions_path,
        {
            "piCommit": "bde81c84405514c8b0f57c34405c152fb129c0ce",
            "promotions": [
                {
                    "action": "retire",
                    "provider": "direct",
                    "model": "m",
                    "reason": "retired upstream",
                    "evidence": "https://provider.invalid/deprecations",
                }
            ],
        },
    )
    before = catalog_path.read_bytes()

    with pytest.raises(ValueError, match="catalog"):
        sync_builtin_model_catalog.main(
            [
                "--catalog",
                str(catalog_path),
                "--pi-catalog",
                str(pi_path),
                "--promotions",
                str(promotions_path),
            ]
        )

    assert catalog_path.read_bytes() == before
```

- [ ] **Step 2: Run the script tests and confirm the CLI modes are absent**

```bash
.venv/bin/python -m pytest tests/test_sync_builtin_model_catalog.py -q
```

Expected: tests fail because `main()` does not accept `argv` and the Pi flags do not exist.

- [ ] **Step 3: Refactor argument parsing without changing default OpenRouter behavior**

Change the signature to:

```python
def main(argv: list[str] | None = None) -> int:
```

Add arguments:

```python
parser.add_argument("--pi-catalog", type=Path)
parser.add_argument("--check", action="store_true")
parser.add_argument("--promotions", type=Path)
args = parser.parse_args(argv)
```

Validate modes before any network call:

```python
if args.check and args.pi_catalog is None:
    parser.error("--check requires --pi-catalog")
if args.promotions is not None and args.pi_catalog is None:
    parser.error("--promotions requires --pi-catalog")
if args.check and args.promotions is not None:
    parser.error("--check cannot be combined with --promotions")
```

When `--pi-catalog` is absent, execute the existing capacity-fixture/network OpenRouter path unchanged.

- [ ] **Step 4: Add read-only check and atomic promotion apply**

Import the Task 1-2 helpers. Add an atomic writer:

```python
import os
import tempfile


def _write_catalog_atomic(path: Path, payload: dict[str, object]) -> None:
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        separators=(",", ":"),
    ) + "\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        dir=path.parent,
        text=True,
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)
```

Add the Pi branch before OpenRouter fetching:

```python
catalog = validate_catalog(json.loads(args.catalog.read_text(encoding="utf-8")))
if args.pi_catalog is not None:
    reference = validate_catalog(
        json.loads(args.pi_catalog.read_text(encoding="utf-8"))
    )
    if args.check:
        report = [
            catalog_drift_to_dict(item)
            for item in compare_pi_catalogs(catalog, reference)
        ]
        print(json.dumps(report, ensure_ascii=False, sort_keys=True))
        return 0
    if args.promotions is None:
        parser.error("Pi apply mode requires --promotions")
    promotion_set = load_promotion_set(
        json.loads(args.promotions.read_text(encoding="utf-8"))
    )
    updated, changed = apply_pi_promotions(catalog, reference, promotion_set)
    _write_catalog_atomic(args.catalog, updated)
    print(json.dumps({"changed": list(changed)}, sort_keys=True))
    return 0
```

Replace the default mode's direct `write_text()` with `_write_catalog_atomic()` after `apply_openrouter_capabilities()` so malformed or interrupted operations cannot truncate the catalog.

- [ ] **Step 5: Run script and existing catalog tests**

```bash
.venv/bin/python -m pytest \
  tests/test_sync_builtin_model_catalog.py \
  tests/test_catalog_generation.py \
  -q
```

Expected: all tests pass and the existing OpenRouter tests remain unchanged.

- [ ] **Step 6: Commit the maintenance-script modes**

```bash
git add \
  scripts/sync_builtin_model_catalog.py \
  tests/test_sync_builtin_model_catalog.py
git commit -m "feat: add gated Pi catalog maintenance"
```

### Task 4: Retire direct Anthropic Opus 4.1 through the gate

**Files:**

- Modify: `tests/test_catalog_generation.py`
- Create: `scripts/catalog_promotions/2026-08-06-anthropic-opus-4-1-retirement.json`
- Modify: `travis/ai/builtin_models.json`

**Interfaces:**

- Consumes: the promotion apply mode from Task 3 and the current Pi generated catalog path.
- Produces: direct Anthropic catalog without Opus 4.1 while partner-platform entries remain unchanged.

- [ ] **Step 1: Add the failing retirement containment regression**

Add to `tests/test_catalog_generation.py`:

```python
def test_direct_anthropic_omits_retired_opus_4_1_but_partner_routes_remain() -> None:
    root = Path(__file__).resolve().parents[1]
    catalog = json.loads(
        (root / "travis" / "ai" / "builtin_models.json").read_text(
            encoding="utf-8"
        )
    )

    assert "claude-opus-4-1" not in catalog["anthropic"]
    assert "claude-opus-4-1-20250805" not in catalog["anthropic"]
    assert "claude-opus-4-8" in catalog["anthropic"]
    assert "anthropic.claude-opus-4-1-20250805-v1:0" in catalog["amazon-bedrock"]
    assert "us.anthropic.claude-opus-4-1-20250805-v1:0" in catalog["amazon-bedrock"]
    assert "claude-opus-4-1" in catalog["cloudflare-ai-gateway"]
    assert "claude-opus-4-1" in catalog["opencode"]
```

- [ ] **Step 2: Run the regression and confirm only the direct entries fail**

```bash
.venv/bin/python -m pytest \
  tests/test_catalog_generation.py::test_direct_anthropic_omits_retired_opus_4_1_but_partner_routes_remain \
  -q
```

Expected: failure because both direct Anthropic records are still present; partner assertions pass.

- [ ] **Step 3: Create the auditable retirement manifest**

Create `scripts/catalog_promotions/2026-08-06-anthropic-opus-4-1-retirement.json`:

```json
{
  "piCommit": "bde81c84405514c8b0f57c34405c152fb129c0ce",
  "promotions": [
    {
      "action": "retire",
      "provider": "anthropic",
      "model": "claude-opus-4-1",
      "reason": "Direct Claude API alias retired with Claude Opus 4.1",
      "evidence": "https://platform.claude.com/docs/en/about-claude/model-deprecations"
    },
    {
      "action": "retire",
      "provider": "anthropic",
      "model": "claude-opus-4-1-20250805",
      "reason": "Direct Claude API model retired on 2026-08-05",
      "evidence": "https://platform.claude.com/docs/en/about-claude/model-deprecations"
    }
  ]
}
```

- [ ] **Step 4: Generate a fresh Pi catalog outside the repository**

From `pi/packages/ai`, run:

```bash
node scripts/generate-models.ts \
  --strict \
  --json-only \
  --json-output /tmp/travis234-pi-model-catalog-2026-08-06
```

Expected: `/tmp/travis234-pi-model-catalog-2026-08-06/models.json` exists, and the Pi checkout still resolves to manifest commit `bde81c84405514c8b0f57c34405c152fb129c0ce`. If Pi has advanced, stop and update the evidence manifest and audit rather than mislabeling the source.

- [ ] **Step 5: Apply only the approved retirement scope**

Run from the Travis234 root:

```bash
.venv/bin/python scripts/sync_builtin_model_catalog.py \
  --catalog travis/ai/builtin_models.json \
  --pi-catalog /tmp/travis234-pi-model-catalog-2026-08-06/models.json \
  --promotions scripts/catalog_promotions/2026-08-06-anthropic-opus-4-1-retirement.json
```

Expected structured output:

```json
{"changed": ["retire:anthropic/claude-opus-4-1", "retire:anthropic/claude-opus-4-1-20250805"]}
```

- [ ] **Step 6: Inspect the generated catalog diff before testing**

```bash
git diff --stat -- travis/ai/builtin_models.json
.venv/bin/python - <<'PY'
import json
from pathlib import Path

before = json.loads(
    __import__("subprocess").check_output(
        ["git", "show", "HEAD:travis/ai/builtin_models.json"],
        text=True,
    )
)
after = json.loads(Path("travis/ai/builtin_models.json").read_text(encoding="utf-8"))
changes = []
for provider in sorted(set(before) | set(after)):
    before_ids = set(before.get(provider, {}))
    after_ids = set(after.get(provider, {}))
    for model_id in sorted(before_ids - after_ids):
        changes.append(("removed", provider, model_id))
    for model_id in sorted(after_ids - before_ids):
        changes.append(("added", provider, model_id))
    for model_id in sorted(before_ids & after_ids):
        if before[provider][model_id] != after[provider][model_id]:
            changes.append(("updated", provider, model_id))
print(changes)
PY
```

Expected:

```text
[
  ('removed', 'anthropic', 'claude-opus-4-1'),
  ('removed', 'anthropic', 'claude-opus-4-1-20250805')
]
```

No other record may change.

- [ ] **Step 7: Run the retirement and full catalog tests**

```bash
.venv/bin/python -m pytest \
  tests/test_catalog_generation.py \
  tests/test_ai_models.py \
  tests/test_subscription_provider_wire_compatibility.py \
  -q
```

Expected: all tests pass; direct retired IDs are absent and partner routes remain.

- [ ] **Step 8: Commit the contained retirement**

```bash
git add \
  tests/test_catalog_generation.py \
  scripts/catalog_promotions/2026-08-06-anthropic-opus-4-1-retirement.json \
  travis/ai/builtin_models.json
git commit -m "fix: retire direct Anthropic Opus 4.1 models"
```

### Task 5: Qualify catalog tooling and packaged artifacts

**Files:**

- Verify only; modify no runtime file unless a failing regression identifies a defect within this plan's approved scope.
- Optionally modify: `docs/verification/full-suite.md` to record exact evidence.

**Interfaces:**

- Consumes: Tasks 1-4.
- Produces: evidence that the shipped catalog loads, the maintenance tool is read-only in check mode, and packaging/container behavior remains intact.

- [ ] **Step 1: Run a real read-only Pi drift report outside tracked paths**

```bash
.venv/bin/python scripts/sync_builtin_model_catalog.py \
  --catalog travis/ai/builtin_models.json \
  --pi-catalog /tmp/travis234-pi-model-catalog-2026-08-06/models.json \
  --check \
  > /tmp/travis234-provider-catalog-drift.json
git diff --exit-code -- travis/ai/builtin_models.json
```

Expected: valid JSON report is written under `/tmp`, and the packaged catalog has no diff from the committed retirement.

- [ ] **Step 2: Run focused catalog/provider suites**

```bash
.venv/bin/python -m pytest \
  tests/test_catalog_generation.py \
  tests/test_sync_builtin_model_catalog.py \
  tests/test_ai_models.py \
  tests/test_ai_provider_capabilities.py \
  tests/ai/providers \
  tests/test_reference_runtime_contract.py \
  -q
```

Expected: all focused tests pass.

- [ ] **Step 3: Run the complete Python suite**

```bash
.venv/bin/python -m pytest -q
```

Expected: all Python tests pass with zero failures.

- [ ] **Step 4: Run npm launcher tests and dry-run packaging**

```bash
npm run test:launcher
npm run pack:launcher
```

Expected: launcher tests and npm pack dry run pass.

- [ ] **Step 5: Build and check Python distributions**

```bash
uv build --clear
uv run twine check dist/*
```

Expected: the current-version wheel and source distribution build and pass metadata checks; the wheel contains the corrected `travis/ai/builtin_models.json`.

- [ ] **Step 6: Smoke the installed wheel's catalog**

Create an isolated environment outside the repository and run:

```bash
PARITY_VENV="$(mktemp -d)/venv"
python3.13 -m venv "$PARITY_VENV"
"$PARITY_VENV/bin/pip" install dist/travis234-2.4.0-py3-none-any.whl
"$PARITY_VENV/bin/python" - <<'PY'
from travis.ai.builtin_models import load_builtin_models

models = {(model.provider, model.id) for model in load_builtin_models()}
assert ("anthropic", "claude-opus-4-1") not in models
assert ("anthropic", "claude-opus-4-1-20250805") not in models
assert ("cloudflare-ai-gateway", "claude-opus-4-1") in models
assert ("opencode", "claude-opus-4-1") in models
print("installed catalog smoke passed")
PY
```

Expected: the isolated-wheel smoke prints `installed catalog smoke passed`. Do not reuse or delete any broad existing directory.

- [ ] **Step 7: Build and smoke-test the release container**

```bash
docker build --no-cache -f Dockerfile.release -t travis234:provider-catalog-pi-parity .
.venv/bin/python evals/container_smoke.py --image travis234:provider-catalog-pi-parity
```

Expected: the image and unprivileged installed-container smoke pass without credentials.

- [ ] **Step 8: Verify protected paths and user-owned files remain untouched**

Run against the base recorded during execution preflight:

```bash
CATALOG_PLAN_BASE="$(cat /tmp/travis234-provider-catalog-plan-base)"
git diff --check "$CATALOG_PLAN_BASE"..HEAD
git diff --exit-code "$CATALOG_PLAN_BASE"..HEAD -- \
  travis/agent \
  travis/compaction \
  travis/coding_agent/session_store.py
git status --short
```

Expected: no whitespace errors, no protected-path changes, and only the two pre-existing user-owned untracked documents remain.

- [ ] **Step 9: Record verification only when requested or required for release evidence**

If editing `docs/verification/full-suite.md`, include exact test counts, package names, installed-wheel result, container outcome, the audited Pi commit, and the `/tmp` drift-report location. Do not claim that all 220 Pi-only records were promoted.

- [ ] **Step 10: Commit verification documentation if it changed**

```bash
git add docs/verification/full-suite.md
git commit -m "docs: record provider catalog parity verification"
```

Skip this commit if the file is unchanged. Do not push, tag, publish, or alter external release state without separate user authorization.

## Stop conditions

Stop and return to design review if implementation appears to require:

- importing all Pi-only records or providers;
- adding a runtime network fetch, cache, or `refresh_models` callback;
- storing catalog state anywhere under a new user path;
- promoting a model with an unsupported API or compatibility key;
- changing OpenRouter's safe output/context behavior;
- removing a partner-platform Anthropic record without provider-specific lifecycle evidence;
- changing `Model`, the provider registry, agent loop, compaction, session persistence, iteration budgeting, or bounded parallel execution; or
- printing credentials, environment contents, or arbitrary model headers.
