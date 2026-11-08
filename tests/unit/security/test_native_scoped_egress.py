"""Native leases reuse scoped ownership and never renew transport authority."""

import base64
import io
import json
import tarfile
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from moonmind.schemas.container_job_models import WorkloadProfileSelection
from moonmind.security import scoped_egress
from moonmind.security.egress_policy import ScopedEgressPolicy
from moonmind.security.native_network import NativeNetworkSelection
from moonmind.security.native_tcp_gateway import NativeTCPConfig
from moonmind.security.scoped_egress import ScopedEgressLease
from tests.unit.security.test_scoped_egress import LeaseDocker
from tests.unit.security.test_scoped_egress import lease_setup as _lease_setup

lease_setup = _lease_setup


def selection():
    return NativeNetworkSelection.model_validate(
        {
            "targets": [
                {
                    "hosts": ["scan.example.com"],
                    "ports": [8443],
                    "purpose": "live-target",
                }
            ],
            "limits": {
                "maxConnections": 3,
                "maxConcurrentConnections": 1,
                "maxConnectionsPerSecond": 2,
                "maxBytes": 1000,
                "maxDurationSeconds": 60,
            },
        }
    )


def lease(runner):
    native = selection()
    policy = ScopedEgressPolicy.model_validate(
        {
            "policyRef": "native-one",
            "allowedWorkflowIds": ["one"],
            "allowedImageSourceRefs": ["client"],
            "nativeNetwork": native.model_dump(by_alias=True),
        }
    )
    return ScopedEgressLease(
        runner,
        policy,
        ownership="native-owned",
        job_id="native-job",
        workload_ref="native-client",
        native_selection=native,
    )


def test_native_does_not_offer_http_proxy_alternative():
    value = lease(AsyncMock())
    assert "MOONMIND_EGRESS_PROXY=" + value.proxy_url in value.proxy_env()
    assert "HTTP_PROXY=" in value.proxy_env()
    assert "HTTPS_PROXY=" in value.proxy_env()


@pytest.mark.asyncio
async def test_retained_state_prevents_dns_readmission(monkeypatch):
    runner = AsyncMock()
    value = lease(runner)
    monkeypatch.setattr(
        value, "_optional", AsyncMock(side_effect=[None, {"Name": value.state_ref}])
    )
    dns = AsyncMock()
    monkeypatch.setattr(scoped_egress, "_public_answers", dns)
    with pytest.raises(RuntimeError, match="retained native state"):
        await value.prepare(timeout_seconds=120)
    dns.assert_not_called()


@pytest.mark.asyncio
async def test_native_rejects_public_ipv6_even_with_safe_ipv4(monkeypatch):
    value = lease(AsyncMock())
    with pytest.raises(RuntimeError, match="IPv4"):
        value._validate_native_answers(
            {"scan.example.com": ["93.184.216.34", "2606:4700:4700::1111"]}
        )


