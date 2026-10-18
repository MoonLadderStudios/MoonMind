"""Codex delivers a durable resolved Skill through launch, turn and result.

The registered ``codex-profile-bound@1`` realizer from the production
composition runs the real profile-bound coordinator, SQL host and bridge
repositories, OAuth host runtime, Omnigent HTTP execution and durable
Temporal artifact gateway. The resolved Skill snapshot is written the way
``agent_skill.resolve`` writes it, under another principal, so the run must
dereference it from durable storage.

External transports are controlled substitutes, named here explicitly:

* Docker: static Compose and on-demand Docker commands are recorded instead
  of run. Their fake reads the Skill through the emitted mounts, so a dropped
  or misconfigured Skills mount fails the journey. On-demand cleanup removes
  only its recorded container and run-owned volumes, preserving the OAuth home.
* Provider: a fake Omnigent server (``FakeOmnigentServer``) plays the Codex
  turn. It returns a workspace file carrying the digest of the Skill the
  launched host received, so the retrieved result proves delivery.

The Provider Profile manager transport grants one isolated test lease; the
production lease client and release owner remain in the journey. No provider
credentials, login or paid model turn are involved.
"""

from __future__ import annotations

import hashlib
import json
import platform
import re
import subprocess
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import pytest
import pytest_asyncio
import yaml
from aiohttp import web
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from api_service.db.models import (
    Base,
    ManagedAgentProviderProfile,
    ProviderCredentialSource,
    ProviderProfileAuthMethod,
    ProviderProfileAuthState,
    RuntimeMaterializationMode,
)
from api_service.services.omnigent_policies import seed_bootstrap_policies
from moonmind.config.settings import settings
from moonmind.omnigent.bridge_artifacts import TemporalOmnigentArtifactGateway
from moonmind.omnigent.bridge_store import OmnigentBridgeSessionStore
from moonmind.omnigent.harness_platform.credential_bindings import create_binding_set
from moonmind.omnigent.harness_platform.planner import compile_execution_plan
from moonmind.omnigent.harness_platform.stores import DbExecutionPlanStore
from moonmind.omnigent.oauth_host_runtime import OmnigentOAuthHostRuntime
from moonmind.omnigent.settings import OMNIGENT_RUNTIME_ACTIVE_SKILLS_DIR
from moonmind.omnigent.stock_agents import CODEX_STOCK_AGENT_NAME
from moonmind.provider_profiles.lease_client import ProviderProfileLeaseClient
from moonmind.schemas.agent_runtime_models import (
    AgentExecutionRequest,
    AgentRuntimeStepExecutionLaunch,
    OmnigentExecutionPlanBinding,
)
from moonmind.security.egress import (
    CONTROL_PLANE_NETWORK_REF,
    DEFAULT_EGRESS_PROFILE,
    EGRESS_FILE_DIGESTS,
    ENFORCER_IMPLEMENTATION,
    OMNIGENT_EGRESS_PROFILE,
)
from moonmind.workflows.adapters.omnigent_client import OmnigentHttpClient
from tests.helpers.omnigent_conformance import (
    FakeOmnigentServer,
    start_fake_omnigent_server,
)
from tests.unit.omnigent.test_plan_and_realizer_dispatch import (
    _DURABLE_SKILL_NAME,
    _codex_plan_inputs,
    _durable_skill_payload,
    _write_durable_skill_snapshot,
)

pytestmark = [pytest.mark.asyncio, pytest.mark.integration, pytest.mark.integration_ci]

PROFILE_ID = "codex"
AUTH_VOLUME = "codex_auth_volume"
SERVER_IMAGE = "ghcr.io/omnigent-ai/omnigent-server@sha256:" + "8" * 64
HOST_IMAGE = "ghcr.io/moonladderstudios/omnigent-host-moonmind@sha256:" + "9" * 64
RESULT_PATH = "skill-delivery.txt"


@pytest_asyncio.fixture()
async def session_factory(tmp_path, monkeypatch):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/codex-journey.db")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr("api_service.db.base.async_session_maker", factory)
    monkeypatch.setattr(settings.workflow, "temporal_artifact_backend", "local_fs")
    monkeypatch.setattr(
        settings.workflow, "temporal_artifact_root", str(tmp_path / "artifacts")
    )
    try:
        yield factory
    finally:
        await engine.dispose()


