"""Image, archive and SBOM jobs -> retained evidence -> reports, issue #5046.

Trivy is a deterministic fixture at the external binary boundary. It emulates
a local registry (tags, platforms, credentials), image archives, SBOM input
and two vulnerability databases. Admission, target authorization, attachment
capture, credential delivery, worker launch arguments, the runner, ingestion,
artifact storage and report dispatch are production code; no daemon socket,
paid registry or cloud account is involved. The registry egress gateway is a
stub here; its enforcement has its own scoped-egress suites.
"""

from __future__ import annotations

import io
import json
import os
import subprocess
import sys
import tarfile
from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest

from api_service.services.container_jobs import (
    ContainerJobAuthorizationError,
    ContainerJobService,
    owner_artifact_principal,
)
from api_service.services.registry_authorization import (
    PrivateImageAuthorizationPolicy,
    PrivateImageAuthorizationService,
)
from moonmind.config.container_backend_settings import (
    CACHE_SOURCES_ENV_KEY,
    EGRESS_POLICIES_ENV_KEY,
    IMAGE_SOURCES_ENV_KEY,
    resolve_container_backend_settings,
)
from moonmind.schemas.container_job_models import (
    ContainerJobActivityRequest,
    ContainerJobBackendError,
    ContainerJobFailureClass,
    ContainerJobSubmitRequest,
    OwnerIdentity,
)
from moonmind.schemas.repository_scanner_models import ImageAssessment
from moonmind.security.image_assessment_runner import run_image_assessment
from moonmind.security.image_assessments import build_image_assessment_job
from moonmind.security.scanner_feeds import publish_snapshot
from moonmind.workflows.temporal.container_job_attachments import storage_for
from moonmind.workflows.temporal.container_job_backend import DockerContainerJobBackend
from moonmind.workflows.temporal.runtime.registry_auth_resolve import (
    RegistryAuthResolutionError,
    RegistryCredential,
)
from moonmind.workflows.temporal.worker_runtime import _container_job_artifact_reader
from tests.integration.security import test_recurring_assessments as recurring
from tests.integration.security.test_recurring_assessments import invoke

environment = recurring.environment

pytestmark = [pytest.mark.asyncio, pytest.mark.integration, pytest.mark.integration_ci]
OWNER = OwnerIdentity(principalType="user", principalId="operator")
PASSWORD = "fixture-registry-password-5046"
REGISTRY = "registry.example.test:5443"
AMD64 = "sha256:" + "1" * 64
ARM64 = "sha256:" + "2" * 64
PRIVATE = "sha256:" + "3" * 64
VULNERABLE = ["pkg:apk/openssl@3.0.1", "pkg:pypi/requests@2.0.0"]
CLEAN = ["pkg:apk/busybox@1.36.1"]
# A local registry: tags resolve to per-platform manifests; one repository
# requires the fixture credential.
CATALOG = {
    "team/app": {
        "tags": {"stable": {"linux/amd64": AMD64, "linux/arm64": ARM64}},
        "packages": VULNERABLE,
    },
    "private/app": {
        "tags": {"1.0": {"linux/amd64": PRIVATE}},
        "packages": VULNERABLE,
        "password": PASSWORD,
    },
}


