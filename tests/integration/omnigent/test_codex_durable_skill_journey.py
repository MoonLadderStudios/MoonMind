"""Shared controlled transports for the durable native Omnigent journeys.

``test_generic_native_durable_journey`` builds on these substitutes:

* ``CodexDocker`` records shared server and egress Docker commands instead of
  running them and answers the egress gateway and image inspections.
* ``RecordingArtifactGateway`` is the durable Temporal artifact gateway,
  recording each committed write in shared order.
* ``HermeticProviderLeases`` is the Provider Profile manager transport: it
  grants one isolated test lease and records its verified release.

No provider credentials, login or paid model turn are involved.
"""

from __future__ import annotations

import hashlib
import json
import platform
from typing import Any

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from moonmind.config.settings import settings
from moonmind.omnigent.bridge_artifacts import TemporalOmnigentArtifactGateway
from moonmind.security.egress import (
    CONTROL_PLANE_NETWORK_REF,
    DEFAULT_EGRESS_PROFILE,
    EGRESS_FILE_DIGESTS,
    ENFORCER_IMPLEMENTATION,
    OMNIGENT_EGRESS_PROFILE,
)
from tests.helpers.omnigent_conformance import FakeOmnigentServer

pytestmark = [pytest.mark.asyncio, pytest.mark.integration, pytest.mark.integration_ci]

PROFILE_ID = "codex"
SERVER_IMAGE = "ghcr.io/omnigent-ai/omnigent-server@sha256:" + "8" * 64
HOST_IMAGE = "ghcr.io/moonladderstudios/omnigent-host-moonmind@sha256:" + "9" * 64


@pytest_asyncio.fixture()
async def session_factory(tmp_path, monkeypatch, empty_sqlite_database):
    engine = create_async_engine(f"sqlite+aiosqlite:///{empty_sqlite_database}")
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


class CodexDocker:
    """Record shared server and egress Docker commands instead of running them."""

    def __init__(
        self,
        *,
        workflow_id: str,
        server: FakeOmnigentServer,
        run_command: Any,
        events: list[tuple[str, str]],
    ) -> None:
        self.server = server
        # Non-Docker commands (the trusted Git clone) run for real.
        self.run_command = run_command
        self.commands: list[tuple[str, ...]] = []
        # Shared with the artifact gateway to order host stops against writes.
        self.events = events
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
        if args[:2] == ("docker", "compose") and " ps " in joined:
            assert args[-1] == "omnigent"
            return (0, f"{self.server_container_id}\n", "")
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
        raise AssertionError(f"unexpected Docker inspect: {format_value} {identity}")


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