class CodexTurnServer(FakeOmnigentServer):
    """Fake Omnigent server whose Codex turn reports the Skill it was given."""

    def __init__(self, *, host_id: str) -> None:
        super().__init__(supports_diff=False)
        self.host_id = host_id
        self.host_name = "omnigent-host-codex"
        self.delivered_skill: bytes | None = None

    async def list_agents(self, request: web.Request) -> web.Response:
        return web.json_response(
            {
                "object": "list",
                "data": [{"id": "agent-codex", "name": CODEX_STOCK_AGENT_NAME}],
                "has_more": False,
            }
        )

    async def list_hosts(self, request: web.Request) -> web.Response:
        return web.json_response(
            {
                "hosts": [
                    {
                        "id": self.host_id,
                        "name": self.host_name,
                        "status": "online",
                        "harnesses": ["codex-native"],
                    }
                ]
            }
        )

    async def list_workspace_files(self, request: web.Request) -> web.Response:
        return web.json_response({"items": [{"path": RESULT_PATH, "type": "file"}]})

    async def get_workspace_file(self, request: web.Request) -> web.Response:
        if request.match_info["path"] != RESULT_PATH or self.delivered_skill is None:
            return web.Response(status=404, text="File not found")
        digest = hashlib.sha256(self.delivered_skill).hexdigest()
        return web.Response(
            body=f"skill sha256:{digest}\n".encode(), content_type="text/plain"
        )


_COMPOSE_VARIABLE = re.compile(r"\$\{(\w+)(?::-([^}]*))?\}")


def _read_through_compose_mount(
    compose_file: Path, service: str, env: Mapping[str, str], container_path: str
) -> bytes:
    """Read ``container_path`` through the service's rendered bind mounts."""

    volumes = yaml.safe_load(compose_file.read_text(encoding="utf-8"))["services"][
        service
    ].get("volumes", [])
    for volume in volumes:
        source, target, *options = _COMPOSE_VARIABLE.sub(
            lambda match: env.get(match[1]) or match[2] or "", str(volume)
        ).split(":")
        if container_path.startswith(target.rstrip("/") + "/"):
            assert options == ["ro"], f"{target} must be mounted read-only"
            host_root = compose_file.parent / source
            return (host_root / container_path[len(target) :].lstrip("/")).read_bytes()
    raise AssertionError(f"{service} mounts nothing at {container_path}")