class NativeDocker(LeaseDocker):
    def __init__(self):
        super().__init__()
        self.volumes = {}
        self.native_healthy = True

    async def __call__(self, args):
        args = tuple(args)
        if args[0] == "ps" and any(v.startswith("volume=") for v in args):
            self.calls.append(args)
            name = next(v.split("=", 1)[1] for v in args if v.startswith("volume="))
            ids = [
                v["Id"]
                for v in self.containers.values()
                if any(m.get("Name") == name for m in v.get("Mounts", []))
            ]
            return 0, "\n".join(ids).encode(), b""
        if args[:2] == ("volume", "inspect"):
            self.calls.append(args)
            if args[-1] not in self.volumes:
                return 1, b"", b"not found"
            return 0, json.dumps(self.volumes[args[-1]]).encode(), b""
        if args[:2] == ("volume", "ls"):
            self.calls.append(args)
            return 0, "\n".join(self.volumes).encode(), b""
        if args[:2] == ("volume", "create"):
            self.calls.append(args)
            self.volumes.setdefault(
                args[-1],
                {
                    "Name": args[-1],
                    "Driver": "local",
                    "Scope": "local",
                    "Options": {},
                    "Labels": dict(
                        v.split("=", 1) for v in self.values(args, "--label")
                    ),
                },
            )
            return 0, args[-1].encode(), b""
        if args[:2] == ("volume", "rm"):
            self.calls.append(args)
            del self.volumes[args[-1]]
            return 0, b"", b""
        if args[0] == "cp":
            self.calls.append(args)
            name = args[1].rsplit("/", 1)[1]
            if name == "moonmind-native-egress":
                stream = io.BytesIO()
                with tarfile.open(fileobj=stream, mode="w") as archive:
                    info = tarfile.TarInfo(name)
                    info.type = tarfile.DIRTYPE
                    archive.addfile(info)
                    for filename in ("startup.json", "snapshot.json"):
                        if filename in self.files:
                            raw = self.files[filename]
                            info = tarfile.TarInfo(name + "/" + filename)
                            info.size = len(raw)
                            archive.addfile(info, io.BytesIO(raw))
                return 0, stream.getvalue(), b""
            raw = self.files.get(name)
            if raw is None:
                return 1, b"", b"missing evidence"
            stream = io.BytesIO()
            with tarfile.open(fileobj=stream, mode="w") as archive:
                info = tarfile.TarInfo(name)
                info.size = len(raw)
                archive.addfile(info, io.BytesIO(raw))
            return 0, stream.getvalue(), b""
        if args[0] == "exec" and args[2] == "python":
            self.calls.append(args)
            return (0 if self.native_healthy else 1), b"", b""
        result = await super().__call__(args)
        if result[0]:
            return result
        if args[0] == "create" and "--mount" in args:
            target = self.container(self.values(args, "--name")[0])
            target["RestartCount"] = 0
            target["State"]["StartedAt"] = "0001-01-01T00:00:00Z"
            for mount in self.values(args, "--mount"):
                parts = dict(part.split("=", 1) for part in mount.split(","))
                target["Mounts"].append(
                    {
                        "Type": "volume",
                        "Name": parts["source"],
                        "Destination": parts["destination"],
                        "RW": True,
                        "Driver": "local",
                    }
                )
        if args[0] == "start":
            target = self.container(args[-1])
            target["State"]["StartedAt"] = "2026-11-08T00:00:00Z"
            env = dict(v.split("=", 1) for v in target["Config"]["Env"])
            if "native_tcp_gateway" in str(target["Config"]["Cmd"]):
                config = NativeTCPConfig.from_mapping(
                    json.loads(base64.b64decode(env["MOONMIND_EGRESS_CONFIG"]))
                )
                startup = {
                    "leaseId": config.lease_id,
                    "configDigest": config.digest,
                    "deadlineUnix": config.deadline_unix,
                    "pid": 10,
                    "processStart": "1234",
                }
                self.files["startup.json"] = json.dumps(startup).encode()
                self.files["snapshot.json"] = json.dumps(
                    {
                        **startup,
                        "schemaVersion": 1,
                        "status": "running",
                        "attempts": 0,
                        "activeConnections": 0,
                        "bytesReserved": 0,
                        "denials": {},
                        "endpoints": config.to_mapping()["endpoints"],
                    }
                ).encode()
        return result


@pytest.fixture
def native_setup(monkeypatch):
    docker = NativeDocker()
    value = lease(docker)
    monkeypatch.setattr(
        scoped_egress,
        "attest_docker_egress",
        AsyncMock(
            return_value=SimpleNamespace(gateway_image_digest="sha256:" + "1" * 64)
        ),
    )
    dns = lambda *a, **k: [(2, 1, 6, "", ("93.184.216.34", 8443))]
    monkeypatch.setattr(scoped_egress.socket, "getaddrinfo", dns)
    return value, docker


@pytest.mark.asyncio
async def test_native_live_restart_preserves_deadline_dns_and_counters(
    native_setup, monkeypatch
):
    value, docker = native_setup
    first = await value.prepare(timeout_seconds=120)
    assert first["expiresAt"] <= time.time() + 60
    counters = json.loads(docker.files["snapshot.json"])
    counters.update(attempts=2, bytesReserved=700, denials={"connection_limit": 1})
    docker.files["snapshot.json"] = json.dumps(counters).encode()
    monkeypatch.setattr(
        scoped_egress.socket,
        "getaddrinfo",
        lambda *a, **k: pytest.fail("DNS must not run again"),
    )
    restored = await lease(docker).prepare(timeout_seconds=600)
    assert restored["expiresAt"] == first["expiresAt"]
    assert restored["snapshotDigest"] == first["snapshotDigest"]
    assert restored["nativeNetwork"]["attempts"] == 2
    assert restored["nativeNetwork"]["bytesReserved"] == 700
    assert restored["deniedConnectionCount"] == 1
    assert len([c for c in docker.calls if c[0] == "create"]) == 1


