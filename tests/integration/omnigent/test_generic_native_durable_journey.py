"""The registered generic owner executes and saves each native harness.

SQLite stores and all production lifecycle owners are real. Docker commands,
Omnigent HTTP provider responses, Temporal manager transport and installed-image
resolver metadata are controlled substitutes. Stock picker/agent wire shapes go
through canonical HTTP discovery and the production native harness overlay.
The fake provider reads the exact durable Skill through the emitted read-only
mount and writes its digest through the workspace mount during the native turn.
This exercises orchestration and wire contracts, not a live stock server,
installed images or paid provider turns. SQLite's single-process lock substitute
does not prove PostgreSQL concurrency. Generic Codex uses its omitted-setting
default; fixture metadata is not qualification evidence.
"""

from __future__ import annotations

import asyncio
import hashlib
import io
import json
import os
import shutil
import tarfile
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
import pytest_asyncio
from aiohttp import web

from api_service.db.models import (
    AgentSkillDefinition,
    ManagedAgentProviderProfile,
    ProviderCredentialSource,
    ProviderProfileSlotLease,
    RuntimeMaterializationMode,
)
from api_service.services import omnigent_execution_plan_service as plan_writer
from api_service.services.omnigent_agent_profile_service import (
    synchronize_omnigent_harness_catalog,
)
from api_service.services.omnigent_policies import seed_bootstrap_policies
from api_service.services.provider_profile_creation import (
    provider_api_key_strategy,
    provider_profile_creation_capabilities,
)
from api_service.services.provider_profile_readiness import (
    provider_profile_launch_ready,
)
from moonmind.omnigent import credential_materializers, production
from moonmind.omnigent.codex_login import CODEX_ACCOUNT_READ_PROBE
from moonmind.omnigent.harness_platform.catalog_service import (
    DbHarnessCatalogRepository,
)
from moonmind.omnigent.host_services import docker_backend
from moonmind.omnigent.runtime_bindings import RuntimeBindingState, stable_binding_id
from moonmind.schemas.agent_runtime_models import AgentExecutionRequest
from moonmind.workflows.temporal.artifacts import (
    TemporalArtifactRepository,
    TemporalArtifactService,
)
from moonmind.workflows.temporal.workflows.provider_profile_manager import (
    workflow_id_for_runtime,
)
from tests.artifact_access import access_for_test
from tests.helpers.omnigent_conformance import (
    FakeOmnigentServer,
    start_fake_omnigent_server,
)
from tests.integration.omnigent import test_codex_durable_skill_journey as codex_journey
from tests.integration.omnigent.test_codex_durable_skill_journey import (
    HOST_IMAGE,
    SERVER_IMAGE,
    HermeticProviderLeases,
    RecordingArtifactGateway,
    StaticCodexDocker,
)
from tests.integration.reliability.test_scratch_saved_work_journey import (
    _serialize_finalization_on_sqlite,
)
from tests.unit.omnigent.test_plan_and_realizer_dispatch import (
    _DURABLE_SKILL_NAME,
    _SNAPSHOT_PRODUCER_PRINCIPAL,
    _durable_skill_payload,
    _write_durable_skill_snapshot,
)
from tests.unit.services.test_omnigent_execution_plan_service import (
    _configure_ready_host_image_pair,
    _snapshot,
)

pytestmark = [pytest.mark.asyncio, pytest.mark.integration, pytest.mark.integration_ci]

# Reuse the durable journey storage fixture, including artifact isolation.
session_factory = codex_journey.session_factory

OWNER = "generic-journey-owner"
PROFILE = "journey-model"
SECRET = "synthetic-opencode-journey-key"
RESULT_PATH = "report.md"
GENERATED = (
    "# Native generated result\n\n"
    f"Skill sha256:{hashlib.sha256(_durable_skill_payload(_DURABLE_SKILL_NAME)).hexdigest()}\n"
).encode()


class NativeArtifactGateway(RecordingArtifactGateway):
    async def write_bytes(self, **kwargs):
        ref = await super().write_bytes(**kwargs)
        # Check every committed artifact while readable. Native idle completion
        # compacts SSE chunks, retiring earlier refs after their replacement.
        assert SECRET.encode() not in await self.read_bytes(ref)
        return ref