class StaticCodexDocker:
    """Record the static Codex host's Docker commands instead of running them."""

    def __init__(
        self,
        *,
        workflow_id: str,
        server: CodexTurnServer,
        run_command: Any,
        events: list[tuple[str, str]],
    ) -> None:
        self.server = server
        # Non-Docker commands (the trusted Git clone) run for real.
        self.run_command = run_command
        self.commands: list[tuple[str, ...]] = []
        # Shared with the artifact gateway to order host stops against writes.
        self.events = events
        self.compose_up_env: dict[str, str] | None = None
        self.container_running = False
        self.server_container_id = hashlib.sha256(
            f"server:{workflow_id}".encode()
        ).hexdigest()
        self.host_container_id = hashlib.sha256(
            f"host:{workflow_id}".encode()
        ).hexdigest()
        self.gateway_image_digest = "sha256:" + "d" * 64
        self.gateway_networks = {
            DEFAULT_EGRESS_PROFILE.network_ref: {},
            "moonmind_sandbox-egress-network": {},
            OMNIGENT_EGRESS_PROFILE.network_ref: {},
            CONTROL_PLANE_NETWORK_REF: {},
        }
        applied_rule_payload = {
            "profileDigest": OMNIGENT_EGRESS_PROFILE.digest,
            "configDigest": "sha256:"
            + hashlib.sha256(
                json.dumps(
                    EGRESS_FILE_DIGESTS, sort_keys=True, separators=(",", ":")
                ).encode()
            ).hexdigest(),
            "gatewayImageDigest": self.gateway_image_digest,
            "internal": True,
            "ipv6": False,
            "idleSeconds": OMNIGENT_EGRESS_PROFILE.idle_seconds,
            "gatewayNetworks": sorted(self.gateway_networks),
            "enforcer": ENFORCER_IMPLEMENTATION,
        }
        self.applied_rule_digest = (
            "sha256:"
            + hashlib.sha256(
                json.dumps(
                    applied_rule_payload, sort_keys=True, separators=(",", ":")
                ).encode()
            ).hexdigest()
        )
        self.client_address = "172.31.0.19"
        self.architecture = {"aarch64": "arm64", "x86_64": "amd64"}.get(
            platform.machine().lower(), platform.machine().lower()
        )

    def _launch_host(self, args: tuple[str, ...], env: dict[str, str]) -> None:
        self.compose_up_env = dict(env)
        self.container_running = True
        self.server.delivered_skill = _read_through_compose_mount(
            Path(args[args.index("-f") + 1]),
            args[-1],
            env,
            f"{OMNIGENT_RUNTIME_ACTIVE_SKILLS_DIR}/{_DURABLE_SKILL_NAME}/SKILL.md",
        )

    def _stop_host(self) -> None:
        self.container_running = False
        self.events.append(("host_stop", self.host_container_id))

    async def __call__(self, *args: str, env=None, **kwargs) -> tuple[int, str, str]:
        if args[0] != "docker":
            return await self.run_command(*args, env=env, **kwargs)
        self.commands.append(tuple(args))
        joined = f" {' '.join(args)} "
        if args[:3] == ("docker", "network", "inspect"):
            return (0, json.dumps({"Internal": True, "EnableIPv6": False}), "")
        if args[:2] == ("docker", "compose"):
            if " up " in joined:
                self._launch_host(args, dict(env or {}))
            elif " stop " in joined:
                self._stop_host()
            elif " ps " in joined:
                identity = (
                    self.server_container_id
                    if args[-1] == "omnigent"
                    else self.host_container_id
                )
                return (0, f"{identity}\n", "")
            return (0, "", "")
        if args[:2] == ("docker", "stop") and args[-1] == self.host_container_id:
            self._stop_host()
            return (0, "", "")
        if args[:2] == ("docker", "inspect"):
            return self._inspect(args[3], args[4])
        if args[:3] == ("docker", "image", "inspect"):
            if args[4].startswith('{"repoDigests"'):
                return (
                    0,
                    json.dumps(
                        {
                            "repoDigests": [SERVER_IMAGE],
                            "architecture": self.architecture,
                        }
                    ),
                    "",
                )
            return (0, json.dumps(self.architecture), "")
        if args[:3] == ("docker", "exec", OMNIGENT_EGRESS_PROFILE.gateway_ref):
            if args[3] == "sha256sum":
                return (
                    0,
                    "".join(
                        f"{EGRESS_FILE_DIGESTS[p.rsplit('/', 1)[-1]].removeprefix('sha256:')}  {p}\n"
                        for p in args[4:]
                    ),
                    "",
                )
            if args[3] == "cat":
                return (0, "", "")
        if args == (
            "docker",
            "exec",
            self.host_container_id,
            "/opt/moonmind/check-runner-projections.sh",
        ):
            return (0, "", "")
        raise AssertionError(f"unexpected host command: {args}")

    def _inspect(self, format_value: str, identity: str) -> tuple[int, str, str]:
        if identity == OMNIGENT_EGRESS_PROFILE.gateway_ref:
            return (
                0,
                json.dumps(
                    {
                        "labels": {"moonmind.egress.enforcer": ENFORCER_IMPLEMENTATION},
                        "networks": self.gateway_networks,
                        "image": self.gateway_image_digest,
                        "health": "healthy",
                    }
                ),
                "",
            )
        if identity == self.server_container_id:
            if format_value == "{{json .Config.Image}}":
                return (0, json.dumps(SERVER_IMAGE), "")
            if format_value == "{{json .Image}}":
                return (0, json.dumps("sha256:" + "8" * 64), "")
        if identity == self.host_container_id:
            if format_value.startswith('{"labels"'):
                return (
                    0,
                    json.dumps(
                        {
                            "labels": {
                                "moonmind.egress.profile": OMNIGENT_EGRESS_PROFILE.ref,
                                "moonmind.egress.profile_digest": OMNIGENT_EGRESS_PROFILE.digest,
                                "moonmind.egress.applied_rule_digest": self.applied_rule_digest,
                            },
                            "networks": {
                                OMNIGENT_EGRESS_PROFILE.network_ref: {
                                    "NetworkID": "network-" + identity[:16],
                                    "EndpointID": "endpoint-" + identity[:16],
                                    "IPAddress": self.client_address,
                                }
                            },
                            "imageRef": HOST_IMAGE,
                            "image": HOST_IMAGE.rsplit("@", 1)[-1],
                        }
                    ),
                    "",
                )
            if format_value == "{{.State.Running}}":
                return (0, "true\n" if self.container_running else "false\n", "")
            if format_value.startswith("{{range .Mounts}}"):
                # The selected profile's own auth volume is attached by name.
                assert f'eq .Name "{AUTH_VOLUME}"' in format_value
                assert 'eq .Destination "/home/app/.codex"' in format_value
                return (0, "valid\n", "")
        raise AssertionError(f"unexpected Docker inspect: {format_value} {identity}")


