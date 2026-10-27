"""Admitted assessment egress, through the shared job and proxy owners (#5038)."""

import json

import pytest

from moonmind.config.container_backend_settings import (
    resolve_container_backend_settings,
)
from moonmind.schemas.container_job_models import (
    ContainerJobSubmitRequest,
    ensure_temporal_safe,
)


def submission(**overrides):
    spec = {
        "imageSourceRef": "security-client",
        "workspaceRef": {"kind": "sandbox", "workspaceId": "assessment-one"},
        "resources": {"cpuMillis": 1000, "memoryMiB": 512},
        "networkMode": "bridge",
        "egressPolicyRef": "assessment-one",
    }
    spec.update(overrides)
    return ContainerJobSubmitRequest.model_validate(
        {
            "idempotencyKey": "scope-request",
            "source": {"source": "workflow", "workflowId": "assessment-one"},
            "spec": spec,
        }
    )


def declaration():
    return {
        "policyRef": "assessment-one",
        "allowedWorkflowIds": ["assessment-one"],
        "allowedImageSourceRefs": ["security-client"],
        "destinations": [
            {
                "host": "feeds.example.com",
                "purpose": "tool-data",
                "protocol": "https",
                "port": 443,
            },
            {
                "host": "cloud.example.com",
                "purpose": "provider-api",
                "protocol": "https",
                "port": 443,
            },
            {
                "host": "lab.example.test",
                "purpose": "live-target",
                "protocol": "http",
                "port": 8080,
                "targetNetworkRef": "moonmind-test-lab",
                "targetContainerRef": "moonmind-test-target",
            },
        ],
    }


def settings(rows=None):
    return resolve_container_backend_settings(
        {
            "MOONMIND_CONTAINER_BACKEND_EGRESS_POLICIES": json.dumps(
                [declaration()] if rows is None else rows
            ),
            "MOONMIND_CONTAINER_BACKEND_IMAGE_SOURCES": json.dumps(
                [{"sourceRef": "security-client", "image": "example/client:latest"}]
            ),
        }
    )


def test_admitted_policy_survives_the_public_durable_job_contract():
    request = submission()
    assert request.spec.egress_policy_ref == "assessment-one"
    encoded = ensure_temporal_safe(request)
    assert (
        ContainerJobSubmitRequest.model_validate_json(encoded).spec.egress_policy_ref
        == "assessment-one"
    )
    assert settings().authorize_egress(request).policy_ref == "assessment-one"


@pytest.mark.parametrize(
    "changes",
    [
        {"networkMode": "none"},
        {"networkMode": "service", "serviceRef": "lab"},
        {"imageSourceRef": None, "image": "example/client:latest"},
        {"networkRef": "host"},
        {"egressPolicyRef": "../other"},
        {"environment": [{"name": "HTTPS_PROXY", "value": "http://other:3128"}]},
        {"environment": [{"name": "NO_PROXY", "value": "*"}]},
    ],
)
def test_caller_cannot_expand_scoped_network_authority(changes):
    with pytest.raises(ValueError):
        submission(**changes)


def test_policy_is_bound_to_the_admitted_workflow_and_image():
    request = submission()
    for denied in (resolve_container_backend_settings({}), settings([])):
        with pytest.raises(ValueError, match="egress"):
            denied.authorize_egress(request)
    request.source.workflow_id = "assessment-two"
    with pytest.raises(ValueError, match="egress"):
        settings().authorize_egress(request)
    request.source.workflow_id = "assessment-one"
    request.spec.image_source_ref = "other-client"
    with pytest.raises(ValueError, match="egress"):
        settings().authorize_egress(request)


@pytest.mark.parametrize(
    "answers",
    [
        [],
        ["93.184.216.34", "127.0.0.1"],
        ["93.184.216.34", "169.254.169.254"],
        ["93.184.216.34", "10.0.0.1"],
        ["93.184.216.34", "::ffff:127.0.0.1"],
        ["93.184.216.34", "fc00::1"],
        ["0.0.0.1"],
        ["224.0.0.1"],
    ],
)
def test_every_dns_answer_must_be_admissible(answers):
    from moonmind.security.scoped_egress import validate_public_answers

    with pytest.raises(RuntimeError):
        validate_public_answers(answers)