@pytest.mark.asyncio
async def test_native_created_unstarted_recovers_original_state(
    native_setup, monkeypatch
):
    value, docker = native_setup
    docker.fail_start = True
    with pytest.raises(RuntimeError, match="start failed"):
        await value.prepare(timeout_seconds=120)
    monkeypatch.setattr(
        scoped_egress.socket,
        "getaddrinfo",
        lambda *a, **k: pytest.fail("DNS must not run again"),
    )
    evidence = await lease(docker).prepare(timeout_seconds=600)
    assert evidence["nativeNetwork"]["attempts"] == 0
    assert len(docker.volumes) == 1
    assert len([c for c in docker.calls if c[0] == "create"]) == 1


@pytest.mark.asyncio
async def test_native_missing_gateway_with_state_cannot_restart(native_setup):
    value, docker = native_setup
    await value.prepare(timeout_seconds=120)
    await docker(("rm", "--force", value.gateway_ref))
    with pytest.raises(RuntimeError, match="retained native state"):
        await lease(docker).prepare(timeout_seconds=120)


@pytest.mark.asyncio
async def test_native_terminal_counters_survive_health_loss_and_cleanup(native_setup):
    value, docker = native_setup
    await value.prepare(timeout_seconds=120)
    counters = json.loads(docker.files["snapshot.json"])
    counters.update(
        attempts=3, bytesReserved=1000, denials={"byte_limit": 1}, status="closed"
    )
    docker.files["snapshot.json"] = json.dumps(counters).encode()
    await docker(("stop", value.gateway_ref))
    docker.native_healthy = False
    evidence = await value.terminal_evidence()
    assert evidence["nativeNetwork"]["bytesReserved"] == 1000
    assert evidence["deniedConnectionCount"] == 1
    assert evidence["coverage"] == "partial"
    await value.cleanup()
    assert not docker.volumes
    assert set(docker.containers) == {"moonmind-test-target"}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "tamper",
    [
        "volume-owner",
        "volume-deadline",
        "mount",
        "command",
        "selection",
        "counter",
        "startup",
        "deadline",
    ],
)
async def test_native_tampering_fails_closed(native_setup, tamper):
    value, docker = native_setup
    await value.prepare(timeout_seconds=120)
    gateway = docker.container(value.gateway_ref)
    if tamper == "volume-owner":
        docker.volumes[value.state_ref]["Labels"]["moonmind.ownership"] = "other"
    elif tamper == "volume-deadline":
        docker.volumes[value.state_ref]["Labels"]["moonmind.expires_at"] = "1"
    elif tamper == "mount":
        gateway["Mounts"][0]["Name"] = "other"
    elif tamper == "command":
        gateway["Config"]["Cmd"] = ["unmetered-proxy"]
    elif tamper == "selection":
        value.native_selection = selection().model_copy(
            update={
                "limits": selection().limits.model_copy(update={"max_connections": 4})
            }
        )
    elif tamper == "counter":
        counters = json.loads(docker.files["snapshot.json"])
        counters["attempts"] = 4
        docker.files["snapshot.json"] = json.dumps(counters).encode()
    elif tamper == "startup":
        docker.files["startup.json"] = b"{}"
    else:
        gateway["Config"]["Labels"]["moonmind.expires_at"] = "1"
    with pytest.raises(RuntimeError):
        await value.attest()


@pytest.mark.asyncio
async def test_missing_final_counters_explicitly_unverified(native_setup):
    value, docker = native_setup
    await value.prepare(timeout_seconds=120)
    del docker.files["snapshot.json"]
    evidence = await value.terminal_evidence()
    assert evidence["coverage"] == "unverified"
    assert "native_counter_evidence_unavailable" in evidence["limitations"]


@pytest.mark.asyncio
async def test_native_state_volume_cannot_be_shared_with_another_container(
    native_setup,
):
    value, docker = native_setup
    await value.prepare(timeout_seconds=120)
    docker.containers["intruder"] = {
        "Id": "other-id",
        "Mounts": [{"Name": value.state_ref}],
    }
    with pytest.raises(RuntimeError, match="exclusive"):
        await value.attest()


@pytest.mark.asyncio
async def test_native_cleanup_stops_gateway_before_final_counter_read(native_setup):
    value, docker = native_setup
    await value.prepare(timeout_seconds=120)
    await value.stop_native()
    assert docker.container(value.gateway_ref)["State"]["Running"] is False
    evidence = await value.terminal_evidence()
    assert "nativeNetwork" in evidence