def install_trivy(tmp_path, monkeypatch, *, failure=None):
    """A Trivy stand-in reading only what the runner admitted."""
    calls = tmp_path / "trivy-calls.jsonl"
    fixture = tmp_path / "trivy-fixture"
    fixture.write_text(
        f"""#!{sys.executable}
import base64, json, os, pathlib, sys, tarfile, time
args = sys.argv[1:]
if args == ["--version"]:
    print("Version: 0.58.1"); raise SystemExit(0)
assert "DOCKER_HOST" not in os.environ and "--privileged" not in args
with open({str(calls)!r}, "a") as log:
    log.write(json.dumps(args) + "\\n")
failure = {failure!r}
if failure == "cancel":
    import signal
    os.kill(os.getppid(), signal.SIGTERM); time.sleep(30)
output = pathlib.Path(args[args.index("--output") + 1])
cache = pathlib.Path(args[args.index("--cache-dir") + 1])
fmt = args[args.index("--format") + 1]
catalog = {CATALOG!r}
meta, digests, packages = {{}}, [], []
if args[0] == "image" and "--input" in args:
    with tarfile.open(args[args.index("--input") + 1]) as bundle:
        packages = json.load(bundle.extractfile("packages.json"))
    meta = {{"ImageID": "sha256:" + "a" * 64,
             "ImageConfig": {{"os": "linux", "architecture": "amd64"}}}}
elif args[0] == "image":
    assert args[args.index("--image-src") + 1] == "remote"
    # The job network reaches the registry only through the admitted proxy.
    if os.environ.get("HTTPS_PROXY") != "http://fixture-gateway:3128":
        print("dial tcp: lookup registry.example.test: no such host", file=sys.stderr)
        raise SystemExit(1)
    reference = args[-1]
    repository, _, tag = reference.removeprefix({REGISTRY!r} + "/").partition(":")
    entry = catalog.get(repository)
    if entry is None or tag not in entry["tags"]:
        print("MANIFEST_UNKNOWN: manifest unknown", file=sys.stderr)
        raise SystemExit(1)
    if "password" in entry:
        try:
            config = json.loads((pathlib.Path(os.environ["DOCKER_CONFIG"])
                                 / "config.json").read_text())
            login = config["auths"][{REGISTRY!r}]
        except (OSError, KeyError, ValueError):
            login = {{}}
        if login.get("password") != entry["password"]:
            print("UNAUTHORIZED: authentication required", file=sys.stderr)
            raise SystemExit(1)
    platform = (args[args.index("--platform") + 1]
                if "--platform" in args else "linux/amd64")
    digest = entry["tags"][tag][platform]
    os_name, arch = platform.split("/")
    digests = [{REGISTRY!r} + "/" + repository + "@" + digest]
    packages = entry["packages"]
    meta = {{"ImageID": "sha256:" + "b" * 64, "RepoDigests": digests,
             "ImageConfig": {{"os": os_name, "architecture": arch}}}}
elif args[0] == "sbom":
    document = json.loads(pathlib.Path(args[-1]).read_text())
    packages = [c["purl"] for c in document.get("components", []) if "purl" in c]
if failure == "malformed":
    output.write_text('{{"SchemaVersion": 2, "Results": [{{"Target": "x"}}')
    raise SystemExit(0)
if fmt == "cyclonedx":
    component = {{"name": "subject", "properties": [
        {{"name": "aquasecurity:trivy:RepoDigest", "value": d}} for d in digests]}}
    if digests:
        component["purl"] = ("pkg:oci/app@" + digests[0].split("@")[1].replace(":", "%3A")
                             + "?arch=" + meta["ImageConfig"]["architecture"])
    # Shaped like Trivy 0.76 output: CycloneDX 1.7, an identifier-less OS
    # component and lock-file application grouping their packages.
    component["bom-ref"] = "subject"
    groups = {{}}
    for p in packages:
        groups.setdefault("os" if p.startswith("pkg:apk/") else "app", []).append(p)
    contexts = {{"os": {{"type": "operating-system", "name": "alpine", "version": "3.20.3"}},
                "app": {{"type": "application", "name": "app/requirements.txt"}}}}
    output.write_text(json.dumps({{
        "bomFormat": "CycloneDX", "specVersion": "1.7",
        "metadata": {{"tools": {{"components": [{{"type": "application",
                      "group": "aquasecurity", "name": "trivy", "version": "0.76.1"}}]}},
                      "component": component}},
        "components": [{{"bom-ref": g, **contexts[g]}} for g in groups] + [
            {{"bom-ref": p, "type": "library", "name": p.split("/")[-1].split("@")[0],
              "purl": p}} for p in packages],
        "dependencies": [{{"ref": "subject", "dependsOn": list(groups)}}] + [
            {{"ref": g, "dependsOn": refs}} for g, refs in groups.items()]}}))
    raise SystemExit(0)
database = json.loads((cache / "db" / "trivy.db").read_text())
vulnerabilities = [
    {{"VulnerabilityID": cve, "PkgName": p.split("/")[-1].split("@")[0],
      "InstalledVersion": p.split("@")[-1],
      "Description": "fixture advisory text"}}
    for p in packages for cve in database.get(p, [])]
output.write_text(json.dumps({{
    "SchemaVersion": 2, "Metadata": meta,
    "Results": [{{"Target": "fixture (alpine 3.20)", "Class": "os-pkgs",
                  "Type": "alpine", "Vulnerabilities": vulnerabilities}}]}}))
"""
    )
    fixture.chmod(0o755)
    original = subprocess.Popen

    def popen(argv, *args, **kwargs):
        if len(argv) > 5 and argv[5] == "trivy":
            argv = [*argv[:5], str(fixture), *argv[6:]]
        return original(argv, *args, **kwargs)

    monkeypatch.setattr(subprocess, "Popen", popen)
    return calls