class NativeTurnServer(FakeOmnigentServer):
    """Controlled native HTTP provider; output comes from its accepted turn."""

    def __init__(self, harness):
        super().__init__(supports_diff=False, terminal_status="idle")
        self.harness = harness
        self.host_id = ""
        self.host_name = ""
        self.workspace: Path | None = None
        self.skills: Path | None = None
        self.delivered_skill: bytes | None = None
        self.model = "example/model"
        self.host_alive = False
        self.stopped_sessions = set()
        self.foreign_consumers = []
        self.on_first_message = None
        self.status = "idle"
        self.items = []
        self.pending_inputs = []
        self.stream_subscribers = set()
        self.emitted_events = []
        self.turn_active = False

    def app(self):
        app = super().app()
        app.router.add_get("/", self.native_document)
        app.router.add_get("/api/version", self.version)
        app.router.add_get("/v1/sessions", self.list_sessions)
        app.router.add_get("/v1/harnesses", self.harnesses)
        app.router.add_get("/v1/hosts/{host_id}", self.get_host)
        app.router.add_get(
            "/v1/hosts/{host_id}/harnesses/{harness}/model-options", self.model_options
        )
        return app

    async def native_document(self, request):
        return web.Response(
            text="<html><head></head><body><div id='root'></div></body></html>",
            content_type="text/html",
        )

    async def version(self, request):
        return web.json_response({"version": "0.10.0"})

    async def harnesses(self, request):
        # The pinned stock picker omits native wrappers. Discovery must use
        # observed stock agents and the production native overlay, not infer
        # native capability from these unrelated picker rows.
        return web.json_response(
            {
                "data": [
                    {
                        "id": harness,
                        "label": label,
                        "capabilities": {
                            "integration_mode": mode,
                            "auth": "omnigent-credential",
                            "streaming": True,
                            "interrupt": True,
                        },
                    }
                    for harness, label, mode in (
                        ("codex", "Codex", "cli-subprocess"),
                        ("claude-sdk", "Claude SDK", "sdk-in-process"),
                    )
                ],
                "setup_steps": {},
            }
        )

    async def list_sessions(self, request):
        return web.json_response(
            {
                "data": [
                    {
                        "id": session,
                        "host_id": self.host_id,
                        "status": (
                            "idle" if session in self.stopped_sessions else self.status
                        ),
                        "runner_online": session not in self.stopped_sessions,
                    }
                    for session in self.session_ids
                ]
                + self.foreign_consumers,
                "has_more": False,
            }
        )

    async def list_hosts(self, request):
        return web.json_response({"hosts": []})

    async def get_host(self, request):
        assert request.match_info["host_id"] == self.host_id
        assert self.host_alive
        return web.json_response(
            {
                "id": self.host_id,
                "name": self.host_name,
                "owner": OWNER,
                "status": "online",
                "harnesses": {self.harness: "ready"},
            }
        )

    async def model_options(self, request):
        assert request.match_info["host_id"] == self.host_id
        assert request.match_info["harness"] == self.harness
        assert self.harness != "opencode-native"
        return web.json_response({"models": [{"id": self.model}]})

    async def list_agents(self, request):
        return web.json_response(
            {
                "object": "list",
                "data": [
                    {
                        "id": self.harness + "-agent",
                        "harness": self.harness,
                        "version": "1.0.0",
                        "name": {
                            "codex-native": "codex-native-ui",
                            "claude-native": "claude-native-ui",
                            "opencode-native": "opencode-native-ui",
                        }[self.harness],
                    }
                ],
                "has_more": False,
            }
        )

    def snapshot(self, session_id):
        # SessionResponse and ConversationItem are the stock snapshot contract.
        # Native success returns to idle; ordered transcript items prove work.
        payload = {
            "id": session_id,
            "agent_id": self.harness + "-agent",
            "agent_name": self.harness + "-ui",
            "harness": self.harness,
            "host_id": self.host_id,
            "created_at": 1,
            "status": self.status,
            "items": self.items,
            "pending_inputs": self.pending_inputs,
            "workspace": "/workspaces/run",
            "active_response_id": None,
        }
        if self.status == "failed":
            payload["last_task_error"] = {
                "code": "controlled_native_failure",
                "message": "controlled native turn failed after writing report",
            }
        return payload

    async def create_session(self, request):
        response = await super().create_session(request)
        if response.status != 200:
            return response
        return web.json_response(
            self.snapshot(json.loads(response.body)["id"]), status=201
        )

    async def get_session(self, request):
        if (fault := await self._fault(request, "sessions.get")) is not None:
            return fault
        return web.json_response(self.snapshot(request.match_info["session_id"]))

    def publish(self, event):
        self.emitted_events.append(event)
        for subscriber in self.stream_subscribers:
            subscriber.put_nowait(event)

    def append_message(self, role, content):
        item = {
            "id": f"item-{len(self.items) + 1}",
            "type": "message",
            "status": "completed",
            "response_id": "native-turn-1",
            "created_at": 1,
            "data": {"role": role, "content": content},
        }
        self.items.append(item)
        return item

    def append_assistant_response(self, text):
        assistant = self.append_message(
            "assistant", [{"type": "output_text", "text": text}]
        )
        self.publish(
            {
                "type": "response.output_item.done",
                "item": {
                    key: value for key, value in assistant.items() if key != "data"
                }
                | assistant["data"],
            }
        )

    async def post_event(self, request):
        if (fault := await self._fault(request, "sessions.events")) is not None:
            return fault
        payload = await request.json()
        self.events.append(payload)
        if payload["type"] == "stop_session":
            self.stopped_sessions.add(request.match_info["session_id"])
        if payload["type"] != "message":
            return web.json_response({"queued": False})
        assert self.host_alive and self.workspace is not None
        self.first_message_posted.set()
        pending_id = f"pending-{sum(e.get('type') == 'message' for e in self.events)}"
        content = payload["data"]["content"]
        self.pending_inputs.append({"pending_id": pending_id, "content": content})
        # The controlled transcript forwarder consumes the queued native input.
        item = self.append_message("user", content)
        self.pending_inputs.remove({"pending_id": pending_id, "content": content})
        self.publish(
            {
                "type": "session.input.consumed",
                "data": {
                    "item_id": item["id"],
                    "type": "message",
                    "data": item["data"],
                    "cleared_pending_id": pending_id,
                },
            }
        )
        if not self.turn_active:
            self.turn_active = True
            self.status = "running"
            session_id = request.match_info["session_id"]
            self.publish(
                {
                    "type": "session.status",
                    "conversation_id": session_id,
                    "status": self.status,
                }
            )
            assert self.skills is not None
            self.delivered_skill = (
                self.skills / _DURABLE_SKILL_NAME / "SKILL.md"
            ).read_bytes()
            digest = hashlib.sha256(self.delivered_skill).hexdigest()
            result = f"# Native generated result\n\nSkill sha256:{digest}\n"
            (self.workspace / RESULT_PATH).write_text(result)
            callback, self.on_first_message = self.on_first_message, None
            if callback is not None:
                await callback()
            self.append_assistant_response(result)
            self.status = self.terminal_status
            assert self.status in {"idle", "failed"}
            terminal = {
                "type": "session.status",
                "conversation_id": session_id,
                "status": self.status,
            }
            if self.status == "failed":
                terminal["error"] = self.snapshot(session_id)["last_task_error"]
            self.publish(terminal)
            self.turn_active = False
        else:
            # A consumed follow-up can produce another native assistant item in
            # the active session. Answer from the actual mounted workspace; the
            # queued acknowledgement below remains separate from this output.
            prompt = "".join(
                part["text"] for part in content if part.get("type") == "input_text"
            )
            report = (self.workspace / RESULT_PATH).read_text()
            answer = (
                report.splitlines()[0]
                if prompt == "Return only the heading of the generated report."
                else report
            )
            self.append_assistant_response(answer)
        # Native injection is queued, not an already-persisted item response.
        return web.json_response({"queued": True, "pending_id": pending_id})

    async def stream(self, request):
        if (fault := await self._fault(request, "sessions.stream")) is not None:
            return fault
        response = web.StreamResponse(headers={"Content-Type": "text/event-stream"})
        queue = asyncio.Queue()
        self.stream_subscribers.add(queue)
        try:
            await response.prepare(request)
            # Stock SSE is live-tail only. Reconnection reads GET /sessions/id;
            # it must not replay a synthetic response.completed from this fake.
            while request.transport is not None and not request.transport.is_closing():
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=0.1)
                except TimeoutError:
                    continue
                await response.write(f"data: {json.dumps(event)}\n\n".encode())
        finally:
            self.stream_subscribers.discard(queue)
        return response

    async def list_changed_files(self, request):
        assert self.host_alive and self.workspace is not None
        return web.json_response(
            {
                "object": "list",
                "data": [
                    {
                        "object": "session.environment.filesystem.entry",
                        "path": RESULT_PATH,
                        "name": RESULT_PATH,
                        "status": "created",
                    }
                ],
                "has_more": False,
            }
        )

    async def list_workspace_files(self, request):
        assert self.host_alive and self.workspace is not None
        return web.json_response(
            {
                "object": "list",
                "base": "/workspaces/run",
                "data": [
                    {
                        "id": RESULT_PATH,
                        "object": "session.environment.filesystem.entry",
                        "path": RESULT_PATH,
                        "name": RESULT_PATH,
                        "type": "file",
                        "bytes": (self.workspace / RESULT_PATH).stat().st_size,
                        "modified_at": 1,
                    }
                ],
                "first_id": RESULT_PATH,
                "last_id": RESULT_PATH,
                "has_more": False,
            }
        )

    async def get_workspace_file(self, request):
        assert self.host_alive and self.workspace is not None
        assert request.match_info["path"] == RESULT_PATH
        body = (self.workspace / RESULT_PATH).read_bytes()
        return web.json_response(
            {
                "object": "session.environment.filesystem.file_content",
                "path": RESULT_PATH,
                "content_type": "text/markdown",
                "bytes": len(body),
                "truncated": False,
                "encoding": "utf-8",
                "content": body.decode(),
            }
        )

    async def get_workspace_diff(self, request):
        assert self.workspace is not None and request.match_info["path"] == RESULT_PATH
        return web.json_response(
            {
                "object": "session.environment.filesystem.file_diff",
                "path": RESULT_PATH,
                "before": None,
                "after": (self.workspace / RESULT_PATH).read_text(),
            }
        )

    async def list_session_files(self, request):
        return web.json_response(
            {
                "object": "list",
                "data": [],
                "first_id": None,
                "last_id": None,
                "has_more": False,
            }
        )


@pytest.mark.parametrize(
    "harness", ["codex-native", "claude-native", "opencode-native"]
)
async def test_native_transport_uses_stock_session_and_turn_contract(tmp_path, harness):
    server = NativeTurnServer(harness)
    server.host_alive = True
    server.host_id = "native-host"
    server.workspace = tmp_path / "workspace"
    server.workspace.mkdir()
    server.skills = tmp_path / "skills"
    skill = server.skills / _DURABLE_SKILL_NAME / "SKILL.md"
    skill.parent.mkdir(parents=True)
    skill.write_bytes(_durable_skill_payload(_DURABLE_SKILL_NAME))
    running = await start_fake_omnigent_server(server)
    try:
        async with httpx.AsyncClient(base_url=running.base_url) as client:
            created = (
                await client.post(
                    "/v1/sessions",
                    json={
                        "agent_id": harness + "-agent",
                        "host_id": server.host_id,
                        "workspace": "/workspaces/run",
                    },
                )
            ).json()
            path = "/v1/sessions/" + created["id"]
            assert created["status"] == "idle"
            initial = (await client.get(path)).json()
            assert initial["status"] == "idle" and initial["items"] == []
            assert initial["agent_id"] == harness + "-agent"
            assert initial["harness"] == harness
            assert initial["host_id"] == server.host_id
            frames = []
            async with client.stream("GET", path + "/stream") as stream:
                acknowledgement = (
                    await client.post(
                        path + "/events",
                        json={
                            "type": "message",
                            "data": {
                                "content": [
                                    {"type": "input_text", "text": "Generate a report."}
                                ]
                            },
                        },
                    )
                ).json()
                assert acknowledgement == {"queued": True, "pending_id": "pending-1"}
                async for line in stream.aiter_lines():
                    if line.startswith("data: "):
                        event = json.loads(line.removeprefix("data: "))
                        frames.append(event)
                        if (
                            event.get("type") == "session.status"
                            and event["status"] == "idle"
                        ):
                            break
            assert [e["status"] for e in frames if e["type"] == "session.status"] == [
                "running",
                "idle",
            ]
            assert all(
                e.get("conversation_id", created["id"]) == created["id"] for e in frames
            )
            consumed = next(
                e["data"] for e in frames if e["type"] == "session.input.consumed"
            )
            assert consumed["cleared_pending_id"] == acknowledgement["pending_id"]
            assert not any(e["type"] == "response.completed" for e in frames)
            output = next(
                e["item"] for e in frames if e["type"] == "response.output_item.done"
            )
            assert output["role"] == "assistant" and "data" not in output
            assert output["content"][0]["text"] == GENERATED.decode()
            final = (await client.get(path)).json()
            assert final["status"] == "idle" and final["pending_inputs"] == []
            assert [item["data"]["role"] for item in final["items"]] == [
                "user",
                "assistant",
            ]
            assert final["items"][0]["id"] == consumed["item_id"]
            assert (
                final["items"][-1]["data"]["content"][0]["text"] == GENERATED.decode()
            )
            assert (server.workspace / RESULT_PATH).read_bytes() == GENERATED
    finally:
        await running.runner.cleanup()