@pytest.mark.asyncio
async def test_backend_uses_selected_native_subset_not_deployment_grant(lease_setup):
    _, _, request, backend, _ = lease_setup
    requested = selection()
    policy = lease(AsyncMock()).policy
    wider = requested.model_copy(
        update={"limits": requested.limits.model_copy(update={"max_connections": 9})}
    )
    policy = policy.model_copy(update={"native_network": wider})
    request.request.spec.workload_profile = WorkloadProfileSelection(
        profileRef="native", mode="probe", nativeNetwork=requested
    )
    backend._settings = SimpleNamespace(authorize_egress=lambda request: policy)
    scoped = backend._scoped_lease(request)
    assert scoped.native_selection == requested
    assert scoped.native_selection.limits.max_connections == 3


@pytest.mark.asyncio
async def test_backend_cleanup_publishes_and_links_final_native_counters(
    lease_setup, native_setup
):
    _, _, request, backend, published = lease_setup
    prototype, docker = native_setup
    request.request.spec.workload_profile = WorkloadProfileSelection(
        profileRef="native", mode="probe", nativeNetwork=selection()
    )
    backend._runner = docker
    value = ScopedEgressLease(
        docker,
        prototype.policy,
        ownership=request.ownership_token,
        job_id=request.job_id,
        workload_ref=backend._name(request),
        native_selection=selection(),
    )
    await value.prepare(timeout_seconds=120)
    result = await backend.cleanup(request)
    assert result.cleanup_succeeded is True
    final = published[request.job_id + "-native-egress-final.json"]
    assert final["nativeNetwork"]["attempts"] == 0
    lifecycle = published[request.job_id + "-egress-lifecycle.json"]
    assert lifecycle["nativeEgressFinalRef"].endswith("-native-egress-final.json")
    assert not docker.volumes
    stop_index = next(i for i, call in enumerate(docker.calls) if call[0] == "stop")
    final_read = max(i for i, call in enumerate(docker.calls) if call[0] == "cp")
    assert stop_index < final_read


@pytest.mark.asyncio
async def test_backend_publish_failure_retains_stopped_native_state(
    lease_setup, native_setup
):
    _, _, request, backend, _ = lease_setup
    prototype, docker = native_setup
    request.request.spec.workload_profile = WorkloadProfileSelection(
        profileRef="native", mode="probe", nativeNetwork=selection()
    )
    backend._runner = docker
    value = ScopedEgressLease(
        docker,
        prototype.policy,
        ownership=request.ownership_token,
        job_id=request.job_id,
        workload_ref=backend._name(request),
        native_selection=selection(),
    )
    await value.prepare(timeout_seconds=120)
    publish = backend._publish

    async def fail_final(request, name, data):
        if name.endswith("-native-egress-final.json"):
            raise RuntimeError("publisher unavailable")
        return await publish(request, name, data)

    backend._publish = fail_final
    result = await backend.cleanup(request)
    assert result.cleanup_succeeded is False
    assert value.state_ref in docker.volumes
    assert docker.container(value.gateway_ref)["State"]["Running"] is False


@pytest.mark.asyncio
async def test_native_dns_checks_every_answer_and_exclusion(native_setup, monkeypatch):
    value, _ = native_setup
    target = (
        selection().targets[0].model_copy(update={"exclusions": ("93.184.216.0/24",)})
    )
    value.native_selection = selection().model_copy(update={"targets": (target,)})
    with pytest.raises(RuntimeError, match="excluded"):
        await value.prepare(timeout_seconds=120)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "answers",
    [
        ["93.184.216.34", "127.0.0.1"],
        ["93.184.216.34", "2606:4700:4700::1111"],
        ["93.184.216.34", "169.254.169.254"],
    ],
)
async def test_native_mixed_dns_answer_set_is_rejected(
    native_setup, monkeypatch, answers
):
    value, docker = native_setup
    monkeypatch.setattr(
        scoped_egress.socket,
        "getaddrinfo",
        lambda *a, **k: [(2, 1, 6, "", (address, 8443)) for address in answers],
    )
    with pytest.raises(RuntimeError):
        await value.prepare(timeout_seconds=120)
    assert not docker.volumes
    assert not any(c[0] == "create" for c in docker.calls)


