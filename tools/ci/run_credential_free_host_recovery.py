#!/usr/bin/env python3
"""Own one provider-free recovery row in the existing exact-artifact CI job.

This is not the concurrency/provider qualification runner. Only test drivers
are mounted into the already built application image; its production imports
remain image-owned. The upstream server, host and their observed identities
are supplied by the job's pinned-source builds, never inferred from latest.
"""

from __future__ import annotations

import argparse
import copy
import json
import os
import re
import shutil
import subprocess
import tempfile
import uuid
import xml.etree.ElementTree as ET
from pathlib import Path

import yaml

TEST_NAME = "test_exact_host_replacement_resumes_same_session_without_provider_input"
TEST_FILE = "test_exact_docker_n_way_concurrency.py"
RECEIPT = "credential-recovery-exact-docker.json"
JUNIT = "credential-recovery-junit.xml"
POSTGRES_IMAGE = "postgres:16@sha256:6efd0df010dc3cb40d5e33e3ef84acecc5e73161bd3df06029ee8698e5e12c60"
READINESS_SCRIPT = """import json
import os
import time
import urllib.error
import urllib.request

request = urllib.request.Request(
    'http://omnigent:8000/v1/agents',
    headers={'Authorization': 'Bearer ' + os.environ['OMNIGENT_API_TOKEN']},
)
# Exercise the same internal control listener used by the actual host. Set
# the request proxy explicitly so an inherited NO_PROXY cannot bypass it.
request.set_proxy('omnigent-egress-proxy:3129', 'http')
opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
deadline = time.monotonic() + 180
while time.monotonic() < deadline:
    try:
        with opener.open(request, timeout=2) as response:
            payload = json.loads(response.read(1024 * 1024))
            if (response.status == 200 and isinstance(payload, dict)
                    and isinstance(payload.get('data'), list)):
                break
    except (OSError, urllib.error.URLError, ValueError):
        pass
    time.sleep(2)
else:
    raise SystemExit('Omnigent internal service did not become ready')
"""


class RecoveryError(RuntimeError):
    """The requested row did not produce current, executed evidence."""


def _require(condition, message):
    if not condition:
        raise RecoveryError(message)


def _command(command, *, check=True):
    result = subprocess.run(
        command, text=True, capture_output=True, timeout=240, check=False
    )
    if check and result.returncode:
        raise RecoveryError(
            f"{command[0]} {command[1]} failed: {result.stderr[-3000:]}"
        )
    return result


def _image(ref):
    rows = json.loads(_command(["docker", "image", "inspect", ref]).stdout)
    _require(len(rows) == 1, f"image unavailable: {ref}")
    return rows[0]


def validate_evidence(root, *, source_commit, host_image_ref, host_image_id):
    """Reject pytest skips, stale receipts and unsupported boolean-only claims."""
    try:
        row = json.loads((root / RECEIPT).read_text())
        cases = ET.parse(root / JUNIT).getroot().findall(".//testcase")
    except (OSError, ValueError, ET.ParseError) as exc:
        raise RecoveryError(f"recovery evidence unavailable: {exc}") from exc
    _require(
        len(cases) == 1
        and cases[0].get("name") == TEST_NAME
        and all(
            cases[0].find(kind) is None for kind in ("skipped", "failure", "error")
        ),
        "the required recovery row was not successfully executed",
    )
    allowed = {
        "schemaVersion",
        "sourceCommit",
        "hostImageRef",
        "before",
        "after",
        "containerReplaced",
        "sameHost",
        "sameSession",
        "sameBridge",
        "runnerReconnected",
        "inputReplayed",
        "workspaceDigest",
    }
    _require(
        isinstance(row, dict) and set(row) <= allowed,
        "unrecognized recovery receipt fields",
    )
    _require(row.get("schemaVersion") == 1, "unsupported recovery receipt")
    _require(row.get("sourceCommit") == source_commit, "stale recovery sourceCommit")
    _require(row.get("hostImageRef") == host_image_ref, "recovery host ref mismatch")
    before, after = row.get("before", {}), row.get("after", {})
    for sample in (before, after):
        _require(isinstance(sample, dict), "missing observed recovery identity")
        _require(
            set(sample)
            <= {
                "containerId",
                "hostImageId",
                "stateVolume",
                "hostId",
                "sessionId",
                "bridgeId",
                "runnerId",
                "messageItemIds",
            },
            "unrecognized observed recovery identity fields",
        )
        for field in (
            "containerId",
            "hostId",
            "sessionId",
            "bridgeId",
            "runnerId",
            "stateVolume",
        ):
            _require(
                isinstance(sample.get(field), str) and bool(sample[field]),
                f"missing observed {field}",
            )
        _require(
            sample.get("hostImageId") == host_image_id, "actual host image mismatch"
        )
        _require(
            sample.get("messageItemIds") == [], "recovery submitted/replayed user input"
        )
    for field in ("hostId", "sessionId", "bridgeId", "stateVolume"):
        _require(before[field] == after[field], f"recovery changed {field}")
    for field in ("containerId", "runnerId"):
        _require(before[field] != after[field], f"recovery did not replace {field}")
    _require(row.get("inputReplayed") is False, "input replay is not provider-free")
    _require(row.get("runnerReconnected") is True, "runner did not reconnect")
    _require(bool(row.get("workspaceDigest")), "missing saved workspace digest")
    return row


