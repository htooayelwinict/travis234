from __future__ import annotations

from pathlib import Path

import yaml


def _workflow() -> dict:
    path = Path(__file__).parents[2] / ".github" / "workflows" / "appv231-release-image.yml"
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def test_release_push_depends_on_tests_and_smoke() -> None:
    workflow = _workflow()
    jobs = workflow["jobs"]
    push = jobs["build-and-push"]
    assert set(push["needs"]) == {"test", "image-smoke"}
    build = push["steps"][-1]
    assert build["with"]["push"] is True
    assert build["with"]["no-cache"] is True


def test_release_tests_use_deterministic_no_color_baseline() -> None:
    test_job = _workflow()["jobs"]["test"]
    assert test_job["env"]["NO_COLOR"] == "1"


def test_release_smoke_build_is_no_cache_and_never_pushes() -> None:
    workflow = _workflow()
    smoke_build = next(
        step for step in workflow["jobs"]["image-smoke"]["steps"]
        if step.get("uses") == "docker/build-push-action@v6"
    )
    assert smoke_build["with"]["load"] is True
    assert smoke_build["with"]["push"] is False
    assert smoke_build["with"]["no-cache"] is True


def test_registry_login_exists_only_in_gated_push_job() -> None:
    workflow = _workflow()
    login_jobs = {
        job_name
        for job_name, job in workflow["jobs"].items()
        if any(step.get("uses") == "docker/login-action@v3" for step in job.get("steps", []))
    }
    assert login_jobs == {"build-and-push"}
    for job_name in ("test", "image-smoke"):
        assert not any(
            step.get("with", {}).get("push") is True
            for step in workflow["jobs"][job_name].get("steps", [])
        )


def test_container_smoke_prepares_linux_writable_workspace(tmp_path) -> None:
    from evals.container_smoke import prepare_npm_workspace

    prepare_npm_workspace(tmp_path)

    assert tmp_path.stat().st_mode & 0o777 == 0o777
    package_json = tmp_path / "package.json"
    assert package_json.is_file()
    assert package_json.stat().st_mode & 0o666 == 0o666