def database(root, advisories, name):
    """One published feed snapshot; matching reads its ``trivy.db`` fixture."""
    stage = root / "staging" / name
    (stage / "db").mkdir(parents=True)
    (root / "snapshots").mkdir(exist_ok=True)
    (stage / "db" / "trivy.db").write_text(json.dumps(advisories))
    now = datetime.now(UTC).isoformat()
    (stage / "db" / "metadata.json").write_text(
        json.dumps({"Version": 2, "UpdatedAt": now, "DownloadedAt": now})
    )
    snapshot, _ = publish_snapshot(
        root,
        stage,
        components=["vulnerability"],
        acquisition="import",
        upstreams={"vulnerability": f"artifact:art_{name}"},
        now=datetime.now(UTC),
    )
    return snapshot.snapshot_id


def image_archive(packages, *, valid=True) -> bytes:
    """A Docker-save layout: manifest, image config, and the fixture inventory."""
    config = json.dumps({"os": "linux", "architecture": "amd64"}).encode()
    manifest = json.dumps([{"Config": "config.json", "Layers": []}]).encode()
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w") as bundle:
        for name, body in (
            ("manifest.json" if valid else "notes.txt", manifest),
            ("config.json", config),
            ("packages.json", json.dumps(packages).encode()),
        ):
            info = tarfile.TarInfo(name)
            info.size = len(body)
            bundle.addfile(info, io.BytesIO(body))
    return buffer.getvalue()


def settings():
    return resolve_container_backend_settings(
        {
            IMAGE_SOURCES_ENV_KEY: json.dumps(
                [
                    {
                        "sourceRef": "repository-security",
                        "image": "ghcr.io/moonladderstudios/repository-security:latest",
                    }
                ]
            ),
            CACHE_SOURCES_ENV_KEY: json.dumps(
                [
                    {
                        "cacheRef": "scanner-db",
                        "volumeName": "scanner-feeds",
                        "target": "/opt/moonmind-scanner-db",
                        "readOnly": True,
                    }
                ]
            ),
            EGRESS_POLICIES_ENV_KEY: json.dumps(
                [
                    {
                        "policyRef": "registry",
                        "allowedWorkflowIds": ["assessment"],
                        "allowedImageSourceRefs": ["repository-security"],
                        "destinations": [
                            {
                                "host": "registry.example.test",
                                "purpose": "registry-api",
                                "protocol": "https",
                                "port": 5443,
                            }
                        ],
                    }
                ]
            ),
        }
    )


AUTHORIZER = PrivateImageAuthorizationService(
    PrivateImageAuthorizationPolicy.model_validate(
        {
            "grants": [
                {
                    "credentialRef": "secret://target-registry",
                    "registry": REGISTRY,
                    "repositories": ["private/*"],
                    "principals": ["user:operator"],
                }
            ]
        }
    )
)


async def owned_artifact(service, payload: bytes) -> str:
    principal = owner_artifact_principal(OWNER)
    artifact, _ = await service.create(
        principal=principal, content_type="application/json"
    )
    await service.write_complete(
        artifact_id=artifact.artifact_id, principal=principal, payload=payload
    )
    return artifact.artifact_id


class EgressLease:
    """Stands in for the scoped egress gateway admitted for registry jobs."""

    gateway_ref, network_ref = "fixture-gateway", "fixture-egress"

    def __init__(self, policy):
        self.policy = policy

    async def prepare(self, **_):
        return {
            "proxyAddress": "10.89.0.2",
            "clientAddress": "10.89.0.3",
            "gatewayIdentity": "fixture",
            "snapshotDigest": "fixture",
        }

    def proxy_env(self):
        return ("HTTPS_PROXY=http://fixture-gateway:3128",)

    async def attest(self, **_):
        raise RuntimeError("fixture gateway has no runtime evidence")


