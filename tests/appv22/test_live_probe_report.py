from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "appV2.2"))

from scripts.live_appv22_complex_vague_file_management_probe import (
    EXPECTED_HELD_SOURCES,
    EXPECTED_SOURCES_ABSENT_AFTER_MOVES,
    build_report,
    create_provider,
    default_report_path,
    seed_repo,
)
from scripts.live_appv22_dual_compaction_rehydration_probe import build_report as build_dual_compaction_report


class _DualCompactionProvider:
    provider_id = "test-provider"
    raw_decisions = []

    def __init__(self):
        self.prompts = [
            {"messages": [], "world": {"world_refs": {"repo": {}}}},
            {
                "messages": [
                    {
                        "name": "context_summary",
                        "summary": {"evidence_refs": ["tool:file_management.repo_snapshot:1"]},
                    }
                ],
                "world": {},
            },
        ]
        self.decisions = [
            {"kind": "tool_call", "payload": {"tool_id": "file_management.repo_snapshot"}, "evidence_refs": []},
            {"kind": "respond", "payload": {}, "evidence_refs": ["tool:file_management.repo_snapshot:1"]},
        ]


def test_dual_compaction_report_proof_separates_initial_observation_from_reobserve(tmp_path):
    report = build_dual_compaction_report(
        repo=tmp_path,
        result={"status": "completed", "events": []},
        provider=_DualCompactionProvider(),
        prompt="p",
    )

    assert report["proof"]["initial_observation_attempted"] is True
    assert report["proof"]["no_reobserve_after_summary_evidence"] is True
    assert "rehydration_attempted" not in report["proof"]


def test_dual_compaction_report_proof_flags_reobserve_after_summary_evidence(tmp_path):
    provider = _DualCompactionProvider()
    provider.prompts.append(
        {
            "messages": [
                {
                    "name": "context_summary",
                    "summary": {"evidence_refs": ["tool:file_management.repo_snapshot:1"]},
                }
            ],
            "world": {},
        }
    )
    provider.decisions.append(
        {
            "kind": "tool_call",
            "payload": {"tool_id": "file_management.repo_snapshot"},
            "evidence_refs": [],
        }
    )

    report = build_dual_compaction_report(
        repo=tmp_path,
        result={"status": "completed", "events": []},
        provider=provider,
        prompt="p",
    )

    assert report["proof"]["no_reobserve_after_summary_evidence"] is False


def test_dual_compaction_report_proof_flags_read_file_after_summary_evidence(tmp_path):
    provider = _DualCompactionProvider()
    provider.prompts.append(
        {
            "messages": [
                {
                    "name": "context_summary",
                    "summary": {"evidence_refs": ["tool:file_management.repo_snapshot:1"]},
                }
            ],
            "world": {},
        }
    )
    provider.decisions.append(
        {
            "kind": "tool_call",
            "payload": {"tool_id": "file_management.read_file"},
            "evidence_refs": [],
        }
    )

    report = build_dual_compaction_report(
        repo=tmp_path,
        result={"status": "completed", "events": []},
        provider=provider,
        prompt="p",
    )

    assert report["proof"]["no_reobserve_after_summary_evidence"] is False


def test_dual_compaction_report_ignores_malformed_summary_without_crashing(tmp_path):
    provider = _DualCompactionProvider()
    provider.prompts = [
        {
            "messages": [
                {
                    "name": "context_summary",
                    "summary": "malformed summary",
                }
            ],
            "world": {},
        }
    ]
    provider.decisions = [
        {"kind": "respond", "payload": {}, "evidence_refs": []},
    ]

    report = build_dual_compaction_report(
        repo=tmp_path,
        result={"status": "completed", "events": []},
        provider=provider,
        prompt="p",
    )

    assert report["prompt_matrix"][0]["context_summary_visible"] is True
    assert report["prompt_matrix"][0]["summary_evidence_ref_count"] == 0


