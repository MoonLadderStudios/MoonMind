"""Retain scoped denials through production Activities, artifacts and replay.

Only Docker is a deterministic command boundary. Workflow, Activity dispatch,
policy/attachment attestation, job identity, projection and artifact persistence
use their production owners. Actual connections belong to the Docker CI suite.
"""

import hashlib
import json
from contextlib import AsyncExitStack
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from temporalio import activity
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Replayer, UnsandboxedWorkflowRunner, Worker

from api_service.db.models import Base
from api_service.services.container_jobs import (
    ContainerJobService,
    owner_artifact_principal,
)
from moonmind.config.container_backend_settings import (
    resolve_container_backend_settings,
)
from moonmind.config.settings import settings
from moonmind.schemas.container_job_models import (
    ContainerJobWorkflowInput,
    OwnerIdentity,
)
from moonmind.security import egress
from moonmind.security.egress_conformance_evidence import (
    parse_and_verify_conformance_evidence,
)
from moonmind.workflows.temporal import worker_runtime
from moonmind.workflows.temporal.activity_runtime import TemporalAgentRuntimeActivities
from moonmind.workflows.temporal.artifacts import (
    LocalTemporalArtifactStore,
    TemporalArtifactRepository,
    TemporalArtifactService,
)
from moonmind.workflows.temporal.container_job_backend import DockerContainerJobBackend
from moonmind.workflows.temporal.workflows.container_job import (
    MoonMindContainerJobWorkflow,
)
from tests.unit.security.test_scoped_egress import LeaseDocker, declaration, submission

pytestmark = [pytest.mark.asyncio, pytest.mark.integration, pytest.mark.integration_ci]


class DiagnosticDocker(LeaseDocker):
    def __init__(self, fault):
        super().__init__()
        self.fault = fault
        self.observations = 0
        self.image = "sha256:" + hashlib.sha256(b"diagnostic-fixture-image").hexdigest()
        self.container("moonmind-test-target")["State"] = {
            "Status": "running",
            "Running": True,
        }

    async def __call__(self, args):
        args = tuple(args)
        installed = egress.DEFAULT_EGRESS_PROFILE
        # Installed deployment evidence comes from the same daemon seam as
        # the job; the production attestor still validates all of these bytes.
        if args[:2] == ("network", "inspect") and args[-1] == installed.network_ref:
            return 0, b'{"Internal":true,"EnableIPv6":false}', b""
        if args[0] == "inspect" and args[-1] == installed.gateway_ref:
            return (
                0,
                json.dumps(
                    {
                        "labels": {
                            "moonmind.egress.enforcer": egress.ENFORCER_IMPLEMENTATION
                        },
                        "networks": dict.fromkeys(
                            egress._EXPECTED_GATEWAY_NETWORKS, {}
                        ),
                        "image": self.image,
                        "health": "healthy",
                    }
                ).encode(),
                b"",
            )
        if args[:3] == ("exec", installed.gateway_ref, "sha256sum"):
            return (
                0,
                "\n".join(
                    egress.EGRESS_FILE_DIGESTS[Path(path).name].removeprefix("sha256:")
                    + "  "
                    + path
                    for path in args[3:]
                ).encode(),
                b"",
            )
        if args[:2] == ("image", "inspect"):
            return 0, self.image.encode(), b""
        result = await super().__call__(args)
        target = self.container(args[-1])
        if target and target["Config"]["Labels"].get("moonmind.container_job"):
            if args[0] == "start":
                address = next(iter(target["NetworkSettings"]["Networks"].values()))[
                    "IPAddress"
                ]
                row = (
                    f"{datetime.now(UTC).timestamp()} 0 {address} TCP_DENIED/403 0 GET "
                    "http://fixture:do-not-retain@unrelated.example:8080/private?token=do-not-retain - HIER_NONE/- -\n"
                )
                self.files["/tmp/moonmind-access.log"] = row.encode() * 25
            if args[:3] == ("inspect", "--format", "{{json .State}}"):
                self.observations += 1
                if self.observations == 1:
                    # The first poll has read a valid denial. Fail only the
                    # next attestation, after its Activity result is recorded.
                    if self.fault == "tamper":
                        self.files["/tmp/moonmind-hosts"] += b"tampered\n"
                    elif self.fault == "unavailable":
                        self.proxy_healthy = False
                    else:
                        self.files["/tmp/moonmind-access.log"] = b""
                        target["State"].update(
                            Status="exited", Running=False, ExitCode=0
                        )
        return result