class Harness:
    def __init__(self, environment, tmp_path, monkeypatch, *, failure=None):
        self.environment = environment
        self.tmp_path = tmp_path
        self.monkeypatch = monkeypatch
        self.db = tmp_path / "feeds"
        self.calls = install_trivy(tmp_path, monkeypatch, failure=failure)
        self.temporal = AsyncMock()
        self.jobs = ContainerJobService(
            environment.session,
            temporal=self.temporal,
            artifacts=environment.service,
            authorizer=AUTHORIZER,
            backend_settings=settings(),
        )
        self.published: dict[str, bytes] = {}
        self.commands: list[tuple] = []

        def lease(backend, request):
            policy = backend._settings.authorize_egress(request.request)
            return EgressLease(policy) if policy is not None else None

        async def egress_evidence(backend, request, evidence, **_):
            return await self.publish(request, f"{request.job_id}-egress.json", b"{}")

        monkeypatch.setattr(DockerContainerJobBackend, "_scoped_lease", lease)
        monkeypatch.setattr(
            DockerContainerJobBackend, "_publish_scoped_egress", egress_evidence
        )

    async def publish(self, request, name, payload):
        # The production principal, linked to the assessment workflow.
        principal = owner_artifact_principal(request.owner)
        artifact, _ = await self.environment.service.create(
            principal=principal,
            content_type="application/json",
            link={
                "namespace": "default",
                "workflow_id": "assessment",
                "run_id": "run",
                "link_type": "output.container-job",
            },
        )
        await self.environment.service.write_complete(
            artifact_id=artifact.artifact_id, principal=principal, payload=payload
        )
        self.published[name] = payload
        return artifact.artifact_id

    async def run(
        self,
        assessment: ImageAssessment,
        key: str,
        *,
        credential=None,
        terminal=None,
    ):
        submission = ContainerJobSubmitRequest(
            idempotencyKey=key,
            source={"source": "workflow", "workflowId": "assessment"},
            spec={
                **build_image_assessment_job(assessment),
                "workspaceRef": {"kind": "sandbox", "workspaceId": key},
            },
        )
        accepted = await self.jobs.submit(owner=OWNER, request=submission)
        started = self.temporal.start_container_job.call_args.args[0]
        workspace = self.tmp_path / "temporal_sandbox" / key / "repo"
        workspace.mkdir(parents=True)
        (workspace / "requirements.txt").write_text("requests==2.0.0\n")

        async def daemon(argv):
            self.commands.append(tuple(argv))
            if argv[0] == "inspect":
                return 1, b"", b"no such container"
            stamp = datetime.now(UTC).isoformat().replace("+00:00", "Z")
            return 0, f"{stamp} {PASSWORD}\n".encode(), f"{PASSWORD}\n".encode()

        async def resolve(ref):
            if credential is None:
                raise RegistryAuthResolutionError("unresolvable")
            return RegistryCredential(username="reader", secret=credential)

        backend = DockerContainerJobBackend(
            workspace_root=self.tmp_path,
            command_runner=daemon,
            evidence_publisher=self.publish,
            artifact_reader=_container_job_artifact_reader(self.environment.service),
            registry_auth_resolver=resolve,
            log_spool_root=self.tmp_path / "live",
            settings=settings(),
        )
        request = ContainerJobActivityRequest(
            jobId=accepted.job_id,
            owner=OWNER,
            ownershipToken=f"{accepted.job_id}:v1",
            request=submission,
            registryAuthorization=started.registry_authorization,
            targetAuthorization=started.target_authorization,
            resolvedWorkspaceRef=str(workspace),
            resolvedImageRef="sha256:" + "a" * 64,
        )
        await backend.create_container(request)
        create = [c for c in self.commands if c[0] == "create"][-1]
        # The runner sees the container environment the launch declared.
        for key in ("HTTP_PROXY", "HTTPS_PROXY", "NO_PROXY"):
            self.monkeypatch.delenv(key, raising=False)
        for i, item in enumerate(create):
            if item == "--env":
                key, _, value = create[i + 1].partition("=")
                self.monkeypatch.setenv(key, value)
        storage = storage_for(self.tmp_path, accepted.job_id)
        code = run_image_assessment(
            assessment,
            inputs=storage.inputs,
            output=storage.outputs / assessment.output_directory,
            database=self.db,
            auth=storage.credentials,
        )
        request.container_ref = "fixture-container"
        request.terminal_state = terminal or ("succeeded" if code == 0 else "failed")
        request.exit_code = code
        await backend._collect_live_logs(request, request.container_ref)
        result = await backend.publish_evidence(request)
        summary = json.loads(self.published[f"{accepted.job_id}-output-summary.json"])
        # The credential's lifetime ends with the container.
        assert not storage.credentials.exists()
        return summary, create, started, result, accepted

    def assert_no_secret(self, *extra):
        for name, payload in self.published.items():
            assert PASSWORD.encode() not in payload, name
        for path in (self.tmp_path / "live").rglob("*"):
            if path.is_file():
                assert PASSWORD.encode() not in path.read_bytes()
        for value in extra:
            assert PASSWORD not in value