@pytest.mark.asyncio
async def test_native_private_route_preserves_container_identity(native_setup):
    value, docker = native_setup
    target = (
        selection()
        .targets[0]
        .model_copy(
            update={
                "hosts": ("10.247.0.4",),
                "target_network_ref": "moonmind-test-lab",
                "target_container_ref": "moonmind-test-target",
            }
        )
    )
    value.native_selection = selection().model_copy(update={"targets": (target,)})
    value.policy = value.policy.model_copy(
        update={
            "native_network": value.native_selection,
            "policy_ref": "assessment-one",
        }
    )
    await value.prepare(timeout_seconds=120)
    docker.containers["moonmind-test-target"]["Id"] = "replaced-id"
    docker.networks["moonmind-test-lab"]["Containers"] = {
        "replaced-id": {},
        docker.container(value.gateway_ref)["Id"]: {},
    }
    with pytest.raises(RuntimeError, match="identity changed"):
        await value.attest()


@pytest.mark.asyncio
async def test_native_numeric_private_target_must_equal_inspected_ip(native_setup):
    value, _ = native_setup
    target = (
        selection()
        .targets[0]
        .model_copy(
            update={
                "hosts": ("10.247.0.5",),
                "target_network_ref": "moonmind-test-lab",
                "target_container_ref": "moonmind-test-target",
            }
        )
    )
    value.native_selection = selection().model_copy(update={"targets": (target,)})
    value.policy = value.policy.model_copy(
        update={
            "native_network": value.native_selection,
            "policy_ref": "assessment-one",
        }
    )
    with pytest.raises(RuntimeError, match="differs from inspected"):
        await value.prepare(timeout_seconds=120)


@pytest.mark.asyncio
async def test_volume_inspect_failure_is_not_absence(native_setup):
    value, docker = native_setup
    await value.prepare(timeout_seconds=120)
    await docker(("rm", "--force", value.gateway_ref))

    async def unavailable(args):
        if tuple(args[:2]) == ("volume", "inspect"):
            return 1, b"", b"daemon inspect failed"
        return await docker(args)

    value.runner = unavailable
    with pytest.raises(RuntimeError, match="identity is unavailable"):
        await value.prepare(timeout_seconds=120)
    assert len([c for c in docker.calls if c[0] == "create"]) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("field", ["unknown", "attempts", "processStart"])
async def test_final_native_evidence_rejects_malformed_counters(native_setup, field):
    value, docker = native_setup
    await value.prepare(timeout_seconds=120)
    counters = json.loads(docker.files["snapshot.json"])
    counters[field] = "invalid"
    docker.files["snapshot.json"] = json.dumps(counters).encode()
    final = await value.terminal_evidence()
    assert final["coverage"] == "unverified"
    assert "nativeNetwork" not in final
    assert final["evidenceError"]


@pytest.mark.asyncio
async def test_native_resolution_keeps_all_answers_and_pins_one(
    native_setup, monkeypatch
):
    value, _ = native_setup
    monkeypatch.setattr(
        scoped_egress.socket,
        "getaddrinfo",
        lambda *a, **k: [
            (2, 1, 6, "", (address, 8443))
            for address in ["93.184.216.35", "93.184.216.34"]
        ],
    )
    result = await value.prepare(timeout_seconds=120)
    assert result["resolvedAddresses"]["scan.example.com"] == [
        "93.184.216.34",
        "93.184.216.35",
    ]
    assert result["nativeNetwork"]["endpoints"] == [
        {"authority": "scan.example.com:8443", "address": "93.184.216.34", "port": 8443}
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["read", "invalid"])
async def test_backend_unverified_final_counters_survive_for_cleanup_retry(
    lease_setup, native_setup, failure
):
    _, _, request, backend, published = lease_setup
    prototype, docker = native_setup
    request.request.spec.workload_profile = WorkloadProfileSelection(
        profileRef="native", mode="probe", nativeNetwork=selection()
    )
    value = ScopedEgressLease(
        docker,
        prototype.policy,
        ownership=request.ownership_token,
        job_id=request.job_id,
        workload_ref=backend._name(request),
        native_selection=selection(),
    )
    await value.prepare(timeout_seconds=120)
    original = docker.files["snapshot.json"]

    async def fail_read(args):
        if args[0] == "cp":
            return 1, b"", b"transient daemon read failure"
        return await docker(args)

    backend._runner = fail_read if failure == "read" else docker
    if failure == "invalid":
        docker.files["snapshot.json"] = b"{}"
    result = await backend.cleanup(request)
    assert result.cleanup_succeeded is False
    assert value.state_ref in docker.volumes
    assert docker.container(value.gateway_ref)["State"]["Running"] is False
    final = published[request.job_id + "-native-egress-final.json"]
    assert final["coverage"] == "unverified"
    assert "native_counter_evidence_unavailable" in final["limitations"]
    backend._runner = docker
    docker.files["snapshot.json"] = original
    retried = await backend.cleanup(request)
    assert retried.cleanup_succeeded is True
    assert not docker.volumes
    final = published[request.job_id + "-native-egress-final.json"]
    assert final["nativeNetwork"]["attempts"] == 0


