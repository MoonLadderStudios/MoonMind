#!/usr/bin/env python3
"""Exercise real scanners, the admitted runner and SBOM generation on native CI."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

from tools.ci.repository_security_image import ARCHITECTURES, ROOT, ImageError, command

FIXTURES = ROOT / "services/repository-security/fixtures"
MARKER = "MOONMIND_TEST_SECRET_0123456789ABCDEF"


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
        f"type=bind,src={FIXTURES},dst=/opt/moonmind-scanner-rules,readonly",
    ]
    for host, target in mounts:
        argv.extend(("--mount", f"type=bind,src={host},dst={target},readonly"))
    argv.extend(("--entrypoint", entrypoint, image, *args))
    return [str(value) for value in argv]


def invoke(argv, evidence: Path, *, exits=(0,)):
    result = subprocess.run(
        argv,
        check=False,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        timeout=300,
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


def owned_directory(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    if os.getuid() == 0:
        os.chown(path, 1000, 1000)
    elif os.getuid() != 1000:
        raise ImageError(
            "run the host qualification harness as root or UID 1000; scanner containers remain UID 1000"
        )


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
import errno, json, os, shutil
from pathlib import Path
assert os.getuid() == 1000
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
print(Path('/usr/local/share/repository-security/inventory.json').read_text())
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
                "eval(user_input)\n" if finding else "print('clean')\n"
            )
            (source / "secrets.txt").write_text(MARKER if finding else "clean control")
            before = snapshot(source, exclude_output=True)
            commands = {
                "opengrep": [
                    "scan",
                    "--config",
                    "/opt/moonmind-scanner-rules/opengrep.yml",
                    "--disable-version-check",
                    "--json",
                    "--output",
                    "/scan-output/report.json",
                    "/workspace/app.py",
                ],
                "gitleaks": [
                    "dir",
                    "/workspace",
                    "--config",
                    "/opt/moonmind-scanner-rules/gitleaks.toml",
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
                "eval(user_input)\n" if finding else "print('clean')\n"
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
                    "opengrep": "opengrep.yml",
                }.get(tool)
                spec = {"scanner": tool, "outputDirectory": "artifacts/scan"}
                mounts = ()
                if rule_name:
                    spec["rules"] = [
                        {
                            "id": "qualification",
                            "path": "/opt/moonmind-scanner-rules/" + rule_name,
                            "sha256": hashlib.sha256(
                                (FIXTURES / rule_name).read_bytes()
                            ).hexdigest(),
                            "license": "MIT",
                        }
                    ]
                else:
                    spec.update(
                        trivyScanners=["vuln"],
                        databaseCacheRef="qualification-db",
                        databaseDigest=database_digest,
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
                if metadata["limitations"] or any(
                    v != "succeeded" for v in metadata["states"].values()
                ):
                    raise ImageError(
                        f"{tool}: admitted runner reported incomplete execution"
                    )
                # For Gitleaks the clean worktree still has the earlier finding in history.
                report_name = "worktree.json" if tool == "gitleaks" else "scan.json"
                count = count_findings(
                    tool, json.loads((output / report_name).read_text())
                )
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
        record.update(
            qualification="passed",
            sbomSha256=hashlib.sha256(sbom.read_bytes()).hexdigest(),
            rules=snapshot(FIXTURES),
        )
        record_path.write_text(json.dumps(record, indent=2) + "\n")
    return record


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image", required=True)
    parser.add_argument("--evidence", required=True, type=Path)
    args = parser.parse_args()
    qualify(args.image, args.evidence.resolve())