def test_global_ipv6_does_not_prevent_using_a_valid_ipv4_answer():
    from moonmind.security.scoped_egress import validate_public_answers

    assert validate_public_answers(["93.184.216.34", "2606:4700:4700::1111"]) == (
        "93.184.216.34",
    )


@pytest.mark.parametrize(
    "field,value",
    [
        ("protocol", "udp"),
        ("host", "127.0.0.1"),
        ("host", "example.com\nhttp_access allow all"),
        ("host", ".example.com"),
        ("host", "api.internal"),
        ("port", 0),
        ("port", True),
        ("targetNetworkRef", "host"),
        ("extraNetwork", "other"),
    ],
)
def test_malformed_deployment_authority_fails_closed(field, value):
    row = declaration()
    row["destinations"][0][field] = value
    with pytest.raises(ValueError, match="egress"):
        settings([row])


def test_unsupported_tool_protocol_is_a_visible_coverage_gap():
    from moonmind.security.egress_policy import tool_proxy_options

    env = {
        "MOONMIND_EGRESS_PROXY": "http://moonmind-egress-a:3128",
        "HTTPS_PROXY": "http://bypass:3128",
    }
    assert tool_proxy_options("httpx", environ=env) == {
        "proxy": env["MOONMIND_EGRESS_PROXY"],
        "trust_env": False,
    }
    assert (
        "--proxy-server=" + env["MOONMIND_EGRESS_PROXY"]
        in tool_proxy_options("chromium", environ=env)["args"]
    )
    with pytest.raises(ValueError, match="unsupported.*coverage"):
        tool_proxy_options("httpx", environ=env, protocol="udp")


