from datetime import datetime, timezone
import json

import pytest
from pydantic import ValidationError

from moonmind.schemas.container_job_models import (
    ArtifactCollectionStatus,
    AuxiliaryOutcome,
    ContainerJobAccepted,
    ContainerJobActivityResult,
    ContainerJobArtifact,
    ContainerJobArtifactPage,
    ContainerJobCancelResult,
    ContainerJobFailureClass,
    ContainerJobLogPage,
    ContainerJobState,
    ContainerJobStatus,
    ContainerJobSubmitRequest,
    ContainerJobWorkflowInput,
    ImageObservation,
    MAX_ARTIFACT_PAGE_ENTRIES,
    MAX_LOG_PAGE_ENTRIES,
    ResolvedContainerLaunchPlan,
    TerminalOutcome,
    ensure_temporal_safe,
    workspace_locator_identity,
)
from moonmind.schemas.workspace_locator_models import (
    ExternalStateLocator,
    ManagedWorkspaceLocator,
    SandboxWorkspaceLocator,
)

JOB_ID = "container-job:" + "a" * 32
NOW = datetime(2026, 1, 2, tzinfo=timezone.utc)


def payload() -> dict:
    return {
        "idempotencyKey": "request-1",
        "source": {"source": "omnigent", "omnigentSessionId": "s"},
        "spec": {
            "image": "ubuntu:24.04",
            "workspaceRef": {"kind": "external_state", "artifactRef": "ws"},
            "resources": {"cpuMillis": 1000, "memoryMiB": 512},
        },
    }


def test_submit_has_deterministic_shared_golden_serialization() -> None:
    model = ContainerJobSubmitRequest.model_validate(payload())
    expected = (
        b'{"contractVersion":"v1","idempotencyKey":"request-1","source":'
        b'{"omnigentSessionId":"s","source":"omnigent"},"spec":{"caches":[],'
        b'"command":[],"entrypoint":[],"environment":[],"image":"ubuntu:24.04",'
        b'"networkMode":"none","outputs":[],"pullPolicy":"if-missing","resources":'
        b'{"cpuMillis":1000,"memoryMiB":512,"pids":256},"timeoutSeconds":1800,'
        b'"workdir":"/workspace","workspaceRef":{"artifactRef":"ws",'
        b'"kind":"external_state"}}}'
    )
    assert ensure_temporal_safe(model) == expected
    # HTTP, MCP, and Temporal consume this one alias-preserving model dump.
    assert model.model_dump(mode="json", by_alias=True, exclude_none=True) == json.loads(expected)


@pytest.mark.parametrize(
    "source", ["http", "mcp", "workflow", "managed_session", "omnigent"]
)
def test_one_contract_family_serializes_every_caller_source(source: str) -> None:
    data = payload()
    data["source"] = {"source": source, "callerRequestId": "request"}
    encoded = ensure_temporal_safe(ContainerJobSubmitRequest.model_validate(data))
    assert json.loads(encoded)["source"] == {
        "callerRequestId": "request", "source": source
    }


@pytest.mark.parametrize("state", list(ContainerJobState))
def test_status_accepts_every_canonical_state(state: ContainerJobState) -> None:
    assert ContainerJobStatus(jobId=JOB_ID, state=state, updatedAt=NOW).state == state.value


@pytest.mark.parametrize("failure", list(ContainerJobFailureClass))
def test_terminal_accepts_every_failure_class(failure: ContainerJobFailureClass) -> None:
    assert TerminalOutcome(failureClass=failure).failure_class == failure.value


@pytest.mark.parametrize("state", ["not_attempted", "succeeded", "failed"])
def test_auxiliary_outcomes_are_independent(state: str) -> None:
    assert AuxiliaryOutcome(state=state).state == state


@pytest.mark.parametrize(
    "field,value",
    [
        ("dockerHost", "tcp://daemon"), ("dockerUrl", "https://daemon"),
        ("socketPath", "/var/run/docker.sock"), ("tlsPath", "/certs"),
        ("hostPath", "/tmp"), ("sourcePath", "/host"), ("privileged", True),
        ("devices", ["/dev/kvm"]), ("pidMode", "host"), ("ipcMode", "host"),
        ("utsMode", "host"), ("usernsMode", "host"),
        ("labels", {"moonmind.owner": "x"}),
        ("registryCredentials", {"password": "x"}),
    ],
)
def test_submit_rejects_every_caller_authority_field(field: str, value: object) -> None:
    data = payload()
    data["spec"][field] = value
    with pytest.raises(ValidationError):
        ContainerJobSubmitRequest.model_validate(data)


@pytest.mark.parametrize("secret_key", ["password", "apiKey", "authToken", "credential"])
def test_history_contracts_reject_secret_like_keys(secret_key: str) -> None:
    data = payload()
    data["source"][secret_key] = "raw"
    with pytest.raises(ValidationError):
        ContainerJobSubmitRequest.model_validate(data)


