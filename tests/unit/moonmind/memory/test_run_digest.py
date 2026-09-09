"""Unit tests for the vector-free Plane B run digest builder (#4109)."""

from __future__ import annotations

from types import SimpleNamespace

from moonmind.memory.run_digest import (
    RUN_DIGEST_ARTIFACT_SCHEMA_VERSION,
    RUN_DIGEST_RECORD_KIND,
    RUN_DIGEST_TRUST_CLASS,
    RunDigest,
    build_run_digest,
    run_digest_artifact_payload,
)


def _record(**overrides: object) -> SimpleNamespace:
    defaults = {
        "workflow_id": "mm:run:123",
        "run_id": "temporal-run-1",
        "namespace": "default",
        "workflow_type": "MoonMind.UserWorkflow",
        "state": "completed",
        "close_status": "completed",
        "title": "Implement MM-762 run digests",
        "memo": {
            "summary": "Workflow completed successfully",
            "summary_artifact_ref": "art_summary",
        },
        "parameters": {
            "task": {"git": {"repository": "MoonLadderStudios/MoonMind"}},
            "publishMode": "pr",
        },
        "search_attributes": {"mm_agent_run_id": "agent-run-1"},
        "artifact_refs": ["art_summary", "art_patch"],
        "input_ref": "art_input",
        "plan_ref": "art_plan",
        "manifest_ref": "art_manifest",
    }
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def test_build_run_digest_uses_terminal_execution_evidence_without_raw_logs() -> None:
    digest = build_run_digest(_record())

    assert digest.record_kind == RUN_DIGEST_RECORD_KIND
    assert digest.intent == "Implement MM-762 run digests"
    assert digest.outcome == "Workflow completed successfully"
    assert digest.repo == "MoonLadderStudios/MoonMind"
    assert digest.security_scope == "repo:MoonLadderStudios/MoonMind"
    assert digest.evidence.workflow_id == "mm:run:123"
    assert digest.evidence.agent_run_id == "agent-run-1"
    assert digest.evidence.summary_artifact_ref == "art_summary"
    assert digest.evidence.artifact_refs == ("art_summary", "art_patch")
    assert "raw log" not in digest.outcome.lower()


def test_build_run_digest_projects_structured_repository_target() -> None:
    parameters = {
        "repository": {
            "provider": "git",
            "connectionRef": "repository-connection:git-default",
            "repository": {"name": "MoonLadderStudios/MoonMind"},
            "branch": {"name": "feature/mm-1219"},
        },
        "publishMode": "pr",
    }

    digest = build_run_digest(_record(parameters=parameters))

    assert digest.repo == "MoonLadderStudios/MoonMind"
    assert digest.security_scope == "repo:MoonLadderStudios/MoonMind"


def test_build_run_digest_is_deterministic_for_restart_recovery() -> None:
    """The same authoritative record rebuilds the same digest after restart."""

    first = build_run_digest(_record())
    second = build_run_digest(_record())

    assert first.model_dump() == second.model_dump()


def test_run_digest_artifact_payload_preserves_identity_and_provenance() -> None:
    digest = build_run_digest(_record())

    payload = run_digest_artifact_payload(digest)

    assert payload["schemaVersion"] == RUN_DIGEST_ARTIFACT_SCHEMA_VERSION
    assert payload["recordKind"] == RUN_DIGEST_RECORD_KIND
    assert payload["source"] == "run_digest:mm:run:123"
    assert payload["trustClass"] == RUN_DIGEST_TRUST_CLASS
    inner = payload["digest"]
    assert inner["workflowId"] == "mm:run:123"
    assert inner["runId"] == "temporal-run-1"
    assert inner["namespaceId"] == "default"
    assert inner["evidence"]["agentRunId"] == "agent-run-1"
    assert inner["evidence"]["artifactRefs"] == ["art_summary", "art_patch"]
    assert "Workflow completed successfully" in inner["outcome"]


def test_run_digest_artifact_payload_round_trips_through_model() -> None:
    """Historical digest artifacts remain readable without a vector database."""

    payload = run_digest_artifact_payload(build_run_digest(_record()))

    restored = RunDigest.model_validate(payload["digest"])

    assert restored.workflow_id == "mm:run:123"
    assert restored.run_id == "temporal-run-1"
    assert restored.evidence.summary_artifact_ref == "art_summary"