class LeaseDocker:
    """Stateful daemon boundary for ownership, retry and tampering tests."""

    def __init__(self):
        self.calls = []
        self.files = {}
        self.forwarding = b"0\n"
        self.proxy_healthy = True
        self.fail_start = False
        self.configured_subnets = set()
        self.networks = {
            "moonmind-test-lab": {
                "Id": "lab-network-id",
                "Driver": "bridge",
                "Internal": True,
                "EnableIPv6": False,
                "Labels": {"moonmind.security.target": "assessment-one"},
                "Options": {"com.docker.network.bridge.inhibit_ipv4": "true"},
                "Containers": {"target-id": {}},
            }
        }
        self.containers = {
            "moonmind-test-target": {
                "Id": "target-id",
                "Config": {"Labels": {"moonmind.security.target": "assessment-one"}},
                "HostConfig": {"NetworkMode": "moonmind-test-lab"},
                "NetworkSettings": {
                    "Networks": {"moonmind-test-lab": {"IPAddress": "10.247.0.4"}}
                },
            }
        }

    def container(self, ref):
        return next(
            (v for k, v in self.containers.items() if ref in (k, v["Id"])), None
        )

    @staticmethod
    def values(args, flag):
        return [args[i + 1] for i, token in enumerate(args[:-1]) if token == flag]

    async def __call__(self, args):
        import base64
        import hashlib

        args = tuple(args)
        self.calls.append(args)
        result = b""
        if args[:2] == ("network", "inspect"):
            if args[-1] not in self.networks:
                return 1, b"", b"No such container"
            result = json.dumps(self.networks[args[-1]]).encode()
        elif args[:2] == ("network", "ls"):
            result = "\n".join(self.networks).encode()
        elif args[:2] == ("network", "create"):
            name = args[-1]
            if "--subnet" in args:
                self.configured_subnets.add(name)
            self.networks[name] = {
                "Id": name + "-id",
                "Driver": "bridge",
                "Internal": "--internal" in args,
                "EnableIPv6": False,
                "Labels": dict(v.split("=", 1) for v in self.values(args, "--label")),
                "Options": dict(v.split("=", 1) for v in self.values(args, "--opt")),
                "IPAM": {
                    "Config": [
                        {
                            "Subnet": (
                                self.values(args, "--subnet")[0]
                                if "--subnet" in args
                                else (
                                    "10.248.0.0/24"
                                    if "--internal" in args
                                    else "10.249.0.0/24"
                                )
                            )
                        }
                    ]
                },
                "Containers": {},
            }
        elif args[:2] == ("network", "connect"):
            target = self.container(args[-1])
            target["NetworkSettings"]["Networks"][args[-2]] = {"IPAddress": ""}
            self.networks[args[-2]]["Containers"][target["Id"]] = {}
        elif args[:2] == ("network", "rm"):
            name = next(k for k, v in self.networks.items() if args[-1] in (k, v["Id"]))
            if self.networks[name]["Containers"]:
                return 1, b"", b"active endpoints"
            del self.networks[name]
            self.configured_subnets.discard(name)
        elif args[0] == "inspect":
            target = self.container(args[-1])
            if target is None:
                return 1, b"", b"No such container"
            fmt = args[args.index("--format") + 1]
            if fmt == "{{json .Config.Labels}}":
                result = json.dumps(target["Config"]["Labels"]).encode()
            elif fmt == "{{json .State}}":
                result = json.dumps(target["State"]).encode()
            elif fmt == "{{.State.Running}}":
                result = str(target["State"]["Running"]).lower().encode()
            else:
                result = json.dumps(target).encode()
        elif args[0] == "ps":
            filters = self.values(args, "--filter")
            targets = self.containers
            if any("moonmind.container_job" in f for f in filters):
                targets = {
                    k: v
                    for k, v in targets.items()
                    if "moonmind.container_job" in v.get("Config", {}).get("Labels", {})
                }
            if (
                "--format" in args
                and args[args.index("--format") + 1] == "{{.Names}}\t{{.State}}"
            ):
                result = "\n".join(
                    k + "\t" + v["State"]["Status"] for k, v in targets.items()
                ).encode()
            elif "-aq" in args:
                result = "\n".join(v["Id"] for v in targets.values()).encode()
            else:
                result = "\n".join(targets).encode()
        elif args[0] == "create":
            name = self.values(args, "--name")[0]
            network = self.values(args, "--network")[0]
            if "--ip" in args and network not in self.configured_subnets:
                return 1, b"", b"static IP requires a user configured subnet"
            image = next(v for v in args if v.startswith("sha256:"))
            self.containers[name] = {
                "Id": name + "-id",
                "Image": image,
                "Config": {
                    "Image": image,
                    "User": self.values(args, "--user")[0]
                    if "--user" in args
                    else "app",
                    "Labels": dict(
                        v.split("=", 1) for v in self.values(args, "--label")
                    ),
                    "Env": self.values(args, "--env"),
                    "Entrypoint": self.values(args, "--entrypoint") or None,
                    "Cmd": list(args[args.index(image) + 1 :]),
                },
                "HostConfig": {
                    "NetworkMode": network,
                    "Privileged": False,
                    "ReadonlyRootfs": "--read-only" in args,
                    "CapDrop": self.values(args, "--cap-drop"),
                    "CapAdd": [],
                    "SecurityOpt": self.values(args, "--security-opt"),
                    "Sysctls": dict(
                        v.split("=", 1) for v in self.values(args, "--sysctl")
                    ),
                    "Tmpfs": {"/tmp": ""} if "--tmpfs" in args else {},
                },
                "Mounts": [],
                "State": {"Status": "created", "Running": False, "ExitCode": 0},
                "NetworkSettings": {
                    "Networks": {
                        network: {
                            "IPAddress": "",
                            "IPAMConfig": {"IPv4Address": self.values(args, "--ip")[0]},
                        }
                    }
                },
            }
            result = (name + "-id").encode()
        elif args[0] == "start":
            if self.fail_start:
                self.fail_start = False
                return 1, b"", b"transient transport failure"
            target = self.container(args[-1])
            target["State"].update(Status="running", Running=True)
            network = target["HostConfig"]["NetworkMode"]
            attachment = target["NetworkSettings"]["Networks"][network]
            attachment["IPAddress"] = attachment["IPAMConfig"]["IPv4Address"]
            self.networks[network]["Containers"][target["Id"]] = {}
            env = dict(v.split("=", 1) for v in target["Config"]["Env"])
            for key, path in (
                ("CONFIG", "/tmp/moonmind-squid.conf"),
                ("HOSTS", "/tmp/moonmind-hosts"),
                ("SNAPSHOT", "/tmp/moonmind-scope.json"),
            ):
                if "MOONMIND_EGRESS_" + key in env:
                    self.files[path] = base64.b64decode(env["MOONMIND_EGRESS_" + key])
            self.files.setdefault("/tmp/moonmind-access.log", b"")
        elif args[0] == "exec":
            if args[2] == "squid" and not self.proxy_healthy:
                return 1, b"", b"squid is not running"
            if args[2] == "sha256sum":
                result = "\n".join(
                    hashlib.sha256(self.files[p]).hexdigest() + "  " + p
                    for p in args[3:]
                ).encode()
            elif args[2:] == ("cat", "/proc/sys/net/ipv4/ip_forward"):
                result = self.forwarding
            elif args[2] == "cat":
                result = self.files[args[3]]
        elif args[0] == "rm":
            target = self.container(args[-1])
            if target:
                for network in self.networks.values():
                    network["Containers"].pop(target["Id"], None)
                self.containers = {
                    k: v for k, v in self.containers.items() if v is not target
                }
        elif args[0] in {"stop", "kill"}:
            self.container(args[-1])["State"].update(Status="exited", Running=False)
        elif args[0] == "logs":
            pass
        else:
            raise AssertionError(args)
        return 0, result, b""