def _options(argv, name):
    return [argv[i + 1] for i, value in enumerate(argv[:-1]) if value == name]


def _mapping(argv, name):
    return dict(value.split("=", 1) for value in _options(argv, name))


def _mounts(argv):
    return {
        mount["dst"]: mount
        for item in _options(argv, "--mount")
        for mount in [dict(part.partition("=")[::2] for part in item.split(","))]
    }


class NativeDocker(StaticCodexDocker):
    """Stateful daemon transport using the legacy journey's egress responses.

    No production host, credential, registration or attestation owner is
    replaced. Their emitted commands determine state and the HTTP host identity.
    Image probes are controlled daemon answers, not real image qualification.
    """

    def __init__(self, *, server, events, workflow_id):
        super().__init__(
            workflow_id=workflow_id, server=server, run_command=None, events=events
        )
        self.volumes = {"journey_oauth": {}}
        self.generations = {}
        self.auth_json = None
        self.host_labels = {}
        self.launch_env = {}
        self.mounts = {}
        self.container_name = ""
        self.host_class = None
        self.create_count = 0
        self.script = ""
        self.network_ref = ""
        self.launch_image = ""
        self.fail_host_removal = False

    async def command(self, argv, *, input_bytes=None, **_kwargs):
        code, out, err = await self._command(list(argv), input_bytes=input_bytes)
        return code, out.encode(), err.encode()

    async def _command(self, argv, *, input_bytes=None):
        self.commands.append(tuple(argv))
        if argv[:3] == ["docker", "volume", "create"]:
            self.volumes[argv[-1]] = _mapping(argv, "--label")
            return 0, argv[-1], ""
        if argv[:3] == ["docker", "volume", "ls"]:
            volume = (
                _options(argv, "--filter")[0].removeprefix("name=^").removesuffix("$")
            )
            if volume not in self.volumes:
                return 0, "", ""
            template = _options(argv, "--format")[0]
            labels = self.volumes[volume]
            if "credential_runtime_ref" in template:
                assert labels["moonmind.credential_runtime_ref"] in template
                assert json.dumps(labels["moonmind.credential_generation"]) in template
                return 0, "owned", ""
            return 0, "present", ""
        if argv[:3] == ["docker", "volume", "inspect"]:
            if argv[-1] not in self.volumes:
                return 1, "", "No such volume"
            labels = self.volumes[argv[-1]]
            return (
                0,
                labels["moonmind.host_lease_ref"]
                + "|"
                + labels["moonmind.host_lease_generation"],
                "",
            )
        if argv[:3] == ["docker", "volume", "rm"]:
            assert argv[-1] != "journey_oauth"
            del self.volumes[argv[-1]]
            self.events.append(("volume_remove", argv[-1]))
            return 0, "", ""
        if argv[:2] == ["docker", "run"]:
            assert "--rm" in argv and "none" == argv[argv.index("--network") + 1]
            mounts = _mounts(argv)
            if "/credential" in mounts:
                volume = mounts["/credential"]["src"]
                assert volume in self.volumes
                if input_bytes:
                    self.auth_json = json.loads(input_bytes)
                if ".moonmind-generation" in " ".join(argv):
                    generation = argv[argv.index("--") + 1]
                    if "readonly" in mounts["/credential"]:
                        assert self.generations[volume] == generation
                    else:
                        self.generations[volume] = generation
            return 0, "", ""
        if argv[:2] == ["docker", "create"]:
            self.create_count += 1
            self.container_name = _options(argv, "--name")[0]
            self.host_labels = _mapping(argv, "--label")
            self.launch_env = _mapping(argv, "--env")
            self.mounts = _mounts(argv)
            self.script = argv[argv.index("-ceu") + 1]
            self.launch_image = argv[argv.index("-ceu") - 1]
            self.network_ref = _options(argv, "--network")[0]
            self.server.host_id = self.launch_env["OMNIGENT_HOST_ID"]
            self.server.host_name = self.launch_env["OMNIGENT_HOST_NAME"]
            self.server.workspace = Path(self.mounts["/workspaces/run"]["src"])
            assert self.server.workspace.is_dir()
            selected_uid = self.host_class.runtime["uid"]
            selected_gid = self.host_class.runtime["gid"]
            assert _options(argv, "--user") == [f"{selected_uid}:{selected_gid}"]
            workspace_owner = self.server.workspace.stat()
            assert (workspace_owner.st_uid, workspace_owner.st_gid) == (
                selected_uid,
                selected_gid,
            )
            skills = self.mounts[self.launch_env["MOONMIND_ACTIVE_SKILLS_DIR"]]
            self.server.skills = Path(skills["src"])
            assert skills["type"] == "bind" and "readonly" in skills
            assert (self.server.skills / "_manifest.json").is_file()
            return 0, self.host_container_id, ""
        if argv[:2] == ["docker", "start"]:
            assert argv[-1] == self.container_name
            self.server.host_alive = self.container_running = True
            self.events.append(("host_start", self.container_name))
            return 0, "", ""
        if argv[:2] == ["docker", "rm"]:
            assert argv[-1] == self.container_name
            if self.fail_host_removal:
                return 1, "", "controlled daemon failure"
            self.server.host_alive = self.container_running = False
            self.events.append(("host_remove", self.container_name))
            return 0, "", ""
        if argv[:2] == ["docker", "logs"]:
            return 0, "controlled native host completed\n", ""
        if argv[:3] == ["docker", "image", "inspect"]:
            if "--format" in argv:
                if _options(argv, "--format")[0] == "{{json .Architecture}}":
                    return 0, json.dumps(self.architecture), ""
                return 0, "sha256:" + "9" * 64, ""
            assert self.host_class is not None
            return (
                0,
                json.dumps(
                    [
                        {
                            "RepoDigests": [self.host_class.imageRef],
                            "Os": "linux",
                            "Architecture": self.architecture,
                            "Config": {
                                "Labels": {
                                    "moonmind.omnigent.build_digest": self.host_class.omnigentBuildDigest
                                }
                            },
                        }
                    ]
                ),
                "",
            )
        if argv[:3] == ["docker", "container", "inspect"]:
            if not self.server.host_alive:
                return 1, "", "No such container"
            template = _options(argv, "--format")[0]
            if template == "{{.State.Status}}|{{.State.ExitCode}}":
                return 0, "running|0", ""
            if template == "{{.Config.Image}}":
                return 0, self.launch_image, ""
            if template == "{{json .Config.Labels}}":
                return 0, json.dumps(self.host_labels), ""
            if template == "{{json .Config.Env}}":
                return (
                    0,
                    json.dumps(
                        [f"{key}={value}" for key, value in self.launch_env.items()]
                    ),
                    "",
                )
            if template == "{{json .Mounts}}":
                return (
                    0,
                    json.dumps(
                        [
                            {
                                "Name" if item["type"] == "volume" else "Source": item[
                                    "src"
                                ],
                                "Destination": target,
                                "RW": "readonly" not in item,
                            }
                            for target, item in self.mounts.items()
                        ]
                    ),
                    "",
                )
            if template.startswith("{{range .Mounts}}"):
                matched = [
                    item
                    for target, item in self.mounts.items()
                    if json.dumps(item["src"]) in template
                    and json.dumps(target) in template
                ]
                assert len(matched) == 1
                return 0, "ro" if "readonly" in matched[0] else "rw", ""
            if "moonmind.host_lease_ref" in template:
                return (
                    0,
                    self.host_labels["moonmind.host_lease_ref"]
                    + "|"
                    + self.host_labels["moonmind.host_lease_generation"],
                    "",
                )
            raise AssertionError(f"unhandled container probe: {argv}")
        if argv[:2] == ["docker", "inspect"] and argv[-1] == self.container_name:
            return self._inspect(argv[3], self.host_container_id)
        if argv[:2] == ["docker", "exec"] and argv[2] == self.container_name:
            assert self.server.host_alive
            if argv[-1] == "--version":
                if argv[3] == "/opt/venv/bin/omnigent":
                    return 0, self.host_class.omnigentVersion, ""
                entry = next(
                    item
                    for item in self.host_class.declaredHarnessImplementations
                    if item.harnessId == self.server.harness
                )
                dependency = next(
                    item
                    for item in entry.runtimeDependencies
                    if item["name"] == argv[3]
                )
                return 0, dependency["version"], ""
            if "list_opencode_cli_model_options" in " ".join(argv):
                return 0, json.dumps({"models": [{"id": self.server.model}]}), ""
            if argv[3:6] in (
                ["codex", "login", "status"],
                ["claude", "auth", "status"],
            ):
                return 0, "", ""
            if CODEX_ACCOUNT_READ_PROBE in argv:
                assert self.server.harness == "codex-native"
                assert self.mounts[argv[-1]]["src"] == "journey_oauth"
                return 0, "ready\n", ""
            if 'cat "$1/.moonmind-generation"' in " ".join(argv):
                return 0, self.generations[self.mounts[argv[-1]]["src"]], ""
            # Image-local shell/runner probes are explicitly controlled here.
            assert argv[3] in {"/bin/sh", "/opt/venv/bin/python"}, argv
            return 0, "", ""
        return await super().__call__(*argv)

    def _inspect(self, format_value, identity):
        if identity == self.host_container_id and format_value.startswith('{"labels"'):
            return (
                0,
                json.dumps(
                    {
                        "labels": self.host_labels,
                        "networks": {
                            self.network_ref: {
                                "NetworkID": "journey-network",
                                "EndpointID": "journey-endpoint",
                                "IPAddress": self.client_address,
                            }
                        },
                        "imageRef": self.launch_image,
                        "image": self.launch_image.rsplit("@", 1)[-1],
                    }
                ),
                "",
            )
        return super()._inspect(format_value, identity)