class OnDemandCodexDocker(StaticCodexDocker):
    """Exercise the emitted on-demand commands and their owned resource state."""

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self.container_name: str | None = None
        self.container_present = False
        self.host_labels: dict[str, str] = {}
        self.mounts: dict[str, dict[str, str]] = {}
        self.owned_volumes: set[str] = set()
        self.launch_env: dict[str, str] = {}

    async def __call__(self, *args: str, env=None, **kwargs):
        if args[:2] == ("docker", "run"):
            self.commands.append(tuple(args))
            mounts = {
                item["dst"]: item
                for index, arg in enumerate(args[:-1])
                if arg == "--mount"
                for item in [
                    dict(
                        part.partition("=")[::2] for part in args[index + 1].split(",")
                    )
                ]
            }
            if set(mounts) == {"/home/app/.cache"}:
                # Cleanup fences the lease-private gh cache before removal.
                assert not kwargs.get("input_bytes")
                assert mounts["/home/app/.cache"]["src"] in self.owned_volumes
                assert json.loads(args[-1])["ownerRef"] == (
                    "host-lease:" + self.host_labels["moonmind.host_lease_id"]
                )
                return (0, "", "")
            assert mounts["/home/app/.codex"]["src"] == AUTH_VOLUME
            if "--rm" in args:
                assert args[-2:] == ("/opt/moonmind/init-oauth-host.sh", HOST_IMAGE)
                return (0, "", "")
            assert (
                "-d" in args and args[-1] == "/opt/moonmind/start-codex-oauth-host.sh"
            )
            self.container_name = args[args.index("--name") + 1]
            self.server.host_name = self.container_name
            self.container_present = self.container_running = True
            self.mounts = mounts
            self.host_labels = dict(
                args[index + 1].split("=", 1)
                for index, arg in enumerate(args[:-1])
                if arg == "--label"
            )
            self.launch_env = dict(
                args[index + 1].split("=", 1)
                for index, arg in enumerate(args[:-1])
                if arg == "--env" and "=" in args[index + 1]
            )
            self.owned_volumes = {
                mount["src"]
                for mount in mounts.values()
                if mount["type"] == "volume" and mount["src"] != AUTH_VOLUME
            }
            skills = mounts[OMNIGENT_RUNTIME_ACTIVE_SKILLS_DIR]
            assert skills["type"] == "bind" and "readonly" in skills
            self.server.delivered_skill = (
                Path(skills["src"]) / _DURABLE_SKILL_NAME / "SKILL.md"
            ).read_bytes()
            return (0, self.host_container_id, "")
        if args[:2] == ("docker", "ps"):
            self.commands.append(tuple(args))
            return (0, "", "")  # The synchronous --rm initializer exited.
        if args[:2] == ("docker", "rm"):
            self.commands.append(tuple(args))
            assert args[-1] == self.container_name
            self.container_present = self.container_running = False
            return (0, "", "")
        if args[:3] == ("docker", "volume", "rm"):
            self.commands.append(tuple(args))
            assert args[-1] != AUTH_VOLUME
            self.owned_volumes.remove(args[-1])
            return (0, "", "")
        if args[:3] == ("docker", "volume", "inspect"):
            self.commands.append(tuple(args))
            return (
                (0, args[-1], "")
                if args[-1] in self.owned_volumes
                else (1, "", "absent")
            )
        if args[:2] == ("docker", "stop") and args[-1] == self.container_name:
            self.commands.append(tuple(args))
            self._stop_host()
            return (0, "", "")
        if args[:2] == ("docker", "exec") and args[2] == self.container_name:
            self.commands.append(tuple(args))
            if args[3:6] == ("git", "-C", "/workspaces/run"):
                return await self.run_command(
                    "git", "-C", self.mounts["/workspaces/run"]["src"], *args[6:]
                )
            assert args[3:] == ("/opt/moonmind/check-runner-projections.sh",) or args[
                3:5
            ] == ("bash", "-lc")
            return (0, "", "")
        return await super().__call__(*args, env=env, **kwargs)

    def _inspect(self, format_value: str, identity: str):
        if identity not in (
            self.server_container_id,
            OMNIGENT_EGRESS_PROFILE.gateway_ref,
        ):
            if identity != self.container_name or not self.container_present:
                return (1, "", "absent")
            if format_value == "{{.Id}}":
                return (0, self.host_container_id, "")
            if format_value == '{{index .Config.Labels "moonmind.host_lease_id"}}':
                return (0, self.host_labels["moonmind.host_lease_id"], "")
            identity = self.host_container_id
        return super()._inspect(format_value, identity)


class RecordingArtifactGateway(TemporalOmnigentArtifactGateway):
    """The durable gateway, recording each committed write in shared order."""

    def __init__(self, session_factory, *, events: list[tuple[str, str]]) -> None:
        super().__init__(session_factory)
        self.events = events

    async def write_bytes(self, **kwargs: Any) -> str:
        ref = await super().write_bytes(**kwargs)
        self.events.append(("artifact_write", ref))
        return ref