@pytest.fixture
def lease_setup(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from moonmind.schemas.container_job_models import ContainerJobActivityRequest
    from moonmind.security import scoped_egress
    from moonmind.workflows.temporal.container_job_backend import (
        DockerContainerJobBackend,
    )

    docker = LeaseDocker()
    monkeypatch.setattr(
        scoped_egress, "_public_answers", AsyncMock(return_value=("93.184.216.34",))
    )
    monkeypatch.setattr(
        scoped_egress,
        "attest_docker_egress",
        AsyncMock(
            return_value=SimpleNamespace(gateway_image_digest="sha256:" + "1" * 64)
        ),
    )
    published = {}

    async def publish(request, name, data):
        published[name] = json.loads(data)
        return "artifacts/" + name

    request = ContainerJobActivityRequest(
        jobId="container-job:" + "a" * 32,
        ownershipToken="scope-owned-token",
        request=submission(command=["true"]),
        resolvedWorkspaceRef=str(tmp_path),
        resolvedImageRef="sha256:" + "2" * 64,
    )
    backend = DockerContainerJobBackend(
        workspace_root=tmp_path,
        settings=settings(),
        command_runner=docker,
        evidence_publisher=publish,
    )
    return scoped_egress, docker, request, backend, published


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["reservation-receipt", "configured-create"])
async def test_scoped_network_setup_failure_recovers_and_cleans_owned_reservations(
    lease_setup, monkeypatch, failure
):
    _, docker, request, backend, _ = lease_setup
    lease = backend._scoped_lease(request)

    async def fail_network_setup(args):
        if tuple(args[:2]) == ("network", "create"):
            if (
                failure == "reservation-receipt"
                and args[-1] == lease.network_ref + "-subnet"
            ):
                await docker(args)
                return 1, b"", b"lost network create receipt"
            if failure == "configured-create" and args[-1] == lease.network_ref:
                return 1, b"", b"network create transport failure"
        return await docker(args)

    monkeypatch.setattr(backend, "_runner", fail_network_setup)
    with pytest.raises(RuntimeError, match="network.*failed"):
        await backend.create_container(request)
    assert (await backend.cleanup(request)).cleanup_succeeded is True
    assert set(docker.networks) == {"moonmind-test-lab"}
    assert set(docker.containers) == {"moonmind-test-target"}

    monkeypatch.setattr(backend, "_runner", docker)
    created = await backend.create_container(request)
    request.container_ref = created.container_ref
    assert lease.network_ref in docker.configured_subnets
    assert set(docker.networks) == {
        "moonmind-test-lab",
        lease.network_ref,
        lease.outbound_ref,
    }
    await backend.start_container(request)
    await backend.remove_container(request)
    assert (await backend.cleanup(request)).cleanup_succeeded is True
    assert set(docker.networks) == {"moonmind-test-lab"}
    assert set(docker.containers) == {"moonmind-test-target"}