class AdmittedManager(HermeticProviderLeases):
    """Inspect the exact workflow-owned grant; never acquire another slot."""

    def __init__(self, events, *, request, plan):
        super().__init__(events)
        self.request, self.plan = request, plan
        self.inspections = []
        self.state_signals = []
        self.expires_at = datetime.now(UTC) + timedelta(hours=1)

    async def update_workflow(self, workflow_id, name, payload):
        runtime = self.request.admitted_provider_capacity.profiles[
            0
        ].provider_runtime_id
        assert workflow_id == workflow_id_for_runtime(runtime)
        assert name == "InspectCredentialLease", name
        self.inspections.append(dict(payload))
        owner = self.request.admitted_provider_capacity.lease_owner_id
        assert payload == {"lease_id": owner, "owner_id": owner}
        return {
            "active": True,
            "profile_id": PROFILE,
            "ownerId": owner,
            "ownerIsWorkflow": True,
            "expiresAt": self.expires_at.isoformat(),
            "executionPlanRef": self.plan.planRef,
            "stepExecutionId": self.request.step_execution.step_execution_id,
            "idempotencyKey": self.request.idempotency_key,
            "credentialGeneration": 3,
            "fencingGeneration": 7,
        }

    async def get_workflow_handle(self, workflow_id):
        manager = self

        class Handle:
            async def signal(self, name, *, args):
                manager.state_signals.append((name, args))

        return Handle()


class StaticNativeDocker(NativeDocker):
    state_volume = "moonmind-test-static-persistent-state"
    retained_identity = "12" * 16

    async def _command(self, argv, *, input_bytes=None):
        if argv[:2] == ["docker", "compose"]:
            self.commands.append(tuple(argv))
            service = "omnigent-host-" + self.server.harness.removesuffix("-native")
            if "config" in argv:
                return (
                    0,
                    json.dumps(
                        {
                            "services": {
                                service: {
                                    "volumes": [
                                        {
                                            "type": "volume",
                                            "source": "state",
                                            "target": "/home/app/.omnigent",
                                        }
                                    ]
                                }
                            },
                            "volumes": {"state": {"name": self.state_volume}},
                        }
                    ),
                    "",
                )
            if "ps" in argv:
                return 0, self.host_container_id if self.server.host_alive else "", ""
            assert "up" in argv and "--no-deps" in argv
            model = json.loads(input_bytes)
            definition = model["services"][service]
            assert definition["read_only"] is True
            assert (
                definition["environment"]["OMNIGENT_HOST_ID"] == self.retained_identity
            )
            command = ["docker", "create"]
            for key, flag in (
                ("container_name", "--name"),
                ("hostname", "--hostname"),
                ("user", "--user"),
            ):
                command.extend([flag, definition[key]])
            for key, value in definition["labels"].items():
                command.extend(["--label", f"{key}={value}"])
            for key, value in definition["environment"].items():
                command.extend(["--env", f"{key}={value}"])
            for mount in definition["volumes"]:
                rendered = (
                    f"type={mount['type']},src={mount['source']},dst={mount['target']}"
                )
                if mount["read_only"]:
                    rendered += ",readonly"
                command.extend(["--mount", rendered])
            command.extend(
                [
                    "--network",
                    definition["networks"][0],
                    "--entrypoint",
                    definition["entrypoint"],
                    definition["image"],
                    *definition["command"],
                ]
            )
            result = await super()._command(command)
            await super()._command(["docker", "start", definition["container_name"]])
            return result
        if argv[:2] == ["docker", "inspect"] and len(argv) == 3:
            return (
                0,
                json.dumps(
                    [
                        {
                            "Name": "/" + self.container_name,
                            "Config": {
                                "Env": [
                                    f"{key}={value}"
                                    for key, value in self.launch_env.items()
                                ]
                            },
                            "Mounts": [
                                {"Name": item["src"], "Destination": target}
                                for target, item in self.mounts.items()
                                if item["type"] == "volume"
                            ],
                        }
                    ]
                ),
                "",
            )
        if (
            argv[:3] == ["docker", "volume", "inspect"]
            and argv[-1] == self.state_volume
        ):
            self.commands.append(tuple(argv))
            self.volumes.setdefault(self.state_volume, {})
            return 0, self.state_volume, ""
        if argv[:2] == [
            "docker",
            "run",
        ] and "load_host_identity_if_present" in " ".join(argv):
            self.commands.append(tuple(argv))
            return (
                0,
                json.dumps(
                    {"id": self.retained_identity, "name": "operator-static-host"}
                ),
                "",
            )
        if argv[:2] == ["docker", "inspect"] and "{{.Image}}" in argv:
            self.commands.append(tuple(argv))
            return 0, HOST_IMAGE, ""
        return await super()._command(argv, input_bytes=input_bytes)