class HermeticProviderLeases:
    """Provider Profile manager transport: one lease, verified release."""

    def __init__(self, events: list[tuple[str, str]]) -> None:
        self.events = events
        self.acquired: list[str] = []
        self.signals: list[tuple[str, dict[str, Any]]] = []

    async def get_client(self):
        class _Client:
            async def start_workflow(self, *_args, **_kwargs):
                return None

        return _Client()

    async def get_workflow_handle(self, _workflow_id):
        leases = self

        class _Handle:
            async def query(self, name):
                assert name == "get_state"
                return {
                    "cleanup_obligations": [
                        {
                            "lease_id": lease_id,
                            "profile_id": PROFILE_ID,
                            "fencing_generation": 7,
                            "claim_id": f"{lease_id}:7",
                            "runId": "run-admitted-1",
                            "evidenceIdentity": "evidence-admitted-1",
                        }
                        for lease_id in leases.acquired
                    ]
                }

        return _Handle()

    async def update_workflow(self, _workflow_id, _name, payload):
        lease_id = (
            f"provider-lease:{payload['execution_profile_ref']}:"
            f"{payload['requester_workflow_id']}"
        )
        self.acquired.append(lease_id)
        return {
            "profile_id": payload["execution_profile_ref"],
            "lease_id": lease_id,
            "already_held": False,
        }

    async def signal_workflow(self, _workflow_id, name, payload):
        self.signals.append((name, dict(payload)))
        self.events.append((name, payload["lease_id"]))


async def _seed_codex_profile(session_factory) -> None:
    async def resolve_image(image_ref: str) -> str:
        return SERVER_IMAGE if "server" in image_ref else HOST_IMAGE

    async with session_factory() as session:
        session.add(
            ManagedAgentProviderProfile(
                profile_id=PROFILE_ID,
                runtime_id="codex_cli",
                provider_id="openai",
                credential_source=ProviderCredentialSource.OAUTH_VOLUME,
                runtime_materialization_mode=RuntimeMaterializationMode.OAUTH_HOME,
                volume_ref=AUTH_VOLUME,
                volume_mount_path="/home/app/.codex",
                max_parallel_runs=1,
                credential_generation=3,
                enabled=True,
                auth_state=ProviderProfileAuthState.CONNECTED,
                last_auth_method=ProviderProfileAuthMethod.OAUTH_VOLUME,
                clear_env_keys=[
                    "OPENAI_API_KEY",
                    "OPENAI_BASE_URL",
                    "OPENAI_ORG_ID",
                    "OPENAI_PROJECT",
                    "MINIMAX_API_KEY",
                ],
            )
        )
        await session.commit()
        await seed_bootstrap_policies(session, image_resolver=resolve_image)


async def _codex_plan(session_factory, skillset_ref: str, launch_policy: str):
    """Compile the Codex plan from persisted launch authority, as the compiler does."""

    from api_service.services.omnigent_execution_plan_service import (
        _resolve_runtime_policy_snapshot,
        persist_json_artifact,
    )
    from moonmind.omnigent.profile_bound_execution import (
        _compile_persisted_effective_launch,
        compile_follow_up_retrieval_policy,
    )
    from moonmind.workflows.temporal.artifacts import (
        TemporalArtifactRepository,
        TemporalArtifactService,
    )

    policy_snapshot = await _resolve_runtime_policy_snapshot(
        policy_ref=launch_policy, session_factory=session_factory, db_session=None
    )
    effective_launch = _compile_persisted_effective_launch(
        policy_snapshot,
        provider_profile_id=PROFILE_ID,
        follow_up_retrieval=compile_follow_up_retrieval_policy(),
    )
    async with session_factory() as session:
        service = TemporalArtifactService(TemporalArtifactRepository(session))
        policy_ref, policy_digest = await persist_json_artifact(
            artifact_service=service,
            principal="system",
            artifact_class="omnigent.launch_policy_snapshot",
            payload=policy_snapshot,
        )
        launch_ref, launch_digest = await persist_json_artifact(
            artifact_service=service,
            principal="system",
            artifact_class="omnigent.effective_launch_snapshot",
            payload=effective_launch,
        )
    architecture = str((effective_launch.get("architectures") or [""])[0])
    if "/" not in architecture:
        architecture = f"linux/{architecture}"
    inputs = _codex_plan_inputs(launch_policy)
    # The plan admits the exact Skill set the request delivers.
    inputs["resolved_skills"] = inputs["resolved_skills"].model_copy(
        update={"resolvedSkillSetRef": f"artifact:{skillset_ref}"}
    )
    inputs["host_class"] = inputs["host_class"].model_copy(
        update={"imageRef": HOST_IMAGE, "architectures": [architecture]}
    )
    inputs["credential_binding_set"] = create_binding_set(
        bindingSetId="codex",
        version=1,
        bindings={
            "primary-model": {
                "providerProfileRef": PROFILE_ID,
                "materializerRef": "codex-oauth-home@1",
            }
        },
    )
    plan = compile_execution_plan(
        **inputs,
        policy_snapshot_ref=f"artifact:{policy_ref}",
        policy_snapshot_digest=policy_digest,
        effective_launch_snapshot_ref=f"artifact:{launch_ref}",
        effective_launch_snapshot_digest=launch_digest,
        host_image_ref=str(effective_launch["hostImageRef"]),
        omnigent_host_build_digest=inputs["host_class"].omnigentBuildDigest,
        host_architecture=architecture,
    )
    return await DbExecutionPlanStore(session_factory).persist(plan)