def test_secret_values_outputs_and_credential_references_are_validated() -> None:
    data = payload()
    data["spec"]["environment"] = [{"name": "API_TOKEN", "value": "raw"}]
    with pytest.raises(ValidationError):
        ContainerJobSubmitRequest.model_validate(data)
    data = payload()
    data["spec"]["outputs"] = [{"name": "x", "relativePath": "/host/x"}]
    with pytest.raises(ValidationError):
        ContainerJobSubmitRequest.model_validate(data)
    data = payload()
    data["spec"]["registryCredentialRef"] = "raw-password"
    with pytest.raises(ValidationError):
        ContainerJobSubmitRequest.model_validate(data)
    data = payload()
    data["spec"]["environment"] = [{"name": "API_TOKEN", "secretRef": "raw-value"}]
    with pytest.raises(ValidationError):
        ContainerJobSubmitRequest.model_validate(data)


@pytest.mark.parametrize("workdir", ["/workspace/..", "/workspace/./repo"])
def test_workdir_rejects_traversal(workdir: str) -> None:
    data = payload()
    data["spec"]["workdir"] = workdir
    with pytest.raises(ValidationError):
        ContainerJobSubmitRequest.model_validate(data)


@pytest.mark.parametrize(
    "locator,expected_identity,expected_type",
    [
        (
            {"kind": "sandbox", "workspaceId": "ws-1"},
            "ws-1",
            SandboxWorkspaceLocator,
        ),
        (
            {
                "kind": "managed_runtime",
                "runtimeId": "rt-1",
                "agentRunId": "agent-run:1",
            },
            "rt-1-agent-run:1",
            ManagedWorkspaceLocator,
        ),
        (
            {"kind": "external_state", "artifactRef": "art://ws"},
            "art://ws",
            ExternalStateLocator,
        ),
    ],
)
def test_workspace_ref_consumes_canonical_3147_locator(
    locator: dict, expected_identity: str, expected_type: type
) -> None:
    data = payload()
    data["spec"]["workspaceRef"] = locator
    spec = ContainerJobSubmitRequest.model_validate(data).spec
    assert isinstance(spec.workspace_ref, expected_type)
    assert spec.workspace_ref.kind == locator["kind"]
    assert workspace_locator_identity(spec.workspace_ref) == expected_identity


@pytest.mark.parametrize(
    "workspace_ref",
    [
        {"kind": "omnigent-session", "sessionId": "ws"},
        {"kind": "moonmind-session", "sessionId": "ws"},
        {"kind": "artifact-workspace", "artifactRef": "ws"},
        {"kind": "sandbox"},
        {"kind": "external_state", "artifactRef": "ws", "hostPath": "/tmp"},
    ],
)
def test_workspace_ref_rejects_incompatible_or_unsafe_locators(
    workspace_ref: dict,
) -> None:
    data = payload()
    data["spec"]["workspaceRef"] = workspace_ref
    with pytest.raises(ValidationError):
        ContainerJobSubmitRequest.model_validate(data)


@pytest.mark.parametrize(
    "legacy,expected",
    [
        ({"kind": "artifact-workspace", "artifactRef": "ws"}, "ws"),
        ({"kind": "moonmind-session", "sessionId": "session-1"}, "session-1"),
    ],
)
def test_workflow_v1_normalizes_legacy_workspace_locators_only_at_temporal_boundary(
    legacy: dict, expected: str
) -> None:
    data = {
        "jobId": "container-job:0123456789abcdef0123456789abcdef",
        "request": payload(),
    }
    data["request"]["spec"]["workspaceRef"] = legacy
    parsed = ContainerJobWorkflowInput.model_validate(data)
    assert parsed.request.spec.workspace_ref.kind == "external_state"
    assert parsed.request.spec.workspace_ref.artifact_ref == expected
    with pytest.raises(ValidationError):
        ContainerJobSubmitRequest.model_validate(data["request"])


def test_documented_container_job_wire_values_are_accepted() -> None:
    data = payload()
    data["spec"].update({"networkMode": "bridge", "pullPolicy": "if-missing"})
    assert ContainerJobSubmitRequest.model_validate(data).spec.image == "ubuntu:24.04"


def test_deployment_owned_image_source_is_an_alternative_to_direct_image() -> None:
    data = payload()
    data["spec"].pop("image")
    data["spec"]["imageSourceRef"] = "moonmind-python-tests"

    spec = ContainerJobSubmitRequest.model_validate(data).spec

    assert spec.image is None
    assert spec.image_source_ref == "moonmind-python-tests"


@pytest.mark.parametrize(
    "image,image_source_ref",
    [
        (None, None),
        ("ubuntu:24.04", "moonmind-python-tests"),
    ],
)
def test_exactly_one_image_authority_is_required(
    image: str | None, image_source_ref: str | None
) -> None:
    data = payload()
    if image is None:
        data["spec"].pop("image")
    else:
        data["spec"]["image"] = image
    if image_source_ref is not None:
        data["spec"]["imageSourceRef"] = image_source_ref

    with pytest.raises(ValidationError):
        ContainerJobSubmitRequest.model_validate(data)