def mounts(create):
    return [create[i + 1] for i, item in enumerate(create) if item == "--mount"]


def assert_daemonless(create):
    joined = json.dumps(create)
    for forbidden in ("docker.sock", "containerd.sock", "podman.sock"):
        assert forbidden not in joined
    assert [a for a in create if a.startswith("--privileged")] == ["--privileged=false"]
    assert create[create.index("--cap-drop") + 1] == "ALL"
    assert "--cap-add" not in create and "--device" not in create
    assert not any("dst=/workspace" in m for m in mounts(create))


def summary_ref(harness, accepted):
    page = json.loads(harness.published[f"{accepted.job_id}-artifacts.json"])
    return next(a["artifactRef"] for a in page["artifacts"] if a["name"] == "summary")


async def report(environment, summary_ref, asset, *, baseline=None, key="occurrence"):
    return await invoke(
        environment.service,
        {
            "scope": {"assets": [asset]},
            "tools": ["retained-report"],
            "reportRefs": [summary_ref],
            **({"baselineRef": baseline} if baseline else {}),
            "credentialRefs": [],
            "networkRefs": [],
            "budget": {
                "baseSeconds": 60,
                "maxSeconds": 120,
                "progressStallSeconds": 60,
            },
        },
        key=key,
    )


async def test_registry_tag_resolves_digest_platform_and_known_findings(
    environment, tmp_path, monkeypatch
):
    harness = Harness(environment, tmp_path, monkeypatch)
    digest = database(harness.db, {"pkg:apk/openssl@3.0.1": ["CVE-2024-0001"]}, "one")
    assessment = ImageAssessment(
        operation="assess",
        target="registry",
        imageReference=f"{REGISTRY}/team/app:stable",
        platform="linux/arm64",
        egressPolicyRef="registry",
        databaseCacheRef="scanner-db",
        databaseDigest=digest,
    )
    summary, create, _started, result, accepted = await harness.run(
        assessment, "registry"
    )
    assert_daemonless(create)
    assert create[create.index("--network") + 1] != "host"
    subject = summary["subject"]
    # The moving tag is recorded as requested; the evidence names the digest.
    assert subject["requestedReference"].endswith("team/app:stable")
    assert subject["resolvedDigest"] == ARM64
    assert (subject["os"], subject["architecture"]) == ("linux", "arm64")
    assert subject["asset"] == f"{REGISTRY}/team/app@{ARM64}"
    assert summary["coverage"] == "complete", summary["limitations"]
    assert [f["ruleId"] for f in summary["result"]["findings"]] == ["CVE-2024-0001"]
    assert summary["execution"]["feed"]["snapshot"]["snapshotId"] == digest
    assert "exploitability" in summary["interpretation"]
    page = json.loads(harness.published[f"{accepted.job_id}-artifacts.json"])
    ref = next(a["artifactRef"] for a in page["artifacts"] if a["name"] == "summary")
    outputs = await report(environment, ref, subject["asset"])
    assert outputs["coverageStatus"] == "complete"
    assert outputs["counts"]["new"] == 1
    assert outputs["scannerSummaries"][0]["target"] == subject["asset"]
    # Native free text from the advisory is not ordinary evidence.
    assert (
        b"fixture advisory text"
        not in harness.published[f"{accepted.job_id}-output-report.json"]
    )