def _seeded_remote(tmp_path: Path) -> Path:
    """A local bare repository with one commit on ``main``."""

    remote = tmp_path / "remote.git"
    seed = tmp_path / "seed"
    seed.mkdir()
    git = [
        "git",
        "-c",
        "user.email=journey@moonmind.test",
        "-c",
        "user.name=MoonMind Journey",
        "-c",
        "init.defaultBranch=main",
    ]
    subprocess.run([*git, "init", "--bare", str(remote)], check=True)
    for args in (
        ("init",),
        ("checkout", "-B", "main"),
        ("commit", "--allow-empty", "-m", "seed"),
        ("push", str(remote), "main"),
    ):
        subprocess.run([*git, *args], cwd=seed, check=True, capture_output=True)
    return remote


def _request(
    plan,
    *,
    workflow_id: str,
    skillset_ref: str,
    repository: Path,
    explicit_target: bool = False,
) -> AgentExecutionRequest:
    step_execution_id = f"{workflow_id}:run:implement:execution:1"
    workspace_id = hashlib.sha256(
        f"{workflow_id}:{step_execution_id}".encode()
    ).hexdigest()[:24]
    return AgentExecutionRequest(
        agentKind="external",
        agentId="omnigent",
        executionProfileRef=PROFILE_ID,
        correlationId=workflow_id,
        idempotencyKey=f"{workflow_id}:implement",
        resolvedSkillsetRef=skillset_ref,
        omnigentExecutionPlan=OmnigentExecutionPlanBinding(
            planRef=plan.planRef,
            planDigest="sha256:" + plan.planRef.rsplit(":", 1)[-1],
            planArtifactRef="art_plan",
            taskInputSnapshotRef="art_input",
            taskInputSnapshotDigest="sha256:" + "f" * 64,
        ),
        stepExecution=AgentRuntimeStepExecutionLaunch(
            workflowId=workflow_id,
            runId="run",
            logicalStepId="implement",
            executionOrdinal=1,
            stepExecutionId=step_execution_id,
            runtimeContextPolicy="fresh_agent_run",
        ),
        workspaceSpec={
            "workspaceLocator": {
                "kind": "sandbox",
                "workspaceId": workspace_id,
                "relativePath": "repo",
            },
            "repository": str(repository),
            "startingBranch": "main",
            "targetBranch": "main",
        },
        parameters={
            "publishMode": "none",
            "selectedSkill": _DURABLE_SKILL_NAME,
            "omnigent": {
                "launchPolicyRef": plan.payload.launchPolicyRef,
                **(
                    {"executionTargetRef": "omnigent-codex@1"}
                    if explicit_target
                    else {}
                ),
                "prompt": {"text": "Do the implement step."},
                "session": {},
            },
        },
    )