async def _compile_plan(
    session_factory,
    harness,
    workflow_id,
    *,
    api_key=False,
    static_mode=False,
    instructions="Generate a report.",
):
    # Reuse the durable Skill producer, then expose its immutable body through
    # the real deployment catalog. Admission must resolve and persist its own
    # exact manifest; no Skill resolver or runtime materializer is substituted.
    skill_snapshot_ref = await _write_durable_skill_snapshot(
        session_factory, workflow_id, "run"
    )
    profile_name = harness.removesuffix("-native")
    oauth = harness != "opencode-native" and not api_key
    runtime_id = {
        "codex": "codex_cli",
        "claude": "claude_code",
        "opencode": "opencode",
    }[profile_name]
    provider_id = "anthropic" if profile_name == "claude" else "openai"
    capabilities = provider_profile_creation_capabilities(
        runtime_id=runtime_id, provider_id=provider_id
    )
    method = next(
        item
        for item in capabilities["authentication_methods"]
        if item["id"] == ("oauth" if oauth else "api_key")
    )
    # Use the same creation-owned materialization and isolation fields as the
    # guided Profile surface, rather than bypassing readiness in lifecycle setup.
    fields = {key: field["value"] for key, field in method["fields"].items()}
    fields["credential_source"] = ProviderCredentialSource(fields["credential_source"])
    fields["runtime_materialization_mode"] = RuntimeMaterializationMode(
        fields["runtime_materialization_mode"]
    )
    profile = ManagedAgentProviderProfile(
        profile_id=PROFILE,
        runtime_id=runtime_id,
        provider_id=provider_id,
        **fields,
        volume_ref="journey_oauth" if oauth else None,
        volume_mount_path=method["imported_volume"]["mount_path"] if oauth else None,
        secret_refs={
            role["role"]: "env://NATIVE_JOURNEY_KEY" for role in method["secret_roles"]
        },
        # Guided API-key setup stores the strategy's key mapping, exactly as the
        # Provider Profile API-key action writes it.
        **(
            {
                "env_template": {
                    strategy.env_key: {"from_secret_ref": strategy.secret_role}
                    for strategy in [provider_api_key_strategy(runtime_id, provider_id)]
                }
            }
            if api_key
            else {}
        ),
        credential_generation=3,
        max_parallel_runs=1,
        enabled=True,
        auth_state="connected",
        last_auth_method="oauth_volume" if oauth else "secret_ref",
    )
    async with session_factory() as session:
        artifacts = TemporalArtifactService(TemporalArtifactRepository(session))
        _snapshot_row, skill_snapshot_bytes = await artifacts.read(
            artifact_id=skill_snapshot_ref,
            access=access_for_test(_SNAPSHOT_PRODUCER_PRINCIPAL),
        )
        skill = json.loads(skill_snapshot_bytes)["skills"][0]
        session.add(
            AgentSkillDefinition(
                slug=skill["skill_name"],
                title="Durable journey Skill",
                artifact_ref=skill["content_ref"],
                content_digest=skill["content_digest"],
            )
        )
        session.add(profile)
        await session.commit()
        await session.refresh(profile)
        assert provider_profile_launch_ready(profile)

        async def resolve_image(image):
            return SERVER_IMAGE if "server" in image else HOST_IMAGE

        await seed_bootstrap_policies(session, image_resolver=resolve_image)
        artifacts = TemporalArtifactService(TemporalArtifactRepository(session))
        snapshot = _snapshot(
            harness=harness,
            policy=f"{profile_name}-{'static' if static_mode else 'on-demand'}@1",
            provider_id=PROFILE,
        )
        snapshot["document"]["skills"] = [_DURABLE_SKILL_NAME]
        if api_key:
            snapshot["document"]["providerRequirements"].update(
                credentialSource=profile.credential_source.value,
                materializationMode=profile.runtime_materialization_mode.value,
            )
        parameters = {
            "targetRuntime": "omnigent",
            "publishMode": "none",
            "model": "example/model",
            "workflow": {
                "instructions": instructions,
                "skill": {"id": _DURABLE_SKILL_NAME},
            },
        }
        input_ref, input_digest = await plan_writer.persist_json_artifact(
            artifact_service=artifacts,
            principal="system",
            artifact_class="omnigent.task_input_snapshot",
            payload=parameters,
        )
        return await plan_writer.compile_and_persist_execution_plan(
            session_factory=session_factory,
            artifact_service=artifacts,
            principal="system",
            workflow_id=workflow_id,
            agent_profile_snapshot=snapshot,
            provider_profile=profile,
            initial_parameters=parameters,
            authored_request_ref=input_ref,
            authored_request_digest=input_digest,
            task_input_snapshot_ref=input_ref,
            task_input_snapshot_digest=input_digest,
            # The durable Skill producer linked these exact inputs to this
            # workflow. A provenance-only system label is not read authority.
            access=access_for_test(
                "system", admitted_principal=f"workflow:{workflow_id}"
            ),
        )


@pytest_asyncio.fixture
async def native_journey(
    request, session_factory, production_transport_pool, tmp_path, monkeypatch
):
    """Production-composed native execution, shared with bound-interaction tests."""
    from moonmind.omnigent.execute import _await_marked_turn_terminal

    async def short_quiescence(**kwargs):
        kwargs.update(interval_seconds=0.005, quiet_period_seconds=0.02)
        return await _await_marked_turn_terminal(**kwargs)

    monkeypatch.setattr(
        "moonmind.omnigent.execute._await_marked_turn_terminal", short_quiescence
    )
    # ``<harness>-api-key`` is that harness's journey with an API-key Provider
    # Profile created by the guided Profile surface.
    selection = (
        request.param if isinstance(request.param, dict) else {"harness": request.param}
    )
    selected_harness = selection["harness"]
    static_mode = bool(selection.get("static"))
    api_key = selected_harness.endswith("-api-key")
    harness = selected_harness.removesuffix("-api-key")
    instructions = (
        "/review --local\nGenerate a report."
        if selection.get("command")
        else "Generate a report."
    )
    from moonmind.workflows.executions.execution_contract import (
        build_authoritative_workflow_input_snapshot,
    )

    command = build_authoritative_workflow_input_snapshot(
        task_payload={"instructions": instructions, "runtime": {"mode": "omnigent"}}
    )["objective"].get("runtimeCommand")
    # These are default-path journeys, independent of the test runner's rollout
    # configuration. Credential readiness and real plan authority stay intact.
    for key in (
        "MOONMIND_OMNIGENT_GENERIC_CODEX_QUALIFIED",
        "MOONMIND_OMNIGENT_RUNTIME_PROVIDER_ROLLOUT",
        "MOONMIND_OMNIGENT_RUNTIME_PROVIDER_ROLLBACK",
        "MOONMIND_CODEX_GENERIC_SELECTION",
        "MOONMIND_CODEX_GENERIC_QUALIFICATION_REF",
        "MOONMIND_CODEX_GENERIC_QUALIFICATION_DIGEST",
        "MOONMIND_CODEX_GENERIC_QUALIFICATION_RESULT",
        "MOONMIND_CODEX_GENERIC_QUALIFICATION_DIMENSIONS",
    ):
        monkeypatch.delenv(key, raising=False)
    await _serialize_finalization_on_sqlite(session_factory)
    workflow_id = "mm:generic-native-durable-journey"
    worker_root = tmp_path / "worker"
    server = NativeTurnServer(harness)
    running = await start_fake_omnigent_server(server)
    events = []
    gateway = NativeArtifactGateway(session_factory, events=events)
    try:
        for key, value in {
            "OMNIGENT_ENABLED": "1",
            "MOONMIND_OMNIGENT_GENERIC_HOST_ENABLED": "true",
            "OMNIGENT_SERVER_URL": running.base_url,
            "MOONMIND_OMNIGENT_HOST_SERVER_URL": running.base_url,
            "MOONMIND_OMNIGENT_EXPECTED_HOST_OWNER": OWNER,
            "OMNIGENT_IMAGE_REF": SERVER_IMAGE,
            "OMNIGENT_SHARED_HOST_IMAGE_REF": HOST_IMAGE,
            "OMNIGENT_OPENCODE_HOST_IMAGE_REF": HOST_IMAGE,
            "WORKFLOW_WORKSPACE_ROOT": str(worker_root),
            "WORKFLOW_DOCKER_DAEMON_MODE": "local",
            "NATIVE_JOURNEY_KEY": SECRET,
        }.items():
            monkeypatch.setenv(key, value)
        _configure_ready_host_image_pair(monkeypatch)
        monkeypatch.setenv("OMNIGENT_IMAGE_REF", SERVER_IMAGE)
        async with session_factory() as session:
            synchronized = await synchronize_omnigent_harness_catalog(session)
        catalog = await DbHarnessCatalogRepository(session_factory).load(
            synchronized["catalogRef"]
        )
        compiled = await _compile_plan(
            session_factory,
            harness,
            workflow_id,
            api_key=api_key,
            static_mode=static_mode,
            instructions=instructions,
        )
        # Verify the public HTTP enums survive discovery, alongside the real
        # deployment overlay's native OAuth/API-key admission contracts.
        observed = {row.id: row for row in catalog.snapshot.harnesses}
        assert observed["codex"].capabilities.integrationMode == "cli-subprocess"
        assert observed["claude-sdk"].capabilities.integrationMode == "sdk-in-process"
        assert observed["codex"].capabilities.authModel == "omnigent-credential"
        assert observed["claude-sdk"].capabilities.authModel == "omnigent-credential"
        assert observed[harness].capabilities.integrationMode == "native-server"
        assert observed[harness].capabilities.authModel == (
            "own-auth" if harness == "opencode-native" else "oauth_volume"
        )
        plan = compiled.envelope
        step_id = workflow_id + ":run:implement:execution:1"
        request = AgentExecutionRequest.model_validate(
            {
                "agentKind": "external",
                "agentId": "omnigent",
                "executionProfileRef": PROFILE,
                "correlationId": workflow_id,
                "idempotencyKey": "native-turn",
                "runtimeCommand": command,
                "resolvedSkillsetRef": compiled.resolved_skillset_ref,
                "omnigentExecutionPlan": compiled.binding.model_dump(
                    by_alias=True, mode="json"
                ),
                "stepExecution": {
                    "workflowId": workflow_id,
                    "runId": "run",
                    "logicalStepId": "implement",
                    "executionOrdinal": 1,
                    "stepExecutionId": step_id,
                    "runtimeContextPolicy": "fresh_agent_run",
                },
                "workspaceSpec": {
                    "workspaceSource": {"kind": "scratch"},
                    "workspaceLocator": {
                        "kind": "sandbox",
                        "workspaceId": hashlib.sha256(
                            f"{workflow_id}:{step_id}".encode()
                        ).hexdigest()[:24],
                        "relativePath": "repo",
                    },
                },
                "admittedProviderCapacity": {
                    "leaseOwnerId": "workflow-owned-grant",
                    "executionPlanRef": plan.planRef,
                    "agentRunWorkflowId": "workflow-owned-grant",
                    "agentRunRunId": "agent-run-1",
                    "stepExecutionId": step_id,
                    "idempotencyKey": "native-turn",
                    "profiles": [
                        {
                            "providerProfileRef": PROFILE,
                            "providerRuntimeId": {
                                "codex-native": "codex_cli",
                                "claude-native": "claude_code",
                                "opencode-native": "opencode",
                            }[harness],
                            "credentialGeneration": 3,
                        }
                    ],
                },
                "parameters": {
                    "publishMode": "none",
                    "metadata": {"moonmind": {"selectedSkill": _DURABLE_SKILL_NAME}},
                    "omnigent": {
                        "prompt": {"text": instructions},
                        "session": {},
                    },
                },
            }
        )
        manager = AdmittedManager(events, request=request, plan=plan)
        # The Activity consumes an already committed workflow grant. Preserve
        # its durable ledger row as well as the controlled manager inspection,
        # so native-interaction authority can check the same live capacity.
        admitted = request.admitted_provider_capacity
        async with session_factory() as session:
            provider_lease = ProviderProfileSlotLease(
                runtime_id=admitted.profiles[0].provider_runtime_id,
                workflow_id=admitted.lease_owner_id,
                profile_id=PROFILE,
                lease_id=admitted.lease_owner_id,
                owner_id=admitted.lease_owner_id,
                owner_kind="workflow",
                owner_is_workflow=True,
                purpose="execution_direct",
                compatibility_class="shared_execution",
                capacity_scope_ref=f"provider-profile:{PROFILE}",
                step_execution_id=step_id,
                idempotency_key=request.idempotency_key,
                execution_plan_ref=plan.planRef,
                credential_generation=3,
                fencing_generation=7,
                lease_state="held",
                expires_at=manager.expires_at,
            )
            session.add(provider_lease)
            await session.commit()
        monkeypatch.setattr(production, "TemporalClientAdapter", lambda: manager)
        docker = (StaticNativeDocker if static_mode else NativeDocker)(
            server=server, events=events, workflow_id=workflow_id
        )
        if static_mode:
            monkeypatch.setenv(
                "MOONMIND_DEPLOYMENT_PROJECT_NAME", "moonmind-test-static"
            )
            monkeypatch.setenv(
                "MOONMIND_DEPLOYMENT_LOCAL_PROJECT_DIR",
                str(Path(__file__).resolve().parents[3]),
            )
        monkeypatch.setattr(docker_backend, "run_runtime_command", docker.command)
        monkeypatch.setattr(
            credential_materializers, "run_runtime_command", docker.command
        )
        # The controlled host writes through this process instead of Docker's
        # uid namespace. Select its actual writer identity; root can still hand
        # off to the normal positive runtime uid without weakening ownership.
        resolve_host = production.OmnigentPlannedHostResolver.__call__

        async def resolve_controlled_host(resolver, selected_plan, *, request):
            host, policy = await resolve_host(resolver, selected_plan, request=request)
            return (
                host.model_copy(
                    update={
                        "runtime": {
                            **host.runtime,
                            "uid": os.getuid() or 1000,
                            "gid": os.getgid() or 1000,
                        },
                    }
                ),
                policy,
            )

        monkeypatch.setattr(
            production.OmnigentPlannedHostResolver,
            "__call__",
            resolve_controlled_host,
        )
        services = production.build_generic_omnigent_execution_services(
            session_factory=session_factory, artifact_gateway=gateway
        )
        # Direct realizer journeys cross the same input admission boundary as
        # Activity dispatch before any execution-scoped reader consumes a ref.
        await gateway.admit_execution_plan_inputs(request=request, plan=plan)
        docker.host_class, _policy = await services.planned_host_resolver(
            plan, request=request
        )
        realizer = services.realizer_registry.require("generic-omnigent-host@1")
        assert plan.payload.executionRealizerRef == realizer.ref
        yield SimpleNamespace(
            request=request,
            plan=plan,
            services=services,
            realizer=realizer,
            docker=docker,
            server=server,
            gateway=gateway,
            events=events,
            manager=manager,
            provider_lease_id=provider_lease.id,
            session_factory=session_factory,
            worker_root=worker_root,
            harness=harness,
            api_key=api_key,
            static_mode=static_mode,
        )
    finally:
        await running.runner.cleanup()