@pytest.mark.parametrize("fault", ["tamper", "unavailable", "log-loss"])
async def test_denials_survive_worker_reconstruction_failure_cleanup_and_replay(
    tmp_path, monkeypatch, record_property, fault
):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/evidence.db")
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    monkeypatch.setattr(worker_runtime, "get_async_session_context", maker)
    store = LocalTemporalArtifactStore(tmp_path / "artifacts")
    daemon = DiagnosticDocker(fault)
    policy = declaration()
    policy["destinations"] = policy["destinations"][-1:]
    backend_settings = resolve_container_backend_settings(
        {
            "MOONMIND_CONTAINER_BACKEND_EGRESS_POLICIES": json.dumps([policy]),
            "MOONMIND_CONTAINER_BACKEND_IMAGE_SOURCES": json.dumps(
                [{"sourceRef": "security-client", "image": "example/client:latest"}]
            ),
        }
    )
    request = submission(command=["true"], timeoutSeconds=60)
    workspace = (
        tmp_path
        / "temporal_sandbox"
        / request.spec.workspace_ref.workspace_id
        / request.spec.workspace_ref.relative_path
    )
    workspace.mkdir(parents=True)
    owner = OwnerIdentity(principalId="operator", principalType="system")
    principal = owner_artifact_principal(owner)
    invocations = []
    try:
        async with maker() as session:
            record, _ = await ContainerJobService(session).repository.create_or_replay(
                owner=owner, request=request
            )
            await session.commit()
            inp = ContainerJobWorkflowInput(
                jobId=record.job_id,
                owner=owner,
                request=request,
                observeIntervalSeconds=1,
            )

        operations = (
            "resolve_workspace",
            "acquire_image",
            "reconcile_container",
            "create_container",
            "start_container",
            "observe_container",
            "stop_container",
            "publish_evidence",
            "remove_container",
            "cleanup",
            "project_status",
            "repair_projection",
        )
        handlers = []
        for operation in operations:

            def bind(op):
                async def handler(payload):
                    # A new backend, Activity owner and DB session for every
                    # call proves retention needs no in-process evidence cache.
                    async with maker() as session:
                        artifacts = TemporalArtifactService(
                            TemporalArtifactRepository(session), store=store
                        )
                        backend = DockerContainerJobBackend(
                            workspace_root=tmp_path,
                            settings=backend_settings,
                            command_runner=daemon,
                            evidence_publisher=worker_runtime._container_job_evidence_publisher(
                                artifacts
                            ),
                            projection_writer=worker_runtime._container_job_projection_writer(
                                "docker", "diagnostic-fixture"
                            ),
                        )
                        runtime = TemporalAgentRuntimeActivities(
                            container_job_backend=backend
                        )
                        invocations.append(op)
                        return await getattr(runtime, "container_job_" + op)(payload)

                return handler

            handler = bind(operation)
            handler.__name__ = "container_job_" + operation
            handlers.append(activity.defn(name="container_job." + operation)(handler))

        queue = "scoped-diagnostics-" + uuid4().hex
        async with await WorkflowEnvironment.start_time_skipping() as env:
            async with AsyncExitStack() as stack:
                await stack.enter_async_context(
                    Worker(
                        env.client,
                        task_queue=queue,
                        workflows=[MoonMindContainerJobWorkflow],
                        workflow_runner=UnsandboxedWorkflowRunner(),
                    )
                )
                await stack.enter_async_context(
                    Worker(
                        env.client,
                        task_queue=settings.temporal.activity_agent_runtime_task_queue,
                        activities=handlers,
                    )
                )
                handle = await env.client.start_workflow(
                    MoonMindContainerJobWorkflow.run,
                    inp.model_dump(mode="json", by_alias=True, exclude_none=True),
                    id="scoped-diagnostics-" + uuid4().hex,
                    task_queue=queue,
                )
                result = await handle.result()
                history = await handle.fetch_history()

        assert result["state"] == "failed"
        assert result["publication"]["state"] == "succeeded"
        assert result["cleanup"]["state"] == "succeeded"
        assert not result["projectionRepairRequired"]
        assert invocations.count("observe_container") >= 2
        assert set(daemon.containers) == {"moonmind-test-target"}
        assert set(daemon.networks) == {"moonmind-test-lab"}

        # Independently reopen the production repository/store after every
        # worker is gone and the owned proxy/attachments have been removed.
        async with maker() as session:
            artifacts = TemporalArtifactService(
                TemporalArtifactRepository(session), store=store
            )
            _, data = await artifacts.read(
                artifact_id=result["publication"]["diagnosticsRef"], principal=principal
            )
            diagnostics = json.loads(data)
            network = diagnostics["egressEvidence"]
            assert network["profileRef"] == policy["policyRef"]
            assert network["coverage"] == "partial"
            assert network["deniedConnectionCount"] == 25
            assert "required_network_scope_denied" in network["limitations"]
            if fault != "log-loss":
                assert network["state"] == "unavailable"
                expected_error = (
                    "tampered" if fault == "tamper" else "process is unavailable"
                )
                assert expected_error in network["error"]
                assert expected_error in diagnostics["message"]
            else:
                assert diagnostics["exitCode"] == 0
            retained = network["retainedDenialObservation"]
            assert retained["deniedConnectionCount"] == 25
            _, snapshot_bytes = await artifacts.read(
                artifact_id=retained["artifactRef"], principal=principal
            )
            snapshot = parse_and_verify_conformance_evidence(
                snapshot_bytes, location="retained-denials"
            )
            assert snapshot["profileRef"] == policy["policyRef"]
            assert (
                snapshot["profileDigest"]
                == backend_settings.authorize_egress(request).digest
            )
            assert snapshot["deniedConnectionCount"] == 25
            assert len(snapshot["denialDiagnostics"]) == 20
            assert all(
                row == "denied unrelated.example:8080 TCP_DENIED/403"
                for row in snapshot["denialDiagnostics"]
            )
            assert (
                b"do-not-retain" not in snapshot_bytes
                and b"/private" not in snapshot_bytes
            )
            _, lifecycle_bytes = await artifacts.read(
                artifact_id=result["cleanup"]["diagnosticsRef"], principal=principal
            )
            lifecycle = parse_and_verify_conformance_evidence(
                lifecycle_bytes, location="cleanup"
            )
            assert lifecycle["retainedDenialObservation"] == retained
            assert (
                lifecycle["runtimeDiagnosticsRef"]
                == result["publication"]["diagnosticsRef"]
            )

        await Replayer(
            workflows=[MoonMindContainerJobWorkflow],
            workflow_runner=UnsandboxedWorkflowRunner(),
        ).replay_workflow(history)
        record_property(
            "scopedDenialEvidence",
            json.dumps(
                {
                    "fault": fault,
                    "jobId": inp.job_id,
                    "runtime": network,
                    "retainedObservation": snapshot,
                    "lifecycle": lifecycle,
                    "replayedHistoryEvents": len(history.events),
                },
                sort_keys=True,
            ),
        )
    finally:
        await engine.dispose()