async def test_archive_and_sbom_paths_run_without_daemon_socket_or_privilege(
    environment, tmp_path, monkeypatch
):
    harness = Harness(environment, tmp_path, monkeypatch)
    digest = database(harness.db, {"pkg:apk/openssl@3.0.1": ["CVE-2024-0001"]}, "one")
    clean = await owned_artifact(environment.service, image_archive(CLEAN))
    summary, create, *_ = await harness.run(
        ImageAssessment(
            operation="assess",
            target="archive",
            inputArtifactRef=clean,
            databaseCacheRef="scanner-db",
            databaseDigest=digest,
        ),
        "archive-clean",
    )
    assert_daemonless(create)
    assert create[create.index("--network") + 1] == "none"
    assert any(
        "dst=/inputs/image" in m and m.endswith(",readonly") for m in mounts(create)
    )
    # A clean control: complete coverage and no findings.
    assert summary["coverage"] == "complete", summary["limitations"]
    assert summary["result"]["findings"] == []
    assert summary["subject"]["asset"].startswith("image-archive@sha256:")
    calls = [json.loads(line) for line in harness.calls.read_text().splitlines()]
    assert calls[-1][:2] == ["image", "--input"]

    malformed = await owned_artifact(
        environment.service, image_archive(CLEAN, valid=False)
    )
    summary, create, *_ = await harness.run(
        ImageAssessment(
            operation="assess",
            target="archive",
            inputArtifactRef=malformed,
            databaseCacheRef="scanner-db",
            databaseDigest=digest,
        ),
        "archive-malformed",
    )
    assert summary["coverage"] == "partial"
    assert "image_archive_malformed" in summary["limitations"]
    assert summary["subject"] is None and summary["nativeRef"] is None

    sbom = await owned_artifact(
        environment.service,
        json.dumps(
            {
                "bomFormat": "CycloneDX",
                "specVersion": "1.5",
                "components": [
                    {"name": "openssl", "purl": VULNERABLE[0]},
                    {"name": "vendored-binary"},
                ],
                "compositions": [{"aggregate": "incomplete"}],
                "signature": {"algorithm": "ES256", "value": "unverified-claim"},
            }
        ).encode(),
    )
    summary, create, *_ = await harness.run(
        ImageAssessment(
            operation="sbom-reassess",
            target="sbom",
            inputArtifactRef=sbom,
            databaseCacheRef="scanner-db",
            databaseDigest=digest,
        ),
        "sbom-incomplete",
    )
    assert_daemonless(create)
    assert create[create.index("--network") + 1] == "none"
    # Known findings still surface, but an incomplete inventory is partial.
    assert [f["ruleId"] for f in summary["result"]["findings"]] == ["CVE-2024-0001"]
    assert {
        "sbom_packages_without_identifiers",
        "sbom_declares_incomplete_inventory",
    } <= set(summary["limitations"])
    provenance = summary["execution"]["provenance"]
    assert provenance == {
        "verification": "not_performed",
        "declaredSubject": None,
        "embeddedClaims": ["signature"],
    }

    broken = await owned_artifact(environment.service, b'{"bomFormat":"CycloneDX"')
    summary, *_ = await harness.run(
        ImageAssessment(
            operation="sbom-reassess",
            target="sbom",
            inputArtifactRef=broken,
            databaseCacheRef="scanner-db",
            databaseDigest=digest,
        ),
        "sbom-malformed",
    )
    assert "sbom_malformed" in summary["limitations"]
    assert summary["coverage"] == "partial" and summary["result"]["findings"] == []


