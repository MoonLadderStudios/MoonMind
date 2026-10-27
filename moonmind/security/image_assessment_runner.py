"""One Trivy image, archive, SBOM or source-inventory operation per Container Job.

Registry images are read with ``--image-src remote`` and archives with
``--input``, so Trivy never consults a Docker, containerd or Podman daemon.
Raw reports stay in private scratch; only checked evidence reaches outputs.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import signal
import stat
import sys
import tarfile
import tempfile
from datetime import UTC, datetime
from pathlib import Path

from moonmind.schemas.repository_scanner_models import ImageAssessment
from moonmind.security.assessments import content_key
from moonmind.security.image_assessments import (
    DATABASE_TARGET,
    IMAGE_FILE,
    IMAGE_INPUT,
    SBOM_FILE,
    SBOM_INPUT,
    SOURCE_INPUT,
    TARGET_AUTH_TARGET,
    ImageExecution,
    Provenance,
    SbomError,
    inspect_sbom,
    subject_from_cyclonedx,
    subject_from_report,
    subject_limitations,
)
from moonmind.security.repository_scanner_runner import BoundedProcesses
from moonmind.security.repository_scanners import sanitize_native
from moonmind.security.scanner_feeds import (
    FeedSnapshotError,
    copy_snapshot,
    feed_evidence,
    load_snapshot,
)

#: Registry failures are classified from Trivy's private stderr; the text
#: itself is never retained.
_REGISTRY_FAILURES = (
    (
        "target_registry_auth_failed",
        re.compile(rb"unauthorized|authentication required", re.I),
    ),
    ("target_registry_access_denied", re.compile(rb"\bdenied\b|forbidden", re.I)),
    ("target_platform_unavailable", re.compile(rb"no child with platform", re.I)),
    (
        "target_not_found",
        re.compile(rb"manifest_unknown|name_unknown|not found", re.I),
    ),
)
_MAX_SOURCE_FILES = 200_000


class InputError(ValueError):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def image_commands(
    assessment: ImageAssessment, *, inputs: Path, raw: Path, cache: Path
) -> list[str]:
    """Only admitted flags: no daemon source, update, config file or ignore file."""
    common = [
        "--cache-dir",
        str(cache),
        "--config=",
        "--ignorefile=",
        "--skip-db-update",
        "--skip-java-db-update",
        "--offline-scan",
        "--skip-version-check",
        "--disable-telemetry",
        "--no-progress",
        "--timeout",
        f"{assessment.timeout_seconds}s",
        "--exit-code=0",
    ]
    if assessment.operation == "sbom-generate":
        report = ["--format", "cyclonedx", "--output", str(raw / "sbom.json")]
    else:
        report = [
            "--format",
            "json",
            "--list-all-pkgs",
            "--scanners",
            "vuln",
            "--output",
            str(raw / "report.json"),
        ]
    if assessment.target == "registry":
        platform = ["--platform", assessment.platform] if assessment.platform else []
        return [
            "trivy",
            "image",
            "--image-src",
            "remote",
            *platform,
            *report,
            *common,
            assessment.image_reference,
        ]
    if assessment.target == "archive":
        archive = str(inputs / IMAGE_INPUT / IMAGE_FILE)
        return ["trivy", "image", "--input", archive, *report, *common]
    if assessment.target == "sbom":
        return ["trivy", "sbom", *report, *common, str(inputs / SBOM_INPUT / SBOM_FILE)]
    return ["trivy", "filesystem", *report, *common, str(inputs / SOURCE_INPUT)]


def _regular(path: Path, limit: int, code: str) -> Path:
    try:
        info = path.lstat()
    except OSError as exc:
        raise InputError(code) from exc
    if not stat.S_ISREG(info.st_mode):
        raise InputError(code)
    if info.st_size > limit:
        raise InputError("input_bound_exceeded")
    return path


def _file_digest(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def _archive_identity(path: Path) -> dict:
    """Read a Docker-save or OCI image-layout tar's image config, unextracted.

    The archive's own config names its platform and image ID, so evidence
    records them even when Trivy's output format does not.
    """
    try:
        with tarfile.open(path, "r:*") as bundle:
            members = {}
            for count, member in enumerate(bundle):
                if count >= 100_000:
                    raise InputError("image_archive_malformed")
                members[member.name.removeprefix("./")] = member

            def blob(name: str, limit: int = 1 << 20) -> bytes:
                member = members.get(name)
                if member is None or not member.isreg() or member.size > limit:
                    raise InputError("image_archive_malformed")
                return bundle.extractfile(member).read()

            def digest_blob(digest) -> bytes:
                if not isinstance(digest, str) or not digest.startswith("sha256:"):
                    raise InputError("image_archive_malformed")
                return blob("blobs/sha256/" + digest.removeprefix("sha256:"))

            if "manifest.json" in members:
                images = json.loads(blob("manifest.json"))
                if not isinstance(images, list):
                    raise InputError("image_archive_malformed")
                if len(images) != 1 or not isinstance(images[0], dict):
                    return {}
                config = blob(images[0].get("Config") or "")
            elif {"index.json", "oci-layout"} <= set(members):
                index = json.loads(blob("index.json"))
                manifests = index.get("manifests") or []
                if len(manifests) != 1:
                    return {}
                manifest = json.loads(digest_blob(manifests[0].get("digest")))
                config = digest_blob((manifest.get("config") or {}).get("digest"))
            else:
                raise InputError("image_archive_malformed")
    except (tarfile.TarError, OSError, EOFError, ValueError, AttributeError) as exc:
        if isinstance(exc, InputError):
            raise
        raise InputError("image_archive_malformed") from exc
    try:
        document = json.loads(config)
    except ValueError:
        return {}
    if not isinstance(document, dict):
        return {}
    identity = {"image_id": "sha256:" + hashlib.sha256(config).hexdigest()}
    for key in ("os", "architecture", "variant"):
        if isinstance(document.get(key), str) and document[key]:
            identity[key] = document[key][:128]
    return identity


def _source_digest(root: Path, limit: int) -> str:
    records, used = [], 0
    for directory, dirs, files in os.walk(root, followlinks=False):
        dirs.sort()
        for name in sorted(files):
            path = Path(directory) / name
            info = path.lstat()
            if not stat.S_ISREG(info.st_mode):
                continue
            used += info.st_size
            if used > limit or len(records) >= _MAX_SOURCE_FILES:
                raise InputError("input_bound_exceeded")
            records.append([path.relative_to(root).as_posix(), _file_digest(path)])
    if not records:
        raise InputError("no_inputs_examined")
    return content_key(records)


def _credential_material(auth: Path) -> set[bytes]:
    """Values the target credential could leak as; never written anywhere."""
    config = json.loads((auth / "config.json").read_bytes())
    found = set()
    for entry in (config.get("auths") or {}).values():
        for key in ("auth", "password", "identitytoken", "registrytoken"):
            value = entry.get(key)
            if isinstance(value, str) and len(value) >= 4:
                found.add(value.encode())
        try:
            _, _, password = (
                base64.b64decode(entry.get("auth") or "").decode().partition(":")
            )
        except ValueError:
            password = ""
        if len(password) >= 4:
            found.add(password.encode())
    return found


def run_image_assessment(
    assessment: ImageAssessment,
    *,
    inputs: Path = Path("/inputs"),
    output: Path,
    database: Path = Path(DATABASE_TARGET),
    auth: Path = Path(TARGET_AUTH_TARGET),
) -> int:
    output.mkdir(parents=True, exist_ok=True)
    processes = BoundedProcesses(
        assessment.timeout_seconds,
        max(assessment.max_report_bytes, assessment.max_sbom_bytes),
    )
    previous = signal.signal(signal.SIGTERM, processes.cancel)
    execution = ImageExecution(
        operation=assessment.operation,
        target=assessment.target,
        version="unavailable",
        state="failed",
    )
    limitations: list[str] = []
    with tempfile.TemporaryDirectory(prefix="moonmind-image-") as scratch:
        root = Path(scratch)
        raw, cache, config = root / "raw", root / "cache", root / "docker"
        for directory in (raw, cache, config):
            directory.mkdir()
        # No inherited environment: no daemon address, proxy override or
        # ambient registry login. Only an admitted target credential is read.
        env = {
            "PATH": "/usr/local/bin:/usr/bin:/bin",
            "HOME": str(root),
            "DOCKER_CONFIG": str(config),
        }
        secrets: set[bytes] = set()
        input_digest, archive_identity = None, {}
        try:
            if assessment.target_credential_ref:
                try:
                    secrets = _credential_material(auth)
                except (OSError, ValueError, AttributeError) as exc:
                    execution.target_credential = "unavailable"
                    raise InputError("target_credential_unavailable") from exc
                env["DOCKER_CONFIG"] = str(auth)
                execution.target_credential = "delivered"
            if assessment.target == "archive":
                archive = _regular(
                    inputs / IMAGE_INPUT / IMAGE_FILE,
                    assessment.max_input_bytes,
                    "image_archive_unavailable",
                )
                archive_identity = _archive_identity(archive)
                input_digest = _file_digest(archive)
            elif assessment.target == "sbom":
                document = _regular(
                    inputs / SBOM_INPUT / SBOM_FILE,
                    assessment.max_sbom_bytes,
                    "sbom_unavailable",
                )
                body = document.read_bytes()
                input_digest = hashlib.sha256(body).hexdigest()
                try:
                    execution.sbom, found = inspect_sbom(
                        body, assessment.max_sbom_bytes
                    )
                except SbomError as exc:
                    raise InputError(exc.code) from exc
                limitations.extend(found)
                execution.provenance = Provenance(
                    declaredSubject=execution.sbom.declared_subject,
                    embeddedClaims=execution.sbom.embedded_claims,
                )
            elif assessment.target == "source":
                input_digest = _source_digest(
                    inputs / SOURCE_INPUT, assessment.max_input_bytes
                )
            if assessment.database_digest:
                snapshot = load_snapshot(
                    database, assessment.database_digest, verify=False
                )
                copy_snapshot(database, snapshot, cache)
                execution.database_digest = snapshot.snapshot_id
                execution.feed = feed_evidence(
                    snapshot,
                    misconfig=False,
                    max_age_hours=assessment.feed_max_age_hours,
                    now=datetime.now(UTC),
                )
                if execution.feed.stale_components:
                    limitations.append("feed_snapshot_stale")
            version = root / "version"
            processes.run(["trivy", "--version"], cwd=root, env=env, capture=version)
            if version.exists():
                match = re.search(
                    rb"\b[0-9]+\.[0-9]+\.[0-9]+\b", version.read_bytes()[:4096]
                )
                if match:
                    execution.version = match[0].decode()
            if execution.version == "unavailable":
                limitations.append("scanner_version_unavailable")
            errors = root / "stderr"
            execution.exit_code, execution.state = processes.run(
                image_commands(assessment, inputs=inputs, raw=raw, cache=cache),
                cwd=root,
                env=env,
                errors=errors,
            )
            if execution.state != "succeeded" or execution.exit_code != 0:
                limitations.append("scanner_exit_failure")
                diagnostic = errors.read_bytes()[:65536] if errors.exists() else b""
                if assessment.target == "registry":
                    limitations.extend(
                        next(
                            (
                                [code]
                                for code, pattern in _REGISTRY_FAILURES
                                if pattern.search(diagnostic)
                            ),
                            [],
                        )
                    )
            limitations.extend(
                _retain(
                    assessment,
                    execution,
                    raw,
                    output,
                    secrets,
                    input_digest,
                    archive_identity,
                )
            )
        except InputError as exc:
            limitations.extend([exc.code, "scanner_prerequisite_failed"])
        except FeedSnapshotError as exc:
            # Absent, partial, corrupt or incompatible data is never coverage.
            limitations.extend([exc.code, "scanner_prerequisite_failed"])
        except Exception:  # noqa: BLE001
            limitations.append("scanner_prerequisite_failed")
        finally:
            signal.signal(signal.SIGTERM, previous)
            if execution.state == "succeeded" and processes.canceled:
                execution.state = "canceled"
            execution.limitations = sorted(set(limitations))
            (output / "execution.json").write_text(
                execution.model_dump_json(by_alias=True)
            )
    ok = (
        execution.state == "succeeded"
        and execution.exit_code == 0
        and not execution.limitations
    )
    print(
        "Image assessment finished; checked evidence is ready."
        if ok
        else "Image assessment incomplete; inspect retained limitations."
    )
    return 0 if ok else 2


def _retain(
    assessment, execution, raw, output, secrets, input_digest, archive_identity
) -> list[str]:
    """Check native output, record the examined subject, then release it."""
    generate = assessment.operation == "sbom-generate"
    path = raw / ("sbom.json" if generate else "report.json")
    limit = assessment.max_sbom_bytes if generate else assessment.max_report_bytes
    if not path.exists():
        return ["missing_malformed_or_truncated_report"]
    body = path.read_bytes()
    if len(body) > limit:
        return ["native_report_exceeds_bound"]
    if any(secret in body for secret in secrets):
        # The target credential must never become evidence.
        return ["credential_material_withheld"]
    limitations = []
    try:
        document = json.loads(body)
        if not isinstance(document, dict):
            raise ValueError("report is not an object")
        if generate:
            execution.sbom, found = inspect_sbom(body, limit)
            if execution.sbom.format != "cyclonedx":
                raise ValueError("generated SBOM format differs from admission")
            limitations.extend(found)
            subject = subject_from_cyclonedx(assessment, document, input_digest)
        else:
            subject = subject_from_report(assessment, document, input_digest)
            if any(
                isinstance(row, dict) and row.get("Type") == "jar"
                for row in document.get("Results") or []
            ) and "java" not in (
                execution.feed.snapshot.components
                if execution.feed and execution.feed.snapshot
                else ()
            ):
                limitations.append("java_db_unavailable")
    except (SbomError, ValueError, TypeError, AttributeError, RecursionError):
        return ["missing_malformed_or_truncated_report"]
    # The archive's own config fills identity Trivy's output left out.
    subject = subject.model_copy(
        update={
            key: value
            for key, value in archive_identity.items()
            if getattr(subject, key) is None
        }
    )
    execution.subject = subject
    limitations.extend(subject_limitations(assessment, subject))
    if generate:
        # A standard SBOM is retained as produced so later reassessment can
        # read it; ingestion checks it again before publication.
        (output / "sbom.json").write_bytes(body)
    else:
        try:
            (output / "report.json").write_bytes(sanitize_native("trivy", body, limit))
        except (ValueError, TypeError, RecursionError):
            limitations.append("native_sanitization_failed")
    return limitations


def main(argv: list[str]) -> int:
    try:
        assessment = ImageAssessment.model_validate_json(argv[1])
        return run_image_assessment(assessment, output=Path(argv[2]))
    # Never print raw exceptions: they can carry paths, registry text or input.
    except Exception:  # noqa: BLE001
        print("Image assessment invocation failed; evidence is incomplete.")
        return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
