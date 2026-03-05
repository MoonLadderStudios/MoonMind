"""Unit tests for the skill/plan contract runtime modules."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime

import pytest

from moonmind.workflows.skills.artifact_store import (
    ArtifactStoreError,
    InMemoryArtifactStore,
)
from moonmind.workflows.skills.plan_interpreter import create_validated_interpreter
from moonmind.workflows.skills.plan_validation import PlanValidationError, validate_plan_payload
from moonmind.workflows.skills.skill_dispatcher import (
    SkillActivityDispatcher,
    SkillDispatchError,
    execute_skill_activity,
    plan_validate_activity,
)
from moonmind.workflows.skills.skill_plan_contracts import SkillResult, parse_plan_definition
from moonmind.workflows.skills.skill_registry import (
    create_registry_snapshot,
    parse_skill_registry,
)


def _registry_payload() -> dict:
    return {
        "skills": [
            {
                "name": "repo.run_tests",
                "version": "1.0.0",
                "description": "Run tests and publish report artifact ref",
                "inputs": {
                    "schema": {
                        "type": "object",
                        "required": ["repo_ref"],
                        "properties": {"repo_ref": {"type": "string"}},
                    }
                },
                "outputs": {
                    "schema": {
                        "type": "object",
                        "required": ["test_report_artifact"],
                        "properties": {
                            "test_report_artifact": {"type": "string"},
                        },
                    }
                },
                "executor": {
                    "activity_type": "mm.skill.execute",
                    "selector": {"mode": "by_capability"},
                },
                "requirements": {"capabilities": ["sandbox"]},
                "policies": {
                    "timeouts": {
                        "start_to_close_seconds": 30,
                        "schedule_to_close_seconds": 120,
                    },
                    "retries": {
                        "max_attempts": 2,
                        "backoff": "exponential",
                        "non_retryable_error_codes": ["INVALID_INPUT"],
                    },
                },
                "security": {"allowed_roles": ["user"]},
            },
            {
                "name": "repo.apply_patch",
                "version": "2.1.0",
                "description": "Apply patch from artifact",
                "inputs": {
                    "schema": {
                        "type": "object",
                        "required": ["repo_ref", "patch_artifact"],
                        "properties": {
                            "repo_ref": {"type": "string"},
                            "patch_artifact": {"type": "string"},
                        },
                    }
                },
                "outputs": {
                    "schema": {
                        "type": "object",
                        "required": ["files_changed"],
                        "properties": {
                            "files_changed": {"type": "integer"},
                        },
                    }
                },
                "executor": {
                    "activity_type": "sandbox.exec",
                    "selector": {"mode": "by_capability"},
                },
                "requirements": {"capabilities": ["sandbox"]},
                "policies": {
                    "timeouts": {
                        "start_to_close_seconds": 60,
                        "schedule_to_close_seconds": 300,
                    },
                    "retries": {
                        "max_attempts": 1,
                        "backoff": "exponential",
                        "non_retryable_error_codes": ["INVALID_INPUT"],
                    },
                },
                "security": {"allowed_roles": ["user"]},
            },
        ]
    }


def _plan_payload(*, snapshot_digest: str, snapshot_ref: str, failure_mode: str = "FAIL_FAST") -> dict:
    created_at = datetime.now(tz=UTC).replace(microsecond=0).isoformat().replace(
        "+00:00", "Z"
    )
    return {
        "plan_version": "1.0",
        "metadata": {
            "title": "Fix tests",
            "created_at": created_at,
            "registry_snapshot": {
                "digest": snapshot_digest,
                "artifact_ref": snapshot_ref,
            },
        },
        "policy": {"failure_mode": failure_mode, "max_concurrency": 2},
        "nodes": [
            {
                "id": "n1",
                "skill": {"name": "repo.run_tests", "version": "1.0.0"},
                "inputs": {"repo_ref": "git:org/repo#branch"},
            },
            {
                "id": "n2",
                "skill": {"name": "repo.apply_patch", "version": "2.1.0"},
                "inputs": {
                    "repo_ref": "git:org/repo#branch",
                    "patch_artifact": {
                        "ref": {
                            "node": "n1",
                            "json_pointer": "/outputs/test_report_artifact",
                        }
                    },
                },
            },
        ],
        "edges": [{"from": "n1", "to": "n2"}],
    }


def _snapshot(store: InMemoryArtifactStore):
    skills = parse_skill_registry(_registry_payload())
    return create_registry_snapshot(skills=skills, artifact_store=store)


def test_artifact_store_is_content_addressed_and_immutable():
    store = InMemoryArtifactStore()
    first = store.put_bytes(b"abc", content_type="text/plain")
    second = store.put_bytes(b"abc", content_type="text/plain")

    assert first.artifact_ref == second.artifact_ref
    assert store.get_bytes(first.artifact_ref) == b"abc"

    with pytest.raises(ArtifactStoreError, match="Artifact not found"):
        store.get_bytes("art:sha256:does-not-exist")


def test_validate_plan_payload_accepts_dag_and_refs():
    store = InMemoryArtifactStore()
    snapshot = _snapshot(store)
    plan_payload = _plan_payload(
        snapshot_digest=snapshot.digest,
        snapshot_ref=snapshot.artifact_ref,
    )

    validated = validate_plan_payload(payload=plan_payload, registry_snapshot=snapshot)

    assert validated.topological_order == ("n1", "n2")


def test_validate_plan_payload_rejects_invalid_reference_pointer():
    store = InMemoryArtifactStore()
    snapshot = _snapshot(store)
    plan_payload = _plan_payload(
        snapshot_digest=snapshot.digest,
        snapshot_ref=snapshot.artifact_ref,
    )
    plan_payload["nodes"][1]["inputs"]["patch_artifact"]["ref"][
        "json_pointer"
    ] = "/outputs/missing_key"

    with pytest.raises(PlanValidationError, match="invalid output path"):
        validate_plan_payload(payload=plan_payload, registry_snapshot=snapshot)


def test_validate_plan_payload_rejects_cyclic_graph():
    store = InMemoryArtifactStore()
    snapshot = _snapshot(store)
    plan_payload = _plan_payload(
        snapshot_digest=snapshot.digest,
        snapshot_ref=snapshot.artifact_ref,
    )
    plan_payload["edges"].append({"from": "n2", "to": "n1"})

    with pytest.raises(PlanValidationError, match="acyclic"):
        validate_plan_payload(payload=plan_payload, registry_snapshot=snapshot)


def test_plan_validate_activity_persists_validated_plan_artifact():
    store = InMemoryArtifactStore()
    snapshot = _snapshot(store)
    plan_payload = _plan_payload(
        snapshot_digest=snapshot.digest,
        snapshot_ref=snapshot.artifact_ref,
    )
    plan_artifact = store.put_json(plan_payload, metadata={"name": "plan.json"})

    response = plan_validate_activity(
        plan_artifact_ref=plan_artifact.artifact_ref,
        registry_snapshot_ref=snapshot.artifact_ref,
        artifact_store=store,
    )

    assert "validated_plan_ref" in response
    saved = store.get_json(response["validated_plan_ref"])
    assert saved["plan_version"] == "1.0"


def test_skill_dispatcher_routes_mm_skill_execute_and_activity_handlers():
    store = InMemoryArtifactStore()
    snapshot = _snapshot(store)
    dispatcher = SkillActivityDispatcher()

    seen_inputs = {}

    def mm_handler(inputs, _context):
        seen_inputs.update(inputs)
        return SkillResult(
            status="SUCCEEDED",
            outputs={"test_report_artifact": "art:sha256:feed"},
            progress={"percent": 100},
        )

    def sandbox_handler(invocation, _context):
        assert invocation.skill_name == "repo.apply_patch"
        return SkillResult(
            status="SUCCEEDED",
            outputs={"files_changed": 1},
            progress={"percent": 100},
        )

    dispatcher.register_skill(skill_name="repo.run_tests", version="1.0.0", handler=mm_handler)
    dispatcher.register_activity(activity_type="sandbox.exec", handler=sandbox_handler)

    result_a = asyncio.run(
        execute_skill_activity(
            invocation_payload={
                "id": "n1",
                "skill": {"name": "repo.run_tests", "version": "1.0.0"},
                "inputs": {"repo_ref": "git:org/repo#branch"},
            },
            registry_snapshot=snapshot,
            dispatcher=dispatcher,
        )
    )
    result_b = asyncio.run(
        execute_skill_activity(
            invocation_payload={
                "id": "n2",
                "skill": {"name": "repo.apply_patch", "version": "2.1.0"},
                "inputs": {
                    "repo_ref": "git:org/repo#branch",
                    "patch_artifact": "art:sha256:feed",
                },
            },
            registry_snapshot=snapshot,
            dispatcher=dispatcher,
        )
    )

    assert result_a.status == "SUCCEEDED"
    assert result_b.outputs["files_changed"] == 1
    assert seen_inputs["repo_ref"] == "git:org/repo#branch"


def test_skill_dispatcher_rejects_missing_handlers():
    store = InMemoryArtifactStore()
    snapshot = _snapshot(store)
    dispatcher = SkillActivityDispatcher()

    with pytest.raises(SkillDispatchError, match="No mm.skill.execute handler"):
        asyncio.run(
            dispatcher.execute(
                invocation=parse_plan_definition(
                    _plan_payload(
                        snapshot_digest=snapshot.digest,
                        snapshot_ref=snapshot.artifact_ref,
                    )
                ).nodes[0],
                snapshot=snapshot,
                context=None,
            )
        )


def test_plan_interpreter_fail_fast_skips_dependents():
    store = InMemoryArtifactStore()
    snapshot = _snapshot(store)
    plan = parse_plan_definition(
        _plan_payload(snapshot_digest=snapshot.digest, snapshot_ref=snapshot.artifact_ref)
    )

    async def executor(invocation):
        if invocation.id == "n1":
            return SkillResult(status="FAILED", outputs={}, progress={"percent": 100})
        raise AssertionError("Dependent node should not execute under fail-fast")

    summary = asyncio.run(
        create_validated_interpreter(
            plan=plan,
            registry_snapshot=snapshot,
            executor=executor,
            artifact_store=store,
            write_progress_artifact=True,
        ).run()
    )

    assert summary.status == "FAILED"
    assert "n2" in summary.skipped
    assert summary.progress_artifact_ref is not None


def test_plan_interpreter_continue_executes_independent_nodes():
    store = InMemoryArtifactStore()
    snapshot = _snapshot(store)
    plan_payload = _plan_payload(
        snapshot_digest=snapshot.digest,
        snapshot_ref=snapshot.artifact_ref,
        failure_mode="CONTINUE",
    )
    # Remove dependency edge and reference so n2 is independent.
    plan_payload["edges"] = []
    plan_payload["nodes"][1]["inputs"]["patch_artifact"] = "art:sha256:feed"
    plan = parse_plan_definition(plan_payload)

    async def executor(invocation):
        if invocation.id == "n1":
            return SkillResult(status="FAILED", outputs={}, progress={"percent": 100})
        return SkillResult(
            status="SUCCEEDED",
            outputs={"files_changed": 2},
            progress={"percent": 100},
        )

    summary = asyncio.run(
        create_validated_interpreter(
            plan=plan,
            registry_snapshot=snapshot,
            executor=executor,
            artifact_store=store,
            write_progress_artifact=False,
        ).run()
    )

    assert summary.status == "PARTIAL"
    assert "n2" in summary.results
