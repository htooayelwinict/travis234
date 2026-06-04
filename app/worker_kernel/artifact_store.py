"""Artifact-store helpers for worker-kernel execution."""

from __future__ import annotations

from app.schemas import ArtifactPayload


def build_artifact_store(artifacts: list[ArtifactPayload]) -> dict[str, ArtifactPayload]:
    store: dict[str, ArtifactPayload] = {}
    for artifact in artifacts:
        normalized = ArtifactPayload.model_validate(artifact)
        store[normalized.id] = normalized
    return store


def promotable_completed_artifacts(artifacts: list[ArtifactPayload]) -> list[ArtifactPayload]:
    return [artifact for artifact in artifacts if artifact.kind != "kernel_memory"]
