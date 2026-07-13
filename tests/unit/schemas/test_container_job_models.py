from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

import moonmind.schemas.container_job_models as contracts
from moonmind.schemas.container_job_models import (
    AuxiliaryOutcome,
    ContainerJobAccepted,
    ContainerJobArtifactPage,
    ContainerJobCancelResult,
    ContainerJobFailureClass,
    ContainerJobLogPage,
    ContainerJobState,
    ContainerJobStatus,
    ContainerJobSubmitRequest,
    ImageObservation,
    ResolvedContainerLaunchPlan,
    TerminalOutcome,
    ensure_temporal_safe,
)

JOB_ID = "container-job:" + "a" * 32
NOW = datetime(2026, 1, 2, tzinfo=timezone.utc)


def payload() -> dict:
    return {
        "idempotencyKey": "request-1",
        "source": {"source": "omnigent", "omnigentSessionId": "s"},
        "spec": {
            "image": {"reference": "ubuntu:24.04"},
            "workspace": {"kind": "sandbox", "workspaceId": "ws"},
            "resources": {"cpuMillis": 1000, "memoryMiB": 512},
        },
    }


def test_submit_has_deterministic_shared_golden_serialization() -> None:
    model = ContainerJobSubmitRequest.model_validate(payload())
    expected = (
        b'{"contractVersion":"v1","idempotencyKey":"request-1","source":'
        b'{"omnigentSessionId":"s","source":"omnigent"},"spec":{"caches":[],'
        b'"command":[],"entrypoint":[],"environment":[],"image":{"reference":'
        b'"ubuntu:24.04"},"networkMode":"restricted","outputs":[],"pullPolicy":'
        b'"if_not_present","resources":{"cpuMillis":1000,"memoryMiB":512,"pids":256},'
        b'"timeoutSeconds":1800,"workdir":"/workspace","workspace":{"kind":"sandbox",'
        b'"relativePath":"repo","workspaceId":"ws"}}}'
    )
    assert ensure_temporal_safe(model) == expected
    # HTTP, MCP, and Temporal consume this one alias-preserving model dump.
    assert model.model_dump(mode="json", by_alias=True, exclude_none=True) == contracts.json.loads(expected)


@pytest.mark.parametrize(
    "source", ["http", "mcp", "workflow", "managed_session", "omnigent"]
)
def test_one_contract_family_serializes_every_caller_source(source: str) -> None:
    data = payload()
    data["source"] = {"source": source, "callerRequestId": "request"}
    encoded = ensure_temporal_safe(ContainerJobSubmitRequest.model_validate(data))
    assert contracts.json.loads(encoded)["source"] == {
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
    data["spec"]["image"]["credentialRef"] = "raw-password"
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
    ContainerJobLogPage(jobId=JOB_ID, entries=[entry] * contracts.MAX_LOG_PAGE_ENTRIES)
    with pytest.raises(ValidationError):
        ContainerJobLogPage(jobId=JOB_ID, entries=[entry] * (contracts.MAX_LOG_PAGE_ENTRIES + 1))
    artifact = {"name": "x", "artifactRef": "artifact://x", "sizeBytes": 1, "sha256": "c" * 64}
    ContainerJobArtifactPage(
        jobId=JOB_ID, artifacts=[artifact] * contracts.MAX_ARTIFACT_PAGE_ENTRIES,
        publication={"state": "succeeded"},
    )
    with pytest.raises(ValidationError):
        ContainerJobArtifactPage(
            jobId=JOB_ID, artifacts=[artifact] * (contracts.MAX_ARTIFACT_PAGE_ENTRIES + 1),
            publication={"state": "succeeded"},
        )


def test_every_history_facing_contract_enforces_temporal_limit(monkeypatch) -> None:
    monkeypatch.setattr(contracts, "MAX_TEMPORAL_PAYLOAD_BYTES", 100)
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
