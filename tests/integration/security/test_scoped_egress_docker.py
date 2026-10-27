"""Real scoped launch on a disposable CI daemon, never a managed-job socket.

tools/test_scoped_egress.sh owns this separate CI entrypoint. The ordinary
managed Python path runs the socket-free connection and authority suites.
"""

from __future__ import annotations

import asyncio
import json
import os
import time
from pathlib import Path
from uuid import uuid4

import pytest

from moonmind.config.container_backend_settings import (
    resolve_container_backend_settings,
)
from moonmind.schemas.container_job_models import ContainerJobActivityRequest
from moonmind.security import egress, scoped_egress
from moonmind.workflows.temporal.container_job_backend import DockerContainerJobBackend

pytestmark = [
    pytest.mark.integration,
    pytest.mark.integration_ci,
    pytest.mark.skipif(
        os.environ.get("MOONMIND_RUN_SCOPED_EGRESS_DOCKER") != "1",
        reason="dedicated isolated CI Docker entrypoint",
    ),
]


def fixture_hosts(*, mixed=False, rebound=False):
    path = Path("/etc/hosts")
    lines = [
        line for line in path.read_text().splitlines() if "example.test" not in line
    ]
    address = "169.254.169.254" if rebound else "93.184.216.15"
    lines.append(
        f"{address} feeds.example.test cloud.example.test registry.example.test"
    )
    if mixed:
        lines.append("169.254.169.254 feeds.example.test")
    path.write_text("\n".join(lines) + "\n")