@pytest.mark.asyncio
async def test_native_live_and_final_artifacts_retain_allocation_and_target_identity(
    native_setup,
):
    value, _ = native_setup
    live = await value.prepare(timeout_seconds=120)
    final = await value.terminal_evidence()
    for evidence in (live, final):
        assert evidence["targetIdentities"] == {}
        assert evidence["nativeLimits"] == selection().limits.model_dump(
            mode="json", by_alias=True
        )
        assert evidence["nativeSelectionDigest"].startswith("sha256:")
    assert live["nativeSelectionDigest"] == final["nativeSelectionDigest"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "condition",
    [
        "empty",
        "marker",
        "started",
        "restarted",
        "malformed",
        "read-failure",
        "changed-after-read",
        "command",
    ],
)
async def test_unstarted_native_cleanup_requires_independent_empty_state_witness(
    lease_setup, native_setup, condition
):
    _, _, request, backend, published = lease_setup
    prototype, docker = native_setup
    request.request.spec.workload_profile = WorkloadProfileSelection(
        profileRef="native", mode="probe", nativeNetwork=selection()
    )
    value = ScopedEgressLease(
        docker,
        prototype.policy,
        ownership=request.ownership_token,
        job_id=request.job_id,
        workload_ref=backend._name(request),
        native_selection=selection(),
    )
    docker.fail_start = True
    with pytest.raises(RuntimeError, match="start failed"):
        await value.prepare(timeout_seconds=120)
    gateway = docker.container(value.gateway_ref)
    if condition == "marker":
        docker.files["startup.json"] = b"old one-shot marker"
    elif condition == "started":
        gateway["State"]["StartedAt"] = "2026-11-08T00:00:00Z"
    elif condition == "restarted":
        gateway["RestartCount"] = 1
    elif condition == "command":
        gateway["Config"]["Entrypoint"] = ["/other"]

    async def witness_runner(args):
        if args[0] == "cp" and args[1].endswith("/moonmind-native-egress"):
            if condition == "malformed":
                return 0, b"not a tar", b""
            if condition == "read-failure":
                return 1, b"", b"daemon read failure"
            result = await docker(args)
            if condition == "changed-after-read":
                gateway["State"]["StartedAt"] = "2026-11-08T00:00:00Z"
            return result
        return await docker(args)

    backend._runner = witness_runner
    result = await backend.cleanup(request)
    final = published[request.job_id + "-native-egress-final.json"]
    if condition == "empty":
        assert result.cleanup_succeeded is True
        assert final["nativeNoUseWitness"]["stateDirectoryEmpty"] is True
        assert final["nativeNoUseWitness"]["gatewayIdentity"] == gateway["Id"]
        assert (
            "nativeNetwork" not in final
        )  # Observed no-start evidence, not invented runtime counters.
        assert not docker.volumes
    else:
        assert result.cleanup_succeeded is False
        assert value.state_ref in docker.volumes
        assert final["coverage"] == "unverified"
        assert "nativeNoUseWitness" not in final


def test_native_numeric_snapshot_cannot_retarget_another_public_address():
    value = lease(AsyncMock())
    target = selection().targets[0].model_copy(update={"hosts": ("93.184.216.34",)})
    value.native_selection = selection().model_copy(update={"targets": (target,)})
    for addresses in (["93.184.216.35"], ["93.184.216.34", "93.184.216.35"]):
        with pytest.raises(RuntimeError, match="numeric"):
            value._validate_native_answers({"93.184.216.34": addresses})
    value._validate_native_answers({"93.184.216.34": ["93.184.216.34"]})