async def test_generated_sbom_is_retained_reloaded_and_reassessed_with_newer_data(
    environment, tmp_path, monkeypatch
):
    harness = Harness(environment, tmp_path, monkeypatch)
    older = database(harness.db, {}, "older")
    image = await owned_artifact(environment.service, image_archive(VULNERABLE))
    generated, create, *_ = await harness.run(
        ImageAssessment(
            operation="sbom-generate", target="archive", inputArtifactRef=image
        ),
        "generate",
    )
    assert_daemonless(create)
    assert generated["coverage"] == "complete", generated["limitations"]
    retained = generated["sbom"]
    assert retained["inventory"]["format"] == "cyclonedx"
    assert retained["inventory"]["specVersion"] == "1.7"
    assert retained["inventory"]["generator"] == "trivy"
    assert retained["inventory"]["generatorVersion"] == "0.76.1"
    # The OS and application grouping components are context, not packages.
    assert retained["inventory"]["components"] == 2
    assert retained["inventory"]["componentsWithoutIdentifiers"] == 0
    assert generated["subject"]["kind"] == "archive"
    # Read from the archive's own image config, not Trivy's SBOM output.
    assert generated["subject"]["os"] == "linux"
    assert generated["subject"]["architecture"] == "amd64"
    assert generated["subject"]["imageId"].startswith("sha256:")
    assert "Trivy inventories packages" in generated["inventoryScope"]
    # Reload the retained standard SBOM through the owner's artifact authority.
    _, reloaded = await environment.service.read(
        artifact_id=retained["artifactRef"], principal=owner_artifact_principal(OWNER)
    )
    document = json.loads(reloaded)
    assert document["bomFormat"] == "CycloneDX"
    assert {c["type"] for c in document["components"]} == {
        "operating-system",
        "application",
        "library",
    }

    before = len(harness.calls.read_text().splitlines())
    first, *_ = await harness.run(
        ImageAssessment(
            operation="sbom-reassess",
            target="sbom",
            inputArtifactRef=retained["artifactRef"],
            databaseCacheRef="scanner-db",
            databaseDigest=older,
        ),
        "reassess-older",
    )
    newer = database(
        harness.db,
        {
            "pkg:apk/openssl@3.0.1": ["CVE-2024-0001"],
            "pkg:pypi/requests@2.0.0": ["CVE-2024-0002"],
        },
        "newer",
    )
    second, *_, flagged_job = await harness.run(
        ImageAssessment(
            operation="sbom-reassess",
            target="sbom",
            inputArtifactRef=retained["artifactRef"],
            databaseCacheRef="scanner-db",
            databaseDigest=newer,
        ),
        "reassess-newer",
    )
    # The same SBOM bytes, newer intelligence, and no image read or rebuilt.
    assert first["subject"]["asset"] == second["subject"]["asset"]
    assert first["subject"]["inputDigest"] == retained["sha256"]
    assert first["result"]["findings"] == []
    for summary in (first, second):
        assert summary["coverage"] == "complete", summary["limitations"]
    assert sorted(f["ruleId"] for f in second["result"]["findings"]) == [
        "CVE-2024-0001",
        "CVE-2024-0002",
    ]
    assert (
        first["execution"]["databaseDigest"],
        second["execution"]["databaseDigest"],
    ) == (older, newer)
    later = [json.loads(line) for line in harness.calls.read_text().splitlines()]
    assert [call[0] for call in later[before:]] == ["sbom", "sbom"]
    assert "does not establish exploitability" in second["interpretation"]

    # A complete clean reassessment of the same inventory clears those findings.
    third, *_, clean_job = await harness.run(
        ImageAssessment(
            operation="sbom-reassess",
            target="sbom",
            inputArtifactRef=retained["artifactRef"],
            databaseCacheRef="scanner-db",
            databaseDigest=older,
        ),
        "reassess-clean",
    )
    asset = second["subject"]["asset"]
    flagged = await report(environment, summary_ref(harness, flagged_job), asset)
    cleared = await report(
        environment,
        summary_ref(harness, clean_job),
        asset,
        baseline=flagged["comparisonRef"],
        key="clean-occurrence",
    )
    assert third["result"]["findings"] == []
    assert flagged["counts"]["new"] == 2
    assert cleared["counts"]["absent_comparable"] == 2
    assert cleared["counts"]["not_reassessed"] == 0


async def test_sbom_metadata_credentials_are_withheld_from_every_publication(
    environment, tmp_path, monkeypatch
):
    harness = Harness(environment, tmp_path, monkeypatch)
    digest = database(harness.db, {}, "one")
    token = "ghp_" + "Fixture5046Metadata" * 2
    sbom = await owned_artifact(
        environment.service,
        json.dumps(
            {
                "bomFormat": "CycloneDX",
                "specVersion": "1.5",
                "metadata": {
                    "tools": {"components": [{"name": f"generator {token}"}]},
                    "component": {"name": f"app {token}"},
                },
                "components": [{"name": "openssl", "purl": VULNERABLE[0]}],
            }
        ).encode(),
    )
    summary, *_ = await harness.run(
        ImageAssessment(
            operation="sbom-reassess",
            target="sbom",
            inputArtifactRef=sbom,
            databaseCacheRef="scanner-db",
            databaseDigest=digest,
        ),
        "sbom-metadata-token",
    )
    assert summary["execution"]["sbom"]["generator"].startswith("generator ")
    assert summary["execution"]["provenance"]["declaredSubject"].startswith("app ")
    for name, payload in harness.published.items():
        assert token.encode() not in payload, name