@pytest.mark.parametrize(
    ("native_journey", "terminal_status"),
    [
        ("codex-native", "idle"),
        ("claude-native", "idle"),
        ("claude-native-api-key", "idle"),
        ("claude-native-api-key", "failed"),
        ("codex-native-api-key", "idle"),
        ("codex-native-api-key", "failed"),
        ("opencode-native", "idle"),
        ("opencode-native", "failed"),
    ],
    indirect=["native_journey"],
)
async def test_registered_generic_native_turn_saves_result_and_cleans_owned_resources(
    native_journey,
    terminal_status,
):
    await assert_native_turn_saved_and_cleaned(native_journey, terminal_status)


@pytest.mark.parametrize(
    "native_journey",
    [
        {"harness": harness, "command": True}
        for harness in ("claude-native", "codex-native", "opencode-native")
    ],
    indirect=True,
)
async def test_shared_commands_follow_admitted_harness_and_preserve_completed_effects(
    native_journey,
):
    # Reuse production plan admission, real lifecycle/credential/artifact owners,
    # wire delivery, post-cleanup download and completed-attempt reconciliation.
    await assert_native_turn_saved_and_cleaned(native_journey)
    messages = [
        event
        for event in native_journey.server.events
        if event.get("type") == "message"
    ]
    assert len(messages) == 1
    text = messages[0]["data"]["content"][0]["text"]
    assert text.count("/review --local") == 1
    assert text.count("Generate a report.") == 1
    assert text.count(f"Selected skill: {_DURABLE_SKILL_NAME}") == 1
    if native_journey.harness == "claude-native":
        assert text.startswith("/review --local\n")
    else:
        assert text.startswith("Literal runtime command text:\n")


