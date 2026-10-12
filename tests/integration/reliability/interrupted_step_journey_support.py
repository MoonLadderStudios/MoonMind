"""Controlled provider and replaceable worker process for MoonMind#4627.

``ControlledOmnigentServer`` stands in for the Omnigent server and the agent's
model provider only. It serves the stock HTTP routes the production bridge,
registration waiter and session cleanup call, and it observes the real Docker
host container instead of trusting its own bookkeeping: a host registers
only while its launched container runs, and a session projects its runner and
host offline once that container is gone. The agent's turn is scripted by the
journey and writes into the workspace the container actually mounts.

``python -m tests.integration.reliability.interrupted_step_journey_support
<config.json>`` runs the activity worker that hosts the agent steps in its own
OS process. Everything it knows comes from the config, PostgreSQL, the artifact
blob root, the workspace root and Docker, so a killed worker leaves nothing in
memory that its replacement could reuse.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from aiohttp import web

from moonmind.omnigent.host_services.docker_backend import DockerCommandBackend
from tests.helpers.omnigent_conformance import _ensure_loopback_proxy_bypass

HOST_OWNER = "moonmind-restartability-journey"
AGENT_ID = "opencode-native-ui"
HARNESS_ID = "opencode-native"
_WORKSPACE_TARGET = "/workspaces/run"


def _texts(value: Any) -> list[str]:
    """Return the text parts of a posted message, in order."""

    if isinstance(value, dict):
        text = value.get("text")
        own = [text] if isinstance(text, str) else []
        return own + [
            part
            for key, item in value.items()
            if key != "text"
            for part in _texts(item)
        ]
    if isinstance(value, list):
        return [part for item in value for part in _texts(item)]
    return []


@dataclass
class ControlledHost:
    """A launched host as Docker reports it."""

    host_id: str
    container: str
    image: str
    step_execution_id: str
    workspace: Path | None


@dataclass
class ControlledSession:
    session_id: str
    host_id: str
    items: list[dict[str, Any]] = field(default_factory=list)
    status: str = "idle"
    active_response_id: str | None = None
    messages: list[str] = field(default_factory=list)
    updates: asyncio.Condition = field(default_factory=asyncio.Condition)


@dataclass
class ControlledTurn:
    """What the scripted agent sees and can do for one accepted turn."""

    server: ControlledOmnigentServer
    session: ControlledSession
    host: ControlledHost
    text: str

    @property
    def logical_step_id(self) -> str:
        return self.host.step_execution_id.rsplit(":", 3)[-3]

    @property
    def execution_ordinal(self) -> int:
        return int(self.host.step_execution_id.rsplit(":", 1)[-1])

    async def work(self, call_id: str) -> None:
        """Record a started tool call: evidence that the turn is running."""

        await self.server._append(
            self.session,
            {
                "id": f"{call_id}-call",
                "type": "function_call",
                "status": "completed",
                "data": {"name": "bash", "call_id": call_id},
            },
        )

    async def finish(self, call_id: str, answer: str) -> None:
        await self.server._append(
            self.session,
            {
                "id": f"{call_id}-output",
                "type": "function_call_output",
                "status": "completed",
                "data": {"call_id": call_id, "output": "ok"},
            },
        )
        await self.server._append(
            self.session,
            {
                "id": f"{call_id}-answer",
                "type": "message",
                "status": "completed",
                "data": {
                    "role": "assistant",
                    "content": [{"type": "output_text", "text": answer}],
                },
            },
            status="idle",
        )


TurnScript = Callable[[ControlledTurn], Awaitable[None]]


class ControlledOmnigentServer:
    """Stock-shaped Omnigent routes over real Docker host observations."""

    def __init__(self, *, backend: DockerCommandBackend, script: TurnScript) -> None:
        self._backend = backend
        self._script = script
        self.hosts: dict[str, ControlledHost] = {}
        self.sessions: dict[str, ControlledSession] = {}
        self.requests: list[str] = []
        self.stream_opens: dict[str, int] = {}
        self._turns_by_session: dict[str, asyncio.Task[None]] = {}
        self.unknown_routes: list[str] = []
        self.turn_tasks: list[asyncio.Task[None]] = []
        self.turn_errors: list[BaseException] = []
        self._runner: web.AppRunner | None = None
        self.url = ""

    async def start(self) -> str:
        # The worker process inherits this environment, so its bridge reaches
        # the loopback stand-in directly.
        _ensure_loopback_proxy_bypass()

        @web.middleware
        async def record(request: web.Request, handler):
            self.requests.append(f"{request.method} {request.path_qs}")
            return await handler(request)

        app = web.Application(middlewares=[record])
        app.router.add_get("/v1/agents", self._agents)
        app.router.add_get("/v1/hosts/{host_id}", self._host)
        app.router.add_post("/v1/sessions", self._create_session)
        app.router.add_get("/v1/sessions/{session_id}", self._get_session)
        app.router.add_post("/v1/sessions/{session_id}/events", self._post_event)
        app.router.add_delete("/v1/sessions/{session_id}", self._delete_session)
        app.router.add_get("/v1/sessions/{session_id}/stream", self._stream)
        app.router.add_route("*", "/{tail:.*}", self._unknown)
        # A killed worker's streams never close cleanly; do not spend the
        # journey's budget waiting for them at shutdown.
        self._runner = web.AppRunner(app, shutdown_timeout=2.0)
        await self._runner.setup()
        site = web.TCPSite(self._runner, "127.0.0.1", 0)
        await site.start()
        port = site._server.sockets[0].getsockname()[1]  # type: ignore[union-attr]
        self.url = f"http://127.0.0.1:{port}"
        return self.url

    async def stop(self) -> None:
        for task in self.turn_tasks:
            task.cancel()
        await asyncio.gather(*self.turn_tasks, return_exceptions=True)
        if self._runner is not None:
            await self._runner.cleanup()

    async def _probe(self, container: str, template: str) -> str | None:
        code, out, _ = await self._backend.run(
            ["docker", "container", "inspect", "--format", template, container],
            check=False,
        )
        return out.strip() if code == 0 else None

    async def _running(self, container: str) -> bool:
        return await self._probe(container, "{{.State.Running}}") == "true"

    async def _discover(self, host_id: str) -> ControlledHost | None:
        code, out, _ = await self._backend.run(
            [
                "docker",
                "ps",
                "--filter",
                "label=moonmind.owner=generic-omnigent-host",
                "--format",
                "{{.Names}}",
            ],
            check=False,
        )
        for name in out.split() if code == 0 else ():
            raw_env = await self._probe(name, "{{json .Config.Env}}")
            if raw_env is None:
                continue
            env = dict(
                item.split("=", 1) for item in json.loads(raw_env) if "=" in item
            )
            if env.get("OMNIGENT_HOST_ID") != host_id:
                continue
            mounts = json.loads(await self._probe(name, "{{json .Mounts}}") or "[]")
            workspace = next(
                (
                    Path(mount["Source"])
                    for mount in mounts
                    if mount.get("Destination") == _WORKSPACE_TARGET
                ),
                None,
            )
            return ControlledHost(
                host_id=host_id,
                container=name,
                image=await self._probe(name, "{{.Config.Image}}") or "",
                step_execution_id=env.get("MOONMIND_STEP_EXECUTION_ID", ""),
                workspace=workspace,
            )
        return None

    async def _agents(self, _request: web.Request) -> web.Response:
        return web.json_response(
            {
                "object": "list",
                "data": [{"id": AGENT_ID, "name": AGENT_ID}],
                "has_more": False,
            }
        )

    async def _host(self, request: web.Request) -> web.Response:
        host_id = request.match_info["host_id"]
        host = self.hosts.get(host_id) or await self._discover(host_id)
        if host is None:
            return web.json_response({"error": "host not found"}, status=404)
        self.hosts[host_id] = host
        online = await self._running(host.container)
        return web.json_response(
            {
                "id": host_id,
                "host_id": host_id,
                "name": host.container,
                "owner": HOST_OWNER,
                "status": "online" if online else "offline",
                "configured_harnesses": {HARNESS_ID: "ready"},
            }
        )

    async def _create_session(self, request: web.Request) -> web.Response:
        payload = await request.json()
        host_id = str(payload.get("host_id") or payload.get("hostId") or "")
        if host_id not in self.hosts:
            return web.json_response({"error": "unknown host"}, status=422)
        session_id = f"session-{len(self.sessions) + 1}"
        self.sessions[session_id] = ControlledSession(session_id, host_id)
        return web.json_response({"id": session_id, "host_id": host_id})

    async def _snapshot(self, session: ControlledSession) -> dict[str, Any]:
        alive = await self._running(self.hosts[session.host_id].container)
        return {
            "id": session.session_id,
            "agent_id": AGENT_ID,
            "harness": HARNESS_ID,
            "status": session.status,
            "active_response_id": session.active_response_id,
            "host_id": session.host_id,
            "runner_id": f"runner-{session.session_id}",
            "runner_online": alive,
            "host_online": alive,
            "host_resumable": False,
            "items": list(session.items),
        }

    async def _get_session(self, request: web.Request) -> web.Response:
        session = self.sessions.get(request.match_info["session_id"])
        if session is None:
            return web.json_response({"error": "session not found"}, status=404)
        return web.json_response(await self._snapshot(session))

    async def _append(
        self,
        session: ControlledSession,
        item: dict[str, Any],
        *,
        status: str | None = None,
    ) -> None:
        async with session.updates:
            session.items.append(item)
            if status is not None:
                session.status = status
                if status == "idle":
                    session.active_response_id = None
            session.updates.notify_all()

    async def _post_event(self, request: web.Request) -> web.Response:
        session = self.sessions.get(request.match_info["session_id"])
        if session is None:
            return web.json_response({"error": "session not found"}, status=404)
        payload = await request.json()
        if payload.get("type") == "stop_session":
            running = self._turns_by_session.get(session.session_id)
            if running is not None:
                running.cancel()
            async with session.updates:
                session.status = "idle"
                session.active_response_id = None
                session.updates.notify_all()
            return web.json_response({"ok": True})
        if payload.get("type") != "message":
            return web.json_response({"ok": True})
        text = "\n".join(_texts(payload.get("data")))
        session.messages.append(text)
        ordinal = len(session.messages)
        session.active_response_id = f"resp-{session.session_id}-{ordinal}"
        await self._append(
            session,
            {
                "id": f"user-{ordinal}",
                "type": "message",
                "status": "completed",
                "data": {
                    "role": "user",
                    "content": [{"type": "input_text", "text": text}],
                },
            },
            status="running",
        )
        turn = ControlledTurn(self, session, self.hosts[session.host_id], text)
        task = asyncio.create_task(self._run_turn(turn))
        self.turn_tasks.append(task)
        self._turns_by_session[session.session_id] = task
        return web.json_response(
            {"pending_id": f"pending-{ordinal}", "item_id": f"user-{ordinal}"}
        )

    async def _run_turn(self, turn: ControlledTurn) -> None:
        try:
            await self._script(turn)
        except asyncio.CancelledError:
            raise
        except BaseException as exc:  # surfaced by the journey's assertions
            self.turn_errors.append(exc)

    async def _delete_session(self, request: web.Request) -> web.Response:
        return web.json_response({"ok": True})

    async def _stream(self, request: web.Request) -> web.StreamResponse:
        session = self.sessions.get(request.match_info["session_id"])
        if session is None:
            return web.json_response({"error": "session not found"}, status=404)
        self.stream_opens[session.session_id] = (
            self.stream_opens.get(session.session_id, 0) + 1
        )
        response = web.StreamResponse(
            status=200, headers={"Content-Type": "text/event-stream"}
        )
        await response.prepare(request)
        sent = 0
        while True:
            async with session.updates:
                try:
                    await asyncio.wait_for(session.updates.wait(), timeout=1.0)
                except TimeoutError:
                    pass
                items = session.items[sent:]
                sent = len(session.items)
            frames = [
                {"type": "response.output_item.done", "item": item} for item in items
            ] or [{"type": "session.heartbeat"}]
            try:
                for frame in frames:
                    await response.write(f"data: {json.dumps(frame)}\n\n".encode())
            except (ConnectionResetError, RuntimeError):
                return response

    async def _unknown(self, request: web.Request) -> web.Response:
        self.unknown_routes.append(f"{request.method} {request.path}")
        return web.json_response({"error": "not served"}, status=404)


def shorten_bridge_timing(
    *, quiet_seconds: float, host_loss_grace_seconds: float
) -> None:
    """Scale the production bridge's waits to a controlled provider's pace.

    The decisions stay production code; only their real-time windows shrink:
    the transcript quiet period before a finished turn is accepted, the
    snapshot poll interval, the grace before an offline host is lost, and the
    Activity heartbeat interval under the journey's short heartbeat timeout.
    """

    from moonmind.omnigent import execute

    execute._ACTIVITY_HEARTBEAT_INTERVAL_SECONDS = 1.0
    execute._SESSION_HOST_LOSS_GRACE_SECONDS = host_loss_grace_seconds
    execute._TERMINAL_RECONCILIATION_INTERVAL_SECONDS = quiet_seconds
    for function in (
        execute._await_marked_turn_terminal,
        execute._reconcile_inactive_marked_turn,
    ):
        function.__kwdefaults__.update(
            {
                "interval_seconds": quiet_seconds / 2,
                "quiet_period_seconds": quiet_seconds,
                "tool_only_quiet_period_seconds": quiet_seconds,
            }
        )


def record(path: str | Path, kind: str, **fields: Any) -> None:
    """Append one worker observation; the file outlives the worker process."""

    with open(path, "a", encoding="utf-8") as handle:
        handle.write(json.dumps({"kind": kind, "pid": os.getpid(), **fields}) + "\n")


def read_records(path: str | Path) -> list[dict[str, Any]]:
    target = Path(path)
    if not target.exists():
        return []
    return [json.loads(line) for line in target.read_text().splitlines() if line]


def journey_host_class(image_ref: str):
    from moonmind.omnigent.harness_platform.host_classes import HostClass

    return HostClass.model_validate(
        {
            "hostClassId": "omnigent-opencode",
            "version": 1,
            "imageRef": image_ref,
            "omnigentVersion": "0.11.1",
            "omnigentBuildDigest": "sha256:" + "2" * 64,
            "architectures": ["linux/amd64"],
            "declaredHarnessImplementations": [
                {
                    "harnessId": HARNESS_ID,
                    "implementationRef": "omnigent-harness-implementation:sha256:"
                    + "3" * 64,
                    "runtimeDependencies": [{"name": "opencode", "version": "1.18.11"}],
                }
            ],
            "integrationModes": ["native-server"],
            "materializerRefs": ["opencode-auth-json@1"],
            "features": {
                "workspaceBind": True,
                "restrictedEgress": True,
                "mountedSkills": True,
            },
            "runtime": {"uid": 1000, "gid": 1000, "home": "/home/app"},
        }
    )


def _journey_realizer(config: dict[str, Any], sessions: Any):
    """The production generic realizer as the replaced worker builds it.

    Durable owners are real and read only PostgreSQL, the artifact store, the
    workspace root and Docker: runtime bindings, host leases, canonical turn
    commands and cleanup authority, artifacts, the workspace publisher and
    materializer, the Docker host launcher, registration waiter and cleanup,
    and the Omnigent bridge. Provider leases and credentials are the
    controlled provider's: stateless, returning the same admitted lease and a
    real pre-created credential volume.
    """

    import subprocess
    from dataclasses import replace

    from moonmind.omnigent.bridge_artifacts import TemporalOmnigentArtifactGateway
    from moonmind.omnigent.bridge_store import OmnigentBridgeSessionStore
    from moonmind.omnigent.control_plane import OmnigentControlPlaneStore
    from moonmind.omnigent.control_plane.cleanup_authority import (
        CanonicalCleanupAuthority,
    )
    from moonmind.omnigent.control_plane.turn_commands import (
        CanonicalTurnCommandService,
    )
    from moonmind.omnigent.credential_materializers import CredentialRuntimeHandle
    from moonmind.omnigent.execute import run_omnigent_execution
    from moonmind.omnigent.harness_platform.host_classes import get_launch_policy
    from moonmind.omnigent.host_leases import DbOmnigentHostLeaseRepository
    from moonmind.omnigent.host_runtime import (
        GenericOmnigentHostRuntime,
        PreparedHostInputs,
    )
    from moonmind.omnigent.host_services.cleanup import DockerOmnigentHostCleanupService
    from moonmind.omnigent.host_services.github_credentials import (
        OmnigentGithubCredentialService,
    )
    from moonmind.omnigent.host_services.launcher import DockerOmnigentHostLauncher
    from moonmind.omnigent.host_services.registration import (
        OmnigentHostRegistrationService,
    )
    from moonmind.omnigent.host_services.runtime_scripts import (
        OmnigentRuntimeScriptService,
    )
    from moonmind.omnigent.host_services.workspace import OmnigentWorkspaceMaterializer
    from moonmind.omnigent.production import omnigent_transport_pool
    from moonmind.omnigent.provider_leases import AcquiredProviderLease
    from moonmind.omnigent.realizers.generic_host import GenericOmnigentHostRealizer
    from moonmind.omnigent.runtime_bindings import DbRuntimeBindingStore
    from moonmind.omnigent.session_cleanup import OmnigentSessionCleanupService
    from moonmind.omnigent.workspace_publication import (
        OmnigentWorkspacePublicationService,
    )
    from moonmind.provider_profiles.lease_client import (
        CredentialLease,
        CredentialLeasePurpose,
    )
    from moonmind.workflows.adapters.omnigent_client import OmnigentHttpClient
    from moonmind.workflows.temporal.runtime.workspace_locators import (
        SandboxWorkspaceRecord,
        SandboxWorkspaceRecordStore,
    )

    records = config["recordsPath"]
    workspace_root = Path(config["workspaceRoot"])
    backend = DockerCommandBackend()
    gateway = TemporalOmnigentArtifactGateway(session_factory=sessions)
    client = OmnigentHttpClient(base_url=config["serverUrl"], api_token=None)
    acquired = AcquiredProviderLease(
        slot="primary-model",
        provider_profile_ref="opencode-go-primary",
        capacity_scope_ref="provider-profile:opencode-go-primary",
        provider_lease_ref="provider-profile-lease:lease-1",
        credential_generation=4,
        lease=CredentialLease(
            profile_id="opencode-go-primary",
            runtime_id="opencode",
            lease_id="lease-1",
            owner_id="owner-1",
            purpose=CredentialLeasePurpose.EXECUTION_OMNIGENT,
        ),
    )

    class ProviderLeases:
        async def acquire_all(self, **kwargs):
            admitted = kwargs.get("admitted_capacity")
            if admitted is None:
                return (acquired,)
            return (
                replace(
                    acquired,
                    admission_epoch=admitted.admission_epoch,
                    admission_run_id=admitted.agent_run_run_id,
                ),
            )

        async def release_all(self, _leases):
            return None

    handle = CredentialRuntimeHandle.model_validate(
        {
            "credentialRuntimeRef": "credential-runtime:sha256:" + "d" * 64,
            "providerProfileRef": "opencode-go-primary",
            "providerLeaseRef": "provider-profile-lease:lease-1",
            "credentialGeneration": 4,
            "materializerRef": "opencode-auth-json@1",
            "attachments": [
                {
                    "kind": "volume",
                    "sourceRef": config["credentialVolume"],
                    "targetPath": "/home/app/.local/share/opencode",
                    "accessMode": "read-only",
                }
            ],
            "cleanupRef": "credential-cleanup:sha256:" + "e" * 64,
            "attestationRef": "artifact://credential-attestation",
        }
    )

    class Credentials:
        async def materialize_all(self, **_kwargs):
            return (handle,)

        async def load_cleanup_handles(self, *_args):
            return (handle,)

        async def cleanup_all(self, _handles):
            return ()

    class Skills:
        async def cleanup(self, _attachment):
            return None

    class GithubCredentials:
        async def validate_repository_intent(self, *, request, plan):
            await OmnigentGithubCredentialService(None).validate_repository_intent(
                request=request, plan=plan
            )

        async def cleanup(self, _attachment):
            return None

    class RuntimeEnvironment:
        def build(self, **_kwargs):
            return {}

    class Attestor:
        # Exact-host probes need a real Omnigent binary; the controlled
        # provider's host has none, so attestation evidence is recorded.
        async def attest(self, **_kwargs):
            return {
                "hostHarnessAttestationRef": "artifact://journey-host-attestation",
                "modelOptionAttestationRef": "artifact://journey-model-options",
            }

    async def daemon_command(argv, input_bytes=None):
        return await backend.run(argv, input_bytes=input_bytes, check=False)

    materializer = OmnigentWorkspaceMaterializer(
        command_runner=daemon_command,
        workspace_root=workspace_root,
        artifact_service=gateway,
    )

    def workspace_of(request) -> Path:
        locator = request.workspace_spec["workspaceLocator"]
        return (
            workspace_root
            / "temporal_sandbox"
            / locator["workspaceId"]
            / locator.get("relativePath", "repo")
        )

    class JourneyHostRuntime(GenericOmnigentHostRuntime):
        async def prepare(
            self,
            *,
            request,
            plan,
            host_class,
            launch_policy,
            repository_owner_ref,
            authority_sink=None,
            **_kwargs,
        ):
            await self.validate_repository_intent(request=request, plan=plan)
            step = request.step_execution
            record(
                records,
                "prepare",
                stepExecutionId=step.step_execution_id,
                restoreRef=request.workspace_spec.get("workspaceCheckpointRestoreRef"),
            )
            workspace = workspace_of(request)
            if not workspace.exists():
                # The sandbox owner checks out admitted source for the attempt.
                workspace.parent.mkdir(parents=True)
                await asyncio.to_thread(
                    subprocess.check_call,
                    ["git", "clone", "-q", config["origin"], str(workspace)],
                )
                SandboxWorkspaceRecordStore(workspace_root).ensure(
                    SandboxWorkspaceRecord(
                        workspace_id=request.workspace_spec["workspaceLocator"][
                            "workspaceId"
                        ],
                        workflow_id=step.workflow_id,
                        step_execution_id=step.step_execution_id,
                        relative_path="repo",
                    )
                )
            owner = workspace.stat()
            attachment = await self._workspace.materialize(
                request,
                mutation=plan.payload.workspaceMutation,
                runtime_uid=owner.st_uid,
                runtime_gid=owner.st_gid,
                plan=plan,
                repository_owner_ref=repository_owner_ref,
                authority_sink=authority_sink,
            )
            if authority_sink is not None:
                await authority_sink({"kind": "workspace", **attachment})
            skills = {
                "kind": "bind",
                "sourceRef": config["skillsDir"],
                "targetPath": "/opt/moonmind-skills",
                "accessMode": "read-only",
                "cleanupRef": "skill-cleanup:journey",
            }
            if authority_sink is not None:
                await authority_sink({"kind": "skills", **skills})
            return PreparedHostInputs(
                workspace_attachment=attachment,
                skill_attachment=skills,
                # The controlled provider's host process, in the OpenCode
                # runtime seed directory the entrypoint requires.
                tool_attachments=(
                    {
                        "kind": "volume",
                        "sourceRef": config["providerVolume"],
                        "targetPath": "/opt/moonmind/opencode-npm-cache",
                        "accessMode": "read-only",
                    },
                ),
                egress_attestation={
                    "networkRef": config["network"],
                    "profileRef": "egress-profile:journey",
                    "profileDigest": "sha256:" + "a" * 64,
                    "appliedRuleDigest": "sha256:" + "b" * 64,
                    "attestationRef": "artifact://journey-egress",
                },
            )

        async def cleanup_prepared(self, prepared):
            return None

    host_runtime = JourneyHostRuntime(
        launcher=DockerOmnigentHostLauncher(
            backend=backend,
            runtime_scripts=OmnigentRuntimeScriptService(),
            server_url=config["serverUrl"],
        ),
        workspace_service=materializer,
        skill_service=Skills(),
        tool_service=object(),
        github_credential_service=GithubCredentials(),
        egress_service=object(),
        runtime_environment_service=RuntimeEnvironment(),
        registration_waiter=OmnigentHostRegistrationService(
            client=client, expected_owner=HOST_OWNER, backend=backend
        ),
        host_attestor=Attestor(),
        cleanup_service=DockerOmnigentHostCleanupService(backend),
    )

    async def resolve_host(_plan):
        return (
            journey_host_class(config["plannedImage"]),
            get_launch_policy("omnigent-on-demand@1"),
        )

    bridge_store = OmnigentBridgeSessionStore(sessions)
    transport_pool = omnigent_transport_pool()

    async def session_driver(request, *, session_authority_sink, **continuation):
        return await run_omnigent_execution(
            request,
            artifact_gateway=gateway,
            run_store=bridge_store,
            session_authority_sink=session_authority_sink,
            transport_pool=transport_pool,
            **continuation,
        )

    publisher = OmnigentWorkspacePublicationService(
        workspace_root, artifact_gateway=gateway
    )
    save = publisher.save_request_workspace

    async def recorded_save(request):
        saved = await save(request)
        record(
            records,
            "save",
            stepExecutionId=request.step_execution.step_execution_id,
            archiveRef=saved.get("archiveRef"),
            archiveDigest=saved.get("archiveDigest"),
        )
        return saved

    publisher.save_request_workspace = recorded_save

    async def deployment_matches(_plan):
        return None

    canonical_store = OmnigentControlPlaneStore(sessions)
    return GenericOmnigentHostRealizer(
        runtime_binding_store=DbRuntimeBindingStore(sessions),
        provider_lease_coordinator=ProviderLeases(),
        credential_provisioning_service=Credentials(),
        host_lease_repository=DbOmnigentHostLeaseRepository(sessions),
        host_runtime=host_runtime,
        planned_host_resolver=resolve_host,
        session_driver=session_driver,
        session_cleanup_service=OmnigentSessionCleanupService(client),
        workspace_publisher=publisher,
        artifact_gateway=gateway,
        turn_command_service=CanonicalTurnCommandService(canonical_store),
        cleanup_authority=CanonicalCleanupAuthority(canonical_store),
        deployment_validator=deployment_matches,
        heartbeat_interval_seconds=0.5,
        heartbeat_ttl_seconds=60,
    )


async def run_activity_worker(config: dict[str, Any]) -> None:
    """Serve the agent-step Activity until this process is killed."""

    from types import SimpleNamespace

    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
    from temporalio.client import Client
    from temporalio.worker import Worker

    import api_service.db.base as db_base
    import moonmind.omnigent.realizers.registry as realizer_registry
    from moonmind.workflows.temporal.activity_catalog import (
        TemporalActivityCatalog,
        build_default_activity_catalog,
    )
    from moonmind.workflows.temporal.activity_runtime import (
        TemporalAgentRuntimeActivities,
        build_activity_bindings,
    )
    from moonmind.workflows.temporal.artifacts import (
        LocalTemporalArtifactStore,
        TemporalArtifactService,
    )
    from moonmind.workflows.temporal.data_converter import (
        MOONMIND_TEMPORAL_DATA_CONVERTER,
    )

    shorten_bridge_timing(
        quiet_seconds=config["bridgeQuietSeconds"],
        host_loss_grace_seconds=config["hostLossGraceSeconds"],
    )
    url = os.environ["MOONMIND_TEST_POSTGRES_URL"].replace(
        "postgresql://", "postgresql+asyncpg://", 1
    )
    engine = create_async_engine(
        url, connect_args={"server_settings": {"search_path": config["schema"]}}
    )
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    blob_root = Path(config["blobRoot"])
    TemporalArtifactService._build_store_from_settings = staticmethod(  # type: ignore[method-assign]
        lambda: LocalTemporalArtifactStore(blob_root)
    )
    db_base.async_session_maker = sessions
    realizer = _journey_realizer(config, sessions)
    realizer_registry.get_default_registry = lambda: SimpleNamespace(
        require=lambda _ref: realizer
    )
    catalog = build_default_activity_catalog()
    selected = TemporalActivityCatalog(
        activities=tuple(
            item
            for item in catalog.activities
            if item.activity_type == "integration.omnigent.profile_bound_execute"
        ),
        fleets=catalog.fleets,
    )
    (binding,) = build_activity_bindings(
        selected, agent_runtime_activities=TemporalAgentRuntimeActivities()
    )
    client = await Client.connect(
        os.environ["MOONMIND_TEST_TEMPORAL_ADDRESS"],
        namespace=config["namespace"],
        data_converter=MOONMIND_TEMPORAL_DATA_CONVERTER,
    )
    worker = Worker(
        client, task_queue=config["taskQueue"], activities=[binding.handler]
    )
    record(config["recordsPath"], "worker_ready")
    await worker.run()


def main() -> None:  # pragma: no cover - executed in the replaceable worker
    config = json.loads(Path(sys.argv[1]).read_text())
    os.environ.update(config["environment"])
    asyncio.run(run_activity_worker(config))


if __name__ == "__main__":  # pragma: no cover
    main()
