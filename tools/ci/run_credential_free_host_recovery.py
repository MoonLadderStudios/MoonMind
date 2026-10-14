#!/usr/bin/env python3
"""Own the provider-free recovery rows in the existing exact-artifact CI job.

This is not the concurrency/provider qualification runner. Only test drivers
are mounted into the already built application image; its production imports
remain image-owned. The upstream server, host and their observed identities
are supplied by the job's pinned-source builds, never inferred from latest.

Two rows run on one isolated stack: the same-session host recovery row, then
the interrupted-step journey (MoonLadderStudios/MoonMind#4627), which replaces
a real host mid-step under a real Temporal server and requires the step's
successor to restore the saved workspace on a new host.
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
JOURNEY_TEST_NAME = "test_exact_omnigent_host_update_restarts_only_the_interrupted_step"
JOURNEY_TEST_FILE = "tests/integration/reliability/test_interrupted_step_replacement_journey.py"
JOURNEY_RECEIPT = "interrupted-step-exact-docker.json"
JOURNEY_JUNIT = "interrupted-step-junit.xml"
JOURNEY_STEPS = ("prepare", "analyze", "implement")
#: The canonical Compose PostgreSQL service's test-only superuser.
DATABASE_URL = "postgresql://postgres:moonmind-test-db@postgres:5432/moonmind"
TEMPORAL_ADDRESS = "temporal:7233"
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
TEMPORAL_READINESS_SCRIPT = """import asyncio
import time

from temporalio.api.workflowservice.v1 import DescribeNamespaceRequest
from temporalio.client import Client


async def ready():
    # Stay inside the driver's per-command budget.
    deadline = time.monotonic() + 200
    while time.monotonic() < deadline:
        try:
            client = await Client.connect('temporal:7233')
            await client.workflow_service.describe_namespace(
                DescribeNamespaceRequest(namespace='default')
            )
            return
        except Exception:
            await asyncio.sleep(2)
    raise SystemExit('Temporal did not serve the default namespace')


asyncio.run(ready())
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


def observe_harness_identity(image_id, build_digest):
    """Read expected receipt identity from the selected immutable artifact."""
    from moonmind.omnigent.harness_platform.catalog_service import _normalize_harness

    _require(
        isinstance(build_digest, str)
        and re.fullmatch(r"sha256:[0-9a-f]{64}", build_digest) is not None,
        "selected host artifact has no valid upstream build identity",
    )
    observed = json.loads(
        _command(
            [
                "docker",
                "run",
                "--rm",
                "--network",
                "none",
                "--entrypoint",
                "/opt/venv/bin/python",
                image_id,
                "-c",
                (
                    "import json; from importlib.metadata import version; "
                    "from omnigent.harness_plugins import valid_harnesses, harness_capabilities; "
                    "cap = harness_capabilities().get('opencode-native'); "
                    "row = {'id': 'opencode-native', 'capabilities': cap.as_dict()} "
                    "if 'opencode-native' in valid_harnesses() and cap is not None else None; "
                    "print(json.dumps({'version': version('omnigent'), 'harness': row}))"
                ),
            ]
        ).stdout
    )
    raw = observed.get("harness")
    _require(
        isinstance(raw, dict) and raw.get("id") == "opencode-native",
        "expected native recovery harness is unavailable",
    )
    harness = _normalize_harness(
        raw,
        omnigent_version=observed["version"],
        omnigent_build_digest=build_digest,
    )
    return {
        "omnigentVersion": harness.implementation.version,
        "omnigentBuildDigest": build_digest,
        "harnessImplementationRef": harness.implementation.implementation_ref(),
    }