def _junit_failure(root):
    """Return the failing row's own message and traceback tail, if recorded."""
    try:
        cases = ET.parse(root / JUNIT).getroot().findall(".//testcase")
    except (OSError, ET.ParseError):
        return ""
    for case in cases:
        for kind in ("failure", "error"):
            found = case.find(kind)
            if found is not None:
                message = found.get("message", "").strip()
                trace = (found.text or "").strip()[-2000:]
                return "\n".join(part for part in (message, trace) if part)
    return ""


def run_test(command, root, **identity):
    # A previous run, including a previously green receipt, cannot satisfy this
    # invocation if prerequisites fail or pytest reports a skip with exit zero.
    for name in (RECEIPT, JUNIT):
        (root / name).unlink(missing_ok=True)
    result = subprocess.run(command, check=False, timeout=600)
    if result.returncode:
        failure = _junit_failure(root)
        raise RecoveryError(
            f"recovery pytest failed (exit {result.returncode})"
            + (f": {failure}" if failure else "")
        )
    return validate_evidence(root, **identity)


def _annotation(text):
    """Escape a GitHub Actions workflow-command message."""
    return text.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")


def compose_document(source, repo_root, server_image, *, moonmind_image):
    """Reuse canonical init/registration commands, replacing deployment wiring."""
    names = (
        "postgres", "omnigent-db-init", "omnigent-agent-init", "omnigent",
        "sandbox-egress-proxy",
    )
    services = {name: copy.deepcopy(source["services"][name]) for name in names}
    for service in services.values():
        service.pop("env_file", None)
        service.pop("profiles", None)
        service.pop("container_name", None)
        service["networks"] = ["test"]
        service["restart"] = "no"
    services["postgres"].update(
        image=POSTGRES_IMAGE,
        environment={
            "POSTGRES_USER": "postgres",
            "POSTGRES_PASSWORD": "moonmind-test-db",
            "POSTGRES_DB": "moonmind",
        },
        volumes=["postgres-data:/var/lib/postgresql/data"],
    )
    services["omnigent-db-init"].update(
        image=POSTGRES_IMAGE,
        environment={
            "PGHOST": "postgres",
            "PGPORT": "5432",
            "PGUSER": "postgres",
            "PGPASSWORD": "moonmind-test-db",
            "PGDATABASE": "moonmind",
            "OMNIGENT_POSTGRES_USER": "omnigent",
            "OMNIGENT_POSTGRES_PASSWORD": "moonmind-test-omnigent",
            "OMNIGENT_POSTGRES_DB": "omnigent",
        },
    )
    database_url = "postgresql://omnigent:moonmind-test-omnigent@postgres:5432/omnigent"
    services["omnigent-agent-init"].update(
        image=server_image,
        environment={"DATABASE_URL": database_url, "ARTIFACT_DIR": "/data/artifacts"},
        volumes=[
            "omnigent-data:/data",
            f"{repo_root}/tools/register_omnigent_agent.py:/opt/moonmind/register_omnigent_agent.py:ro",
            f"{repo_root}/services/omnigent/agents/opencode-native-ui:/opt/moonmind/agents/opencode-native-ui:ro",
        ],
    )
    services["omnigent"].update(
        image=server_image,
        environment={
            "DATABASE_URL": database_url,
            "ARTIFACT_DIR": "/data/artifacts",
            "HOST": "0.0.0.0",
            "PORT": "8000",
            # The stock upstream host dials its tunnel without the MoonMind
            # control credential, so strict header auth refuses it before
            # registration (exit 78). Serve the deployment's default
            # single-user "local" owner instead; the internal-only network
            # keeps that unauthenticated listener unreachable from outside.
            "OMNIGENT_AUTH_ENABLED": "0",
        },
        # Internal-only Docker networks do not publish host ports. Both the
        # readiness probe and actual recovery driver use this same network.
        ports=[],
        volumes=["omnigent-data:/data"],
    )
    # Reuse the candidate's baked policy and canonical proxy entrypoint. The
    # fixture has only an internal network, so even allowlisted external
    # destinations have no route; package-registry access is also disabled.
    services["sandbox-egress-proxy"].update(
        image=moonmind_image,
        environment={"MOONMIND_PACKAGE_REGISTRY_EGRESS_ENABLED": "false"},
        networks={"test": {"aliases": ["omnigent-egress-proxy"]}},
        ports=[],
        volumes=[],
    )
    return {
        "services": services,
        "networks": {"test": {"internal": True}},
        "volumes": {"postgres-data": {}, "omnigent-data": {}},
    }