def test_deployment_image_source_rejects_caller_provisioning_overrides() -> None:
    data = payload()
    data["spec"].pop("image")
    data["spec"].update(
        {
            "imageSourceRef": "moonmind-python-tests",
            "pullPolicy": "always",
        }
    )

    with pytest.raises(ValidationError):
        ContainerJobSubmitRequest.model_validate(data)


def test_image_observation_and_async_identity_serialization() -> None:
    image = ImageObservation(
        requestedReference="ubuntu:24.04", resolvedDigest="sha256:" + "b" * 64,
        cachePresent=True, cacheHit=True, pullLockWaitMs=5, pullDurationMs=10,
    )
    accepted = ContainerJobAccepted(jobId=JOB_ID, createdAt=NOW)
    assert accepted.state == "queued"
    assert image.model_dump(mode="json", by_alias=True)["resolvedDigest"].startswith("sha256:")
    with pytest.raises(ValidationError):
        ContainerJobAccepted(jobId="job-1", createdAt=NOW)


def test_log_and_artifact_pages_enforce_bounds() -> None:
    entry = {"sequence": 1, "timestamp": NOW, "stream": "stdout", "text": "x"}
    ContainerJobLogPage(jobId=JOB_ID, entries=[entry] * MAX_LOG_PAGE_ENTRIES)
    with pytest.raises(ValidationError):
        ContainerJobLogPage(jobId=JOB_ID, entries=[entry] * (MAX_LOG_PAGE_ENTRIES + 1))
    artifact = {"name": "x", "artifactRef": "artifact://x", "sizeBytes": 1, "sha256": "c" * 64}
    ContainerJobArtifactPage(
        jobId=JOB_ID, artifacts=[artifact] * MAX_ARTIFACT_PAGE_ENTRIES,
        publication={"state": "succeeded"},
    )
    with pytest.raises(ValidationError):
        ContainerJobArtifactPage(
            jobId=JOB_ID, artifacts=[artifact] * (MAX_ARTIFACT_PAGE_ENTRIES + 1),
            publication={"state": "succeeded"},
        )


def test_status_enum_covers_distinct_lifecycle_phases() -> None:
    # The finer-grained phases required "at least" by #3258 must exist.
    for name in (
        "resolving_workspace",
        "workspace_not_visible",
        "building_image",
        "starting",
        "publishing_artifacts",
        "cleaning_up",
    ):
        assert ContainerJobState(name).value == name


def test_output_manifest_artifact_allows_missing_and_collected_entries() -> None:
    missing = ContainerJobArtifact(
        name="report",
        relativePath="dist/report.txt",
        collectionStatus="missing",
        detail="declared output was not produced",
    )
    assert missing.artifact_ref is None
    assert missing.sha256 is None
    assert missing.collection_status == ArtifactCollectionStatus.MISSING

    collected = ContainerJobArtifact(
        name="report",
        artifactRef="artifact://x",
        sizeBytes=12,
        sha256="a" * 64,
        mediaType="text/plain",
        relativePath="dist/report.txt",
    )
    assert collected.collection_status == ArtifactCollectionStatus.COLLECTED
    assert collected.media_type == "text/plain"


def test_activity_result_carries_observations_and_cursor() -> None:
    result = ContainerJobActivityResult(
        logCursor="2024-01-01T00:00:03+00:00|3",
        workspaceProbe="visible",
        startedAt=NOW,
        finishedAt=NOW,
        durationMs=1500,
        eventsRef="artifact://events",
    )
    dumped = result.model_dump(by_alias=True, exclude_none=True)
    assert dumped["logCursor"] == "2024-01-01T00:00:03+00:00|3"
    assert dumped["workspaceProbe"] == "visible"
    assert dumped["durationMs"] == 1500
    assert dumped["eventsRef"] == "artifact://events"


def test_every_history_facing_contract_enforces_temporal_limit(monkeypatch) -> None:
    monkeypatch.setattr("moonmind.schemas.container_job_models.MAX_TEMPORAL_PAYLOAD_BYTES", 100)
    with pytest.raises(ValidationError, match="payload must serialize"):
        ContainerJobStatus(jobId=JOB_ID, state="running", backendRef="b" * 80, updatedAt=NOW)
    with pytest.raises(ValidationError, match="payload must serialize"):
        ResolvedContainerLaunchPlan(
            jobId=JOB_ID, backendKind="docker", backendRef="local",
            resolvedWorkspaceRef="workspace://" + "x" * 80,
            spec=payload()["spec"],
        )
    with pytest.raises(ValidationError, match="payload must serialize"):
        ContainerJobCancelResult(jobId=JOB_ID, state="canceling", accepted=True)