@pytest.mark.asyncio
async def test_real_backend_launch_recovery_reuses_pinned_policy_and_deadline(
    lease_setup,
):
    module, docker, request, backend, _ = lease_setup
    created = await backend.create_container(request)
    request.container_ref = created.container_ref
    lease = backend._scoped_lease(request)
    first = await lease.attest(workload=True, expected_image=request.resolved_image_ref)
    recovered = await backend.reconcile_container(request)
    second = await lease.prepare(timeout_seconds=9999)
    assert recovered.container_ref == created.container_ref
    assert first["snapshotDigest"] == second["snapshotDigest"]
    assert first["expiresAt"] == second["expiresAt"]
    assert module._public_answers.await_count == 2
    launch = next(
        c for c in docker.calls if c[0] == "create" and created.container_ref in c
    )
    assert launch[launch.index("--network") + 1] == lease.network_ref
    assert all(
        "host" != c[c.index("--network") + 1] for c in docker.calls if "--network" in c
    )
    assert set(
        docker.container(created.container_ref)["NetworkSettings"]["Networks"]
    ) == {lease.network_ref}


@pytest.mark.asyncio
async def test_interrupted_proxy_create_is_reconciled_without_new_dns_or_lease(
    lease_setup,
):
    module, docker, request, backend, _ = lease_setup
    lease = backend._scoped_lease(request)
    docker.fail_start = True
    with pytest.raises(RuntimeError):
        await lease.prepare(timeout_seconds=100)
    evidence = await lease.prepare(timeout_seconds=9999)
    assert module._public_answers.await_count == 2
    assert evidence["expiresAt"] == float(
        docker.networks[lease.network_ref]["Labels"]["moonmind.expires_at"]
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "tamper",
    [
        "policy",
        "hosts",
        "forwarding",
        "gateway-network",
        "gateway-replacement",
        "target-ip",
        "foreign-peer",
        "secondary-workload-network",
        "client-address",
        "missing-proxy",
        "proxy-process",
        "host-interface",
    ],
)
async def test_tampered_scope_stops_workload_without_a_fallback(lease_setup, tamper):
    _, docker, request, backend, _ = lease_setup
    created = await backend.create_container(request)
    request.container_ref = created.container_ref
    lease = backend._scoped_lease(request)
    if tamper in {"policy", "hosts"}:
        path = (
            "/tmp/moonmind-squid.conf" if tamper == "policy" else "/tmp/moonmind-hosts"
        )
        docker.files[path] += b"tampered\n"
    elif tamper == "forwarding":
        docker.forwarding = b"1\n"
    elif tamper == "gateway-network":
        docker.container(lease.gateway_ref)["NetworkSettings"]["Networks"][
            "control-plane"
        ] = {}
    elif tamper == "gateway-replacement":
        docker.container(lease.gateway_ref)["Id"] = "replacement-id"
    elif tamper == "target-ip":
        docker.container("moonmind-test-target")["NetworkSettings"]["Networks"][
            "moonmind-test-lab"
        ]["IPAddress"] = "10.247.0.99"
    elif tamper == "foreign-peer":
        docker.networks[lease.network_ref]["Containers"]["other-assessment-id"] = {}
    elif tamper == "client-address":
        docker.container(created.container_ref)["NetworkSettings"]["Networks"][
            lease.network_ref
        ]["IPAMConfig"]["IPv4Address"] = "10.248.0.99"
    elif tamper == "missing-proxy":
        del docker.containers[lease.gateway_ref]
    elif tamper == "proxy-process":
        docker.proxy_healthy = False
    elif tamper == "host-interface":
        docker.networks[lease.network_ref]["Options"][
            "com.docker.network.bridge.inhibit_ipv4"
        ] = "false"
    else:
        docker.container(created.container_ref)["NetworkSettings"]["Networks"][
            "bridge"
        ] = {}
    with pytest.raises(RuntimeError):
        await backend.start_container(request)
    assert docker.container(created.container_ref)["State"]["Running"] is False
    assert len([c for c in docker.calls if c[0] == "create"]) == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("denied", [False, True])