async def assert_native_turn_saved_and_cleaned(
    journey, terminal_status="idle", *, expected_messages=1
):
    """Reuse the full durable lifecycle proof for separately admitted plans."""
    request, plan, services = journey.request, journey.plan, journey.services
    realizer, server, docker = journey.realizer, journey.server, journey.docker
    manager, gateway, worker_root = (
        journey.manager,
        journey.gateway,
        journey.worker_root,
    )
    server.terminal_status = terminal_status
    result = await realizer.execute(request, plan)
    if terminal_status == "idle":
        assert result.failure_class is None, result
    else:
        assert result.failure_class is not None
        assert "failed" in result.summary
    expected_skill = _durable_skill_payload(_DURABLE_SKILL_NAME)
    assert server.delivered_skill == expected_skill
    admitted_skills = plan.payload.resolvedSkills
    admitted_manifest = await gateway.read_admitted_input(
        admitted_skills["resolvedSkillSetRef"], request=request
    )
    assert (
        "sha256:" + hashlib.sha256(admitted_manifest).hexdigest()
        == admitted_skills["resolvedSkillSetDigest"]
    )
    selected = json.loads(admitted_manifest)["skills"]
    assert len(selected) == 1 and selected[0]["skill_name"] == _DURABLE_SKILL_NAME
    assert (
        await gateway.read_admitted_input(selected[0]["content_ref"], request=request)
        == expected_skill
    )
    assert server.skills is not None and not server.skills.exists()
    assert len(server.session_ids) == 1 and docker.create_count == 1
    created = server.create_payloads[0]
    assert created["host_id"] == server.host_id
    assert (
        created["host_type"] == "external" and created["workspace"] == "/workspaces/run"
    )
    assert created["agent_id"] == journey.harness + "-agent"
    assert created["model_override"] == server.model
    assert created["terminal_launch_args"] == (
        [
            "--permission-mode",
            "bypassPermissions",
            "--disallowedTools",
            "ScheduleWakeup",
        ]
        if journey.harness == "claude-native"
        else []
    )
    messages = [event for event in server.events if event.get("type") == "message"]
    assert len(messages) == expected_messages
    assert "Generate a report." in messages[0]["data"]["content"][0]["text"]
    assert "sessions.stream" in server.route_calls
    assert not server.host_alive
    assert docker.volumes == {"journey_oauth": {}}
    assert (
        len(manager.inspections) == 1 and not manager.acquired and not manager.signals
    )
    async with journey.session_factory() as session:
        provider_lease = await session.get(
            ProviderProfileSlotLease, journey.provider_lease_id
        )
        assert provider_lease.lease_state == "held"
        assert provider_lease.purpose == "execution_direct"
        assert provider_lease.compatibility_class == "shared_execution"
        assert provider_lease.capacity_scope_ref == f"provider-profile:{PROFILE}"
        assert provider_lease.fencing_generation == 7
        assert provider_lease.released_at is None
    binding = await services.runtime_binding_store.get(
        stable_binding_id(
            execution_plan_ref=plan.planRef, idempotency_key=request.idempotency_key
        )
    )
    assert binding.state is RuntimeBindingState.cleaned
    host_lease = await services.host_lease_repository.get(binding.hostLeaseRef)
    assert host_lease.status == "cleaned"
    assert binding.providerLeases["primary-model"]["leaseOwnerIsWorkflow"] is True
    handle = next(iter(binding.credentialRuntimeHandles.values()))
    attachment = handle["attachments"][0]
    materialization = json.loads(await gateway.read_text(handle["attestationRef"]))
    cleanup = json.loads(
        await gateway.read_text(binding.attestationRefs["cleanupAttestationRef"])
    )
    credential_cleanup = cleanup["results"]["credentials"][0]
    if getattr(journey, "api_key", False):
        # The Profile's key reaches the host only as the read-only Omnigent
        # provider config, and the run-owned volume is removed at cleanup.
        assert handle["materializerRef"] == "omnigent-provider-config@1"
        assert attachment["targetPath"] == "/home/app/.moonmind-provider-config"
        assert attachment["accessMode"] == "read-only"
        assert docker.launch_env["OMNIGENT_CONFIG_HOME"] == (
            "/home/app/.moonmind-provider-config"
        )
        family, base_url = {
            "claude-native": ("anthropic", "https://api.anthropic.com"),
            "codex-native": ("openai", "https://api.openai.com/v1"),
        }[journey.harness]
        assert docker.auth_json == {
            "providers": {
                "moonmind": {
                    "kind": "key",
                    "default": [family, "pi"],
                    family: {
                        "base_url": base_url,
                        "api_key": SECRET,
                        "models": {"default": "model"},
                    },
                }
            }
        }
        assert materialization["providerBaseUrl"] == base_url
        assert credential_cleanup["removed"] is True
    elif journey.harness == "opencode-native":
        assert handle["materializerRef"] == "opencode-auth-json@1"
        assert attachment["targetPath"] == "/run/mm-credentials/opencode"
        assert attachment["accessMode"] == "read-only"
        assert docker.auth_json == {"openai": {"type": "api", "key": SECRET}}
        assert credential_cleanup["removed"] is True
        assert "auth.json" in docker.script
    else:
        runtime = journey.harness.removesuffix("-native")
        assert handle["materializerRef"] == f"{runtime}-oauth-home@1"
        assert attachment == {
            "kind": "volume",
            "sourceRef": "journey_oauth",
            "targetPath": f"/home/app/.{runtime}",
            "accessMode": "read-write",
        }
        assert materialization["secretCopied"] is False
        assert docker.auth_json is None
        assert credential_cleanup["removed"] is False
        assert credential_cleanup["evidence"]["preserved"] is True
        assert "journey_oauth" in docker.volumes
    assert docker.generations[attachment["sourceRef"]] == "3"
    assert SECRET not in json.dumps(docker.commands)
    assert SECRET not in json.dumps(binding.model_dump(mode="json"))
    saved = result.metadata["savedWorkspaceCheckpoint"]
    assert saved == binding.phaseResults["saved"]

    # Saved bytes and the external session checkpoint precede host removal.
    def artifact_id(ref):
        return ref.removeprefix("artifact:").lstrip("/")

    writes = {
        artifact_id(ref): index
        for index, (kind, ref) in enumerate(journey.events)
        if kind == "artifact_write"
    }
    host_removed = journey.events.index(("host_remove", docker.container_name))
    assert writes[artifact_id(saved["archiveRef"])] < host_removed
    assert writes[artifact_id(result.metadata["stateCheckpointRef"])] < host_removed
    assert (
        writes[artifact_id(binding.attestationRefs["cleanupAttestationRef"])]
        > host_removed
    )
    for index, (kind, _ref) in enumerate(journey.events):
        if kind == "volume_remove":
            assert (
                host_removed
                < index
                < writes[artifact_id(binding.attestationRefs["cleanupAttestationRef"])]
            )
    assert any(event.get("type") == "stop_session" for event in server.events)
    archive = await gateway.read_bytes(saved["archiveRef"])
    assert "sha256:" + hashlib.sha256(archive).hexdigest() == saved["archiveDigest"]
    with tarfile.open(fileobj=io.BytesIO(archive)) as bundle:
        assert bundle.extractfile(RESULT_PATH).read() == GENERATED
    manifest = json.loads(
        await gateway.read_text(result.metadata["captureManifestRef"])
    )
    assert manifest["patchUnavailable"] is True
    assert manifest["workspaceDiffs"] == []
    assert "comparison" in manifest["workspaceDiffsUnavailable"]
    captured = next(
        item for item in manifest["workspaceFiles"] if item["path"] == RESULT_PATH
    )
    assert await gateway.read_bytes(captured["artifactRef"]) == GENERATED
    shutil.rmtree(worker_root)
    assert await gateway.read_bytes(saved["archiveRef"]) == archive
    assert await gateway.read_bytes(captured["artifactRef"]) == GENERATED
    replay = await realizer.execute(request, plan)
    assert replay.metadata["savedWorkspaceCheckpoint"] == saved
    assert replay.failure_class == result.failure_class
    assert (
        docker.create_count == 1
        and len(server.session_ids) == 1
        and len(manager.inspections) == 1
    )
    return result


@pytest.mark.parametrize(
    "native_journey", ["claude-native-api-key", "codex-native-api-key"], indirect=True
)
async def test_api_key_route_cancellation_cleans_owned_host_and_provider_config(
    native_journey, monkeypatch
):
    """An explicit cancel mid-turn still removes the run-scoped key config."""
    journey = native_journey
    server, docker = journey.server, journey.docker
    monkeypatch.setattr(
        "moonmind.omnigent.activity_ownership.delivery_was_revoked", lambda: False
    )
    execution = asyncio.create_task(
        journey.realizer.execute(journey.request, journey.plan)
    )

    async def cancel_running_turn():
        execution.cancel()

    server.on_first_message = cancel_running_turn
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(execution, 30)

    assert len(server.session_ids) == 1 and docker.create_count == 1
    assert ("host_remove", docker.container_name) in journey.events
    assert not server.host_alive
    # Only the deployment-owned OAuth volume survives; the provider config
    # volume holding the key was owned by this attempt and is gone.
    assert docker.volumes == {"journey_oauth": {}}
    assert any(kind == "volume_remove" for kind, _ref in journey.events)
    binding = await journey.services.runtime_binding_store.get(
        stable_binding_id(
            execution_plan_ref=journey.plan.planRef,
            idempotency_key=journey.request.idempotency_key,
        )
    )
    assert binding.state is RuntimeBindingState.cleaned
    handle = next(iter(binding.credentialRuntimeHandles.values()))
    assert handle["materializerRef"] == "omnigent-provider-config@1"
    cleanup = json.loads(
        await journey.gateway.read_text(
            binding.attestationRefs["cleanupAttestationRef"]
        )
    )
    assert cleanup["results"]["credentials"][0]["removed"] is True
    host_lease = await journey.services.host_lease_repository.get(binding.hostLeaseRef)
    assert host_lease.status == "cleaned"
    # The workflow owns the provider grant; cancellation does not release it
    # from inside the Activity, and the key never reaches a recorded command.
    async with journey.session_factory() as session:
        provider_lease = await session.get(
            ProviderProfileSlotLease, journey.provider_lease_id
        )
        assert provider_lease.lease_state == "held"
    assert SECRET not in json.dumps(docker.commands)
    assert SECRET not in json.dumps(binding.model_dump(mode="json"))