@pytest.mark.parametrize(
    ("launch_policy", "explicit_target", "retained_binding"),
    [
        ("codex-static@1", False, None),
        ("codex-static@1", True, None),
        ("codex-on-demand@1", False, None),
        ("codex-on-demand@1", True, None),
        ("codex-on-demand@1", False, "idle"),
        ("codex-on-demand@1", False, "busy"),
        ("codex-on-demand@1", False, "idle_static"),
        ("codex-on-demand@1", False, "busy_static"),
        ("codex-on-demand@1", False, "valid"),
        ("codex-static@1", False, "valid"),
    ],
)
@pytest.mark.parametrize("auth_mode", ["disabled", "oidc"])
async def test_registered_codex_realizer_delivers_durable_skill_through_turn_and_result(
    session_factory,
    production_transport_pool,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    auth_mode: str,
    launch_policy: str,
    explicit_target: bool,
    retained_binding: str | None,
) -> None:
    from moonmind.config.settings import settings
    from moonmind.omnigent import production, profile_bound_execution

    # The Skill set is owned by its producer, so under authentication only
    # the Step Execution's admitted scope can read it (#4633).
    monkeypatch.setattr(settings.oidc, "AUTH_PROVIDER", auth_mode)

    workflow_id = "mm:codex-durable-skill-journey"
    host_id = "host-" + hashlib.sha256(workflow_id.encode()).hexdigest()[:16]
    server = CodexTurnServer(host_id=host_id)
    running = await start_fake_omnigent_server(server)
    try:
        for key, value in {
            "OMNIGENT_ENABLED": "1",
            "OMNIGENT_SERVER_URL": running.base_url,
            "MOONMIND_OMNIGENT_HOST_SERVER_URL": running.base_url,
            "MOONMIND_OMNIGENT_EXPECTED_HOST_OWNER": "codex-journey-owner",
            "OMNIGENT_IMAGE_REF": SERVER_IMAGE,
            "OMNIGENT_SHARED_HOST_IMAGE_REF": HOST_IMAGE,
            "OMNIGENT_OPENCODE_HOST_IMAGE_REF": (
                "ghcr.io/moonladderstudios/omnigent-host-opencode@sha256:" + "a" * 64
            ),
        }.items():
            monkeypatch.setenv(key, value)
        monkeypatch.setattr(production, "generic_host_enabled", lambda: True)
        monkeypatch.delenv("OMNIGENT_CODEX_HOST_LAUNCH_PROFILE", raising=False)
        monkeypatch.delenv("WORKFLOW_DOCKER_DAEMON_MODE", raising=False)
        monkeypatch.delenv("WORKFLOW_WORKSPACE_DAEMON_ROOT", raising=False)

        await _seed_codex_profile(session_factory)
        skillset_ref = await _write_durable_skill_snapshot(
            session_factory, workflow_id, "run"
        )
        events: list[tuple[str, str]] = []
        durable_gateway = RecordingArtifactGateway(session_factory, events=events)
        leases = HermeticProviderLeases(events)
        runtime = OmnigentOAuthHostRuntime(
            client=OmnigentHttpClient(base_url=running.base_url, api_token="journey"),
            workspace_root=tmp_path / "workspaces",
            repository_source_root=tmp_path,
        )
        docker_class = (
            OnDemandCodexDocker if "on-demand" in launch_policy else StaticCodexDocker
        )
        docker = docker_class(
            workflow_id=workflow_id,
            server=server,
            run_command=runtime._run,
            events=events,
        )
        runtime._run = docker  # type: ignore[method-assign]
        runtime._align_workspace_ownership = (  # type: ignore[method-assign]
            lambda *_args, **_kwargs: None
        )

        production_coordinator = (
            profile_bound_execution.OmnigentProfileBoundExecutionCoordinator
        )
        coordinators: list[Any] = []

        def coordinator_with_hermetic_transports(**kwargs):
            # Only the Docker and Provider Profile manager transports are
            # replaced; the realizer still supplies its durable gateway, run
            # store, plan, turn commands and real Omnigent execution runner.
            kwargs["lease_client"] = ProviderProfileLeaseClient(leases)
            kwargs["host_runtime"] = runtime
            coordinator = production_coordinator(**kwargs)
            coordinators.append(coordinator)
            return coordinator

        monkeypatch.setattr(
            profile_bound_execution,
            "OmnigentProfileBoundExecutionCoordinator",
            coordinator_with_hermetic_transports,
        )
        monkeypatch.setattr(
            "moonmind.repositories.lore_runtime."
            "build_lore_repository_adapter_from_environment",
            lambda: None,
        )

        services = production.build_generic_omnigent_execution_services(
            session_factory=session_factory,
            artifact_gateway=durable_gateway,
            run_store=OmnigentBridgeSessionStore(session_factory),
        )
        realizer = services.realizer_registry.require("codex-profile-bound@1")
        plan = await _codex_plan(session_factory, skillset_ref, launch_policy)
        if retained_binding:
            from moonmind.omnigent.oauth_hosts import OmnigentOAuthHostRepository

            # Retained on-demand bindings can lack their mode marker, including
            # older records that also persisted a discovered static host ID.
            snapshot = json.loads(
                await TemporalOmnigentArtifactGateway(
                    session_factory, principal="system"
                ).read_text(plan.payload.effectiveLaunchSnapshotRef)
            )
            hosts = OmnigentOAuthHostRepository(session_factory)
            retained = await hosts.create_or_update_static_binding(
                profile_id=PROFILE_ID,
                endpoint_ref="default",
                static_host_id=(
                    host_id
                    if (retained_binding == "valid" and "static" in launch_policy)
                    or retained_binding.endswith("_static")
                    else None
                ),
                host_launch_profile_ref=(
                    "codex-oauth-v1"
                    if retained_binding == "valid" and "on-demand" in launch_policy
                    else None
                ),
                execution_profile_ref="omnigent-codex@1",
                launch_policy_ref=launch_policy,
                effective_launch_snapshot=snapshot,
            )
            if retained_binding.startswith("busy"):
                from moonmind.omnigent.host_failures import OmnigentOAuthHostError

                await hosts.create_or_get_host_lease(
                    binding=retained,
                    provider_lease_id="prior-provider-lease",
                    holder_workflow_id="mm:prior-workflow",
                    agent_run_id="prior-agent",
                    idempotency_key="prior-host-attempt",
                )
                with pytest.raises(OmnigentOAuthHostError) as blocked:
                    await realizer.execute(
                        _request(
                            plan,
                            workflow_id=workflow_id,
                            skillset_ref=skillset_ref,
                            repository=_seeded_remote(tmp_path),
                        ),
                        plan,
                    )
                assert blocked.value.code == "OMNIGENT_HOST_BINDING_LEASED"
                assert await hosts.get_binding_for_profile(PROFILE_ID) == retained
                assert not docker.commands and not server.create_payloads
                return
        result = await realizer.execute(
            _request(
                plan,
                workflow_id=workflow_id,
                skillset_ref=skillset_ref,
                repository=_seeded_remote(tmp_path),
                explicit_target=explicit_target,
            ),
            plan,
        )
        if retained_binding:
            current = await hosts.get_binding_for_profile(PROFILE_ID)
            if retained_binding == "valid":
                assert current.host_launch_profile_ref == retained.host_launch_profile_ref
                assert current.static_host_id == retained.static_host_id
            else:
                assert current.host_launch_profile_ref == launch_policy
                assert current.static_host_id is None
    finally:
        await running.runner.cleanup()

    # The launched host received exactly the durable Skill bytes.
    expected_skill = _durable_skill_payload(_DURABLE_SKILL_NAME)
    assert server.delivered_skill == expected_skill
    assert len(coordinators) == 1

    # One controlled-provider turn on the bound host, activating the mounted
    # Skills.
    assert len(server.session_ids) == 1
    assert server.create_payloads[0]["host_id"] == host_id
    turn_messages = [event for event in server.events if event.get("type") == "message"]
    assert len(turn_messages) == 1
    turn_text = turn_messages[0]["data"]["content"][0]["text"]
    assert (
        f"{OMNIGENT_RUNTIME_ACTIVE_SKILLS_DIR}/{_DURABLE_SKILL_NAME}/SKILL.md"
        in turn_text
    )

    # The result is retrieved from durable storage, not the host.
    assert result.failure_class is None
    assert result.metadata["executionPlanRef"] == plan.planRef
    manifest = json.loads(
        await durable_gateway.read_text(result.metadata["captureManifestRef"])
    )
    captured = {item["path"]: item for item in manifest["workspaceFiles"]}
    assert RESULT_PATH in captured
    delivered = await durable_gateway.read_bytes(captured[RESULT_PATH]["artifactRef"])
    assert delivered == (
        f"skill sha256:{hashlib.sha256(expected_skill).hexdigest()}\n".encode()
    )

    # The session is saved durably before its host is released.
    assert result.metadata["checkpointKind"] == "external_state_ref"
    saved = json.loads(
        await durable_gateway.read_text(result.metadata["stateCheckpointRef"])
    )
    assert saved
    checkpoint_write = events.index(
        ("artifact_write", result.metadata["stateCheckpointRef"])
    )
    host_stops = [
        index for index, (kind, _ref) in enumerate(events) if kind == "host_stop"
    ]
    assert host_stops
    assert checkpoint_write < host_stops[0]
    assert result.metadata["runtimeBindingState"] == "cleanup_complete"
    runtime_chain = result.metadata["authorityChain"]["runtime"]
    assert runtime_chain["hostMode"] == (
        "on_demand_docker" if "on-demand" in launch_policy else "static_compose"
    )
    assert "skills_tools" in runtime_chain["mountClasses"]

    # The selected profile's OAuth home is attached, never copied or logged in.
    if isinstance(docker, OnDemandCodexDocker):
        assert docker.launch_env["CODEX_CREDENTIAL_GENERATION"] == "3"
        assert not docker.container_present and not docker.owned_volumes
    else:
        assert docker.compose_up_env is not None
        assert docker.compose_up_env["CODEX_VOLUME_NAME"] == AUTH_VOLUME
        assert docker.compose_up_env["CODEX_CREDENTIAL_GENERATION"] == "3"
    flat_commands = [" ".join(command) for command in docker.commands]
    assert not any(" login" in command for command in flat_commands)
    assert not any(" cp " in f" {command} " for command in flat_commands)
    assert len(leases.acquired) == 1
    assert [name for name, _payload in leases.signals] == ["release_slot"]
    assert leases.signals[0][1]["lease_id"] == leases.acquired[0]
    assert host_stops[-1] < events.index(("release_slot", leases.acquired[0]))