async def test_terminal_observation_retains_denials_made_during_exit(
    lease_setup, monkeypatch, denied
):
    _, docker, request, backend, published = lease_setup
    created = await backend.create_container(request)
    request.container_ref = created.container_ref
    await backend.start_container(request)
    assert docker.files["/tmp/moonmind-access.log"] == b""

    async def exit_during_inspection(args):
        if tuple(args[:3]) == ("inspect", "--format", "{{json .State}}"):
            if denied:
                docker.files["/tmp/moonmind-access.log"] = (
                    b"1793077200 0 10.248.0.3 TCP_DENIED/403 0 CONNECT unrelated.example:443 - HIER_NONE/- -\n"
                )
            docker.container(created.container_ref)["State"].update(
                Status="exited", Running=False, ExitCode=0
            )
        return await docker(args)

    monkeypatch.setattr(backend, "_runner", exit_during_inspection)
    result = await backend.observe_container(request)

    assert result.running is False and result.exit_code == 0
    assert result.terminal_state == ("failed" if denied else "succeeded")
    if denied:
        observation = result.egress_denial_observation
        assert observation.denied_connection_count == 1
        evidence = published[observation.artifact_ref.removeprefix("artifacts/")]
        assert evidence["coverage"] == "partial"
        assert evidence["deniedConnectionCount"] == 1
        assert evidence["denialDiagnostics"]
    else:
        assert result.egress_denial_observation is None


@pytest.mark.asyncio
async def test_denied_endpoint_cannot_produce_a_clean_exit_zero_result(lease_setup):
    _, docker, request, backend, published = lease_setup
    created = await backend.create_container(request)
    request.container_ref = created.container_ref
    await backend.start_container(request)
    docker.files["/tmp/moonmind-access.log"] = (
        b"1793077200 0 10.248.0.3 TCP_DENIED/403 0 CONNECT unrelated.example:443 - HIER_NONE/- -\n"
    )
    docker.container(created.container_ref)["State"].update(
        Status="exited", Running=False, ExitCode=0
    )
    result = await backend.observe_container(request)
    assert result.terminal_state == "failed" and result.exit_code == 0
    evidence = await backend._runtime_egress_evidence(request)
    assert (
        evidence["profileRef"] == "assessment-one" and evidence["coverage"] == "partial"
    )
    assert evidence["deniedConnectionCount"] == 1
    assert evidence["denialDiagnostics"]
    await backend.remove_container(request)
    cleaned = await backend.cleanup(request)
    assert cleaned.cleanup_succeeded is True
    assert set(docker.networks) == {"moonmind-test-lab"}
    assert set(docker.containers) == {"moonmind-test-target"}
    assert any(
        p.get("profileRef") == "assessment-one"
        and p.get("cleanupResult") == "succeeded"
        for p in published.values()
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["policy", "missing-proxy", "gateway-replacement"])
async def test_observed_denials_survive_attestation_failure_and_cleanup(
    lease_setup, failure
):
    _, docker, request, backend, published = lease_setup
    created = await backend.create_container(request)
    request.container_ref = created.container_ref
    await backend.start_container(request)
    docker.files["/tmp/moonmind-access.log"] = (
        b"1793077200 0 10.248.0.3 TCP_DENIED/403 0 CONNECT unrelated.example:443 - HIER_NONE/- -\n"
    )
    observed = await backend.observe_container(request)
    assert observed.running is True
    assert any(p.get("deniedConnectionCount") == 1 for p in published.values())
    request.egress_denial_observation = observed.egress_denial_observation
    lease = backend._scoped_lease(request)
    if failure == "policy":
        docker.files["/tmp/moonmind-squid.conf"] += b"tampered\n"
    elif failure == "missing-proxy":
        await docker(("rm", "--force", lease.gateway_ref))
    else:
        gateway = docker.container(lease.gateway_ref)
        previous_id = gateway["Id"]
        gateway["Id"] = "replacement-id"
        for network in docker.networks.values():
            if previous_id in network["Containers"]:
                network["Containers"][gateway["Id"]] = network["Containers"].pop(
                    previous_id
                )
    with pytest.raises(RuntimeError) as error:
        await backend.observe_container(request)
    assert docker.container(created.container_ref)["State"]["Running"] is False
    await backend._publish_runtime_diagnostics(
        request, logs_ref=None, stdout_ref=None, stderr_ref=None, manifest=[]
    )
    diagnostics = published[f"{request.job_id}-diagnostics.json"]["egressEvidence"]
    assert diagnostics["state"] == "unavailable"
    assert diagnostics["error"] == str(error.value)
    assert diagnostics["coverage"] == "partial"
    retained = diagnostics["retainedDenialObservation"]
    assert retained["deniedConnectionCount"] == 1
    snapshot = published[retained["artifactRef"].removeprefix("artifacts/")]
    assert snapshot["profileRef"] == "assessment-one"
    assert snapshot["denialDiagnostics"]
    await backend.remove_container(request)
    assert (await backend.cleanup(request)).cleanup_succeeded is True
    assert snapshot in published.values()
    assert (
        published[f"{request.job_id}-egress-lifecycle.json"][
            "retainedDenialObservation"
        ]
        == retained
    )


