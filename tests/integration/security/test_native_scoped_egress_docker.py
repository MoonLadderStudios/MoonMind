"""Real native client and isolated-daemon lease qualification.

The loopback case proves immediate-banner interoperability, not isolation.
Docker cases run only in the existing dedicated test-only daemon entrypoint.
"""

from __future__ import annotations

import asyncio
import json
import os
import time
from pathlib import Path
from uuid import uuid4

import pytest
import pytest_asyncio

from moonmind.security.native_tcp_gateway import NativeTCPConfig, NativeTCPGateway
from tests.integration.security.native_smtp_fixture import (
    SMTPFixture,
    certificate,
    curl_command,
)

pytestmark = [pytest.mark.integration, pytest.mark.integration_ci]
# Same route guard as the existing Docker suite. The dedicated entrypoint sets
# this variable, so no supported journey may skip there on missing tools/bugs.
docker_only = pytest.mark.skipif(
    os.environ.get("MOONMIND_RUN_SCOPED_EGRESS_DOCKER") != "1",
    reason="dedicated isolated CI Docker entrypoint",
)


@pytest.mark.asyncio
async def test_curl_smtp_starttls_immediate_banner(tmp_path):
    cert, key = certificate(tmp_path)
    fixture = SMTPFixture(cert, key)
    server = await asyncio.start_server(fixture.handle, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    state = tmp_path / "state"
    state.mkdir()
    config = NativeTCPConfig.from_mapping(
        {
            "leaseId": "curl-proof",
            "deadlineUnix": time.time() + 30,
            "listenHost": "127.0.0.1",
            "listenPort": 0,
            "clientIp": "127.0.0.1",
            "endpoints": [
                {
                    "authority": f"native.example.test:{port}",
                    "address": "127.0.0.1",
                    "port": port,
                }
            ],
            "maxConnections": 5,
            "maxConcurrentConnections": 1,
            "maxConnectionsPerSecond": 5,
            "maxBytes": 100000,
            "handshakeSeconds": 2,
            "connectSeconds": 2,
            "idleSeconds": 5,
        }
    )
    gateway = NativeTCPGateway(config, state_directory=state)
    await gateway.start()
    try:
        for _ in range(3):
            process = await asyncio.create_subprocess_exec(
                *curl_command(f"http://127.0.0.1:{gateway.bound_port}", cert, port),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(process.communicate(), 12)
            assert process.returncode == 0, stderr.decode()
            assert b"250 observed" in stdout
        assert (
            fixture.counts["connections"]
            == fixture.counts["starttls"]
            == fixture.counts["noop"]
            == 3
        )
        assert "AUTH" not in fixture.counts["commands"]
        assert "MAIL" not in fixture.counts["commands"]
        assert gateway.snapshot()["attempts"] == 3
        assert gateway.snapshot()["bytesReserved"] > fixture.counts["bytesReceived"]
    finally:
        await gateway.close()
        await fixture.close()
        server.close()
        await server.wait_closed()


class DockerLab:
    """Driver-owned resources only. Workloads receive no daemon socket."""

    def __init__(self, root, image):
        self.root, self.image = root, image
        self.prefix = "moonmind-test-native-" + uuid4().hex[:10]
        self.containers, self.networks, self.leases, self.calls, self.targets = (
            [],
            [],
            [],
            [],
            [],
        )
        self.artifacts = root / "artifacts/integration-ci/scoped-egress" / self.prefix
        self.artifacts.mkdir(parents=True)

    async def runner(self, args):
        self.calls.append(tuple(args))
        process = await asyncio.create_subprocess_exec(
            "/usr/libexec/moonmind/docker-real",
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(process.communicate(), 45)
        return process.returncode, stdout, stderr

    async def run(self, *args):
        code, out, err = await self.runner(args)
        assert code == 0, err.decode()[-2000:]
        return out.decode().strip()

    async def network(self, suffix, *, internal=True, policy=None, subnet=None):
        name = self.prefix + "-" + suffix
        self.networks.append(name)
        args = ["network", "create", "--label", "moonmind.test.owner=" + self.prefix]
        if internal:
            args += [
                "--internal",
                "--opt",
                "com.docker.network.bridge.inhibit_ipv4=true",
            ]
        if policy:
            args += ["--label", "moonmind.security.target=" + policy]
        if subnet:
            args += ["--subnet", subnet]
        await self.run(*args, name)
        return name

    async def installed(self, monkeypatch):
        from moonmind.security import egress, scoped_egress

        networks = {
            key: await self.network(key, internal=key != "external")
            for key in ("restricted", "sandbox", "omnigent", "external")
        }
        gateway = self.prefix + "-installed"
        self.containers.append(gateway)
        await self.run(
            "create",
            "--name",
            gateway,
            "--user",
            "0:0",
            "--network",
            networks["external"],
            "--label",
            f"moonmind.egress.enforcer={egress.ENFORCER_IMPLEMENTATION}",
            "--mount",
            f"type=bind,src={self.root / 'docker/moonmind-egress'},dst=/opt/moonmind-egress,readonly",
            "--mount",
            f"type=bind,src={self.root / 'docker/sandbox-egress-proxy'},dst=/app/docker/sandbox-egress-proxy,readonly",
            "--tmpfs",
            "/run/moonmind-egress",
            "--health-cmd",
            "sh /opt/moonmind-egress/policy.sh check",
            "--health-interval",
            "1s",
            "--health-retries",
            "10",
            "--entrypoint",
            "/bin/sh",
            self.image,
            "/opt/moonmind-egress/policy.sh",
            "start",
        )
        for key in ("restricted", "sandbox", "omnigent"):
            await self.run(
                "network",
                "connect",
                *(["--alias", "omnigent-egress-proxy"] if key == "omnigent" else []),
                networks[key],
                gateway,
            )
        await self.run("start", gateway)
        for _ in range(40):
            status = await self.run(
                "inspect", "--format", "{{.State.Health.Status}}", gateway
            )
            if status == "healthy":
                break
            await asyncio.sleep(0.25)
        assert status == "healthy"
        installed = egress.DEFAULT_EGRESS_PROFILE.model_copy(
            update={"network_ref": networks["restricted"], "gateway_ref": gateway}
        )
        monkeypatch.setattr(scoped_egress, "DEFAULT_EGRESS_PROFILE", installed)
        monkeypatch.setitem(
            egress.EGRESS_PROFILE_DIGESTS, installed.ref, installed.digest
        )
        monkeypatch.setattr(
            egress, "_EXPECTED_GATEWAY_NETWORKS", frozenset(networks.values())
        )

    async def target(
        self, suffix, network, policy="native-fixtures", address=None, port=2525
    ):
        name = self.prefix + "-" + suffix
        self.containers.append(name)
        await self.run(
            "run",
            "-d",
            "--name",
            name,
            "--network",
            network,
            *(["--ip", address] if address else []),
            *(["--user", "0:0"] if port < 1024 else []),
            "--label",
            "moonmind.test.owner=" + self.prefix,
            "--label",
            "moonmind.security.target=" + policy,
            "--mount",
            f"type=bind,src={self.root},dst=/workspace,readonly",
            "--entrypoint",
            "python",
            self.image,
            "/workspace/tests/integration/security/native_smtp_fixture.py",
            "/tmp/smtp",
            str(port),
        )
        for _ in range(40):
            code, _, _ = await self.runner(
                ("exec", name, "test", "-f", "/tmp/smtp/ready")
            )
            if code == 0:
                break
            await asyncio.sleep(0.1)
        assert code == 0
        self.targets.append(name)
        return name

    async def counts(self, target):
        return json.loads(
            await self.run("exec", target, "cat", "/tmp/smtp/counts.json")
        )

    async def new_lease(self, network, target, *, start=True, targets=None, **limits):
        from moonmind.security.egress_policy import ScopedEgressPolicy
        from moonmind.security.native_network import NativeNetworkSelection
        from moonmind.security.scoped_egress import ScopedEgressLease

        selection = NativeNetworkSelection.model_validate(
            {
                "targets": targets
                or [
                    {
                        "hosts": ["native.example.test"],
                        "ports": [2525],
                        "purpose": "live-target",
                        "targetNetworkRef": network,
                        "targetContainerRef": target,
                    }
                ],
                "limits": {
                    "maxConnections": 30,
                    "maxBytes": 200000,
                    "maxConnectionsPerSecond": 20,
                    "maxConcurrentConnections": 3,
                    "maxDurationSeconds": 90,
                    **limits,
                },
            }
        )
        policy = ScopedEgressPolicy.model_validate(
            {
                "policyRef": "native-fixtures",
                "allowedWorkflowIds": ["native-fixtures"],
                "allowedImageSourceRefs": ["native-fixture-client"],
                "nativeNetwork": selection.model_dump(by_alias=True),
            }
        )
        number = len(self.leases)
        lease = ScopedEgressLease(
            self.runner,
            policy,
            ownership=self.prefix + f"-{number}",
            job_id=self.prefix + f"-{number}",
            workload_ref=self.prefix + f"-client-{number}",
            native_selection=selection,
        )
        # Production resource identity is deterministic; the disposable driver
        # prefixes only its own fixture ownership/job inputs.
        self.leases.append(lease)
        if not start:
            return lease
        return await self.start_lease(lease)

    async def start_lease(self, lease):
        evidence = await lease.prepare(timeout_seconds=120)
        self.containers.append(lease.workload_ref)
        await self.run(
            "run",
            "-d",
            "--name",
            lease.workload_ref,
            "--network",
            lease.network_ref,
            "--ip",
            evidence["clientAddress"],
            "--cap-drop",
            "ALL",
            "--security-opt",
            "no-new-privileges",
            "--label",
            "moonmind.ownership=" + lease.ownership,
            "--label",
            "moonmind.egress.scope=" + lease.policy.digest,
            "--label",
            "moonmind.egress.gateway-id=" + evidence["gatewayIdentity"],
            "--label",
            "moonmind.egress.snapshot-digest=" + evidence["snapshotDigest"],
            "--mount",
            f"type=bind,src={self.root},dst=/workspace,readonly",
            "--env",
            "MOONMIND_EGRESS_PROXY=" + lease.proxy_url,
            "--entrypoint",
            "sleep",
            self.image,
            "180",
        )
        return lease, evidence

    async def client(self, lease, code, *args):
        return await self.run("exec", lease.workload_ref, "python", "-c", code, *args)

    async def evidence(self, lease, name):
        value = await lease.terminal_evidence()
        (self.artifacts / (name + ".json")).write_text(
            json.dumps(
                {
                    "egress": value,
                    "targetCounters": {
                        target: await self.counts(target) for target in self.targets
                    },
                },
                indent=2,
            )
        )
        return value

    async def close(self):
        errors = []
        # Preserve bounded evidence even if an assertion interrupted the case.
        for index, lease in enumerate(self.leases):
            try:
                await self.evidence(lease, f"teardown-{index}")
            except (RuntimeError, AssertionError) as exc:
                errors.append(str(exc))
        for name in reversed(self.containers):
            await self.runner(("rm", "--force", name))
        for lease in reversed(self.leases):
            try:
                await lease.cleanup()
            except RuntimeError as exc:
                errors.append(str(exc))
        for name in reversed(self.networks):
            await self.runner(("network", "rm", name))
        assert not errors, "native fixture evidence/cleanup failed: " + "; ".join(
            errors
        )


@pytest_asyncio.fixture
async def docker_lab(monkeypatch):
    assert os.environ.get("MOONMIND_RUN_SCOPED_EGRESS_DOCKER") == "1", (
        "requires tools/test_scoped_egress.sh isolated daemon driver"
    )
    lab = DockerLab(
        Path(__file__).resolve().parents[3], os.environ["MOONMIND_NATIVE_TEST_IMAGE"]
    )
    try:
        await lab.installed(monkeypatch)
        yield lab
    finally:
        await lab.close()


# The socket helper does not assert supported scanner/tool compatibility. It
# deliberately bypasses client proxy settings to exercise the transport wall.
CONNECT = """
import json,socket,sys
from urllib.parse import urlsplit
p=urlsplit(sys.argv[1]); authority=sys.argv[2]
s=socket.create_connection((p.hostname,p.port),timeout=2)
s.settimeout(3)
s.sendall(('CONNECT '+authority+' HTTP/1.1\\r\\nHost: '+authority+'\\r\\n\\r\\n').encode())
f=s.makefile('rb')
try: head=f.readline()
except ConnectionResetError: head=b''
if head.startswith(b'HTTP/1.1 200'):
 assert f.readline()==b'\\r\\n'
 banner=f.readline()
 assert banner.startswith(b'220 '),banner
 print(json.dumps({'accepted':True,'banner':banner.decode().strip()}))
else:
 assert head==b'',head
 print(json.dumps({'accepted':False}))
s.close()
"""


@docker_only
@pytest.mark.asyncio
async def test_native_docker_endpoint_source_and_direct_isolation(docker_lab):
    lab = docker_lab
    network = await lab.network("target", policy="native-fixtures")
    target = await lab.target("target", network)
    other_network = await lab.network("other", policy="other-assessment")
    peer = await lab.target("peer", other_network, policy="other-assessment")
    lease, initial = await lab.new_lease(network, target)
    inspect = json.loads(await lab.run("inspect", "--format", "{{json .}}", peer))
    peer_ip = inspect["NetworkSettings"]["Networks"][other_network]["IPAddress"]
    target_ip = initial["resolvedAddresses"]["native.example.test"][0]
    assert json.loads(
        await lab.client(lease, CONNECT, lease.proxy_url, "native.example.test:2525")
    )["accepted"]
    for authority in (
        f"{target_ip}:2525",
        "native.example.test:2526",
        "other.example.test:2525",
        f"{peer_ip}:2525",
        f"{peer}:2525",
        "169.254.169.254:80",
        "172.17.0.1:80",
    ):
        assert not json.loads(
            await lab.client(lease, CONNECT, lease.proxy_url, authority)
        )["accepted"]
    direct = """
import socket,sys,os,json
os.environ.update(HTTP_PROXY='',HTTPS_PROXY='',ALL_PROXY='',NO_PROXY='*')
results=[]
for host,port in zip(sys.argv[1::2],sys.argv[2::2]):
 try: s=socket.create_connection((host,int(port)),timeout=.3)
 except OSError: results.append(host)
 else: s.close();raise AssertionError('direct socket escaped '+host)
print(json.dumps(results))
"""
    blocked = json.loads(
        await lab.client(
            lease,
            direct,
            target_ip,
            "2525",
            peer_ip,
            "2525",
            peer,
            "2525",
            "169.254.169.254",
            "80",
            "172.17.0.1",
            "80",
        )
    )
    assert len(blocked) == 5
    # An unselected source on the same internal network is refused before dial.
    rogue = lab.prefix + "-rogue"
    lab.containers.append(rogue)
    await lab.run(
        "run",
        "-d",
        "--name",
        rogue,
        "--network",
        lease.network_ref,
        "--entrypoint",
        "sleep",
        lab.image,
        "60",
    )
    result = json.loads(
        await lab.run(
            "exec",
            rogue,
            "python",
            "-c",
            CONNECT,
            lease.proxy_url,
            "native.example.test:2525",
        )
    )
    assert result["accepted"] is False
    await lab.run("rm", "--force", rogue)
    assert (await lab.counts(target))["connections"] == 1
    assert (await lab.counts(peer))["connections"] == 0
    evidence = await lab.evidence(lease, "endpoint-source-direct-isolation")
    assert evidence["nativeNetwork"]["attempts"] == 1
    assert evidence["nativeNetwork"]["denials"]["client"] >= 1
    assert evidence["nativeNetwork"]["denials"]["destination"] >= 7


@docker_only
@pytest.mark.asyncio
async def test_native_docker_curl_and_reattachment(docker_lab):
    lab = docker_lab
    network = await lab.network("target", policy="native-fixtures")
    target = await lab.target("target", network)
    lease, before = await lab.new_lease(network, target)
    # Capture only the public CA, never the fixture's private key.
    ca = lab.artifacts / "native-ca.pem"
    await lab.run("cp", target + ":/tmp/smtp/native-ca.pem", str(ca))
    await lab.run("cp", str(ca), lease.workload_ref + ":/tmp/native-ca.pem")
    for _ in range(3):
        output = await lab.run(
            "exec",
            lease.workload_ref,
            *curl_command(lease.proxy_url, "/tmp/native-ca.pem"),
        )
        assert "250 observed" in output
    from moonmind.security.scoped_egress import ScopedEgressLease

    resumed = ScopedEgressLease(
        lab.runner,
        lease.policy,
        ownership=lease.ownership,
        job_id=lease.job_id,
        workload_ref=lease.workload_ref,
        native_selection=lease.native_selection,
    )
    after = await resumed.prepare(timeout_seconds=9999)
    assert after["gatewayIdentity"] == before["gatewayIdentity"]
    assert after["expiresAt"] == before["expiresAt"]
    assert after["snapshotDigest"] == before["snapshotDigest"]
    assert after["nativeNetwork"]["attempts"] == 3
    counts = await lab.counts(target)
    assert counts["connections"] == counts["starttls"] == counts["noop"] == 3
    assert not {"AUTH", "MAIL", "RCPT", "DATA"}.intersection(counts["commands"])
    (lab.artifacts / "smtp-target-counts.json").write_text(json.dumps(counts))
    await lab.evidence(lease, "curl-starttls-reattachment")


@docker_only
@pytest.mark.asyncio
@pytest.mark.parametrize(
    "boundary",
    [
        "maxConnections",
        "maxBytes",
        "maxConnectionsPerSecond",
        "maxConcurrentConnections",
    ],
)
async def test_native_docker_cumulative_budgets(docker_lab, boundary):
    lab = docker_lab
    network = await lab.network("target", policy="native-fixtures")
    target = await lab.target("target", network)
    value = 40 if boundary == "maxBytes" else 1
    limits = {boundary: value}
    lease, _ = await lab.new_lease(network, target, **limits)
    code = """
import socket,sys,json,time
from urllib.parse import urlsplit
p=urlsplit(sys.argv[1]); boundary=sys.argv[2]
def connect():
 s=socket.create_connection((p.hostname,p.port),timeout=2);s.settimeout(3)
 s.sendall(b'CONNECT native.example.test:2525 HTTP/1.1\\r\\nHost: native.example.test:2525\\r\\n\\r\\n')
 f=s.makefile('rb')
 try:head=f.readline()
 except ConnectionResetError:head=b''
 if head.startswith(b'HTTP/1.1 200'):
  assert f.readline()==b'\\r\\n'; return s,f,True
 assert head==b'';return s,f,False
s,f,ok=connect();assert ok
banner=f.readline();assert banner.startswith(b'220')
if boundary=='maxBytes':
 s.sendall(b'EHLO client\\r\\n');assert f.read()==b''
else:
 if boundary=='maxConnections': s.close();f.close()
 second,other,ok=connect();assert not ok;other.close();second.close()
f.close();s.close()
print('budget enforced')
"""
    assert await lab.client(lease, code, lease.proxy_url, boundary) == "budget enforced"
    evidence = await lab.evidence(lease, "budget-" + boundary)
    counters = evidence["nativeNetwork"]
    assert counters["attempts"] == 1
    assert (await lab.counts(target))["connections"] == 1
    reason = {
        "maxConnections": "attempts",
        "maxBytes": "bytes",
        "maxConnectionsPerSecond": "rate",
        "maxConcurrentConnections": "concurrency",
    }[boundary]
    assert counters["denials"][reason] >= 1
    if boundary == "maxBytes":
        assert (await lab.counts(target))["commands"] == []
        assert counters["bytesReserved"] <= value
    else:
        assert counters["bytesReserved"] > 0


@docker_only
@pytest.mark.asyncio
@pytest.mark.parametrize("ending", ["deadline", "cancel", "restart", "replace"])
async def test_native_docker_terminal_lifecycle_and_other_assessment(
    docker_lab, ending
):
    lab = docker_lab
    network = await lab.network("target", policy="native-fixtures")
    target = await lab.target("target", network)
    lease, before = await lab.new_lease(
        network, target, maxDurationSeconds=12 if ending == "deadline" else 90
    )
    other_network = await lab.network("other-assessment", policy="native-fixtures")
    other_target = await lab.target("other-target", other_network)
    other, other_before = await lab.new_lease(other_network, other_target)
    assert json.loads(
        await lab.client(other, CONNECT, other.proxy_url, "native.example.test:2525")
    )["accepted"]
    # Keep protocol traffic active across cancellation/deadline. Idle timeout
    # cannot explain closure, and the target observes every complete command.
    active = """
import socket,sys,time,json
from urllib.parse import urlsplit
p=urlsplit(sys.argv[1]);s=socket.create_connection((p.hostname,p.port),timeout=2);s.settimeout(2)
s.sendall(b'CONNECT native.example.test:2525 HTTP/1.1\\r\\nHost: native.example.test:2525\\r\\n\\r\\n')
f=s.makefile('rb');assert f.readline().startswith(b'HTTP/1.1 200');assert f.readline()==b'\\r\\n';assert f.readline().startswith(b'220')
commands=0
while True:
 try:
  s.sendall(b'HELO observer\\r\\n')
  line=f.readline()
  if not line:break
  assert line.startswith(b'250');commands+=1
  time.sleep(.05)
 except OSError:break
print(json.dumps({'closed':True,'commands':commands}))
"""
    task = asyncio.create_task(lab.client(lease, active, lease.proxy_url))
    for _ in range(40):
        if "HELO" in (await lab.counts(target))["commands"]:
            break
        await asyncio.sleep(0.05)
    assert "HELO" in (await lab.counts(target))["commands"]
    if ending != "deadline":
        await lab.run("stop", "--time", "1", lease.gateway_ref)
    result = json.loads(await asyncio.wait_for(task, 18))
    assert result["closed"] and result["commands"] > 0
    evidence = await lab.evidence(lease, ending)
    assert evidence["nativeNetwork"]["attempts"] == 1
    assert evidence["nativeNetwork"]["bytesReserved"] > 0
    assert evidence["nativeNetwork"]["deadlineUnix"] == before["expiresAt"]
    if ending == "deadline":
        assert time.time() >= before["expiresAt"]
    if ending == "restart":
        await lab.run("start", lease.gateway_ref)
        for _ in range(40):
            status = await lab.run(
                "inspect", "--format", "{{.State.Status}}", lease.gateway_ref
            )
            if status == "exited":
                break
            await asyncio.sleep(0.1)
        assert status == "exited"
        code, stdout, stderr = await lab.runner(("logs", lease.gateway_ref))
        assert code == 0 and b"FileExistsError" in stdout + stderr
        with pytest.raises(RuntimeError):
            await lease.prepare(timeout_seconds=9999)
    if ending == "replace":
        await lab.run("rm", "--force", lease.gateway_ref)
        with pytest.raises(RuntimeError, match="retained native state"):
            await lease.prepare(timeout_seconds=9999)
    await lab.run("rm", "--force", lease.workload_ref)
    await lease.cleanup()
    # The other assessment's gateway, original deadline, state and target are
    # still live after owned cleanup, on their separate admitted network.
    after = await other.prepare(timeout_seconds=9999)
    assert after["gatewayIdentity"] == other_before["gatewayIdentity"]
    assert after["expiresAt"] == other_before["expiresAt"]
    assert json.loads(
        await lab.client(other, CONNECT, other.proxy_url, "native.example.test:2525")
    )["accepted"]
    assert (await lab.counts(target))["connections"] == 1
    assert (await lab.counts(other_target))["connections"] == 2
    code, _, _ = await lab.runner(("volume", "inspect", lease.state_ref))
    assert code != 0
    assert (lab.artifacts / (ending + ".json")).is_file()


@docker_only
@pytest.mark.asyncio
async def test_native_docker_interrupted_prepare_retains_one_shot(docker_lab):
    lab = docker_lab
    network = await lab.network("target", policy="native-fixtures")
    target = await lab.target("target", network)
    lease, before = await lab.new_lease(network, target)
    # Simulate loss before the worker persists its receipt: Docker resources,
    # not a local success flag, determine whether reconstruction may proceed.
    from moonmind.security.scoped_egress import ScopedEgressLease

    retry = ScopedEgressLease(
        lab.runner,
        lease.policy,
        ownership=lease.ownership,
        job_id=lease.job_id,
        workload_ref=lease.workload_ref,
        native_selection=lease.native_selection,
    )
    recovered = await retry.prepare(timeout_seconds=9999, previously_admitted=False)
    assert recovered["gatewayIdentity"] == before["gatewayIdentity"]
    assert recovered["expiresAt"] == before["expiresAt"]
    assert json.loads(
        await lab.client(lease, CONNECT, lease.proxy_url, "native.example.test:2525")
    )["accepted"]
    await lab.evidence(lease, "interrupted-before-receipt")
    await lab.run("rm", "--force", lease.gateway_ref)
    with pytest.raises(RuntimeError, match="retained native state"):
        await retry.prepare(timeout_seconds=9999, previously_admitted=False)
    assert (await lab.counts(target))["connections"] == 1


@docker_only
@pytest.mark.asyncio
async def test_native_docker_dns_pinning_rebinding_and_exclusions(docker_lab):
    lab = docker_lab
    hostfile = Path("/etc/hosts")
    original = hostfile.read_text()
    target_declaration = {
        "hosts": ["native.example.test"],
        "ports": [2525],
        "purpose": "live-target",
    }
    lease = await lab.new_lease(None, None, start=False, targets=[target_declaration])
    # A global-looking fixture address exists only on this test-owned Docker
    # network. No public or live third-party target is contacted.
    lab.networks.append(lease.outbound_ref)
    await lab.run(
        "network",
        "create",
        "--subnet",
        "93.184.217.0/24",
        "--label",
        "moonmind.ownership=" + lease.ownership,
        "--label",
        "moonmind.egress.scope=" + lease.policy.digest,
        "--label",
        f"moonmind.expires_at={int(time.time()) + 120}",
        lease.outbound_ref,
    )
    target = await lab.target(
        "public-target", lease.outbound_ref, address="93.184.217.15"
    )
    try:
        hostfile.write_text(
            original
            + "\n93.184.217.15 native.example.test\n169.254.169.254 native.example.test\n"
        )
        with pytest.raises(RuntimeError, match="(private|unapproved|unsafe)"):
            await lease.prepare(timeout_seconds=120)
        assert (await lab.counts(target))["connections"] == 0
        hostfile.write_text(original + "\n93.184.217.15 native.example.test\n")
        lease, before = await lab.start_lease(lease)
        hostfile.write_text(original + "\n169.254.169.254 native.example.test\n")
        assert json.loads(
            await lab.client(
                lease, CONNECT, lease.proxy_url, "native.example.test:2525"
            )
        )["accepted"]
        recovered = await lease.prepare(timeout_seconds=9999)
        assert (
            recovered["resolvedAddresses"]
            == before["resolvedAddresses"]
            == {"native.example.test": ["93.184.217.15"]}
        )
        assert recovered["expiresAt"] == before["expiresAt"]
        assert not json.loads(
            await lab.client(lease, CONNECT, lease.proxy_url, "169.254.169.254:2525")
        )["accepted"]
        await lab.evidence(lease, "public-dns-pinning-rebinding")
        assert (await lab.counts(target))["connections"] == 1
        # Excluded numeric DNS answers are rejected at admission, before any
        # gateway launch. Host exclusions alone must not leave a DNS alias hole.
        hostfile.write_text(original + "\n93.184.217.15 native.example.test\n")
        excluded = await lab.new_lease(
            None,
            None,
            start=False,
            targets=[{**target_declaration, "exclusions": ["93.184.217.15"]}],
        )
        with pytest.raises(RuntimeError, match="exclu"):
            await excluded.prepare(timeout_seconds=120)
        assert (await lab.counts(target))["connections"] == 1
    finally:
        hostfile.write_text(original)


@docker_only
@pytest.mark.asyncio
@pytest.mark.parametrize("resolution", ["resume", "cancel"])
async def test_native_docker_created_before_reply_reconciles(docker_lab, resolution):
    lab = docker_lab
    network = await lab.network("target", policy="native-fixtures")
    target = await lab.target("target", network)
    lease = await lab.new_lease(network, target, start=False)
    interrupted = False

    async def lose_create_reply(args):
        nonlocal interrupted
        result = await lab.runner(args)
        if (
            not interrupted
            and args[0] == "create"
            and lease.gateway_ref in args
            and result[0] == 0
        ):
            interrupted = True
            raise ConnectionError("test-only lost reply after confirmed gateway create")
        return result

    lease.runner = lose_create_reply
    with pytest.raises(ConnectionError, match="lost reply"):
        await lease.prepare(timeout_seconds=120)
    assert interrupted
    identity = await lab.run("inspect", "--format", "{{.Id}}", lease.gateway_ref)
    lease.runner = lab.runner
    if resolution == "cancel":
        evidence = await lab.evidence(lease, "never-started-cancellation")
        assert evidence["nativeNoUseWitness"]["stateDirectoryEmpty"] is True
        assert evidence["nativeNoUseWitness"]["gatewayIdentity"] == identity
        await lease.cleanup()
        for args in (
            ("inspect", lease.gateway_ref),
            ("volume", "inspect", lease.state_ref),
            ("network", "inspect", lease.network_ref),
        ):
            code, _, _ = await lab.runner(args)
            assert code != 0
        assert (await lab.counts(target))["connections"] == 0
        return
    lease, recovered = await lab.start_lease(lease)
    assert recovered["gatewayIdentity"] == identity
    assert json.loads(
        await lab.client(lease, CONNECT, lease.proxy_url, "native.example.test:2525")
    )["accepted"]
    assert (await lab.counts(target))["connections"] == 1
    await lab.evidence(lease, "created-before-reply-recovery")


@docker_only
@pytest.mark.asyncio
async def test_native_docker_captured_ca_observation_profile(docker_lab):
    """Actual backend renders a fixed profile and captures a public CA input."""
    from moonmind.config.container_backend_settings import (
        resolve_container_backend_settings,
    )
    from moonmind.schemas.container_job_models import (
        AuxiliaryOutcome,
        ContainerJobActivityRequest,
        ContainerJobSpec,
    )
    from moonmind.security.workload_profiles import (
        AdmittedAssessmentScope,
        WorkloadProfile,
        resolve_profile_job,
        resolved_job_within_scope,
    )
    from moonmind.workflows.temporal.container_job_backend import (
        DockerContainerJobBackend,
    )

    lab = docker_lab
    network = await lab.network("target", policy="native-fixtures")
    target = await lab.target("target", network)
    template = await lab.new_lease(network, target, start=False)
    native = template.native_selection.model_dump(mode="json", by_alias=True)
    workspace = lab.artifacts / "profile-input"
    ca_directory = workspace / "ca"
    ca_directory.mkdir(parents=True)
    await lab.run(
        "cp", target + ":/tmp/smtp/native-ca.pem", str(ca_directory / "native-ca.pem")
    )
    # Deployment-owned fixed operation. It reads only the trusted proxy setting
    # and captured public CA, never a user-provided shell command or credential.
    command = (
        "import os,subprocess; subprocess.run(['curl','--silent','--show-error',"
        "'--max-time','8','--noproxy','','--proxy',os.environ['MOONMIND_EGRESS_PROXY'],"
        "'--proxytunnel','--user-agent','','--ssl-reqd','--cacert','/inputs/ca/native-ca.pem',"
        "'--request','NOOP','smtp://native.example.test:2525'],check=True)"
    )
    profile_data = {
        "profileRef": "native-observation",
        "imageSourceRef": "native-fixture-client",
        "entrypoint": ["python", "-c"],
        "modes": [
            {"name": "observe", "actionClass": "tcp-observation", "argv": [command]}
        ],
        "networkMode": "bridge",
        "egressPolicyRef": "native-fixtures",
        "nativeNetwork": native,
        "maxCpuMillis": 2000,
        "maxMemoryMiB": 1024,
        "maxTimeoutSeconds": 120,
    }
    profile = WorkloadProfile.model_validate(profile_data)
    spec = resolve_profile_job(
        ContainerJobSpec.model_validate(
            {
                "workspaceRef": {"kind": "sandbox", "workspaceId": "native-fixtures"},
                "workspaceReadOnly": True,
                "inputAttachments": [{"name": "ca", "workspacePath": "ca"}],
                "resources": {"cpuMillis": 1000, "memoryMiB": 512, "pids": 128},
                "timeoutSeconds": 120,
                "workloadProfile": {
                    "profileRef": "native-observation",
                    "mode": "observe",
                    "assessmentRef": "native-assessment",
                    "nativeNetwork": native,
                },
            }
        ),
        (profile,),
    )
    scope = AdmittedAssessmentScope.model_validate(
        {
            "assessmentRef": "native-assessment",
            "profileRefs": ["native-observation"],
            "actionClasses": ["tcp-observation"],
            "targets": [{"workspacePath": "ca"}, {"nativeNetwork": native}],
            "networkRefs": ["native-fixtures"],
            "maxJobs": 1,
            "maxJobSeconds": 120,
            "evidenceIntent": "TLS-verified SMTP capability observation without authentication or delivery",
        }
    )
    resolved_job_within_scope(spec, scope, (profile,))
    settings = resolve_container_backend_settings(
        {
            "MOONMIND_CONTAINER_BACKEND_EGRESS_POLICIES": json.dumps(
                [template.policy.model_dump(mode="json", by_alias=True)]
            ),
            "MOONMIND_CONTAINER_BACKEND_IMAGE_SOURCES": json.dumps(
                [{"sourceRef": "native-fixture-client", "image": lab.image}]
            ),
            "MOONMIND_CONTAINER_BACKEND_WORKLOAD_PROFILES": json.dumps([profile_data]),
        }
    )

    async def publish(request, name, body):
        destination = lab.artifacts / name.replace(":", "-")
        destination.write_bytes(body)
        return str(destination.relative_to(lab.root))

    backend = DockerContainerJobBackend(
        workspace_root=lab.root,
        settings=settings,
        # This is the same disposable daemon socket mounted by the dedicated
        # driver. Exercise the production adapter, including native binary cp.
        docker_binary="/usr/libexec/moonmind/docker-real",
        docker_host="unix:///var/run/docker.sock",
        evidence_publisher=publish,
    )
    request = ContainerJobActivityRequest(
        jobId="container-job:" + uuid4().hex,
        ownershipToken=lab.prefix + "-profile",
        request={
            "idempotencyKey": lab.prefix + "-profile",
            "source": {"source": "workflow", "workflowId": "native-fixtures"},
            "spec": spec,
        },
        resolvedWorkspaceRef=str(workspace),
        resolvedImageRef=await lab.run(
            "image", "inspect", "--format", "{{.Id}}", lab.image
        ),
    )
    lease = backend._scoped_lease(request)
    lab.leases.append(lease)
    try:
        created = await backend.create_container(request)
        request.container_ref = created.container_ref
        lab.containers.append(created.container_ref)
        await backend.start_container(request)
        for _ in range(80):
            observed = await backend.observe_container(request)
            if observed.terminal_state:
                break
            await asyncio.sleep(0.1)
        request.terminal_state = observed.terminal_state
        request.exit_code = observed.exit_code
        assert observed.terminal_state == "succeeded" and observed.exit_code == 0
        counts = await lab.counts(target)
        assert counts["connections"] == counts["starttls"] == counts["noop"] == 1
        assert not {"AUTH", "MAIL", "RCPT", "DATA"}.intersection(counts["commands"])
        await lab.evidence(lease, "captured-ca-profile")
        inspect = json.loads(
            await lab.run("inspect", "--format", "{{json .}}", request.container_ref)
        )
        assert all(
            "docker.sock" not in mount["Destination"] for mount in inspect["Mounts"]
        )
        assert set(inspect["NetworkSettings"]["Networks"]) == {lease.network_ref}
    finally:
        if request.container_ref:
            # Exercise the actual terminal lifecycle, including failed tools.
            # Unpublished output retention is intentional, not a cleanup defect.
            published = await backend.publish_evidence(request)
            for reference in (
                published.logs_ref,
                published.artifacts_ref,
                published.diagnostics_ref,
            ):
                assert reference and (lab.root / reference).is_file()
            request.publication = AuxiliaryOutcome(
                state="succeeded",
                diagnosticsRef=published.diagnostics_ref,
            )
            await backend.remove_container(request)
        cleanup = await backend.cleanup(request)
        assert cleanup.cleanup_succeeded, cleanup.model_dump(mode="json", by_alias=True)


@docker_only
@pytest.mark.asyncio
async def test_native_docker_production_runner_preserves_archive_authority(docker_lab):
    """Native authority is binary evidence, not text to rewrite as a secret."""
    from moonmind.workflows.temporal.container_job_backend import (
        DockerContainerJobBackend,
    )

    lab = docker_lab
    network = await lab.network("target", policy="native-fixtures")
    # Test-only root permits the standard SMTP port on hosts that retain the
    # privileged-port boundary. No TLS client uses this different hostname.
    target = await lab.target("target", network, port=587)
    lease = await lab.new_lease(
        network,
        target,
        start=False,
        targets=[
            {
                "hosts": ["session.example.com"],
                "ports": [587],
                "purpose": "live-target",
                "targetNetworkRef": network,
                "targetContainerRef": target,
            }
        ],
    )
    backend = DockerContainerJobBackend(
        workspace_root=lab.root,
        docker_binary="/usr/libexec/moonmind/docker-real",
        docker_host="unix:///var/run/docker.sock",
    )
    # Do not inject the raw fixture runner: use exactly the default production
    # adapter whose normal text path deliberately applies secret redaction.
    lease.runner = backend._runner
    lease, initial = await lab.start_lease(lease)
    result = json.loads(
        await lab.client(lease, CONNECT, lease.proxy_url, "session.example.com:587")
    )
    assert result["accepted"]
    live = await lease.attest(workload=True, expected_image=lab.image)
    expected = [
        {
            "authority": "session.example.com:587",
            "address": initial["resolvedAddresses"]["session.example.com"][0],
            "port": 587,
        }
    ]
    assert live["nativeNetwork"]["endpoints"] == expected
    assert live["nativeNetwork"]["attempts"] == 1
    await lease.stop_native()
    final = await lab.evidence(lease, "production-runner-native-archive")
    assert final["nativeNetwork"]["endpoints"] == expected
    assert (
        final["nativeNetwork"]["configDigest"] == live["nativeNetwork"]["configDigest"]
    )
    assert final["nativeNetwork"]["attempts"] == 1
    counts = await lab.counts(target)
    assert counts["connections"] == 1 and counts["starttls"] == counts["noop"] == 0
    assert counts["commands"] == []
