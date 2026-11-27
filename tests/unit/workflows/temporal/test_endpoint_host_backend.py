"""Worker handling of live endpoint jobs: credential, recheck and evidence (#5440)."""

from __future__ import annotations

import json
import stat
from datetime import datetime, timedelta, timezone

import pytest

from moonmind.auth.secret_refs import SecretMissingError
from moonmind.config.container_backend_settings import (
    IMAGE_SOURCES_ENV_KEY,
    resolve_container_backend_settings,
)
from moonmind.schemas.container_job_models import (
    ContainerJobActivityRequest,
    ContainerJobBackendError,
    ContainerJobFailureClass,
    ContainerJobSubmitRequest,
    OwnerIdentity,
    WorkloadCredentialAuthorization,
)
from moonmind.security.endpoint_assessments import EndpointAssessmentSummary
from moonmind.security.workload_credentials import authorize_workload_credentials
from moonmind.security.workload_profiles import WORKLOAD_PROFILES_ENV_KEY
from moonmind.workflows.temporal.container_job_attachments import claim, storage_for
from moonmind.workflows.temporal.container_job_backend import DockerContainerJobBackend
from tests.unit.security.test_endpoint_hosts import (
    _ARF,
    PROFILE,
    grant,
    resolved,
    scope,
)

JOB = "container-job:" + "e" * 32
OWNER = OwnerIdentity(principalId="operator", principalType="user")
KEY_VALUE = "-----BEGIN OPENSSH PRIVATE KEY-----\nseed-host-key-bytes\n-----END OPENSSH PRIVATE KEY-----\n"
SETTINGS = resolve_container_backend_settings(
    {
        IMAGE_SOURCES_ENV_KEY: json.dumps(
            [{"sourceRef": "repository-security", "image": "ghcr.io/x/security:1"}]
        ),
        WORKLOAD_PROFILES_ENV_KEY: json.dumps(
            [PROFILE.model_dump(mode="json", by_alias=True)]
        ),
    }
)


class Secrets:
    def __init__(self):
        self.store = {"db://host-key": (2, KEY_VALUE)}
        self.calls = []

    async def __call__(self, ref, revision):
        self.calls.append((ref, revision))
        if ref not in self.store or self.store[ref][0] != revision:
            raise SecretMissingError("not active at that revision")
        return self.store[ref][1]


class Fixture:
    def __init__(self, tmp_path, *, state="succeeded"):
        self.root = tmp_path / "workspaces"
        self.root.mkdir()
        spec = resolved()
        now = datetime.now(timezone.utc)
        admitted = scope(
            grants=[grant(expiresAt=(now + timedelta(hours=4)).isoformat())]
        )
        decisions = authorize_workload_credentials(
            spec,
            admitted,
            SETTINGS.workload_profiles,
            principal="user:operator",
            now=now,
        )
        self.request = ContainerJobActivityRequest(
            jobId=JOB,
            owner=OWNER,
            ownershipToken=f"{JOB}:v1",
            request=ContainerJobSubmitRequest(
                idempotencyKey="k", source={"source": "mcp"}, spec=spec
            ),
            workloadCredentials=tuple(
                WorkloadCredentialAuthorization.model_validate({**d, "jobId": JOB})
                for d in decisions
            ),
            resolvedImageRef="sha256:" + "d" * 64,
            containerRef="job-container",
            terminalState=state,
        )
        self.commands = []
        self.published = {}
        self.secrets = Secrets()
        self.backend = DockerContainerJobBackend(
            workspace_root=self.root,
            settings=SETTINGS,
            command_runner=self.daemon,
            evidence_publisher=self.publish,
            secret_resolver=self.secrets,
            log_spool_root=tmp_path / "live",
        )
        self.storage = storage_for(self.root, JOB)
        claim(self.storage, f"{JOB}:v1")

    async def daemon(self, argv):
        self.commands.append(tuple(argv))
        return 0, b"", b""

    async def publish(self, _request, name, payload):
        self.published[name] = payload
        return "art_" + name.rsplit("-", 1)[-1].split(".")[0]


@pytest.mark.asyncio
async def test_host_credential_is_a_read_only_file_never_an_argument(tmp_path):
    fixture = Fixture(tmp_path)
    args = await fixture.backend._deliver_workload_credentials(fixture.request)
    assert KEY_VALUE not in " ".join(args)
    assert "--env-file" not in args
    mounts = [args[i + 1] for i, arg in enumerate(args) if arg == "--mount"]
    assert any(m.endswith("dst=/run/moonmind-credentials,readonly") for m in mounts)
    key = fixture.storage.workload_credentials / "files" / "host"
    assert key.read_text() == KEY_VALUE
    assert stat.S_IMODE(key.stat().st_mode) == 0o444
    assert fixture.secrets.calls == [("db://host-key", 2)]
    assert KEY_VALUE not in fixture.request.model_dump_json(by_alias=True)