@pytest.mark.asyncio
async def test_cancellation_cleanup_survives_policy_revocation(lease_setup):
    _, docker, request, backend, _ = lease_setup
    created = await backend.create_container(request)
    request.container_ref = created.container_ref
    await backend.start_container(request)
    await backend.stop_container(request)
    await backend.remove_container(request)
    backend._settings = settings([])
    assert (await backend.cleanup(request)).cleanup_succeeded is True
    assert set(docker.containers) == {"moonmind-test-target"}
    assert set(docker.networks) == {"moonmind-test-lab"}


@pytest.mark.asyncio
async def test_cleanup_preserves_a_replacement_owned_by_another_attempt(lease_setup):
    _, docker, request, backend, _ = lease_setup
    await backend.create_container(request)
    lease = backend._scoped_lease(request)
    docker.container(lease.gateway_ref)["Config"]["Labels"]["moonmind.ownership"] = (
        "other-attempt"
    )
    with pytest.raises(RuntimeError, match="ownership"):
        await lease.cleanup()
    assert docker.container(lease.gateway_ref) is not None


@pytest.mark.asyncio
async def test_api_rejects_unadmitted_policy_before_persistence():
    from unittest.mock import AsyncMock

    from api_service.services.container_jobs import ContainerJobService
    from moonmind.schemas.container_job_models import OwnerIdentity

    session = AsyncMock()
    service = ContainerJobService(session, backend_settings=settings([]))
    with pytest.raises(ValueError, match="egress"):
        await service.submit(
            owner=OwnerIdentity(principalId="operator", principalType="system"),
            request=submission(),
        )
    session.add.assert_not_called()


@pytest.mark.asyncio
async def test_saved_launch_cannot_readmit_new_dns_after_lease_loss(lease_setup):
    module, _, request, backend, _ = lease_setup
    created = await backend.create_container(request)
    request.container_ref = created.container_ref
    request.egress_attestation_ref = created.diagnostics_ref
    await backend.remove_container(request)
    await backend._scoped_lease(request).cleanup()
    request.container_ref = None
    with pytest.raises(RuntimeError, match="saved.*lease"):
        await backend.create_container(request)
    assert module._public_answers.await_count == 2


def test_offline_admission_does_not_load_proxy_configuration(tmp_path):
    import os
    import subprocess
    import sys

    (tmp_path / "omnigent-provider-domains.txt").write_text("not a domain\n")
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "from moonmind.config.container_backend_settings import resolve_container_backend_settings; assert not resolve_container_backend_settings({}).egress_policies",
        ],
        env={**os.environ, "MOONMIND_EGRESS_POLICY_DIRECTORY": str(tmp_path)},
        capture_output=True,
        check=False,
        timeout=15,
    )
    assert result.returncode == 0, result.stderr.decode()