def test_probe_report_contains_full_matrix(tmp_path):
    (tmp_path / "README.md").write_text("# probe\n", encoding="utf-8")
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "app.py").write_text("print('protected runtime file')\n", encoding="utf-8")
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_probe.py").write_text("def test_probe():\n    assert True\n", encoding="utf-8")
    (tmp_path / "assets").mkdir()
    (tmp_path / "assets" / "logo.svg").write_text("<svg></svg>\n", encoding="utf-8")
    (tmp_path / "secrets").mkdir()
    (tmp_path / "secrets" / "prod.env").write_text("TOKEN=protected\n", encoding="utf-8")
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "existing.md").write_text("protected docs prefix\n", encoding="utf-8")
    (tmp_path / "docs" / "standup.md").write_text("moved team note\n", encoding="utf-8")
    (tmp_path / "docs" / "spec.md").write_text("moved first spec\n", encoding="utf-8")
    (tmp_path / "notes" / "team").mkdir(parents=True)
    (tmp_path / "notes" / "team" / "keep_decisions.md").write_text("keep\n", encoding="utf-8")
    (tmp_path / "projects" / "beta").mkdir(parents=True)
    (tmp_path / "projects" / "beta" / "spec.md").write_text("held collision\n", encoding="utf-8")
    (tmp_path / "tmp" / "session").mkdir(parents=True)
    (tmp_path / "tmp" / "session" / "run.log").write_text("held collision\n", encoding="utf-8")
    (tmp_path / "tmp" / "session" / "keep_trace.json").write_text('{"keep": true}\n', encoding="utf-8")
    (tmp_path / "tmp" / "other").mkdir(parents=True)
    (tmp_path / "artifacts" / "logs").mkdir(parents=True)
    (tmp_path / "artifacts" / "logs" / "run.log").write_text("moved run log\n", encoding="utf-8")
    (tmp_path / "docs" / "workspace_manifest.json").write_text(
        '{"moves": [{"source": "notes/team/standup.md", "destination": "docs/standup.md"}],'
        ' "held": [{"source": "projects/beta/spec.md", "reason": "destination collision"},'
        ' {"source": "tmp/session/run.log", "reason": "destination collision"}],'
        ' "collisions": [{"source": "tmp/session/run.log", "destination": "artifacts/logs/run.log"}]}',
        encoding="utf-8",
    )
    result = {
        "status": "completed",
        "events": [
            {"event_type": "DecisionProposed", "payload": {"kind": "tool_call"}},
            {
                "event_type": "ToolCallCompleted",
                "payload": {"tool_id": "file_management.repo_snapshot"},
            },
            {
                "event_type": "MutationApplied",
                "payload": {"receipt_id": "mut_workspace_cleanup"},
            },
            {
                "event_type": "VerificationRecorded",
                "payload": {"verification_id": "verify_1"},
            },
        ],
    }

    report = build_report(
        repo=tmp_path,
        result=result,
        provider=None,
        prompt="Can you clean this mess up safely and keep a record?",
    )

    assert report["status"] == "completed"
    assert report["user_prompt"] == "Can you clean this mess up safely and keep a record?"
    assert report["provider"] is None
    assert report["totals"]["events"] == 4
    assert report["totals"]["decisions"] == 1
    assert report["totals"]["tool_calls"] == 1
    assert report["totals"]["mutation_receipts"] == 1
    assert report["totals"]["verification_receipts"] == 1
    assert report["costs"] == {
        "available": False,
        "source": None,
        "model_calls": None,
        "total_tokens": None,
        "cost": None,
    }
    assert report["event_order"] == [
        "DecisionProposed",
        "ToolCallCompleted",
        "MutationApplied",
        "VerificationRecorded",
    ]
    assert report["file_management"]["protected_paths_preserved"]["src/app.py"] is True
    assert report["file_management"]["protected_paths_preserved"]["secrets/prod.env"] is True
    assert report["file_management"]["expected_destinations_present"]["docs/standup.md"] is True
    assert report["file_management"]["expected_destinations_present"]["docs/spec.md"] is True
    assert report["file_management"]["expected_destinations_present"]["artifacts/logs/run.log"] is True
    assert report["file_management"]["expected_sources_absent_after_moves"]["notes/team/standup.md"] is True
    assert report["file_management"]["expected_sources_absent_after_moves"]["tmp/other/run.log"] is True
    assert report["file_management"]["expected_held_sources_present"]["tmp/session/run.log"] is True
    assert "tmp/other/run.log" in EXPECTED_SOURCES_ABSENT_AFTER_MOVES
    assert "tmp/session/run.log" in EXPECTED_HELD_SOURCES
    assert report["file_management"]["manifest"]["exists"] is True
    assert report["file_management"]["manifest"]["path"] == "docs/workspace_manifest.json"
    assert report["file_management"]["manifest"]["shape"]["moves"] is True
    assert report["file_management"]["manifest"]["shape"]["held"] is True
    assert report["file_management"]["manifest"]["shape"]["collisions"] is True
    assert report["file_management"]["held_or_collision_info"]["available"] is True
    assert report["file_management"]["held_or_collision_info"]["missing_sources"] == []
    assert report["file_management"]["held_or_collision_info"]["covered_sources"] == EXPECTED_HELD_SOURCES
    for source in EXPECTED_HELD_SOURCES:
        assert report["file_management"]["held_or_collision_info"]["expected_sources"][source]["covered"] is True
    assert report["file_management"]["violations"] == []
    assert "docs/workspace_manifest.json" in report["files"]