def readiness_command(*, image, network, token):
    """Probe the built service from the exact driver's isolated network."""
    return [
        "docker",
        "run",
        "--rm",
        "--read-only",
        "--cap-drop",
        "ALL",
        "--security-opt",
        "no-new-privileges",
        "--network",
        network,
        "--env",
        f"OMNIGENT_API_TOKEN={token}",
        "--entrypoint",
        "python",
        image,
        "-c",
        READINESS_SCRIPT,
    ]


def test_command(
    *,
    image,
    network,
    name,
    work,
    dependencies,
    source_commit,
    host_image_ref,
    token,
    project,
):
    command = [
        "docker",
        "run",
        "--rm",
        "--name",
        name,
        "--user",
        "0:0",
        "--network",
        network,
        "--workdir",
        "/app",
        "--entrypoint",
        "python",
        "--mount",
        "type=bind,src=/var/run/docker.sock,dst=/var/run/docker.sock",
        "--mount",
        f"type=bind,src={work},dst={work}",
        "--mount",
        f"type=bind,src={work / 'driver'},dst=/test-driver,readonly",
        "--mount",
        f"type=bind,src={dependencies},dst=/opt/test-dependencies,readonly",
    ]
    environment = {
        "PYTHONPATH": "/app",
        "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1",
        "OMNIGENT_ENABLED": "1",
        "OMNIGENT_SERVER_URL": "http://omnigent:8000",
        "OMNIGENT_API_TOKEN": token,
        "OMNIGENT_HOST_RUNNER_TOKEN": token,
        "MOONMIND_OMNIGENT_HOST_SERVER_URL": "http://omnigent:8000",
        "MOONMIND_OMNIGENT_EXPECTED_HOST_OWNER": "local",
        "MOONMIND_OMNIGENT_CONCURRENCY_NETWORK": network,
        "MOONMIND_OMNIGENT_CONCURRENCY_HOST_IMAGE": host_image_ref,
        "MOONMIND_OMNIGENT_CONCURRENCY_EVIDENCE_DIR": str(work),
        "MOONMIND_CREDENTIAL_RECOVERY_SOURCE_COMMIT": source_commit,
        # Match the repository-wide test fixture without importing host source.
        "WORKFLOW_TEST_MODE": "1",
        "AUTH_PROVIDER": "disabled",
        "MOONMIND_ALLOW_LOCAL_ENCRYPTION_KEY_GENERATION": "1",
        "COMPOSE_PROJECT_NAME": project,
    }
    for key, value in environment.items():
        command.extend(["--env", f"{key}={value}"])
    command.extend(
        [
            image,
            "-c",
            (
                "import sys; sys.path.append('/opt/test-dependencies'); "
                "import pytest; raise SystemExit(pytest.main(sys.argv[1:]))"
            ),
            "-p",
            "pytest_asyncio.plugin",
            "-c",
            "/dev/null",
            "--confcutdir=/test-driver",
            f"/test-driver/{TEST_FILE}::{TEST_NAME}",
            "-q",
            "--tb=short",
            f"--basetemp={work / 'workspace'}",
            f"--junitxml={work / JUNIT}",
        ]
    )
    return command


