#!/usr/bin/env python3
"""Exercise real scanners, the admitted runner and SBOM generation on native CI."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import tarfile
import tempfile
import time
from pathlib import Path
from xml.etree import ElementTree

from tools.ci.repository_security_image import ARCHITECTURES, ROOT, ImageError, command

FIXTURES = ROOT / "services/repository-security/fixtures"
MARKER = "MOONMIND_TEST_SECRET_0123456789ABCDEF"
#: Debian 12 root filesystems that differ only in APT settings, evaluated with
#: the shipped ComplianceAsCode ANSSI minimal profile. Both rules read files
#: alone, so an offline copy decides them.
ENDPOINT_FIXTURES = FIXTURES / "endpoint"
ENDPOINT_PROFILE = "xccdf_org.ssgproject.content_profile_anssi_np_nt28_minimal"
ENDPOINT_RULES = (
    "xccdf_org.ssgproject.content_rule_apt_conf_disallow_unauthenticated",
    "xccdf_org.ssgproject.content_rule_apt_sources_list_official",
)
_XCCDF = "{http://checklists.nist.gov/xccdf/1.2}"


def snapshot(root: Path, *, exclude_output=False):
    return [
        [
            path.relative_to(root).as_posix(),
            hashlib.sha256(path.read_bytes()).hexdigest(),
        ]
        for path in sorted(root.rglob("*"))
        if path.is_file()
        and not (exclude_output and "artifacts" in path.relative_to(root).parts)
    ]


def container(
    image,
    source,
    output,
    *args,
    scratch,
    entrypoint="python",
    mounts=(),
    network="none",
    extra=(),
):
    argv = [
        "docker",
        "run",
        "--rm",
        "--network",
        network,
        "--read-only",
        "--cap-drop=ALL",
        "--security-opt=no-new-privileges",
        "--pids-limit=256",
        "--memory=2g",
        "--cpus=2",
        "--mount",
        f"type=bind,src={source},dst=/workspace,readonly",
        "--mount",
        f"type=bind,src={output},dst=/scan-output",
        # Like a scanner job's owned scratch, /tmp is disk, not memory: the
        # runner copies the admitted Trivy feed snapshot (over 1.5 GB) there,
        # and Opengrep's one-file release executes its unpacked runtime from it.
        "--mount",
        f"type=bind,src={scratch},dst=/tmp",
        "--mount",
        f"type=bind,src={FIXTURES},dst=/opt/moonmind-scanner-rules/qualification,readonly",
    ]
    for host, target in mounts:
        argv.extend(("--mount", f"type=bind,src={host},dst={target},readonly"))
    argv.extend(extra)
    argv.extend(("--entrypoint", entrypoint, image, *args))
    return [str(value) for value in argv]


def readiness_container(image: str, probe: list[str]) -> list[str]:
    """Use the same offline, source-free envelope as runtime readiness."""
    return [
        "docker", "run", "--rm", "--pull=never", "--network", "none", "--hostname", "localhost",
        "--read-only", "--cap-drop=ALL", "--security-opt=no-new-privileges",
        "--cpus", "1", "--memory", "1g", "--pids-limit", "128",
        "--tmpfs", "/tmp:rw,exec,nosuid,nodev,size=536870912",
        "--env", "HOME=/tmp", "--env", "TMPDIR=/tmp",
        "--entrypoint", "timeout", image,
        "--signal=TERM", "--kill-after=5s", "75s", *probe,
    ]


def qualify_readiness(image: str, evidence: Path, *, kind: str = "repository") -> None:
    """Run packaged helpers and upstream startup under the production limits."""
    evidence.mkdir(parents=True, exist_ok=True)
    if kind in {"web", "cloud"}:
        prowler_startup = (
            'p="$(command -v prowler)"; if [ -z "$p" ]; then '
            'for candidate in /home/*/.venv/bin/prowler; do '
            'if [ -x "$candidate" ]; then p="$candidate"; break; fi; '
            'done; fi; [ -n "$p" ] || exit 127; exec "$p" --version'
        )
        probe = (
            ["/zap/zap.sh", "-Xmx512m", "-dir", "/tmp/zap", "-version"]
            if kind == "web" else ["sh", "-c", prowler_startup]
        )
        output = invoke(
            readiness_container(image, probe), evidence / f"readiness-{kind}.json",
            timeout=90,
        )
        if len(output.encode()) > 16384 or not re.search(r"\b[0-9]+\.[0-9]+", output):
            raise ImageError(f"{kind}: readiness version probe did not identify a release")
        return
    if kind != "repository":
        raise ImageError("unknown readiness qualification kind")
    selections = {
        "gitleaks": {"repositoryScan": {"scanner": "gitleaks", "outputDirectory": "report"}},
        "opengrep": {"repositoryScan": {"scanner": "opengrep", "outputDirectory": "report"}},
        "trivy-misconfig": {"repositoryScan": {
            "scanner": "trivy", "trivyScanners": ["misconfig"], "outputDirectory": "report",
        }},
        "sbom-generate": {"imageAssessment": {"operation": "sbom-generate", "target": "source"}},
    }
    for label, selection in selections.items():
        raw = json.dumps(selection, sort_keys=True, separators=(",", ":"))
        output = invoke(
            readiness_container(image, ["python", "-I", "-m", "moonmind.security.scanner_readiness", raw]),
            evidence / f"readiness-{label}.json", timeout=90,
        )
        if len(output.encode()) > 16384:
            raise ImageError(f"{label}: readiness helper output exceeds its runtime limit")
        try:
            result = json.loads(output)
        except ValueError as exc:
            raise ImageError(f"{label}: readiness helper did not return JSON") from exc
        if (
            not isinstance(result, dict)
            or result.get("schemaVersion") != "scanner-readiness/v1"
            or result.get("selectionDigest") != hashlib.sha256(raw.encode()).hexdigest()
            or result.get("status") != "ready"
            or not isinstance(result.get("checks"), list)
            or not result["checks"]
            or any(not isinstance(check, dict) or check.get("status") not in ("ready", "unverified") for check in result["checks"])
            or not any(check.get("code") == "scanner_available" for check in result["checks"])
        ):
            raise ImageError(f"{label}: packaged readiness helper is not ready; see retained evidence")


def invoke(argv, evidence: Path, *, exits=(0,), timeout=300):
    result = subprocess.run(
        argv,
        check=False,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        timeout=timeout,
    )
    evidence.write_text(
        json.dumps(
            {"command": argv, "exitCode": result.returncode, "output": result.stdout},
            indent=2,
        )
        + "\n"
    )
    if result.returncode not in exits:
        # These commands use synthetic qualification inputs, never operator source.
        diagnostic = result.stdout[-4096:].strip()
        raise ImageError(
            f"scanner qualification failed ({result.returncode}); see {evidence}\n"
            f"{diagnostic}"
        )
    return result.stdout


def count_findings(scanner: str, report):
    if scanner == "gitleaks":
        if not isinstance(report, list):
            raise ImageError("invalid Gitleaks report")
        return len(report)
    if scanner == "opengrep":
        if not isinstance(report.get("results"), list) or report.get("errors"):
            raise ImageError("invalid or incomplete Opengrep report")
        return len(report["results"])
    if report.get("SchemaVersion") != 2 or not isinstance(report.get("Results"), list):
        raise ImageError("invalid Trivy report")
    return sum(
        len(row.get("Secrets", [])) + len(row.get("Vulnerabilities", []))
        for row in report["Results"]
    )


def assert_default_findings(report: dict, vulnerable: bool) -> None:
    expected = (
        {
            ("moonmind-python-dynamic-eval", "app.py"),
            ("moonmind-python-shell-command", "app.py"),
            ("moonmind-javascript-dynamic-eval", "app.js"),
            ("moonmind-javascript-dynamic-eval", "app.ts"),
        }
        if vulnerable
        else set()
    )
    actual = {
        (row["check_id"].split(".")[-1], Path(row["path"]).name)
        for row in report["results"]
    }
    if actual != expected:
        raise ImageError(f"packaged default rules: expected {expected}, got {actual}")


def owned_directory(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    if os.getuid() == 0:
        os.chown(path, 1000, 1000)
    elif os.getuid() != 1000:
        raise ImageError(
            "run the host qualification harness as root or UID 1000; scanner containers remain UID 1000"
        )


def rootfs_archive(tree: Path, destination: Path) -> None:
    """A reproducible root filesystem tar of one fixture tree, owned by root."""
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tarfile.open(destination, "w") as bundle:
        for path in sorted(tree.rglob("*")):
            info = bundle.gettarinfo(path, arcname=path.relative_to(tree).as_posix())
            info.uid = info.gid = 0
            info.uname = info.gname = "root"
            info.mtime = 0
            if path.is_file():
                with path.open("rb") as stream:
                    bundle.addfile(info, stream)
            else:
                bundle.addfile(info)
    destination.chmod(0o644)


def endpoint_results(path: Path) -> dict[str, str]:
    """Rule results of a retained XCCDF ``TestResult``, read with the stdlib."""
    root = ElementTree.parse(path).getroot()
    return {
        row.get("idref"): (row.findtext(f"{_XCCDF}result") or "").strip()
        for row in root.iter(f"{_XCCDF}rule-result")
    }


def run_endpoint(image, source, output, scratch, evidence, label, assessment, inputs):
    """One admitted endpoint runner invocation under the scanner job limits."""
    result = output / "endpoint"
    if result.exists():
        shutil.rmtree(result)
    invoke(
        container(
            image,
            source,
            output,
            "-I",
            "-m",
            "moonmind.security.endpoint_assessment_runner",
            json.dumps(assessment),
            "/scan-output/endpoint",
            scratch=scratch,
            mounts=((inputs, "/inputs"),),
        ),
        evidence / f"endpoint-{label}.json",
        exits=(0, 2),
    )
    execution = json.loads((result / "execution.json").read_text())
    for path in result.iterdir():
        shutil.copyfile(path, evidence / f"endpoint-{label}-{path.name}")
    if execution.get("state") != "succeeded" or not (result / "results.xml").is_file():
        raise ImageError(
            f"endpoint {label}: runner did not retain results; "
            f"limitations={execution.get('limitations')}"
        )
    return endpoint_results(result / "results.xml"), execution


def qualify_endpoint(image, source, output, scratch, evidence) -> dict:
    """Real OpenSCAP separates the hardened and weakened fixtures offline.

    The weakened copy fails exactly the rules the hardened copy passes, file
    ownership rules are reported as needing the device, and importing the
    retained result yields the same rule results.
    """
    found = {}
    for kind in ("hardened", "weakened"):
        inputs = scratch.parent / f"endpoint-{kind}"
        rootfs_archive(ENDPOINT_FIXTURES / kind, inputs / "endpoint/rootfs.tar")
        found[kind] = run_endpoint(
            image,
            source,
            output,
            scratch,
            evidence,
            kind,
            {
                "target": "rootfs",
                "asset": "qualification-endpoint",
                "inputArtifactRef": "art_qualification",
                "datastream": "ssg-debian12-ds.xml",
                "profile": ENDPOINT_PROFILE,
                "ruleIds": list(ENDPOINT_RULES),
            },
            inputs,
        )
    for kind, expected in (("hardened", "pass"), ("weakened", "fail")):
        actual = {rule: found[kind][0].get(rule) for rule in ENDPOINT_RULES}
        if set(actual.values()) != {expected}:
            raise ImageError(
                f"endpoint {kind} fixture: expected {expected} for every rule, got {actual}"
            )
    offline = found["hardened"][1].get("offlineRules") or {}
    if offline.get("xccdf_org.ssgproject.content_rule_file_owner_etc_shadow") != (
        "metadata"
    ):
        raise ImageError("endpoint: ownership rules were not reported as device-only")
    inputs = scratch.parent / "endpoint-import"
    (inputs / "endpoint").mkdir(parents=True)
    shutil.copyfile(
        evidence / "endpoint-hardened-results.xml", inputs / "endpoint/results.xml"
    )
    (inputs / "endpoint/results.xml").chmod(0o644)
    imported, _ = run_endpoint(
        image,
        source,
        output,
        scratch,
        evidence,
        "import",
        {
            "target": "results",
            "asset": "qualification-endpoint",
            "inputArtifactRef": "art_qualification",
            "profile": ENDPOINT_PROFILE,
        },
        inputs,
    )
    if imported != found["hardened"][0]:
        raise ImageError("endpoint: imported results differ from the evaluated result")
    return {
        "datastream": "ssg-debian12-ds.xml",
        "profile": ENDPOINT_PROFILE,
        "contentRelease": found["hardened"][1].get("contentRelease"),
        "scannerVersion": found["hardened"][1].get("version"),
        "rules": {
            kind: {rule: found[kind][0][rule] for rule in ENDPOINT_RULES}
            for kind in found
        },
    }


#: The live fixture host: Debian 12 with sshd and Debian's OpenSCAP, the
#: endpoint fixture's APT settings, and an ``inspector`` login. The base
#: image's own DEB822 sources are removed once packages are installed, since
#: ``apt_sources_list_official`` also reads them and the offline copy has
#: none. The ``slow`` variant's evaluation never finishes, for the timeout and
#: stop journeys.
ENDPOINT_HOST = "endpoint.qualification.test"
ENDPOINT_HOST_BASE = "debian:bookworm-slim"
_FIXTURE_HOST = """\
FROM {base}
RUN apt-get -o Acquire::Retries=3 update \\
    && apt-get install --no-install-recommends -y openssh-server \\
    && {{ apt-get install --no-install-recommends -y openscap-scanner \\
         || apt-get install --no-install-recommends -y libopenscap8; }} \\
    && rm -rf /var/lib/apt/lists/* /etc/apt/sources.list.d/* \\
    && useradd --create-home --shell /bin/sh inspector \\
    && install -d -m 0700 -o inspector -g inspector /home/inspector/.ssh \\
    && mkdir -p /run/sshd \\
    && rm -f /etc/ssh/ssh_host_* \\
    && ssh-keygen -q -t ed25519 -N '' -f /etc/ssh/ssh_host_ed25519_key
COPY authorized_keys /home/inspector/.ssh/authorized_keys
COPY etc/ /etc/
COPY bin/ /usr/local/bin/
RUN chown inspector:inspector /home/inspector/.ssh/authorized_keys \\
    && chmod 0600 /home/inspector/.ssh/authorized_keys
CMD ["/usr/sbin/sshd", "-D", "-e", "-o", "LogLevel=VERBOSE", \\
     "-o", "PasswordAuthentication=no", "-o", "KbdInteractiveAuthentication=no"]
"""
#: The slow host's first evaluation stalls; later ones run normally, so a
#: stopped run can be restarted on the same host.
_SLOW_OSCAP = (
    "#!/bin/sh\n"
    'if [ "$1" = xccdf ] && mkdir /tmp/.moonmind-stalled 2>/dev/null; then sleep 900; fi\n'
    'exec /usr/bin/oscap "$@"\n'
)


def ssh_keypair(image: str, directory: Path, name: str, evidence: Path) -> Path:
    """An ed25519 key made with the scanner image's own OpenSSH client."""
    owned_directory(directory)
    invoke(
        [
            "docker",
            "run",
            "--rm",
            "--network",
            "none",
            "--user",
            "1000:1000",
            "--mount",
            f"type=bind,src={directory},dst=/keys",
            "--entrypoint",
            "ssh-keygen",
            image,
            "-q",
            "-t",
            "ed25519",
            "-N",
            "",
            "-C",
            "qualification",
            "-f",
            f"/keys/{name}",
        ],
        evidence / f"endpoint-host-key-{name}.json",
    )
    return directory / name


def fixture_host_image(variant: str, public_key: Path, scratch: Path, evidence) -> str:
    context = scratch / f"fixture-host-{variant}"
    if context.exists():
        shutil.rmtree(context)
    fixture = ENDPOINT_FIXTURES / ("weakened" if variant == "weakened" else "hardened")
    shutil.copytree(fixture / "etc/apt", context / "etc/apt")
    (context / "bin").mkdir()
    if variant == "slow":
        (context / "bin/oscap").write_text(_SLOW_OSCAP)
        (context / "bin/oscap").chmod(0o755)
    shutil.copyfile(public_key.with_suffix(".pub"), context / "authorized_keys")
    (context / "Dockerfile").write_text(_FIXTURE_HOST.format(base=ENDPOINT_HOST_BASE))
    tag = f"moonmind-qualification-endpoint-host:{variant}-{os.getpid()}"
    invoke(
        ["docker", "build", "--pull", "-t", tag, str(context)],
        evidence / f"endpoint-host-build-{variant}.json",
        timeout=900,
    )
    return tag


class EndpointHostLab:
    """Two internal networks joined only by the native TCP gateway.

    The job shares one network with the gateway, and the fixture host the
    other, so the job reaches the host only through admitted CONNECT tunnels.
    """

    def __init__(self, image: str, scratch: Path, evidence: Path):
        self.image, self.scratch, self.evidence = image, scratch, evidence
        self.prefix = f"moonmind-qual-endpoint-{os.getpid()}"
        octet = 16 + os.getpid() % 200
        self.jobs = (f"{self.prefix}-jobs", f"10.231.{octet}")
        self.targets = (f"{self.prefix}-targets", f"10.232.{octet}")
        self.containers: list[str] = []

    def __enter__(self):
        for name, net in (self.jobs, self.targets):
            command(
                "docker",
                "network",
                "create",
                "--internal",
                "--subnet",
                f"{net}.0/24",
                name,
            )
        return self

    def __exit__(self, *_):
        for name in reversed(self.containers):
            subprocess.run(
                ["docker", "rm", "-f", name], check=False, capture_output=True
            )
        for name, _ in (self.jobs, self.targets):
            subprocess.run(
                ["docker", "network", "rm", name], check=False, capture_output=True
            )

    def host(self, label: str, tag: str) -> str:
        name = f"{self.prefix}-host-{label}"
        self.containers.append(name)
        command(
            "docker",
            "run",
            "-d",
            "--name",
            name,
            "--network",
            self.targets[0],
            "--ip",
            f"{self.targets[1]}.3",
            tag,
        )
        return name

    def gateway(self, label: str, port: int, limits: dict, deadline: int) -> Path:
        """One lease's gateway admitting only the fixture host and port.

        Like a worker lease, it closes at ``deadline``: the native duration
        from its start, which the runner also receives.
        """
        state = self.scratch.parent / f"endpoint-gateway-{label}"
        owned_directory(state)
        config = self.scratch.parent / f"endpoint-gateway-{label}.json"
        config.write_text(
            json.dumps(
                {
                    "leaseId": f"qualification-{label}",
                    "deadlineUnix": deadline,
                    "listenHost": f"{self.jobs[1]}.2",
                    "listenPort": 3128,
                    "clientIp": f"{self.jobs[1]}.3",
                    "endpoints": [
                        {
                            "authority": f"{ENDPOINT_HOST}:{port}",
                            "address": f"{self.targets[1]}.3",
                            "port": port,
                        }
                    ],
                    "maxConnections": limits["maxConnections"],
                    "maxConcurrentConnections": limits["maxConcurrentConnections"],
                    "maxConnectionsPerSecond": limits["maxConnectionsPerSecond"],
                    "maxBytes": limits["maxBytes"],
                    "handshakeSeconds": 10,
                    "connectSeconds": 5,
                    "idleSeconds": 30,
                }
            )
        )
        config.chmod(0o644)
        name = f"{self.prefix}-gateway-{label}"
        self.containers.append(name)
        command(
            "docker",
            "run",
            "-d",
            "--name",
            name,
            "--network",
            self.jobs[0],
            "--ip",
            f"{self.jobs[1]}.2",
            "--read-only",
            "--cap-drop=ALL",
            "--security-opt=no-new-privileges",
            "--mount",
            f"type=bind,src={config},dst=/config.json,readonly",
            "--mount",
            f"type=bind,src={state},dst=/state",
            "--entrypoint",
            "python",
            self.image,
            "-I",
            "/opt/moonmind-scanner-python/moonmind/security/native_tcp_gateway.py",
            "--config",
            "/config.json",
            "--state-directory",
            "/state",
        )
        command(
            "docker",
            "network",
            "connect",
            "--ip",
            f"{self.targets[1]}.2",
            self.targets[0],
            name,
        )
        for _ in range(100):
            snapshot = state / "snapshot.json"
            if (
                snapshot.exists()
                and json.loads(snapshot.read_text()).get("status") == "running"
            ):
                return state
            time.sleep(0.1)
        raise ImageError(f"endpoint host {label}: native gateway did not start")

    def retire(self, *names: str) -> None:
        """Remove finished fixtures so the next journey can reuse their addresses."""
        for name in names:
            subprocess.run(
                ["docker", "rm", "-f", name], check=False, capture_output=True
            )
            if name in self.containers:
                self.containers.remove(name)

    def remote_directories(self, host: str) -> list[str]:
        listing = subprocess.run(
            [
                "docker",
                "exec",
                host,
                "sh",
                "-c",
                "ls -d /tmp/tmp.* 2>/dev/null || true",
            ],
            check=False,
            capture_output=True,
            text=True,
        )
        return listing.stdout.split()


def run_endpoint_host(
    lab, source, output, label, assessment, key: Path, *, deadline: int, detach=False
):
    """The admitted runner in the scanner image, reaching the host via the gateway."""
    result = output / f"endpoint-host-{label}"
    if result.exists():
        shutil.rmtree(result)
    argv = container(
        lab.image,
        source,
        output,
        "-I",
        "-m",
        "moonmind.security.endpoint_assessment_runner",
        json.dumps(assessment),
        f"/scan-output/endpoint-host-{label}",
        scratch=lab.scratch,
        network=lab.jobs[0],
        mounts=((key, "/run/moonmind-credentials/host"),),
        extra=(
            "--ip",
            f"{lab.jobs[1]}.3",
            "--env",
            f"MOONMIND_EGRESS_PROXY=http://{lab.jobs[1]}.2:3128",
            "--env",
            f"MOONMIND_EGRESS_DEADLINE={deadline}",
            *(("--detach", "--name", f"{lab.prefix}-job-{label}") if detach else ()),
        ),
    )
    if detach:
        lab.containers.append(f"{lab.prefix}-job-{label}")
        command(*argv)
        return result
    invoke(
        argv, lab.evidence / f"endpoint-host-{label}.json", exits=(0, 2), timeout=600
    )
    return result


def endpoint_host_execution(result: Path, evidence: Path, label: str) -> dict:
    for path in result.iterdir():
        shutil.copyfile(path, evidence / f"endpoint-host-{label}-{path.name}")
    return json.loads((result / "execution.json").read_text())


def qualify_endpoint_host(image, source, output, scratch, evidence, offline) -> dict:
    """Controlled live-host journeys through the real gateway, SSH and OpenSCAP.

    Success on hardened and weakened hosts must match the offline results;
    an unknown host key stops before authentication; a wrong key, a closed
    port, a timeout and a stop each end with their bounded code, and every
    run that reached the host removes its temporary directory.
    """
    keys = scratch.parent / "endpoint-host-keys"
    client = ssh_keypair(image, keys, "client", evidence)
    stranger = ssh_keypair(image, keys, "stranger", evidence)
    tags = {
        variant: fixture_host_image(variant, client, scratch.parent, evidence)
        for variant in ("hardened", "weakened", "slow")
    }
    # Each fixture image has its own generated host key; pin the one in use.
    host_keys = {
        variant: " ".join(
            command(
                "docker",
                "run",
                "--rm",
                "--network",
                "none",
                "--entrypoint",
                "cat",
                tag,
                "/etc/ssh/ssh_host_ed25519_key.pub",
            ).split()[:2]
        )
        for variant, tag in tags.items()
    }
    limits = {
        "maxConnections": 8,
        "maxBytes": 512 * 1024 * 1024,
        "maxConnectionsPerSecond": 4,
        "maxConcurrentConnections": 1,
        "maxDurationSeconds": 600,
    }

    def assessment(port, key, timeout):
        return {
            "target": "host",
            "asset": "qualification-endpoint",
            "datastream": "ssg-debian12-ds.xml",
            "profile": ENDPOINT_PROFILE,
            "ruleIds": list(ENDPOINT_RULES),
            "assessmentRef": "qualification",
            "credentialRef": "db://qualification-host",
            "egressPolicyRef": "native-qualification",
            "timeoutSeconds": timeout,
            "host": {
                "address": ENDPOINT_HOST,
                "port": port,
                "account": "inspector",
                "hostKey": key,
                "limits": {**limits, "maxDurationSeconds": min(timeout, 600)},
            },
        }

    record, journeys = {}, {}
    with EndpointHostLab(image, scratch, evidence) as lab:

        def journey(
            label,
            variant,
            *,
            port=22,
            key=None,
            credential=client,
            timeout=600,
            host=None,
            keep_host=False,
        ):
            host = host or lab.host(label, tags[variant])
            # The gateway closes when the admitted native duration ends, so a
            # timed-out evaluation must leave its cleanup inside that window.
            duration = min(timeout, limits["maxDurationSeconds"])
            deadline = int(time.time()) + duration
            state = lab.gateway(
                label, port, {**limits, "maxDurationSeconds": duration}, deadline
            )
            pinned = key or host_keys[variant]
            result = run_endpoint_host(
                lab,
                source,
                output,
                label,
                assessment(port, pinned, timeout),
                credential,
                deadline=deadline,
            )
            execution = endpoint_host_execution(result, evidence, label)
            gateway = json.loads((state / "snapshot.json").read_text())
            logs = subprocess.run(
                ["docker", "logs", host], check=False, capture_output=True, text=True
            )
            (evidence / f"endpoint-host-{label}-sshd.log").write_text(
                logs.stderr + logs.stdout
            )
            journeys[label] = {
                "state": execution["state"],
                "limitations": execution["limitations"],
                "gateway": {
                    name: gateway.get(name)
                    for name in (
                        "attempts",
                        "bytesReserved",
                        "dialOutcomes",
                        "endpoints",
                    )
                },
                "remoteDirectoriesLeft": lab.remote_directories(host),
            }
            lab.retire(f"{lab.prefix}-gateway-{label}")
            if not keep_host:
                lab.retire(host)
            return result, execution, journeys[label], logs.stderr + logs.stdout

        for kind in ("hardened", "weakened"):
            result, execution, seen, _ = journey(kind, kind)
            if (
                execution["state"] != "succeeded"
                or not (result / "results.xml").is_file()
            ):
                raise ImageError(f"endpoint host {kind}: {execution['limitations']}")
            found = endpoint_results(result / "results.xml")
            record[kind] = {rule: found.get(rule) for rule in ENDPOINT_RULES}
            if record[kind] != offline[kind]:
                raise ImageError(
                    f"endpoint host {kind}: live {record[kind]} differs from offline {offline[kind]}"
                )
            if seen["remoteDirectoriesLeft"] or seen["gateway"]["attempts"] != 5:
                raise ImageError(f"endpoint host {kind}: unexpected transport {seen}")

        expectations = (
            (
                "unknown-identity",
                "hardened",
                {"key": " ".join(stranger.with_suffix(".pub").read_text().split()[:2])},
                "host_identity_unverified",
            ),
            (
                "wrong-credential",
                "hardened",
                {"credential": stranger},
                "host_authentication_failed",
            ),
            ("unreachable", "hardened", {"port": 2222}, "host_unreachable"),
        )
        for label, variant, changes, code in expectations:
            _, execution, seen, logs = journey(label, variant, **changes)
            if code not in execution["limitations"] or execution["state"] != "failed":
                raise ImageError(f"endpoint host {label}: {execution}")
            if label == "unknown-identity" and (
                "publickey" in logs or "Accepted" in logs
            ):
                # Key exchange failed first: no credential was offered.
                raise ImageError(
                    "endpoint host: authentication followed an unknown key"
                )

        _, execution, seen, _ = journey("timeout", "slow", timeout=60)
        if execution["state"] != "timed_out" or seen["remoteDirectoriesLeft"]:
            raise ImageError(f"endpoint host timeout: {execution} {seen}")

        host = lab.host("stop", tags["slow"])
        deadline = int(time.time()) + limits["maxDurationSeconds"]
        lab.gateway("stop", 22, limits, deadline)
        result = run_endpoint_host(
            lab,
            source,
            output,
            "stop",
            assessment(22, host_keys["slow"], 600),
            client,
            deadline=deadline,
            detach=True,
        )
        for _ in range(600):
            if lab.remote_directories(host):
                break
            time.sleep(0.1)
        command("docker", "stop", "--time", "20", f"{lab.prefix}-job-stop")
        execution = endpoint_host_execution(result, evidence, "stop")
        journeys["stop"] = {
            "state": execution["state"],
            "limitations": execution["limitations"],
            "remoteDirectoriesLeft": lab.remote_directories(host),
        }
        lab.retire(f"{lab.prefix}-job-stop", f"{lab.prefix}-gateway-stop")
        if (
            execution["state"] != "canceled"
            or journeys["stop"]["remoteDirectoriesLeft"]
        ):
            raise ImageError(f"endpoint host stop: {journeys['stop']}")

        # The stopped run restarts as a new run under a new lease on the same
        # host; the stopped run's evidence stays retained beside it.
        result, execution, seen, _ = journey("restart", "slow", host=host)
        found = (
            endpoint_results(result / "results.xml")
            if (result / "results.xml").is_file()
            else {}
        )
        if (
            execution["state"] != "succeeded"
            or seen["remoteDirectoriesLeft"]
            or {rule: found.get(rule) for rule in ENDPOINT_RULES} != offline["hardened"]
        ):
            raise ImageError(f"endpoint host restart: {execution}")
    return {
        "host": ENDPOINT_HOST,
        "base": ENDPOINT_HOST_BASE,
        "hostKeyType": host_keys["hardened"].split()[0],
        "rules": record,
        "journeys": journeys,
    }


def qualify(image: str, evidence: Path) -> dict:
    evidence.mkdir(parents=True, exist_ok=True)
    inspection = json.loads(command("docker", "image", "inspect", image))[0]
    (evidence / "image.json").write_text(json.dumps(inspection, indent=2) + "\n")
    if (
        inspection["Architecture"] not in ARCHITECTURES
        or inspection["Os"] != "linux"
        or inspection["Config"]["User"] != "1000:1000"
    ):
        raise ImageError(
            "qualification requires the selected native Linux image and UID"
        )
    record = {
        "architecture": inspection["Architecture"],
        "imageId": inspection["Id"],
        "qualification": "failed",
        "sourceRevision": os.environ.get("GITHUB_SHA", "unavailable"),
    }
    record_path = evidence / "qualification.json"
    record_path.write_text(json.dumps(record, indent=2) + "\n")
    qualify_readiness(image, evidence)
    with tempfile.TemporaryDirectory(prefix="repository-security-") as directory:
        root = Path(directory)
        root.chmod(0o755)
        source = root / "source"
        source.mkdir()
        output = source / "artifacts/scan"
        owned_directory(output)
        scratch = root / "scratch"
        owned_directory(scratch)
        argv = container(
            image,
            source,
            output,
            "-I",
            "-c",
            """
import errno, hashlib, json, os, shutil
from pathlib import Path
assert os.getuid() == 1000
for location in ('/opt/moonmind-scanner-db', '/opt/moonmind-scanner-feeds'):
    status = Path(location).stat()
    assert (status.st_uid, status.st_gid) == (1000, 1000)
    assert status.st_mode & 0o700 == 0o700
assert not any(k.startswith(('OPENAI_', 'ANTHROPIC_', 'AWS_', 'AZURE_', 'GOOGLE_')) or k in ('DOCKER_HOST', 'GOOGLE_APPLICATION_CREDENTIALS') for k in os.environ)
assert not Path('/var/run/docker.sock').exists() and not Path('/run/docker.sock').exists()
assert not any(shutil.which(tool) for tool in ('aws', 'gcloud', 'az', 'docker', 'kubectl', 'nmap', 'nuclei'))
assert not any(path.name == 'trivy.db' for base in ('/opt', '/usr/local/share', '/home/scanner') for path in Path(base).rglob('trivy.db'))
for location in ('/workspace/forbidden', '/home/scanner/forbidden', '/usr/local/forbidden'):
    try: Path(location).write_text('must not write')
    except OSError as error: assert error.errno in (errno.EROFS, errno.EACCES)
    else: raise AssertionError(location)
Path('/tmp/allowed').write_text('private temporary data')
Path('/scan-output/allowed').write_text('declared output')
inventory = json.loads(Path('/usr/local/share/repository-security/inventory.json').read_text())
manifest = json.loads(Path('/opt/moonmind-scanner-rules/default/manifest.json').read_text())
assert inventory['defaultRules'] == manifest and manifest['rules']
for rule in manifest['rules']:
    assert hashlib.sha256(Path(rule['path']).read_bytes()).hexdigest() == rule['sha256']
assert Path('/usr/local/share/repository-security/notices/default-rules-LICENSE').is_file()
assert Path('/usr/local/share/repository-security/notices/default-rules-NOTICE').is_file()
print(json.dumps(inventory))
""",
            scratch=scratch,
        )
        inventory = json.loads(invoke(argv, evidence / "isolation.json"))
        record["inventory"] = inventory
        (evidence / "inventory.json").write_text(json.dumps(inventory, indent=2) + "\n")
        (output / "allowed").unlink()
        for tool, args in (
            ("opengrep", ["--version"]),
            ("gitleaks", ["version"]),
            ("trivy", ["--version"]),
            ("git", ["--version"]),
        ):
            text = invoke(
                container(
                    image, source, output, *args, scratch=scratch, entrypoint=tool
                ),
                evidence / f"{tool}-version.json",
            )
            if (
                tool != "git"
                and inventory["tools"][tool]["version"].removeprefix("v") not in text
            ):
                raise ImageError(f"{tool}: executable version differs from inventory")
        # Standalone rule-driven CLI jobs. Trivy uses its admitted offline
        # database below, including a real analyzed package in the clean case.
        for finding in (True, False):
            kind = "finding" if finding else "clean"
            (source / "app.py").write_text(
                "eval(user_input)\nimport subprocess\nsubprocess.run(user_input, shell=True)\n"
                if finding
                else "print('clean')\nimport subprocess\nsubprocess.run(['echo', 'clean'], shell=False)\n"
            )
            for name in ("app.js", "app.ts"):
                (source / name).write_text(
                    "eval(userInput);\n" if finding else "JSON.parse(userInput);\n"
                )
            (source / "secrets.txt").write_text(MARKER if finding else "clean control")
            before = snapshot(source, exclude_output=True)
            commands = {
                "opengrep": [
                    "scan",
                    "--config",
                    "/opt/moonmind-scanner-rules/default/python.yml",
                    "--config",
                    "/opt/moonmind-scanner-rules/default/javascript.yml",
                    "--disable-version-check",
                    "--json",
                    "--output",
                    "/scan-output/report.json",
                    "/workspace",
                ],
                "gitleaks": [
                    "dir",
                    "/workspace",
                    "--config",
                    "/opt/moonmind-scanner-rules/qualification/gitleaks.toml",
                    "--gitleaks-ignore-path",
                    "/dev/null",
                    "--redact=100",
                    "--no-banner",
                    "--report-format=json",
                    "--report-path",
                    "/scan-output/report.json",
                ],
            }
            for tool, args in commands.items():
                report = output / "report.json"
                report.unlink(missing_ok=True)
                invoke(
                    container(
                        image, source, output, *args, scratch=scratch, entrypoint=tool
                    ),
                    evidence / f"{tool}-{kind}.json",
                    exits=(0, 1) if tool == "gitleaks" and finding else (0,),
                )
                native = json.loads(report.read_text())
                count = count_findings(tool, native)
                if tool == "opengrep":
                    assert_default_findings(native, finding)
                if (count > 0) != finding:
                    raise ImageError(
                        f"{tool}: unexpected {kind} result: {count} findings"
                    )
                shutil.copyfile(report, evidence / f"{tool}-{kind}-report.json")
                report.unlink()
            if snapshot(source, exclude_output=True) != before:
                raise ImageError("scanner changed the read-only source")
        # Publish a separately identified feed snapshot, never into an image
        # layer, through the same entry point as a production feed refresh.
        cache = root / "database"
        owned_directory(cache)
        invoke(
            container(
                image,
                source,
                cache,
                "-I",
                "-m",
                "moonmind.security.scanner_feeds",
                json.dumps(
                    {
                        "feedCacheRef": "qualification-db",
                        "egressPolicyRef": "qualification",
                    }
                ),
                "/scan-output/feeds",
                "/scan-output/refresh",
                scratch=scratch,
                network="bridge",
            ),
            evidence / "database-acquisition.json",
        )
        shutil.copyfile(cache / "refresh/refresh.json", evidence / "database.json")
        database_digest = json.loads((cache / "feeds/current.json").read_text())[
            "snapshotId"
        ]
        # Exercise the exact admitted Python invocation, not only binary flags.
        for finding in (True, False):
            kind = "finding" if finding else "clean"
            (source / "app.py").write_text(
                "eval(user_input)\nimport subprocess\nsubprocess.run(user_input, shell=True)\n"
                if finding
                else "print('clean')\nimport subprocess\nsubprocess.run(['echo', 'clean'], shell=False)\n"
            )
            for name in ("app.js", "app.ts"):
                (source / name).write_text(
                    "eval(userInput);\n" if finding else "JSON.parse(userInput);\n"
                )
            (source / "secrets.txt").write_text(MARKER if finding else "clean control")
            (source / "requirements.txt").write_text(
                "requests==2.19.1\n"
                if finding
                else "moonmind-qualification-no-advisories==1.0.0\n"
            )
            for tool in ("gitleaks", "opengrep", "trivy"):
                for path in output.iterdir():
                    path.unlink()
                # Gitleaks history needs real metadata in the immutable input.
                if tool == "gitleaks":
                    subprocess.run(
                        ["git", "-c", "safe.directory=*", "init", str(source)],
                        check=True,
                        capture_output=True,
                    )
                    subprocess.run(
                        [
                            "git",
                            "-c",
                            "safe.directory=*",
                            "-C",
                            str(source),
                            "add",
                            "app.py",
                            "secrets.txt",
                            "requirements.txt",
                        ],
                        check=True,
                    )
                    subprocess.run(
                        [
                            "git",
                            "-c",
                            "safe.directory=*",
                            "-c",
                            "user.name=Qualification",
                            "-c",
                            "user.email=qualification@example.invalid",
                            "-C",
                            str(source),
                            "commit",
                            "-m",
                            "Synthetic fixture",
                        ],
                        check=True,
                        capture_output=True,
                    )
                rule_name = {
                    "gitleaks": "gitleaks.toml",
                }.get(tool)
                spec = {"scanner": tool, "outputDirectory": "artifacts/scan"}
                mounts = ()
                if rule_name:
                    spec["rules"] = [
                        {
                            "id": "qualification",
                            "path": "/opt/moonmind-scanner-rules/qualification/"
                            + rule_name,
                            "sha256": hashlib.sha256(
                                (FIXTURES / rule_name).read_bytes()
                            ).hexdigest(),
                            "license": "MIT",
                        }
                    ]
                elif tool == "trivy":
                    spec.update(
                        trivyScanners=["vuln"],
                        databaseCacheRef="qualification-db",
                    )
                    mounts = ((cache / "feeds", "/opt/moonmind-scanner-db"),)
                before = snapshot(source, exclude_output=True)
                try:
                    invoke(
                        container(
                            image,
                            source,
                            output,
                            "-I",
                            "-m",
                            "moonmind.security.repository_scanner_runner",
                            json.dumps(spec),
                            scratch=scratch,
                            mounts=mounts,
                        ),
                        evidence / f"runner-{tool}-{kind}.json",
                    )
                except ImageError as error:
                    # The runner's sanitized metadata names what was incomplete.
                    execution = output / "execution.json"
                    if not execution.exists():
                        raise
                    shutil.copyfile(
                        execution, evidence / f"runner-{tool}-{kind}-execution.json"
                    )
                    metadata = json.loads(execution.read_text())
                    raise ImageError(
                        f"{error}\nlimitations={metadata.get('limitations')} "
                        f"states={metadata.get('states')} exits={metadata.get('exits')}"
                    ) from error
                metadata = json.loads((output / "execution.json").read_text())
                if tool == "trivy" and metadata["databaseDigest"] != database_digest:
                    raise ImageError(
                        "default feed selection did not retain the prepared snapshot"
                    )
                if tool == "opengrep" and not metadata["effectiveRules"]:
                    raise ImageError("default rule provenance was not retained")
                if metadata["limitations"] or any(
                    v != "succeeded" for v in metadata["states"].values()
                ):
                    raise ImageError(
                        f"{tool}: admitted runner reported incomplete execution"
                    )
                # For Gitleaks the clean worktree still has the earlier finding in history.
                report_name = "worktree.json" if tool == "gitleaks" else "scan.json"
                native = json.loads((output / report_name).read_text())
                count = count_findings(tool, native)
                if tool == "opengrep":
                    assert_default_findings(native, finding)
                if (count > 0) != finding:
                    raise ImageError(f"{tool}: admitted runner {kind} assertion failed")
                if snapshot(source, exclude_output=True) != before:
                    raise ImageError("admitted runner changed the source")
                for path in output.iterdir():
                    shutil.copyfile(
                        path, evidence / f"runner-{tool}-{kind}-{path.name}"
                    )
        # Scan a read-only image archive with the packaged Trivy: no Docker socket.
        archive = root / "image.tar"
        command("docker", "save", "--output", archive, image)
        # Docker writes the archive owner-only; the scanner runs as UID 1000.
        archive.chmod(0o644)
        invoke(
            container(
                image,
                source,
                output,
                "image",
                "--input=/input/image.tar",
                "--format=spdx-json",
                "--output=/scan-output/sbom.spdx.json",
                "--skip-db-update",
                "--skip-java-db-update",
                "--skip-version-check",
                "--disable-telemetry",
                "--offline-scan",
                "--cache-dir=/tmp/cache",
                scratch=scratch,
                entrypoint="trivy",
                mounts=((archive, "/input/image.tar"),),
            ),
            evidence / "sbom-generation.json",
        )
        sbom = output / "sbom.spdx.json"
        data = json.loads(sbom.read_text())
        if not data.get("spdxVersion", "").startswith("SPDX-") or not data.get(
            "packages"
        ):
            raise ImageError("image SBOM is missing its SPDX package inventory")
        shutil.copyfile(sbom, evidence / "sbom.spdx.json")
        endpoint = qualify_endpoint(image, source, output, scratch, evidence)
        endpoint_host = qualify_endpoint_host(
            image, source, output, scratch, evidence, endpoint["rules"]
        )
        record.update(
            qualification="passed",
            endpoint=endpoint,
            endpointHost=endpoint_host,
            sbomSha256=hashlib.sha256(sbom.read_bytes()).hexdigest(),
            rules=inventory["defaultRules"],
            qualificationRules=snapshot(FIXTURES),
        )
        record_path.write_text(json.dumps(record, indent=2) + "\n")
    return record


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image", required=True)
    parser.add_argument("--evidence", required=True, type=Path)
    parser.add_argument("--readiness-only", action="store_true")
    parser.add_argument("--kind", choices=("repository", "web", "cloud"), default="repository")
    args = parser.parse_args()
    if args.readiness_only:
        qualify_readiness(args.image, args.evidence.resolve(), kind=args.kind)
    else:
        if args.kind != "repository":
            parser.error("upstream kinds require --readiness-only")
        qualify(args.image, args.evidence.resolve())