def validate_evidence(
    root, *, source_commit, host_image_ref, host_image_id, expected_harness_identity
):
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
        "omnigentVersion",
        "omnigentBuildDigest",
        "harnessImplementationRef",
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
    for field, pattern in (
        ("omnigentVersion", r"[0-9]+\.[0-9]+\.[0-9]+[A-Za-z0-9.+-]*"),
        ("omnigentBuildDigest", r"sha256:[0-9a-f]{64}"),
        (
            "harnessImplementationRef",
            r"omnigent-harness-implementation:sha256:[0-9a-f]{64}",
        ),
    ):
        value = row.get(field)
        _require(
            isinstance(value, str)
            and re.fullmatch(pattern, value) is not None
            and value == expected_harness_identity.get(field),
            f"recovery {field} does not match the selected host artifact/harness",
        )
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


def _sha256(value):
    return isinstance(value, str) and re.fullmatch(r"sha256:[0-9a-f]{64}", value)


def validate_journey_evidence(root, *, source_commit, host_image_ref, host_image_id):
    """Accept only an executed journey whose step moved to a new real host."""
    try:
        row = json.loads((root / JOURNEY_RECEIPT).read_text())
        cases = ET.parse(root / JOURNEY_JUNIT).getroot().findall(".//testcase")
    except (OSError, ValueError, ET.ParseError) as exc:
        raise RecoveryError(f"interrupted-step evidence unavailable: {exc}") from exc
    _require(
        len(cases) == 1
        and cases[0].get("name") == JOURNEY_TEST_NAME
        and all(
            cases[0].find(kind) is None for kind in ("skipped", "failure", "error")
        ),
        "the required interrupted-step journey was not successfully executed",
    )
    _require(
        isinstance(row, dict)
        and set(row)
        <= {
            "schemaVersion",
            "sourceCommit",
            "hostImageRef",
            "workflowId",
            "logicalStepId",
            "steps",
            "predecessor",
            "successor",
            "hostLoss",
            "savedWorkspaceDigest",
            "restoredWorkspaceDigest",
            "completedWorkspaceDigest",
        },
        "unrecognized interrupted-step receipt fields",
    )
    _require(row.get("schemaVersion") == 1, "unsupported interrupted-step receipt")
    _require(row.get("sourceCommit") == source_commit, "stale journey sourceCommit")
    _require(row.get("hostImageRef") == host_image_ref, "journey host ref mismatch")
    _require(
        isinstance(row.get("workflowId"), str) and bool(row["workflowId"]),
        "missing journey workflow identity",
    )
    _require(row.get("logicalStepId") == "implement", "wrong interrupted step")
    _require(
        row.get("steps")
        == [{"logicalStepId": step, "status": "completed"} for step in JOURNEY_STEPS],
        "the original workflow did not complete every step",
    )
    attempts = (row.get("predecessor"), row.get("successor"))
    for ordinal, attempt in enumerate(attempts, start=1):
        _require(isinstance(attempt, dict), "missing observed step attempt")
        _require(
            set(attempt)
            <= {
                "executionOrdinal",
                "containerName",
                "hostId",
                "hostImageId",
                "sessionId",
            },
            "unrecognized step attempt fields",
        )
        _require(attempt.get("executionOrdinal") == ordinal, "wrong step attempt")
        for field in ("containerName", "hostId", "sessionId"):
            _require(
                isinstance(attempt.get(field), str) and bool(attempt[field]),
                f"missing observed {field}",
            )
        _require(
            attempt.get("hostImageId") == host_image_id, "actual host image mismatch"
        )
    for field in ("containerName", "hostId", "sessionId"):
        _require(
            attempts[0][field] != attempts[1][field],
            f"the step successor did not get a new {field}",
        )
    _require(
        row.get("hostLoss")
        == {"runnerOnline": False, "hostOnline": False, "hostResumable": False},
        "host loss was not observed through the Omnigent session",
    )
    saved = row.get("savedWorkspaceDigest")
    _require(_sha256(saved), "missing saved workspace digest")
    _require(
        row.get("restoredWorkspaceDigest") == saved,
        "the successor did not restore the saved workspace",
    )
    _require(
        _sha256(row.get("completedWorkspaceDigest"))
        and row["completedWorkspaceDigest"] != saved,
        "the successor did not complete the step",
    )
    return row