def test_probe_report_flags_protected_collision_and_missing_move_expectations(tmp_path):
    (tmp_path / "README.md").write_text("# probe\n", encoding="utf-8")
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "workspace_manifest.json").write_text('{"moves": []}', encoding="utf-8")

    report = build_report(repo=tmp_path, result={"status": "completed", "events": []}, provider=None, prompt="p")

    assert report["file_management"]["protected_paths_preserved"]["src/app.py"] is False
    assert report["file_management"]["expected_destinations_present"]["docs/standup.md"] is False
    assert report["file_management"]["manifest"]["shape"]["held"] is False
    assert report["file_management"]["held_or_collision_info"]["available"] is False
    assert "protected path missing: src/app.py" in report["file_management"]["violations"]
    assert "expected destination missing: docs/standup.md" in report["file_management"]["violations"]
    assert "manifest missing key: held" in report["file_management"]["violations"]
    assert "held/collision record missing" in report["file_management"]["violations"]


def test_probe_report_flags_empty_held_collision_records(tmp_path):
    (tmp_path / "README.md").write_text("# probe\n", encoding="utf-8")
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "app.py").write_text("print('protected runtime file')\n", encoding="utf-8")
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_probe.py").write_text("def test_probe():\n    assert True\n", encoding="utf-8")
    (tmp_path / "assets").mkdir()
    (tmp_path / "assets" / "logo.svg").write_text("<svg></svg>\n", encoding="utf-8")
    (tmp_path / "secrets").mkdir()
    (tmp_path / "secrets" / "prod.env").write_text("TOKEN=protected\n", encoding="utf-8")
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "existing.md").write_text("protected docs prefix\n", encoding="utf-8")
    (tmp_path / "docs" / "standup.md").write_text("moved team note\n", encoding="utf-8")
    (tmp_path / "docs" / "spec.md").write_text("moved first spec\n", encoding="utf-8")
    (tmp_path / "notes" / "team").mkdir(parents=True)
    (tmp_path / "notes" / "team" / "keep_decisions.md").write_text("keep\n", encoding="utf-8")
    (tmp_path / "projects" / "beta").mkdir(parents=True)
    (tmp_path / "projects" / "beta" / "spec.md").write_text("held collision\n", encoding="utf-8")
    (tmp_path / "tmp" / "session").mkdir(parents=True)
    (tmp_path / "tmp" / "session" / "run.log").write_text("held collision\n", encoding="utf-8")
    (tmp_path / "tmp" / "session" / "keep_trace.json").write_text('{"keep": true}\n', encoding="utf-8")
    (tmp_path / "artifacts" / "logs").mkdir(parents=True)
    (tmp_path / "artifacts" / "logs" / "run.log").write_text("moved run log\n", encoding="utf-8")
    (tmp_path / "docs" / "workspace_manifest.json").write_text(
        '{"moves": [], "held": [], "collisions": []}',
        encoding="utf-8",
    )

    report = build_report(repo=tmp_path, result={"status": "completed", "events": []}, provider=None, prompt="p")

    assert report["file_management"]["held_or_collision_info"]["available"] is False
    assert report["file_management"]["held_or_collision_info"]["aggregate_available"] is False
    assert report["file_management"]["held_or_collision_info"]["manifest_entries"] == 0
    assert report["file_management"]["held_or_collision_info"]["event_mentions"] == 0
    assert report["file_management"]["held_or_collision_info"]["missing_sources"] == EXPECTED_HELD_SOURCES
    assert "held/collision record missing" in report["file_management"]["violations"]