@pytest.mark.parametrize(
    "native_journey", ["claude-native-api-key", "codex-native-api-key"], indirect=True
)
async def test_api_key_successor_restores_saved_work_with_fresh_provider_config(
    native_journey, monkeypatch
):
    """The restarted step resumes from the interrupted attempt's verified save.

    The interrupted attempt's host, workspace and run-scoped provider config
    are gone. The successor attempt of the same logical step rematerializes the
    key config from its Profile, restores the saved bytes from artifact
    storage, and leaves the interrupted attempt's completed effects alone.
    """
    journey = native_journey
    request, plan, realizer = journey.request, journey.plan, journey.realizer
    server, docker = journey.server, journey.docker
    interrupted = await assert_native_turn_saved_and_cleaned(journey, "failed")
    saved = interrupted.metadata["savedWorkspaceCheckpoint"]
    predecessor_config = docker.auth_json
    predecessor_workspace = server.workspace
    predecessor_binding_id = stable_binding_id(
        execution_plan_ref=plan.planRef, idempotency_key=request.idempotency_key
    )
    predecessor_binding = await journey.services.runtime_binding_store.get(
        predecessor_binding_id
    )
    created_volumes = [
        argv[-1]
        for argv in docker.commands
        if argv[:3] == ("docker", "volume", "create")
        and argv[-1].startswith("mm-omnigent-credential-")
    ]
    assert len(created_volumes) == 1

    # The Run workflow hands the successor the same plan and the verified save
    # as workspaceCheckpointRestoreRef (run.py interrupted-step carry).
    workflow_id = request.step_execution.workflow_id
    step_id = workflow_id + ":run:implement:execution:2"
    payload = request.model_dump(by_alias=True, mode="json", exclude_none=True)
    payload["idempotencyKey"] = "native-turn-2"
    payload["stepExecution"].update(executionOrdinal=2, stepExecutionId=step_id)
    payload["workspaceSpec"] = {
        "workspaceCheckpointRestoreRef": saved["archiveRef"],
        "workspaceLocator": {
            "kind": "sandbox",
            "workspaceId": hashlib.sha256(
                f"{workflow_id}:{step_id}".encode()
            ).hexdigest()[:24],
            "relativePath": "repo",
        },
    }
    # A restarted step runs in a new AgentRun child, so its credential
    # runtime identity is distinct from the interrupted attempt's.
    payload["admittedProviderCapacity"].update(
        stepExecutionId=step_id,
        idempotencyKey="native-turn-2",
        agentRunRunId="agent-run-2",
    )
    successor = AgentExecutionRequest.model_validate(payload)
    journey.manager.request = successor
    await journey.gateway.admit_execution_plan_inputs(request=successor, plan=plan)

    restored = {}
    observed_command = docker.command

    async def observe_launch(argv, **kwargs):
        if list(argv[:2]) == ["docker", "create"]:
            workspace = Path(_mounts(list(argv))["/workspaces/run"]["src"])
            report = workspace / RESULT_PATH
            restored["bytes"] = report.read_bytes() if report.exists() else None
        return await observed_command(argv, **kwargs)

    monkeypatch.setattr(docker_backend, "run_runtime_command", observe_launch)
    monkeypatch.setattr(credential_materializers, "run_runtime_command", observe_launch)
    server.terminal_status, server.status = "idle", "idle"
    server.items, server.events = [], []
    docker.auth_json = None

    result = await realizer.execute(successor, plan)

    assert result.failure_class is None, result
    # Saved work came from artifact storage: the interrupted attempt's worker
    # root was deleted before the successor launched.
    assert restored == {"bytes": GENERATED}
    assert server.workspace != predecessor_workspace
    assert len(server.session_ids) == 2 and docker.create_count == 2
    messages = [event for event in server.events if event.get("type") == "message"]
    assert len(messages) == 1
    # The key config was materialized again into a new run-scoped volume and
    # removed with the successor's host.
    created_volumes = [
        argv[-1]
        for argv in docker.commands
        if argv[:3] == ("docker", "volume", "create")
        and argv[-1].startswith("mm-omnigent-credential-")
    ]
    assert len(created_volumes) == 2 and created_volumes[0] != created_volumes[1]
    assert docker.auth_json == predecessor_config
    assert docker.volumes == {"journey_oauth": {}}
    binding = await journey.services.runtime_binding_store.get(
        stable_binding_id(
            execution_plan_ref=plan.planRef, idempotency_key=successor.idempotency_key
        )
    )
    assert binding.state is RuntimeBindingState.cleaned
    assert binding.hostLeaseRef != predecessor_binding.hostLeaseRef
    handle = next(iter(binding.credentialRuntimeHandles.values()))
    assert handle["materializerRef"] == "omnigent-provider-config@1"
    assert SECRET not in json.dumps(binding.model_dump(mode="json"))
    # The interrupted attempt's recorded outcome is untouched, and redelivering
    # it neither launches a host nor repeats its turn.
    assert (
        await journey.services.runtime_binding_store.get(predecessor_binding_id)
    ) == predecessor_binding
    journey.manager.request = request
    replay = await realizer.execute(request, plan)
    assert replay.metadata["savedWorkspaceCheckpoint"] == saved
    assert replay.failure_class == interrupted.failure_class
    assert len(server.session_ids) == 2 and docker.create_count == 2


@pytest.mark.parametrize("owner_closed", [True, False])
@pytest.mark.parametrize("native_journey", ["codex-native"], indirect=True)
async def test_credential_maintenance_drains_generic_host_mounting_oauth_home(
    native_journey, owner_closed
):
    """OAuth maintenance releases a generic host left holding the OAuth home.

    The turn completes but its host removal fails, so the binding stays
    cleanup-pending with the Profile's OAuth volume mounted read-write.
    Credential maintenance releases it through the generic realizer's own
    reconciliation once its workflow has closed, and refuses to proceed while
    that workflow is still open.
    """
    from moonmind.omnigent.generic_host_janitor import GenericOmnigentHostJanitor
    from moonmind.omnigent.harness_platform.failures import HarnessPlatformError

    journey = native_journey
    services, docker, server = journey.services, journey.docker, journey.server
    docker.fail_host_removal = True
    result = await journey.realizer.execute(journey.request, journey.plan)
    assert result.failure_class is None, result
    binding_id = stable_binding_id(
        execution_plan_ref=journey.plan.planRef,
        idempotency_key=journey.request.idempotency_key,
    )
    binding = await services.runtime_binding_store.get(binding_id)
    assert binding.state is not RuntimeBindingState.cleaned
    host_lease = await services.host_lease_repository.get(binding.hostLeaseRef)
    assert host_lease.status != "cleaned"
    assert server.host_alive
    assert docker.mounts["/home/app/.codex"]["src"] == "journey_oauth"
    assert "readonly" not in docker.mounts["/home/app/.codex"]

    docker.fail_host_removal = False
    owners = []

    async def owner_has_closed(candidate):
        owners.append(candidate.bindingId)
        return owner_closed

    janitor = GenericOmnigentHostJanitor(
        host_leases=services.host_lease_repository,
        runtime_bindings=services.runtime_binding_store,
        realizer=services.generic_realizer,
        owner_has_closed=owner_has_closed,
    )
    # Another Profile's maintenance leaves this binding alone.
    assert await janitor.drain_profile("another-profile") == {"drained": 0}
    assert owners == []
    if not owner_closed:
        with pytest.raises(HarnessPlatformError, match=binding_id):
            await janitor.drain_profile(PROFILE)
        assert server.host_alive
        assert (
            await services.host_lease_repository.get(binding.hostLeaseRef)
        ).status != "cleaned"
        return
    assert await janitor.drain_profile(PROFILE) == {"drained": 1}
    assert owners == [binding_id]
    assert not server.host_alive
    assert ("host_remove", docker.container_name) in journey.events
    assert (
        await services.host_lease_repository.get(binding.hostLeaseRef)
    ).status == "cleaned"
    assert (
        await services.runtime_binding_store.get(binding_id)
    ).state is RuntimeBindingState.cleaned
    # The Profile-owned OAuth home is preserved for the maintenance itself.
    assert "journey_oauth" in docker.volumes
    # A repeated maintenance attempt finds nothing left to drain.
    assert await janitor.drain_profile(PROFILE) == {"drained": 0}