def _junit_failure(root, junit=JUNIT):
    """Return the failing row's own message and traceback tail, if recorded."""
    try:
        cases = ET.parse(root / junit).getroot().findall(".//testcase")
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


def run_journey(command, root, **identity):
    for name in (JOURNEY_RECEIPT, JOURNEY_JUNIT):
        (root / name).unlink(missing_ok=True)
    result = subprocess.run(command, check=False, timeout=1500)
    if result.returncode:
        failure = _junit_failure(root, JOURNEY_JUNIT)
        raise RecoveryError(
            f"interrupted-step journey failed (exit {result.returncode})"
            + (f": {failure}" if failure else "")
        )
    return validate_journey_evidence(root, **identity)


def _annotation(text):
    """Escape a GitHub Actions workflow-command message."""
    return text.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")


def compose_document(source, repo_root, server_image, *, moonmind_image):
    """Reuse canonical init/registration commands, replacing deployment wiring."""
    names = (
        "postgres", "omnigent-db-init", "omnigent-agent-init", "omnigent",
        "sandbox-egress-proxy", "temporal",
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
    # The canonical Temporal server on the same isolated PostgreSQL. Its
    # auto-setup creates the default namespace the journey's workers use.
    services["temporal"].update(
        environment={
            "DB": "postgres12",
            "DB_PORT": "5432",
            "POSTGRES_SEEDS": "postgres",
            "POSTGRES_USER": "postgres",
            "POSTGRES_PWD": "moonmind-test-db",
            "DBNAME": "temporal",
            "VISIBILITY_DBNAME": "temporal_visibility",
            "NUM_HISTORY_SHARDS": "1",
            "DYNAMIC_CONFIG_FILE_PATH": (
                "/etc/temporal/dynamicconfig/development-sql.yaml"
            ),
            "SKIP_ADD_CUSTOM_SEARCH_ATTRIBUTES": "true",
            "BIND_ON_IP": "0.0.0.0",
        },
        ports=[],
        volumes=[
            f"{repo_root}/services/temporal/dynamicconfig:/etc/temporal/dynamicconfig:ro"
        ],
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
            "OMNIGENT_AUTH_ENABLED": "1",
            "OMNIGENT_AUTH_PROVIDER": "header",
            "OMNIGENT_AUTH_HEADER": "Authorization",
            # Upstream trims configured prefixes. End on the namespace
            # delimiter so the authenticated identity has no leading space.
            "OMNIGENT_AUTH_HEADER_STRIP_PREFIX": "Bearer moonmind-test-",
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


def temporal_readiness_command(*, image, network):
    """Wait until the isolated Temporal server serves its default namespace."""
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
        "--entrypoint",
        "python",
        image,
        "-c",
        TEMPORAL_READINESS_SCRIPT,
    ]


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
    target=f"/test-driver/{TEST_FILE}::{TEST_NAME}",
    junit=JUNIT,
    extra_environment=None,
    pytest_options=("--confcutdir=/test-driver",),
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
        "MOONMIND_OMNIGENT_EXPECTED_HOST_OWNER": token.removeprefix("moonmind-test-"),
        "MOONMIND_OMNIGENT_CONCURRENCY_NETWORK": network,
        "MOONMIND_OMNIGENT_CONCURRENCY_HOST_IMAGE": host_image_ref,
        "MOONMIND_OMNIGENT_CONCURRENCY_EVIDENCE_DIR": str(work),
        "MOONMIND_CREDENTIAL_RECOVERY_SOURCE_COMMIT": source_commit,
        # Match the repository-wide test fixture without importing host source.
        "WORKFLOW_TEST_MODE": "1",
        "AUTH_PROVIDER": "disabled",
        "MOONMIND_ALLOW_LOCAL_ENCRYPTION_KEY_GENERATION": "1",
        "COMPOSE_PROJECT_NAME": project,
        **(extra_environment or {}),
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
            *pytest_options,
            target,
            "-q",
            "--tb=short",
            f"--basetemp={work / 'workspace'}",
            f"--junitxml={work / junit}",
        ]
    )
    return command