@pytest.mark.asyncio
async def test_real_scoped_launch_connections_and_owned_lifecycle(monkeypatch):
    root = Path(__file__).resolve().parents[3]
    image = os.environ["MOONMIND_SCOPED_TEST_IMAGE"]
    prefix = "moonmind-test-egress-" + uuid4().hex[:10]
    containers, networks, calls, artifacts = [], [], [], {}
    leases = []
    publication_sequence = 0

    async def runner(args):
        calls.append(tuple(args))
        proc = await asyncio.create_subprocess_exec(
            "/usr/libexec/moonmind/docker-real",
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=30)
        return proc.returncode, stdout, stderr

    async def run(*args):
        code, stdout, stderr = await runner(args)
        assert code == 0, stderr.decode()[-2000:]
        return stdout.decode().strip()

    async def publish(request, name, body):
        nonlocal publication_sequence
        publication_sequence += 1
        artifacts[name] = json.loads(body)
        evidence = root / "artifacts/integration-ci/scoped-egress"
        evidence.mkdir(parents=True, exist_ok=True)
        artifact = evidence / f"{publication_sequence:03d}-{name.replace(':', '-')}"
        artifact.write_bytes(body)
        return str(artifact.relative_to(root))

    async def network(name, *, internal=False, labels=(), subnet=None):
        networks.append(name)
        args = ["network", "create", "--label", f"moonmind.test.owner={prefix}"]
        if internal:
            args.extend(
                (
                    "--internal",
                    "--opt",
                    "com.docker.network.bridge.inhibit_ipv4=true",
                )
            )
        if subnet:
            args.extend(("--subnet", subnet))
        for label in labels:
            args.extend(("--label", label))
        await run(*args, name)

    async def fixture(name, network_name, *, policy=None, address=None):
        containers.append(name)
        args = [
            "run",
            "-d",
            "--name",
            name,
            "--network",
            network_name,
            "--label",
            f"moonmind.test.owner={prefix}",
        ]
        if policy:
            args.extend(("--label", f"moonmind.security.target={policy}"))
        if address:
            args.extend(("--ip", address))
        await run(
            *args,
            "--mount",
            f"type=bind,src={root},dst=/workspace,readonly",
            "--workdir",
            "/workspace",
            "--env",
            "PYTHONPATH=/workspace",
            "--entrypoint",
            "python",
            image,
            "tests/integration/security/test_scoped_egress_connections.py",
        )
        for _ in range(20):
            code, _, _ = await runner(
                (
                    "exec",
                    name,
                    "python",
                    "-c",
                    "import socket; socket.create_connection(('127.0.0.1',8080),timeout=1).close()",
                )
            )
            if code == 0:
                return
            await asyncio.sleep(0.2)
        pytest.fail("scoped HTTP fixture did not become ready")

    try:
        # Exercise the unchanged installed-gateway attestation with real config,
        # health, internal networks and image identity, under test-only names.
        installed_networks = {
            key: prefix + "-" + key
            for key in ("restricted", "sandbox", "omnigent", "external")
        }
        for key, name in installed_networks.items():
            await network(name, internal=key != "external")
        installed_gateway = prefix + "-installed-proxy"
        containers.append(installed_gateway)
        await run(
            "create",
            "--name",
            installed_gateway,
            "--user",
            "0:0",
            "--network",
            installed_networks["external"],
            "--label",
            f"moonmind.egress.enforcer={egress.ENFORCER_IMPLEMENTATION}",
            "--mount",
            f"type=bind,src={root / 'docker/moonmind-egress'},dst=/opt/moonmind-egress,readonly",
            "--mount",
            f"type=bind,src={root / 'docker/sandbox-egress-proxy'},dst=/app/docker/sandbox-egress-proxy,readonly",
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
            image,
            "/opt/moonmind-egress/policy.sh",
            "start",
        )
        for key in ("restricted", "sandbox", "omnigent"):
            args = ["network", "connect"]
            if key == "omnigent":
                args.extend(("--alias", "omnigent-egress-proxy"))
            await run(*args, installed_networks[key], installed_gateway)
        await run("start", installed_gateway)
        for _ in range(30):
            status = await run(
                "inspect", "--format", "{{.State.Health.Status}}", installed_gateway
            )
            if status == "healthy":
                break
            await asyncio.sleep(0.5)
        assert status == "healthy"
        installed = egress.DEFAULT_EGRESS_PROFILE.model_copy(
            update={
                "network_ref": installed_networks["restricted"],
                "gateway_ref": installed_gateway,
            }
        )
        monkeypatch.setattr(scoped_egress, "DEFAULT_EGRESS_PROFILE", installed)
        monkeypatch.setitem(
            egress.EGRESS_PROFILE_DIGESTS, installed.ref, installed.digest
        )
        monkeypatch.setattr(
            egress, "_EXPECTED_GATEWAY_NETWORKS", frozenset(installed_networks.values())
        )
        private_network, unrelated_network = prefix + "-lab", prefix + "-other-lab"
        await network(
            private_network,
            internal=True,
            labels=("moonmind.security.target=scoped-fixtures",),
        )
        await network(
            unrelated_network,
            internal=True,
            labels=("moonmind.security.target=other-assessment",),
        )
        target, unrelated = prefix + "-target", prefix + "-unrelated"
        await fixture(target, private_network, policy="scoped-fixtures")
        await fixture(unrelated, unrelated_network, policy="other-assessment")
        peer = json.loads(await run("inspect", "--format", "{{json .}}", unrelated))
        peer_address = peer["NetworkSettings"]["Networks"][unrelated_network][
            "IPAddress"
        ]
        peer_hostname = peer["Name"].lstrip("/")
        assert peer["State"]["Running"] is True
        assert (
            peer["Config"]["Labels"]["moonmind.security.target"] == "other-assessment"
        )
        assert set(peer["NetworkSettings"]["Networks"]) == {unrelated_network}
        # Confirm the unrelated peer serves both its real address and Docker
        # name from its own network before testing isolation from the job.
        peer_health = json.loads(
            await run(
                "exec",
                unrelated,
                "python",
                "-c",
                "import httpx,json,sys; "
                "c=httpx.Client(trust_env=False,timeout=5); "
                "responses=[c.get('http://'+host+':8080/') for host in sys.argv[1:]]; "
                "assert all(r.status_code==200 and 'admitted-scope-fixture' in r.text for r in responses); "
                "print(json.dumps([{'host':h,'status':r.status_code} for h,r in zip(sys.argv[1:],responses)]))",
                peer_address,
                peer_hostname,
            )
        )
        declaration = {
            "policyRef": "scoped-fixtures",
            "allowedWorkflowIds": ["scoped-fixtures"],
            "allowedImageSourceRefs": ["fixture-client"],
            "destinations": [
                {
                    "host": f"{name}.example.test",
                    "purpose": purpose,
                    "protocol": "https",
                    "port": 8443,
                }
                for name, purpose in (
                    ("feeds", "tool-data"),
                    ("cloud", "provider-api"),
                    ("registry", "registry-api"),
                )
            ]
            + [
                {
                    "host": "lab.example.test",
                    "purpose": "live-target",
                    "protocol": "http",
                    "port": 8080,
                    "targetNetworkRef": private_network,
                    "targetContainerRef": target,
                }
            ],
        }
        settings = resolve_container_backend_settings(
            {
                "MOONMIND_CONTAINER_BACKEND_EGRESS_POLICIES": json.dumps([declaration]),
                "MOONMIND_CONTAINER_BACKEND_IMAGE_SOURCES": json.dumps(
                    [{"sourceRef": "fixture-client", "image": image}]
                ),
            }
        )
        backend = DockerContainerJobBackend(
            workspace_root=root,
            settings=settings,
            command_runner=runner,
            evidence_publisher=publish,
        )
        resolved_image = await run("image", "inspect", "--format", "{{.Id}}", image)
        request = ContainerJobActivityRequest(
            jobId="container-job:" + uuid4().hex,
            ownershipToken=prefix + "-job",
            request={
                "idempotencyKey": prefix,
                "source": {"source": "workflow", "workflowId": "scoped-fixtures"},
                "spec": {
                    "imageSourceRef": "fixture-client",
                    "workspaceRef": {"kind": "sandbox", "workspaceId": "fixtures"},
                    "workspaceReadOnly": True,
                    "resources": {"cpuMillis": 2000, "memoryMiB": 1024, "pids": 512},
                    "timeoutSeconds": 120,
                    "networkMode": "bridge",
                    "egressPolicyRef": "scoped-fixtures",
                    "command": ["sleep", "90"],
                },
            },
            resolvedWorkspaceRef=str(root),
            resolvedImageRef=resolved_image,
        )
        lease = backend._scoped_lease(request)
        leases.append(lease)
        # A globally addressed fixture on the owned outbound network supplies
        # public feed/API traffic without an Internet or paid-provider gate.
        await network(
            lease.outbound_ref,
            labels=(
                f"moonmind.ownership={request.ownership_token}",
                f"moonmind.egress.scope={lease.policy.digest}",
                f"moonmind.expires_at={int(time.time()) + 120}",
            ),
            subnet="93.184.216.0/24",
        )
        public_fixture = prefix + "-public-fixture"
        await fixture(public_fixture, lease.outbound_ref, address="93.184.216.15")
        # The driver uses its own test hosts file; production DNS resolution is
        # still called, validates every answer, and pins the resulting address.
        await asyncio.to_thread(fixture_hosts, mixed=True)
        with pytest.raises(RuntimeError, match="DNS includes an unapproved"):
            await backend.create_container(request)
        await asyncio.to_thread(fixture_hosts)
        await asyncio.sleep(0.5)
        created = await backend.create_container(request)
        request.container_ref = created.container_ref
        containers.append(created.container_ref)
        before = await lease.attest(workload=True, expected_image=resolved_image)
        await asyncio.to_thread(fixture_hosts, rebound=True)
        await backend.start_container(request)
        # SDK, browser and child process connect from the real job namespace.
        code = """
import json,os,subprocess,sys,socket,httpx
from moonmind.security.egress_policy import tool_proxy_options
peer_hosts=sys.argv[1:]
peer_checks=[]
with httpx.Client(**tool_proxy_options('httpx',environ=os.environ),verify=False,timeout=5) as c:
 for host in ('feeds','cloud','registry'):
  assert c.get('https://'+host+'.example.test:8443/').status_code==200
 assert c.get('http://lab.example.test:8080/').status_code==200
 assert c.get('http://lab.example.test:8080/redirect',follow_redirects=True).status_code==403
 for url in ('http://unrelated.example.test:8080/','http://169.254.169.254/','http://api:8000/','http://lab.example.test:8443/','http://93.184.216.15:8080/'):
  assert c.get(url).status_code==403
 for host in peer_hosts:
  status=c.get('http://'+host+':8080/').status_code
  assert status==403,(host,status)
  peer_checks.append({'host':host,'transport':'admitted-proxy','status':status})
for host,port in (('93.184.216.15',8443),('169.254.169.254',80),('172.17.0.1',80)):
 try: s=socket.create_connection((host,port),timeout=.3)
 except OSError: pass
 else: s.close(); raise AssertionError('direct route escaped')
for host in peer_hosts:
 try: s=socket.create_connection((host,8080),timeout=.3)
 except OSError as exc: peer_checks.append({'host':host,'transport':'direct-socket','error':type(exc).__name__})
 else: s.close(); raise AssertionError('direct private-peer route escaped: '+host)
os.environ['NO_PROXY']='*'
with httpx.Client(trust_env=True,timeout=.5) as c:
 try: c.get('http://93.184.216.15:8080/')
 except httpx.RequestError: pass
 else: raise AssertionError('proxy override escaped')
 for host in peer_hosts:
  try: c.get('http://'+host+':8080/')
  except httpx.RequestError as exc: peer_checks.append({'host':host,'transport':'no-proxy-override','error':type(exc).__name__})
  else: raise AssertionError('private-peer proxy bypass escaped: '+host)
for host in peer_hosts:
 env=dict(os.environ)
 os.environ.update(HTTP_PROXY='http://'+host+':8080',http_proxy='http://'+host+':8080',NO_PROXY='',no_proxy='')
 try:
  with httpx.Client(trust_env=True,timeout=.5) as c:
   try: c.get('http://lab.example.test:8080/')
   except httpx.RequestError as exc: peer_checks.append({'host':host,'transport':'alternate-proxy','error':type(exc).__name__})
   else: raise AssertionError('private-peer alternate proxy escaped: '+host)
 finally:
  os.environ.clear(); os.environ.update(env)
child=subprocess.run([sys.executable,'-c',"import os,httpx;from moonmind.security.egress_policy import tool_proxy_options;assert httpx.get('https://feeds.example.test:8443/',verify=False,timeout=5,**tool_proxy_options('httpx',environ=os.environ)).status_code==200"],capture_output=True,timeout=10)
assert child.returncode==0,child.stderr
browser=subprocess.run(['chromium','--headless','--no-sandbox','--disable-dev-shm-usage','--disable-background-networking','--no-first-run','--user-data-dir=/tmp/scoped-browser',*tool_proxy_options('chromium',environ=os.environ)['args'],'--dump-dom','http://lab.example.test:8080/'],capture_output=True,timeout=30)
assert browser.returncode==0 and b'admitted-scope-fixture' in browser.stdout,browser.stderr[-2000:]
print(json.dumps({'crossAssessmentPeerChecks':peer_checks}))
"""
        connection_results = json.loads(
            await run(
                "exec",
                request.container_ref,
                "python",
                "-c",
                code,
                peer_address,
                peer_hostname,
            )
        )
        assert {
            (row["host"], row["transport"])
            for row in connection_results["crossAssessmentPeerChecks"]
        } == {
            (host, transport)
            for host in (peer_address, peer_hostname)
            for transport in (
                "admitted-proxy", "direct-socket", "no-proxy-override", "alternate-proxy"
            )
        }
        # A reconstructed worker reuses the exact selected lease and snapshot.
        recovered = await backend.reconcile_container(request)
        after = await lease.prepare(timeout_seconds=9999)
        assert recovered.container_ref == request.container_ref
        assert after["snapshotDigest"] == before["snapshotDigest"]
        assert after["expiresAt"] == before["expiresAt"]
        assert after["deniedConnectionCount"] >= 5 and after["coverage"] == "partial"
        await backend._publish_scoped_egress(request, after)
        # Gateway replacement cannot silently inherit the workload's original
        # binding even when a replacement advertises the same policy labels.
        gateway_create = next(
            c for c in calls if c[0] == "create" and lease.gateway_ref in c
        )
        await run("rm", "--force", lease.gateway_ref)
        await run(*gateway_create)
        for name in (lease.outbound_ref, private_network):
            await run("network", "connect", name, lease.gateway_ref)
        await run("start", lease.gateway_ref)
        await lease._wait_ready()
        with pytest.raises(RuntimeError, match="workload identity"):
            await lease.attest(workload=True, expected_image=resolved_image)
        # Network evidence remains available even when the command exits zero.
        await run("exec", request.container_ref, "sh", "-c", "kill -TERM 1")
        await backend.stop_container(request)
        # Tampering is detected through live bytes, then cleanup removes only
        # the lease's own proxy and job attachment, retaining all target peers.
        await run(
            "exec",
            lease.gateway_ref,
            "sh",
            "-c",
            "chmod u+w /tmp/moonmind-hosts; printf '127.0.0.1 lab.example.test\\n' >> /tmp/moonmind-hosts",
        )
        with pytest.raises(RuntimeError, match="tampered"):
            await lease.attest()
        await backend.remove_container(request)
        # The fixture's public endpoint is an independently owned peer, so the
        # network removal must report failure until the fixture owner removes it.
        result = await backend.cleanup(request)
        assert result.cleanup_succeeded is False
        await run("inspect", "--type", "container", target, unrelated, public_fixture)
        await run("rm", "--force", public_fixture)
        assert (await backend.cleanup(request)).cleanup_succeeded is True
        offline = request.model_copy(deep=True)
        offline.job_id = "container-job:" + uuid4().hex
        offline.ownership_token = prefix + "-offline"
        offline.container_ref = offline.egress_attestation_ref = None
        offline.request.spec.egress_policy_ref = None
        offline.request.spec.network_mode = "none"
        offline.request.spec.command = [
            "python",
            "-c",
            "import socket; s=socket.socket(); s.settimeout(.3); assert s.connect_ex(('93.184.216.15',8443)) != 0; print('offline route denied')",
        ]
        offline_created = await backend.create_container(offline)
        offline.container_ref = offline_created.container_ref
        containers.append(offline.container_ref)
        await backend.start_container(offline)
        for _ in range(20):
            observed = await backend.observe_container(offline)
            if observed.terminal_state:
                break
            await asyncio.sleep(0.2)
        assert observed.terminal_state == "succeeded" and observed.exit_code == 0
        await backend.remove_container(offline)
        assert (await backend.cleanup(offline)).cleanup_succeeded is True
        evidence = root / "artifacts/integration-ci/scoped-egress/connections.json"
        evidence.write_text(
            json.dumps(
                {
                    "candidate": os.environ.get("MOONMIND_TEST_CANDIDATE"),
                    "connectionScript": "passed",
                    "ownedCleanup": "passed",
                    "mixedDnsAndRebinding": "passed",
                    "gatewayReplacement": "passed",
                    "offline": "passed",
                    "policyRef": "scoped-fixtures",
                    "deniedConnectionCount": after["deniedConnectionCount"],
                    "crossAssessmentPeer": {
                        "containerIdentity": peer["Id"],
                        "assessment": peer["Config"]["Labels"][
                            "moonmind.security.target"
                        ],
                        "network": unrelated_network,
                        "health": peer_health,
                        "checks": connection_results["crossAssessmentPeerChecks"],
                    },
                },
                indent=2,
            )
        )
    finally:
        for name in reversed(containers):
            await runner(("rm", "--force", name))
        for lease in leases:
            try:
                await lease.cleanup()
            except RuntimeError:
                # Best-effort teardown must continue removing the other fixtures.
                pass
        for name in reversed(networks):
            await runner(("network", "rm", name))