@pytest.mark.asyncio
async def test_production_runner_preserves_native_archives_but_redacts_diagnostics(
    tmp_path,
):
    import sys

    from moonmind.workflows.temporal.container_job_backend import (
        DockerContainerJobBackend,
    )

    executable = tmp_path / "docker-archive-fixture"
    executable.write_text(
        "#!" + sys.executable + "\n"
        "import io,json,sys,tarfile\n"
        'payload=json.dumps({"authority":"session.example.com:587"}).encode()\n'
        "stream=io.BytesIO()\n"
        'with tarfile.open(fileobj=stream,mode="w") as archive:\n'
        ' info=tarfile.TarInfo("snapshot.json"); info.size=len(payload)\n'
        " archive.addfile(info,io.BytesIO(payload))\n"
        "sys.stdout.buffer.write(stream.getvalue())\n"
        'sys.stderr.write("password=fixture-private-value")\n'
    )
    executable.chmod(0o700)
    backend = DockerContainerJobBackend(
        workspace_root=tmp_path, docker_binary=str(executable)
    )
    code, stdout, stderr = await backend._run(
        ("cp", "a" * 64 + ":/var/lib/moonmind-native-egress/snapshot.json", "-")
    )
    assert code == 0
    with tarfile.open(fileobj=io.BytesIO(stdout), mode="r:") as archive:
        assert json.load(archive.extractfile("snapshot.json")) == {
            "authority": "session.example.com:587"
        }
    assert b"fixture-private-value" not in stderr
    _, ordinary, _ = await backend._run(("logs", "a" * 64))
    assert b"session.example.com:587" not in ordinary


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "interruption",
    ["lifecycle", "volume-rm", "network-rm", "completion-ack", "final-ack"],
)
async def test_native_cleanup_reconciles_durable_final_receipt_after_interruption(
    lease_setup, native_setup, interruption
):
    _, _, request, backend, published = lease_setup
    prototype, docker = native_setup
    requested = selection().model_copy(
        update={
            "targets": (
                selection()
                .targets[0]
                .model_copy(
                    update={"hosts": ("session.example.com",), "ports": (587,)}
                ),
            )
        }
    )
    request.request.spec.workload_profile = WorkloadProfileSelection(
        profileRef="native", mode="probe", nativeNetwork=requested
    )
    backend._runner = docker
    scoped = ScopedEgressLease(
        docker,
        prototype.policy.model_copy(
            update={
                "policy_ref": request.request.spec.egress_policy_ref,
                "native_network": requested,
            }
        ),
        ownership=request.ownership_token,
        job_id=request.job_id,
        workload_ref=backend._name(request),
        native_selection=requested,
    )
    initial = await scoped.prepare(timeout_seconds=120)
    durable = {}
    original_publish = backend._publish

    async def publish(req, name, data):
        ref = await original_publish(req, name, data)
        durable[name] = (ref, data)
        if (
            interruption == "lifecycle"
            and name.endswith("-egress-lifecycle.json")
            or interruption == "final-ack"
            and name.endswith("-native-egress-final.json")
        ):
            raise RuntimeError("publication acknowledgement lost")
        return ref

    async def recover(req, name):
        return durable.get(name)

    backend._publish = publish
    backend._reconcile_evidence = recover

    async def interrupt_rm(args):
        if (
            interruption == "volume-rm"
            and tuple(args[:2]) == ("volume", "rm")
            or interruption == "network-rm"
            and tuple(args[:2]) == ("network", "rm")
        ):
            return 1, b"", b"interrupted cleanup"
        return await docker(args)

    backend._runner = interrupt_rm
    if interruption == "lifecycle":
        with pytest.raises(RuntimeError, match="acknowledgement"):
            await backend.cleanup(request)
    else:
        await backend.cleanup(request)
    verified = durable[request.job_id + "-native-egress-final.json"]
    assert (
        json.loads(verified[1])["nativeNetwork"]["configDigest"]
        == initial["nativeNetwork"]["configDigest"]
    )
    cp_count = len([call for call in docker.calls if call[0] == "cp"])
    from moonmind.workflows.temporal.container_job_backend import (
        DockerContainerJobBackend,
    )

    # A fresh worker has no process-local receipt or attachment ledger.
    restored = DockerContainerJobBackend(
        workspace_root=backend._workspace_root,
        settings=backend._settings,
        command_runner=docker,
        evidence_publisher=original_publish,
        evidence_reconciler=recover,
    )
    result = await restored.cleanup(request)
    assert result.cleanup_succeeded is True
    assert not docker.volumes
    assert scoped.gateway_ref not in docker.containers
    assert scoped.network_ref not in docker.networks
    assert len([call for call in docker.calls if call[0] == "cp"]) == cp_count
    assert (
        published[request.job_id + "-native-egress-final.json"]["nativeNetwork"][
            "attempts"
        ]
        == 0
    )
    assert (
        published[request.job_id + "-egress-lifecycle.json"]["nativeEgressFinalRef"]
        == verified[0]
    )