@pytest.mark.parametrize(
    "change,failure",
    [
        ("rotated", ContainerJobFailureClass.CREDENTIAL_UNRESOLVED),
        ("revoked", ContainerJobFailureClass.CREDENTIAL_UNRESOLVED),
        ("expired", ContainerJobFailureClass.CREDENTIAL_EXPIRED),
    ],
)
@pytest.mark.asyncio
async def test_each_observation_fails_closed_when_the_credential_lapses(
    tmp_path, monkeypatch, change, failure
):
    fixture = Fixture(tmp_path)
    stopped = []

    async def stop(request):
        stopped.append(request.container_ref)

    monkeypatch.setattr(fixture.backend, "stop_container", stop)
    await fixture.backend._recheck_host_credentials(fixture.request)
    assert stopped == []
    request = fixture.request
    if change == "rotated":
        fixture.secrets.store["db://host-key"] = (3, KEY_VALUE)
    elif change == "revoked":
        del fixture.secrets.store["db://host-key"]
    else:
        request = request.model_copy(
            update={
                "workload_credentials": tuple(
                    item.model_copy(
                        update={
                            "expires_at": datetime.now(timezone.utc)
                            - timedelta(seconds=1)
                        }
                    )
                    for item in request.workload_credentials
                )
            }
        )
    with pytest.raises(ContainerJobBackendError) as failed:
        await fixture.backend._recheck_host_credentials(request)
    assert failed.value.failure_class is failure
    assert KEY_VALUE not in str(failed.value)
    assert stopped == ["job-container"]


@pytest.mark.parametrize("state", ["succeeded", "canceled", "failed"])
@pytest.mark.asyncio
async def test_completed_host_evidence_survives_a_stop_in_the_offline_shape(
    tmp_path, monkeypatch, state
):
    fixture = Fixture(tmp_path, state=state)
    outputs = tmp_path / "outputs"
    (outputs / "endpoint-assessment").mkdir(parents=True)
    (outputs / "endpoint-assessment/results.xml").write_bytes(_ARF)
    (outputs / "endpoint-assessment/execution.json").write_text(
        json.dumps(
            {
                "target": "host",
                "version": "1.3.7",
                "exitCode": 2,
                "state": "succeeded",
                "contentFile": "ssg-debian12-ds.xml",
                "contentDigest": "b" * 64,
                "contentRelease": "0.1.82",
                "limitations": [],
            }
        )
    )
    monkeypatch.setattr(fixture.backend, "_output_root", lambda _: outputs)

    async def no_capture_limits(_):
        return []

    monkeypatch.setattr(fixture.backend, "_capture_limitations", no_capture_limits)
    manifest = await fixture.backend._collect_declared_outputs(fixture.request)
    assert [(m.name, m.collection_status) for m in manifest] == [
        ("results", "collected"),
        ("execution", "collected"),
        ("summary", "collected"),
    ]
    summary = EndpointAssessmentSummary.model_validate_json(
        fixture.published[f"{JOB}-output-summary.json"]
    )
    assert summary.schema_version == "endpoint-assessment/v1"
    assert summary.subject.observation == "live-host"
    assert summary.subject.address == "93.184.216.34:22"
    assert summary.native_ref == "art_results"
    assert (summary.coverage == "complete") is (state == "succeeded")
    for payload in fixture.published.values():
        assert KEY_VALUE.encode() not in payload


@pytest.mark.asyncio
async def test_host_launch_carries_the_original_lease_deadline_on_each_attempt(
    tmp_path, monkeypatch
):
    """The runner reserves cleanup time before the gateway's actual close."""
    from contextlib import asynccontextmanager
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    fixture = Fixture(tmp_path)
    backend = fixture.backend
    original_expiry = 1800000000.25
    scoped = SimpleNamespace(
        network_ref="native-network",
        gateway_ref="native-gateway",
        policy=SimpleNamespace(digest="digest"),
        prepare=AsyncMock(
            return_value={
                "proxyAddress": "172.20.0.2",
                "clientAddress": "172.20.0.3",
                "gatewayIdentity": "gateway-id",
                "snapshotDigest": "digest",
                "expiresAt": original_expiry,
            }
        ),
        proxy_env=lambda: ["MOONMIND_EGRESS_PROXY=http://native-gateway:3128"],
    )

    @asynccontextmanager
    async def guard(*args, **kwargs):
        yield original_expiry

    monkeypatch.setattr(backend, "_assessment_guard", guard)
    monkeypatch.setattr(backend, "_scoped_lease", lambda request: scoped)
    monkeypatch.setattr(backend, "_reject_ownership_collision", AsyncMock())
    monkeypatch.setattr(backend, "_prepare_attachments", AsyncMock(return_value=[]))
    monkeypatch.setattr(
        backend, "_publish_scoped_egress", AsyncMock(return_value="art_lease")
    )
    launch = AsyncMock(return_value=(0, b"", b""))
    monkeypatch.setattr(backend, "_assessment_launch_command", launch)
    for _ in range(2):
        await backend.create_container(fixture.request)
    assert launch.call_args_list
    for call in launch.call_args_list:
        argv = call.args[1]
        assert [
            item for item in argv if item.startswith("MOONMIND_EGRESS_DEADLINE=")
        ] == [f"MOONMIND_EGRESS_DEADLINE={original_expiry}"]
        assert KEY_VALUE not in " ".join(argv)
