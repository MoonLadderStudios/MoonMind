"""Contract tests for the versioned workspace-intent record (issue #3558)."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from moonmind.schemas.workspace_intent import (
    CREDENTIAL_INJECTION_IN_MEMORY_ONLY,
    WORKSPACE_INTENT_PRODUCER_VERSION,
    WORKSPACE_INTENT_SCHEMA_VERSION,
    WorkspaceIntentAssetProjection,
    WorkspaceIntentRecord,
    assert_no_runtime_shortcut_keys,
)

_SANDBOX_LOCATOR = {
    "kind": "sandbox",
    "workspaceId": "ws-abc",
    "relativePath": "repo",
}


def _record(**overrides):
    payload = {
        "createdAt": datetime(2026, 1, 1, tzinfo=UTC),
        "workflowId": "wf-1",
        "stepExecutionId": "wf-1:run:step:execution:1",
        "repository": "https://github.com/acme/widgets.git",
        "repositoryKind": "github_https",
        "startingBranch": "main",
        "targetBranch": "feature/x",
        "publishMode": "pr",
        "repositoryMutation": True,
        "requiredCapabilities": ["gh", "git"],
        "workspaceLocator": dict(_SANDBOX_LOCATOR),
    }
    payload.update(overrides)
    return WorkspaceIntentRecord(**payload)


def test_record_stamps_version_and_deterministic_digest() -> None:
    record = _record()
    assert record.schema_version == WORKSPACE_INTENT_SCHEMA_VERSION
    assert record.producer_version == WORKSPACE_INTENT_PRODUCER_VERSION
    assert record.credential_injection_policy == CREDENTIAL_INJECTION_IN_MEMORY_ONLY
    assert record.intent_digest is not None
    assert record.intent_digest.startswith("sha256:")
    assert record.intent_digest == record.compute_digest()


def test_digest_excludes_created_at_so_retries_reproduce_intent() -> None:
    first = _record(createdAt=datetime(2026, 1, 1, tzinfo=UTC))
    later = _record(createdAt=datetime(2030, 7, 30, 12, 0, tzinfo=UTC))
    assert first.intent_digest == later.intent_digest


def test_digest_changes_when_governing_value_changes() -> None:
    base = _record()
    changed = _record(targetBranch="feature/y")
    assert base.intent_digest != changed.intent_digest


def test_capabilities_are_case_normalized_and_deduped() -> None:
    record = _record(requiredCapabilities=["GH", "gh", " Git "])
    assert record.required_capabilities == ("gh", "git")


def test_ref_lists_are_stripped_and_deduped_preserving_order() -> None:
    record = _record(inputRefs=[" artifact://a ", "artifact://a", "artifact://b"])
    assert record.input_refs == ("artifact://a", "artifact://b")


def test_record_is_frozen_so_digest_governing_state_cannot_drift() -> None:
    record = _record()
    # A frozen record rejects field reassignment, so the finalized digest can
    # never observe values other than the ones it was computed over.
    with pytest.raises(ValidationError):
        record.target_branch = "feature/y"
    # Collection fields are tuples, so no caller can append past the digest.
    assert isinstance(record.required_capabilities, tuple)
    assert isinstance(record.input_refs, tuple)
    with pytest.raises(AttributeError):
        record.required_capabilities.append("extra")  # type: ignore[attr-defined]
    # The nested typed locator is frozen too.
    with pytest.raises(ValidationError):
        record.workspace_locator.relative_path = "elsewhere"


def test_tampered_digest_is_rejected() -> None:
    with pytest.raises(ValidationError):
        _record(intentDigest="sha256:deadbeef")


def test_credential_shaped_value_is_rejected() -> None:
    with pytest.raises(ValidationError):
        _record(startingBranch="token=ghp_supersecretvalue")


def test_docker_authority_value_is_rejected() -> None:
    with pytest.raises(ValidationError):
        _record(repository="unix:///var/run/docker.sock")


def test_locator_union_stays_type_safe_and_distinct() -> None:
    external = _record(
        repository=None,
        repositoryKind=None,
        repositoryMutation=False,
        publishMode="none",
        requiredCapabilities=[],
        workspaceLocator={"kind": "external_state", "artifactRef": "artifact://ext"},
    )
    assert external.workspace_locator.kind == "external_state"
    # A bogus locator kind is rejected by the discriminated union.
    with pytest.raises(ValidationError):
        _record(workspaceLocator={"kind": "bind_mount", "path": "/var/lib"})


def test_evidence_is_bounded_credential_free_and_path_safe() -> None:
    record = _record(
        repository="/work/agent_jobs/local/repo",
        repositoryKind="local",
        inputRefs=["artifact://in1", "artifact://in2"],
        restoreInputRefs=["artifact://chk"],
        externalStateRefs=["external-state:sess"],
        skillProjections=[
            WorkspaceIntentAssetProjection(
                name="pr-resolver", digest="sha256:aa"
            )
        ],
    )
    evidence = record.evidence()
    # Raw local paths never leak; only a redaction marker is exposed.
    assert evidence["repository"] == "[local-source]"
    assert evidence["repositoryKind"] == "local"
    # Counts and digests are exposed, never unbounded bodies.
    assert evidence["inputRefCount"] == 2
    assert evidence["restoreInputRefCount"] == 1
    assert evidence["externalStateRefCount"] == 1
    assert evidence["skillProjectionCount"] == 1
    assert evidence["skillProjectionDigests"] == ["sha256:aa"]
    assert evidence["intentDigest"] == record.intent_digest


def test_assert_no_runtime_shortcut_keys_flags_smuggled_authority() -> None:
    assert_no_runtime_shortcut_keys(
        {"repository": "owner/repo", "workspaceLocator": dict(_SANDBOX_LOCATOR)}
    )
    for shortcut in ("dockerVolume", "bindSource", "hostId", "volumeName"):
        with pytest.raises(ValueError):
            assert_no_runtime_shortcut_keys({shortcut: "smuggled"})


def test_portable_skill_inputs_are_not_treated_as_runtime_shortcuts() -> None:
    # A selected Skill's portable inputs may legitimately be named ``volume``,
    # ``bind``, ``daemon``, or ``hostId``; those grant no host authority (which
    # derives only from the typed locator), so they must not be rejected.
    assert_no_runtime_shortcut_keys(
        {
            "skill": {
                "name": "some-skill",
                "inputs": {
                    "volume": "loud",
                    "bind": True,
                    "daemon": "background",
                    "hostId": "logical-name",
                },
            }
        }
    )
    # A genuine shortcut key outside the portable inputs payload is still flagged.
    with pytest.raises(ValueError):
        assert_no_runtime_shortcut_keys(
            {"skill": {"name": "s"}, "dockerSocket": "/var/run/docker.sock"}
        )


def test_workspace_locator_payload_round_trips_typed_identity() -> None:
    record = _record()
    payload = record.workspace_locator_payload()
    assert payload == {
        "kind": "sandbox",
        "workspaceId": "ws-abc",
        "relativePath": "repo",
    }