def _cleanup(compose, network, driver_name):
    _command(["docker", "rm", "-f", driver_name], check=False)
    # Restrict orphan cleanup to the unique internal test network. These can
    # remain if pytest was interrupted before its fenced host cleanup ran.
    ids = _command(
        ["docker", "ps", "-aq", "--filter", f"network={network}"], check=False
    ).stdout.split()
    volumes = set()
    for container in ids:
        result = _command(["docker", "inspect", container], check=False)
        if result.returncode == 0:
            for mount in json.loads(result.stdout)[0].get("Mounts", []):
                if mount.get("Type") == "volume":
                    volumes.add(mount["Name"])
        _command(["docker", "rm", "-f", container], check=False)
    _command([*compose, "down", "--volumes", "--remove-orphans"], check=False)
    for volume in sorted(volumes):
        _command(["docker", "volume", "rm", volume], check=False)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--moonmind-image", required=True)
    parser.add_argument("--server-image", required=True)
    parser.add_argument("--host-image", required=True)
    parser.add_argument("--pr-head", required=True)
    parser.add_argument("--base-commit", required=True)
    parser.add_argument("--dependencies", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args(argv)
    root = Path(__file__).resolve().parents[2]
    output = args.output_dir.resolve()
    output.mkdir(parents=True, exist_ok=True)
    # Clear before even Docker/source prerequisites are checked.
    for name in (RECEIPT, JUNIT, "credential-recovery-result.json"):
        (output / name).unlink(missing_ok=True)
    work = Path(tempfile.mkdtemp(prefix="moonmind-test-recovery-"))
    project = "moonmind-test-recovery-" + uuid.uuid4().hex[:12]
    network, driver_name = project + "_test", project + "-driver"
    token = "moonmind-test-" + uuid.uuid4().hex
    if os.environ.get("GITHUB_ACTIONS") == "true":
        print(f"::add-mask::{token}", flush=True)
    compose = [
        "docker",
        "compose",
        "--project-name",
        project,
        "--env-file",
        "/dev/null",
        "-f",
        str(work / "compose.json"),
    ]
    identity = {}
    app = None
    row = None
    phase = "setup"
    try:
        _require(shutil.which("docker") is not None, "Docker is unavailable")
        _command(["docker", "info"])
        source = _command(["git", "-C", str(root), "rev-parse", "HEAD"]).stdout.strip()
        _require(
            source == args.pr_head,
            "checkout differs from the explicitly selected candidate head",
        )
        pin = _command(
            ["git", "-C", str(root), "rev-parse", "HEAD:omnigent"]
        ).stdout.strip()
        actual_pin = _command(
            ["git", "-C", str(root / "omnigent"), "rev-parse", "HEAD"]
        ).stdout.strip()
        _require(
            actual_pin == pin, "upstream checkout differs from the candidate gitlink"
        )
        app = _image(args.moonmind_image)
        _require(
            app["Config"].get("Labels", {}).get("org.opencontainers.image.revision")
            == source,
            "candidate image was not built from the checked-out source",
        )
        images = {}
        for role, ref in (("server", args.server_image), ("host", args.host_image)):
            _require(
                re.fullmatch(r"127\.0\.0\.1:[0-9]+/[^\s@]+@sha256:[0-9a-f]{64}", ref),
                f"{role} must use the job-local immutable registry artifact",
            )
            image = _image(ref)
            _require(
                ref in image.get("RepoDigests", []),
                f"{role} registry digest is not present locally",
            )
            _require(
                image["Config"]
                .get("Labels", {})
                .get("org.opencontainers.image.revision")
                == pin,
                f"{role} was not built from the pinned upstream source",
            )
            if role == "host":
                _require(
                    image["Config"].get("Labels", {}).get("moonmind.source.revision")
                    == source,
                    "host layer was not built from the candidate MoonMind source",
                )
            images[role] = {"ref": ref, "id": image["Id"]}
        identity = {
            "sourceCommit": source,
            "prHeadCommit": args.pr_head,
            "baseCommit": args.base_commit,
            "omnigentSourceCommit": pin,
            "moonmindImageId": app["Id"],
            "images": images,
        }
        (output / "credential-recovery-artifacts.json").write_text(
            json.dumps(identity, indent=2) + "\n"
        )
        document = compose_document(
            yaml.safe_load((root / "docker-compose.yaml").read_text()),
            root,
            args.server_image,
            moonmind_image=app["Id"],
        )
        (work / "compose.json").write_text(json.dumps(document, indent=2) + "\n")
        (work / "driver").mkdir(exist_ok=True)
        shutil.copyfile(
            root / "tests/integration/omnigent" / TEST_FILE,
            work / "driver" / TEST_FILE,
        )
        _command([*compose, "up", "-d", "omnigent", "sandbox-egress-proxy"])
        for service, role, expected_image in (
            ("omnigent", "server", images["server"]["id"]),
            ("sandbox-egress-proxy", "proxy", app["Id"]),
        ):
            container = _command([*compose, "ps", "-q", service]).stdout.strip()
            actual = json.loads(_command(["docker", "inspect", container]).stdout)[0]
            _require(
                actual["Image"] == expected_image,
                f"running {role} differs from the built artifact",
            )
            identity[f"{role}ContainerId"] = actual["Id"]
        _command(readiness_command(image=app["Id"], network=network, token=token))
        command = test_command(
            image=app["Id"],
            network=network,
            name=driver_name,
            work=work,
            dependencies=args.dependencies.resolve(),
            source_commit=source,
            host_image_ref=args.host_image,
            token=token,
            project=project,
        )
        phase = "test"
        row = run_test(
            command,
            work,
            source_commit=source,
            host_image_ref=args.host_image,
            host_image_id=images["host"]["id"],
        )
        (output / "credential-recovery-result.json").write_text(
            json.dumps({**identity, "status": "passed", "recovery": row}, indent=2)
            + "\n"
        )
        return 0
    except (RecoveryError, OSError, ValueError, subprocess.SubprocessError) as exc:
        detail = str(exc).replace(token, "[test-owner]")
        (output / "credential-recovery-result.json").write_text(
            json.dumps(
                {
                    **identity,
                    "status": "unavailable" if phase == "setup" else "failed",
                    "detail": detail,
                },
                indent=2,
            )
            + "\n"
        )
        print(f"credential-free recovery {phase} failed: {detail}")
        if os.environ.get("GITHUB_ACTIONS") == "true":
            # The check-run annotation keeps the cause readable where the
            # Actions log and uploaded evidence are not.
            print(
                f"::error title=Credential-free recovery {phase} failed::"
                + _annotation(detail),
                flush=True,
            )
        return 1
    finally:
        if shutil.which("docker"):
            try:
                logs = _command(
                    [*compose, "logs", "--no-color", "--tail", "200"], check=False
                )
                if row is None:
                    print((logs.stdout + logs.stderr).replace(token, "[test-owner]"))
                _cleanup(compose, network, driver_name)
                if app is not None:
                    # The driver needs root only for this job's Docker socket.
                    # Return fixture/report ownership to the job user before
                    # redaction and removal; never leave root-owned fixture
                    # directories in the uploaded artifact tree.
                    _command(
                        [
                            "docker",
                            "run",
                            "--rm",
                            "--network",
                            "none",
                            "--user",
                            "0:0",
                            "--mount",
                            f"type=bind,src={work},dst=/evidence",
                            "--entrypoint",
                            "chown",
                            app["Id"],
                            "-R",
                            f"{os.getuid()}:{os.getgid()}",
                            "/evidence",
                        ]
                    )
            except (OSError, subprocess.SubprocessError, RecoveryError) as exc:
                print(f"test-only recovery cleanup unavailable: {type(exc).__name__}")
        # Publish only a validated allowlisted receipt. A rejected receipt may
        # contain arbitrary diagnostic fields and must never enter artifacts.
        if row is not None:
            (output / RECEIPT).write_text(json.dumps(row, indent=2) + "\n")
        junit = work / JUNIT
        if junit.is_file():
            try:
                original = ET.parse(junit).getroot()
                summary = ET.Element("testsuites")
                suite = ET.SubElement(
                    summary, "testsuite", name="credential-free-recovery"
                )
                for case in original.findall(".//testcase"):
                    projected = ET.SubElement(
                        suite, "testcase", name=case.get("name", "unknown")
                    )
                    for kind in ("skipped", "error", "failure"):
                        if case.find(kind) is not None:
                            ET.SubElement(
                                projected, kind, message="See the owning Actions log"
                            )
                ET.ElementTree(summary).write(output / JUNIT, encoding="unicode")
            except (OSError, ET.ParseError):
                # The projected summary is auxiliary evidence; an unreadable
                # report is omitted rather than failing cleanup of the run.
                pass
        shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