def test_probe_report_requires_held_collision_records_for_each_expected_source(tmp_path):
    (tmp_path / "README.md").write_text("# probe\n", encoding="utf-8")
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "app.py").write_text("print('protected runtime file')\n", encoding="utf-8")
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_probe.py").write_text("def test_probe():\n    assert True\n", encoding="utf-8")
    (tmp_path / "assets").mkdir()
    (tmp_path / "assets" / "logo.svg").write_text("<svg></svg>\n", encoding="utf-8")
    (tmp_path / "secrets").mkdir()
    (tmp_path / "secrets" / "prod.env").write_text("TOKEN=protected\n", encoding="utf-8")
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "existing.md").write_text("protected docs prefix\n", encoding="utf-8")
    (tmp_path / "docs" / "standup.md").write_text("moved team note\n", encoding="utf-8")
    (tmp_path / "docs" / "spec.md").write_text("moved first spec\n", encoding="utf-8")
    (tmp_path / "notes" / "team").mkdir(parents=True)
    (tmp_path / "notes" / "team" / "keep_decisions.md").write_text("keep\n", encoding="utf-8")
    (tmp_path / "projects" / "beta").mkdir(parents=True)
    (tmp_path / "projects" / "beta" / "spec.md").write_text("held collision\n", encoding="utf-8")
    (tmp_path / "tmp" / "session").mkdir(parents=True)
    (tmp_path / "tmp" / "session" / "run.log").write_text("held collision\n", encoding="utf-8")
    (tmp_path / "tmp" / "session" / "keep_trace.json").write_text('{"keep": true}\n', encoding="utf-8")
    (tmp_path / "artifacts" / "logs").mkdir(parents=True)
    (tmp_path / "artifacts" / "logs" / "run.log").write_text("moved run log\n", encoding="utf-8")
    (tmp_path / "docs" / "workspace_manifest.json").write_text(
        '{"moves": [],'
        ' "held": [{"source": "unrelated/held.md", "reason": "destination collision"}],'
        ' "collisions": [{"source": "unrelated/collision.log", "destination": "artifacts/logs/run.log"}]}',
        encoding="utf-8",
    )
    result = {
        "status": "completed",
        "events": [
            {
                "event_type": "ToolCallCompleted",
                "payload": {"held": [{"source": "unrelated/event.md", "reason": "collision"}]},
            },
        ],
    }

    report = build_report(repo=tmp_path, result=result, provider=None, prompt="p")

    info = report["file_management"]["held_or_collision_info"]
    assert info["available"] is False
    assert info["missing_sources"] == EXPECTED_HELD_SOURCES
    for source in EXPECTED_HELD_SOURCES:
        assert info["expected_sources"][source]["covered"] is False
        assert f"held/collision record missing for expected source: {source}" in report["file_management"]["violations"]


def test_seeded_log_comments_match_expected_matrix(tmp_path):
    repo = seed_repo(tmp_path / "probe")

    assert (repo / "tmp" / "session" / "run.log").read_text(encoding="utf-8") == (
        "Hold this log because artifacts/logs/run.log is claimed.\n"
    )
    assert (repo / "tmp" / "other" / "run.log").read_text(encoding="utf-8") == (
        "Move this run log into artifacts/logs.\n"
    )
    assert "tmp/session/run.log" in EXPECTED_HELD_SOURCES
    assert "tmp/other/run.log" in EXPECTED_SOURCES_ABSENT_AFTER_MOVES


class _NestedUsageProvider:
    provider_id = "outer"

    def __init__(self):
        self.delegate = _UsageDelegate()


class _UsageDelegate:
    def __init__(self):
        self.client = _UsageClient()


class _UsageClient:
    def usage_snapshot(self):
        return {"model_calls": 0, "total_tokens": 0, "cost": 0.0}


def test_cost_extraction_marks_nested_zero_usage_available(tmp_path):
    report = build_report(repo=tmp_path, result={"status": "completed", "events": []}, provider=_NestedUsageProvider(), prompt="p")

    assert report["costs"] == {
        "available": True,
        "source": "delegate.client.usage_snapshot",
        "model_calls": 0,
        "total_tokens": 0,
        "cost": 0.0,
    }


def test_default_report_path_is_provider_specific_and_output_arg_can_override(tmp_path):
    assert default_report_path("deterministic").name == "live-appv22-complex-vague-file-management-probe.deterministic.json"
    assert default_report_path("appv2-env").name == "live-appv22-complex-vague-file-management-probe.appv2-env.json"
    assert default_report_path("deterministic").name != "live-appv22-complex-vague-file-management-probe.json"
    assert default_report_path("deterministic", output=tmp_path / "custom.json") == tmp_path / "custom.json"


def test_appv2_env_probe_passes_file_management_tool_name_map(monkeypatch):
    captured = {}
    provider = object()

    def fake_create_appv22_provider_from_appv2_env(*, dotenv_path, tool_name_map=None):
        captured["dotenv_path"] = dotenv_path
        captured["tool_name_map"] = tool_name_map
        return provider

    monkeypatch.setattr(
        "scripts.live_appv22_complex_vague_file_management_probe.create_appv22_provider_from_appv2_env",
        fake_create_appv22_provider_from_appv2_env,
    )

    assert create_provider("appv2-env", dotenv_path=".env") is provider
    assert captured["dotenv_path"] == ".env"
    assert captured["tool_name_map"]["repo_snapshot"] == "file_management.repo_snapshot"
    assert captured["tool_name_map"]["read_file"] == "file_management.read_file"