def journey_command(**kwargs):
    """Run the interrupted-step journey from the copied repository tests."""
    return test_command(
        **kwargs,
        target=f"/test-driver/{JOURNEY_TEST_FILE}::{JOURNEY_TEST_NAME}",
        junit=JOURNEY_JUNIT,
        extra_environment={
            # Import the journey's repository test helpers; production
            # imports still resolve from the image's /app first.
            "PYTHONPATH": "/app:/test-driver",
            "MOONMIND_TEST_TEMPORAL_ADDRESS": TEMPORAL_ADDRESS,
            "MOONMIND_TEST_POSTGRES_URL": DATABASE_URL,
        },
        # The journey needs only builtin fixtures; repository conftest files
        # may import tooling that is not part of the deployable image.
        pytest_options=("--rootdir=/test-driver", "--noconftest"),
    )


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
    for name in (
        RECEIPT,
        JUNIT,
        JOURNEY_RECEIPT,
        JOURNEY_JUNIT,
        "credential-recovery-result.json",
    ):
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
    journey = None
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
                expected_harness_identity = observe_harness_identity(
                    image["Id"],
                    image["Config"].get("Labels", {}).get("moonmind.omnigent.build_digest"),
                )
            images[role] = {"ref": ref, "id": image["Id"]}
        identity = {
            "sourceCommit": source,
            "prHeadCommit": args.pr_head,
            "baseCommit": args.base_commit,
            "omnigentSourceCommit": pin,
            "moonmindImageId": app["Id"],
            "images": images,
            "expectedHarnessIdentity": expected_harness_identity,
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
        # The journey imports repository test helpers, so it runs from a copy
        # of the candidate's tests; the image's production source stays /app.
        shutil.copytree(
            root / "tests",
            work / "driver" / "tests",
            ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
        )
        _command([*compose, "up", "-d", "omnigent", "sandbox-egress-proxy", "temporal"])
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
        _command(temporal_readiness_command(image=app["Id"], network=network))
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
            expected_harness_identity=expected_harness_identity,
        )
        journey = run_journey(
            journey_command(
                image=app["Id"],
                network=network,
                name=driver_name,
                work=work,
                dependencies=args.dependencies.resolve(),
                source_commit=source,
                host_image_ref=args.host_image,
                token=token,
                project=project,
            ),
            work,
            source_commit=source,
            host_image_ref=args.host_image,
            host_image_id=images["host"]["id"],
        )
        (output / "credential-recovery-result.json").write_text(
            json.dumps(
                {
                    **identity,
                    "status": "passed",
                    "recovery": row,
                    "interruptedStep": journey,
                },
                indent=2,
            )
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
                if journey is None:
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
        for name, receipt in ((RECEIPT, row), (JOURNEY_RECEIPT, journey)):
            if receipt is not None:
                (output / name).write_text(json.dumps(receipt, indent=2) + "\n")
        for name in (JUNIT, JOURNEY_JUNIT):
            _project_junit(work / name, output / name)
        shutil.rmtree(work, ignore_errors=True)


def _project_junit(source, target):
    """Publish only case names and outcomes; the Actions log owns details."""
    if not source.is_file():
        return
    try:
        original = ET.parse(source).getroot()
        summary = ET.Element("testsuites")
        suite = ET.SubElement(summary, "testsuite", name="credential-free-recovery")
        for case in original.findall(".//testcase"):
            projected = ET.SubElement(
                suite, "testcase", name=case.get("name", "unknown")
            )
            for kind in ("skipped", "error", "failure"):
                if case.find(kind) is not None:
                    ET.SubElement(projected, kind, message="See the owning Actions log")
        ET.ElementTree(summary).write(target, encoding="unicode")
    except (OSError, ET.ParseError):
        # The projected summary is auxiliary evidence; an unreadable
        # report is omitted rather than failing cleanup of the run.
        pass


if __name__ == "__main__":
    raise SystemExit(main())