@pytest.mark.parametrize("case", ["authorized", "wrong", "unresolvable", "denied"])
async def test_private_registry_credentials_scope_and_secret_handling(
    environment, tmp_path, monkeypatch, case
):
    harness = Harness(environment, tmp_path, monkeypatch)
    digest = database(harness.db, {"pkg:apk/openssl@3.0.1": ["CVE-2024-0001"]}, "one")
    reference = f"{REGISTRY}/{'other' if case == 'denied' else 'private'}/app:1.0"
    assessment = ImageAssessment(
        operation="assess",
        target="registry",
        imageReference=reference,
        egressPolicyRef="registry",
        targetCredentialRef="secret://target-registry",
        databaseCacheRef="scanner-db",
        databaseDigest=digest,
    )
    if case == "denied":
        # Outside the credential's repository scope: refused before any job.
        with pytest.raises(ContainerJobAuthorizationError) as denied:
            await harness.run(assessment, "denied")
        assert (
            denied.value.authorization.failure_class
            == ContainerJobFailureClass.REPOSITORY_SCOPE_MISMATCH
        )
        harness.temporal.start_container_job.assert_not_called()
        assert not harness.calls.exists()
        return
    if case == "unresolvable":
        with pytest.raises(ContainerJobBackendError) as unresolved:
            await harness.run(assessment, case, credential=None)
        assert (
            unresolved.value.failure_class
            is ContainerJobFailureClass.CREDENTIAL_UNRESOLVED
        )
        assert not harness.calls.exists()
        return
    summary, create, started, result, accepted = await harness.run(
        assessment,
        case,
        credential=PASSWORD if case == "authorized" else "expired-or-wrong",
    )
    credential_mount = next(
        m for m in mounts(create) if "dst=/run/moonmind-target-registry" in m
    )
    assert credential_mount.endswith(",readonly")
    assert PASSWORD not in json.dumps(create)
    assert started.target_authorization.credential_ref == "secret://target-registry"
    assert started.target_authorization.scope == "private/*"
    if case == "authorized":
        assert summary["coverage"] == "complete", summary["limitations"]
        assert summary["subject"]["resolvedDigest"] == PRIVATE
        assert summary["execution"]["targetCredential"] == "delivered"
    else:
        assert "target_registry_auth_failed" in summary["limitations"]
        assert summary["coverage"] == "partial"
    record = await harness.jobs.repository.get_for_owner(
        owner=OWNER, job_id=accepted.job_id
    )
    harness.assert_no_secret(
        json.dumps(record.request_json),
        started.model_dump_json(by_alias=True),
        json.dumps(summary),
        os.environ.get("DOCKER_CONFIG", ""),
    )
    record.logs_ref, record.artifacts_ref = result.logs_ref, result.artifacts_ref
    await environment.session.flush()


@pytest.mark.parametrize("failure", ["malformed", "cancel"])
async def test_malformed_reports_and_cancellation_stay_partial_through_adapters(
    environment, tmp_path, monkeypatch, failure
):
    harness = Harness(environment, tmp_path, monkeypatch, failure=failure)
    digest = database(harness.db, {}, "one")
    image = await owned_artifact(environment.service, image_archive(VULNERABLE))
    summary, *_ = await harness.run(
        ImageAssessment(
            operation="assess",
            target="archive",
            inputArtifactRef=image,
            databaseCacheRef="scanner-db",
            databaseDigest=digest,
            timeoutSeconds=5,
        ),
        failure,
        terminal="canceled" if failure == "cancel" else None,
    )
    assert summary["coverage"] == "partial"
    if failure == "cancel":
        assert summary["execution"]["state"] == "canceled"
        assert "container_job_canceled" in summary["limitations"]
    else:
        assert "missing_malformed_or_truncated_report" in summary["limitations"]
    assert summary["nativeRef"] is None