@pytest.mark.asyncio
async def test_expanded_dns_snapshot_rejected_before_owned_allocation(
    native_setup, monkeypatch
):
    value, docker = native_setup
    data = selection().model_dump(by_alias=True)
    data["targets"][0]["hosts"] = [f"host{i}.example.com" for i in range(256)]
    requested = NativeNetworkSelection.model_validate(data)
    value.native_selection = requested
    value.policy = value.policy.model_copy(update={"native_network": requested})
    monkeypatch.setattr(
        scoped_egress.socket,
        "getaddrinfo",
        lambda *args, **kwargs: [
            (2, 1, 6, "", (f"93.184.216.{i}", 8443)) for i in range(1, 33)
        ],
    )
    with pytest.raises(ValueError, match="transport"):
        await value.prepare(timeout_seconds=120)
    assert not docker.volumes
    assert not any(
        call[0] == "create" or call[:2] == ("network", "create")
        for call in docker.calls
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("legacy", [False, True])
async def test_native_transport_errors_are_partial_evidence_not_scope_denials(
    native_setup, legacy
):
    value, docker = native_setup
    await value.prepare(timeout_seconds=120)
    counters = json.loads(docker.files["snapshot.json"])
    if legacy:
        counters["denials"] = {"io": 1}
    else:
        counters["transportErrors"] = {"io": 1}
    docker.files["snapshot.json"] = json.dumps(counters).encode()
    for evidence in (await value.attest(), await value.terminal_evidence()):
        assert evidence["nativeNetwork"] == counters
        assert evidence["deniedConnectionCount"] == 0
        assert evidence["transportErrorCount"] == 1
        assert evidence["coverage"] == "partial"
        assert "native_transport_errors" in evidence["limitations"]
        assert "required_network_scope_denied" not in evidence["limitations"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "reason",
    [
        "header",
        "client",
        "destination",
        "attempts",
        "rate",
        "concurrency",
        "bytes",
        "idle",
        "timeout",
        "deadline",
        "persistence",
    ],
)
async def test_native_transport_errors_do_not_hide_enforcement_denials(
    native_setup, reason
):
    value, docker = native_setup
    await value.prepare(timeout_seconds=120)
    counters = json.loads(docker.files["snapshot.json"])
    counters["transportErrors"] = {"io": 1}
    counters["denials"] = {reason: 1}
    docker.files["snapshot.json"] = json.dumps(counters).encode()
    for evidence in (await value.attest(), await value.terminal_evidence()):
        assert evidence["deniedConnectionCount"] == 1
        assert evidence["transportErrorCount"] == 1
        assert "required_network_scope_denied" in evidence["limitations"]
        assert "native_transport_errors" in evidence["limitations"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "errors", [None, [], {"io": -1}, {"io": True}, {"io": 2**63}, {"destination": 1}]
)
async def test_native_transport_error_counters_are_strict_and_bounded(
    native_setup, errors
):
    value, docker = native_setup
    await value.prepare(timeout_seconds=120)
    counters = json.loads(docker.files["snapshot.json"])
    counters["transportErrors"] = errors
    docker.files["snapshot.json"] = json.dumps(counters).encode()
    with pytest.raises(RuntimeError):
        await value.attest()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "exit_code,denied", [(0, False), (7, False), (0, True), (7, True)]
)
async def test_backend_transport_observation_preserves_tool_exit_and_scope_failure(
    lease_setup, native_setup, monkeypatch, exit_code, denied
):
    _, docker, request, backend, _ = lease_setup
    value, native_docker = native_setup
    await value.prepare(timeout_seconds=120)
    counters = json.loads(native_docker.files["snapshot.json"])
    counters["transportErrors"] = {"io": 1}
    counters["denials"] = {"header": 1} if denied else {}
    native_docker.files["snapshot.json"] = json.dumps(counters).encode()
    evidence = await value.attest()
    created = await backend.create_container(request)
    request.container_ref = created.container_ref
    await backend.start_container(request)
    docker.container(created.container_ref)["State"].update(
        Status="exited", Running=False, ExitCode=exit_code
    )
    # Exercise real terminal-result handling with independently attested native observations.
    monkeypatch.setattr(
        backend,
        "_scoped_lease",
        lambda request: SimpleNamespace(attest=AsyncMock(return_value=evidence)),
    )
    observed = await backend.observe_container(request)
    assert observed.terminal_state == (
        "succeeded" if exit_code == 0 and not denied else "failed"
    )
    assert observed.exit_code == exit_code
    assert (observed.egress_denial_observation is not None) == denied
