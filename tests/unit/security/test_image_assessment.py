"""Image, archive and SBOM admission, identity and runner bounds (issue #5046)."""

from __future__ import annotations

import base64
import io
import json
import os
import subprocess
import sys
import tarfile
from datetime import UTC, datetime

import pytest

from api_service.services.registry_authorization import (
    PrivateImageAuthorizationPolicy,
    PrivateImageAuthorizationService,
)
from moonmind.config.container_backend_settings import (
    resolve_container_backend_settings,
)
from moonmind.schemas.container_job_models import (
    ContainerJobFailureClass,
    ContainerJobSpec,
    ContainerJobSubmitRequest,
    OwnerIdentity,
)
from moonmind.schemas.repository_scanner_models import ImageAssessment
from moonmind.security import image_assessment_runner as runner
from moonmind.security.egress_policy import parse_egress_policies
from moonmind.security.image_assessments import (
    SbomError,
    build_image_assessment_job,
    image_policy_admits,
    inspect_sbom,
    normalize_image_report,
    subject_from_cyclonedx,
    subject_from_report,
    subject_limitations,
)
from moonmind.security.scanner_feeds import publish_snapshot

WORKSPACE = {"kind": "sandbox", "workspaceId": "image"}
DIGEST = "sha256:" + "d" * 64
OTHER = "sha256:" + "e" * 64
DB = "a" * 64


def registry(**overrides) -> ImageAssessment:
    return ImageAssessment.model_validate(
        {
            "operation": "assess",
            "target": "registry",
            "imageReference": "registry.example.test:5443/team/app:stable",
            "egressPolicyRef": "registry",
            "databaseCacheRef": "scanner-db",
            "databaseDigest": DB,
            **overrides,
        }
    )


def archive(**overrides) -> ImageAssessment:
    return ImageAssessment.model_validate(
        {
            "operation": "assess",
            "target": "archive",
            "inputArtifactRef": "art_image",
            "databaseCacheRef": "scanner-db",
            "databaseDigest": DB,
            **overrides,
        }
    )


def spec(assessment: ImageAssessment, **overrides) -> dict:
    value = {**build_image_assessment_job(assessment), "workspaceRef": WORKSPACE}
    value.update(overrides)
    return value


# ------------------------------------------------------------ admission


ARCHIVE = {
    "operation": "assess",
    "target": "archive",
    "inputArtifactRef": "art_image",
    "databaseCacheRef": "scanner-db",
    "databaseDigest": DB,
}
REGISTRY = {
    **ARCHIVE,
    "target": "registry",
    "inputArtifactRef": None,
    "imageReference": "registry.example.test:5443/team/app:stable",
    "egressPolicyRef": "registry",
}


@pytest.mark.parametrize(
    "document",
    [
        # Source vulnerability scanning stays with RepositoryScan.
        {**ARCHIVE, "target": "source", "inputArtifactRef": None},
        {**ARCHIVE, "operation": "sbom-reassess"},
        {**REGISTRY, "imageReference": None},
        {**REGISTRY, "egressPolicyRef": None},
        {**ARCHIVE, "platform": "linux/amd64"},
        {**ARCHIVE, "targetCredentialRef": "secret://target-registry"},
        {**REGISTRY, "targetCredentialRef": "plain-password"},
        {**REGISTRY, "imageReference": "https://registry.example.test/app:1"},
        {**REGISTRY, "inputArtifactRef": "art_image"},
        # Matching requires admitted offline data; generation reads none.
        {**ARCHIVE, "databaseCacheRef": None, "databaseDigest": None},
        {**ARCHIVE, "operation": "sbom-generate"},
    ],
)
def test_admission_rejects_mismatched_targets_inputs_and_authority(document):
    ImageAssessment.model_validate(REGISTRY)
    ImageAssessment.model_validate(ARCHIVE)
    with pytest.raises(ValueError):
        ImageAssessment.model_validate(document)


def test_three_operations_are_independently_selectable():
    generate = ImageAssessment(
        operation="sbom-generate", target="source", outputDirectory="inventory"
    )
    reassess = ImageAssessment(
        operation="sbom-reassess",
        target="sbom",
        inputArtifactRef="art_sbom",
        databaseCacheRef="scanner-db",
        databaseDigest=DB,
    )
    assert [o["name"] for o in generate.outputs()] == ["sbom", "execution", "summary"]
    assert [o["name"] for o in reassess.outputs()] == [
        "report",
        "execution",
        "summary",
    ]
    assert [o["name"] for o in registry().outputs()][0] == "report"


def test_offline_jobs_mount_only_captured_inputs_and_read_only_data():
    for assessment, name in ((archive(), "image"), (registry(), None)):
        parsed = ContainerJobSpec.model_validate(spec(assessment))
        assert parsed.environment == [] and parsed.registry_credential_ref is None
        assert parsed.image_source_ref == "repository-security"
        assert [c.read_only for c in parsed.caches] == [True]
        if name:
            assert parsed.network_mode == "none" and parsed.egress_policy_ref is None
            assert [a.name for a in parsed.input_attachments] == [name]
            assert parsed.input_attachments[0].artifact_ref == "art_image"
        else:
            # A registry image is read from the registry, never a local daemon.
            assert parsed.network_mode == "bridge"
            assert parsed.egress_policy_ref == "registry"
            assert parsed.input_attachments == []


@pytest.mark.parametrize(
    "override",
    [
        {"environment": [{"name": "DOCKER_HOST", "value": "unix:///var/run/d.sock"}]},
        {"environment": [{"name": "TRIVY_PASSWORD", "secretRef": "env://PASSWORD"}]},
        {"caches": [{"cacheRef": "docker", "target": "/var/run"}]},
        {"inputAttachments": [{"name": "source", "workspacePath": "."}]},
        {"command": ["-c", "import os"]},
        {"networkMode": "bridge"},
        {"registryCredentialRef": "secret://pull"},
        {"timeoutSeconds": 86400},
        {"outputs": []},
    ],
)
def test_job_rejects_daemon_access_credentials_and_redirection(override):
    with pytest.raises(ValueError):
        ContainerJobSpec.model_validate(spec(archive(), **override))


def test_job_requires_deployment_scanner_image_and_its_own_workload():
    direct = spec(archive())
    direct.pop("imageSourceRef")
    direct["image"] = "attacker.example/trivy:latest"
    with pytest.raises(ValueError):
        ContainerJobSpec.model_validate(direct)
    with pytest.raises(ValueError):
        ContainerJobSpec.model_validate(
            spec(
                archive(),
                workloadProfile={"profileRef": "trivy", "mode": "scan"},
            )
        )


def _policy(*destinations):
    return parse_egress_policies(
        json.dumps(
            [
                {
                    "policyRef": "registry",
                    "allowedWorkflowIds": ["assessment"],
                    "allowedImageSourceRefs": ["repository-security"],
                    "destinations": [
                        {
                            "host": host,
                            "purpose": purpose,
                            "protocol": "https",
                            "port": port,
                        }
                        for host, purpose, port in destinations
                    ],
                }
            ]
        )
    )


def test_registry_egress_is_limited_to_the_admitted_registry():
    ok = _policy(
        ("registry.example.test", "registry-api", 5443),
        ("blobs.example.test", "registry-api", 443),
    )[0]
    assert image_policy_admits(ok, registry())
    assert not image_policy_admits(
        _policy(("registry.example.test", "registry-api", 443))[0], registry()
    )
    assert not image_policy_admits(
        _policy(
            ("registry.example.test", "registry-api", 5443),
            ("api.openai.com", "provider-api", 443),
        )[0],
        registry(),
    )
    hub = registry(imageReference="alpine:3.20")
    assert image_policy_admits(
        _policy(("registry-1.docker.io", "registry-api", 443))[0], hub
    )
    submission = ContainerJobSubmitRequest.model_validate(
        {
            "idempotencyKey": "image",
            "source": {"source": "workflow", "workflowId": "assessment"},
            "spec": spec(registry()),
        }
    )
    settings = resolve_container_backend_settings(
        {
            "MOONMIND_CONTAINER_BACKEND_EGRESS_POLICIES": json.dumps(
                [
                    {
                        "policyRef": "registry",
                        "allowedWorkflowIds": ["assessment"],
                        "allowedImageSourceRefs": ["repository-security"],
                        "destinations": [
                            {
                                "host": "api.openai.com",
                                "purpose": "provider-api",
                                "protocol": "https",
                                "port": 443,
                            }
                        ],
                    }
                ]
            )
        }
    )
    with pytest.raises(ValueError, match="only its registry"):
        settings.authorize_egress(submission)


# --------------------------------------- target credential authorization


def _grants(**grant):
    return PrivateImageAuthorizationService(
        PrivateImageAuthorizationPolicy.model_validate(
            {
                "grants": [
                    {
                        "credentialRef": "secret://target-registry",
                        "registry": "registry.example.test:5443",
                        "repositories": ["team/*"],
                        "principals": ["user:operator"],
                        **grant,
                    }
                ]
            }
        )
    )


OWNER = OwnerIdentity(principalType="user", principalId="operator")


def test_target_credential_is_authorized_by_scope_principal_and_reference():
    service = _grants()
    allowed = service.authorize_reference(
        owner=OWNER,
        image="registry.example.test:5443/team/app:stable",
        credential_ref="secret://target-registry",
    )
    assert allowed.authorized and allowed.scope == "team/*"
    for owner, image, ref, failure in (
        (
            OWNER,
            "registry.example.test:5443/other/app:1",
            "secret://target-registry",
            ContainerJobFailureClass.REPOSITORY_SCOPE_MISMATCH,
        ),
        (
            OwnerIdentity(principalType="user", principalId="sibling"),
            "registry.example.test:5443/team/app:1",
            "secret://target-registry",
            ContainerJobFailureClass.IMAGE_USE_DENIED,
        ),
        (
            OWNER,
            "registry.example.test:5443/team/app:1",
            "secret://unknown",
            ContainerJobFailureClass.IMAGE_USE_DENIED,
        ),
        # A protected target without a credential is refused, not read anonymously.
        (
            OWNER,
            "registry.example.test:5443/team/app:1",
            None,
            ContainerJobFailureClass.IMAGE_USE_DENIED,
        ),
    ):
        denied = service.authorize_reference(
            owner=owner, image=image, credential_ref=ref
        )
        assert not denied.authorized and denied.failure_class == failure


# ------------------------------------------------------------ SBOM checks


def cyclonedx(components=None, **extra) -> bytes:
    return json.dumps(
        {
            "bomFormat": "CycloneDX",
            "specVersion": "1.5",
            "metadata": {
                "tools": {"components": [{"name": "trivy", "version": "0.58.1"}]},
                "component": {"name": "app", "purl": "pkg:oci/app@" + DIGEST},
            },
            "components": (
                [{"name": "openssl", "version": "3.0.1", "purl": "pkg:apk/openssl"}]
                if components is None
                else components
            ),
            **extra,
        }
    ).encode()


def test_sbom_inventory_records_generator_coverage_and_unverified_claims():
    inventory, limitations = inspect_sbom(
        cyclonedx(
            [
                {"name": "openssl", "purl": "pkg:apk/openssl@3.0.1"},
                {"name": "vendored-binary"},
            ],
            compositions=[{"aggregate": "incomplete"}],
            signature={"algorithm": "ES256", "value": "unverified"},
        ),
        1 << 20,
    )
    assert (inventory.generator, inventory.generator_version) == ("trivy", "0.58.1")
    assert inventory.components == 2 and inventory.components_without_identifiers == 1
    assert inventory.embedded_claims == ["signature"]
    assert inventory.declared_subject == "pkg:oci/app@" + DIGEST
    assert set(limitations) == {
        "sbom_packages_without_identifiers",
        "sbom_declares_incomplete_inventory",
    }
    spdx, limitations = inspect_sbom(
        json.dumps(
            {
                "spdxVersion": "SPDX-2.3",
                "name": "app",
                "creationInfo": {"creators": ["Tool: trivy-0.58.1"]},
                "packages": [
                    {
                        "name": "openssl",
                        "externalRefs": [
                            {"referenceType": "purl", "referenceLocator": "pkg:apk/o"}
                        ],
                    }
                ],
            }
        ).encode(),
        1 << 20,
    )
    assert spdx.format == "spdx" and spdx.generator == "trivy" and not limitations
    assert inspect_sbom(cyclonedx([]), 1 << 20)[1] == ["sbom_inventory_empty"]


@pytest.mark.parametrize(
    "body, code",
    [
        (b"{not json", "sbom_malformed"),
        (b"[]", "sbom_malformed"),
        (b'{"bomFormat":"CycloneDX","specVersion":"1.0"}', "sbom_format_unsupported"),
        (b'{"spdxVersion":"SPDX-2.3","packages":[{"version":"1"}]}', "sbom_malformed"),
        (b'{"kind":"other"}', "sbom_format_unsupported"),
        (cyclonedx([{"version": "1"}]), "sbom_malformed"),
        (cyclonedx() + b" " * 2048, "sbom_exceeds_bound"),
    ],
)
def test_malformed_unsupported_and_oversized_sboms_are_explicit(body, code):
    with pytest.raises(SbomError) as error:
        inspect_sbom(body, len(cyclonedx()) + 10)
    assert error.value.code == code


# --------------------------------------------------------- target identity


def test_registry_report_records_resolved_digest_and_platform():
    report = {
        "Metadata": {
            "ImageID": OTHER,
            "RepoDigests": [
                "elsewhere.example.test/team/app@" + "sha256:" + "f" * 64,
                "registry.example.test:5443/team/app@" + DIGEST,
            ],
            "ImageConfig": {"os": "linux", "architecture": "arm64", "variant": "v8"},
        }
    }
    subject = subject_from_report(registry(platform="linux/arm64"), report, None)
    assert subject.resolved_digest == DIGEST and subject.image_id == OTHER
    assert subject.asset == "registry.example.test:5443/team/app@" + DIGEST
    assert subject.requested_reference.endswith(":stable")
    assert (subject.os, subject.architecture) == ("linux", "arm64")
    assert subject_limitations(registry(platform="linux/arm64"), subject) == []
    assert subject_limitations(registry(platform="linux/amd64"), subject) == [
        "target_platform_mismatch"
    ]
    pinned = registry(imageReference="registry.example.test:5443/team/app@" + OTHER)
    assert subject_limitations(pinned, subject) == ["target_digest_mismatch"]
    # A moving tag without a resolved digest is never recorded as the target.
    unresolved = subject_from_report(registry(), {"Metadata": {}}, None)
    assert unresolved.resolved_digest is None
    assert set(subject_limitations(registry(), unresolved)) == {
        "target_digest_unresolved",
        "target_platform_unrecorded",
    }


def test_generated_cyclonedx_records_digest_and_architecture():
    document = json.loads(cyclonedx())
    document["metadata"]["component"] = {
        "name": "registry.example.test:5443/team/app:stable",
        "purl": "pkg:oci/app@sha256%3A"
        + "d" * 64
        + "?arch=amd64&repository_url=registry.example.test%3A5443%2Fteam%2Fapp",
        "properties": [{"name": "aquasecurity:trivy:ImageID", "value": OTHER}],
    }
    subject = subject_from_cyclonedx(
        registry(operation="sbom-generate", databaseCacheRef=None, databaseDigest=None),
        document,
        None,
    )
    assert subject.resolved_digest == DIGEST and subject.architecture == "amd64"
    assert subject.image_id == OTHER


def test_report_findings_are_attributed_to_the_examined_subject():
    body = json.dumps(
        {
            "SchemaVersion": 2,
            "Results": [
                {
                    "Target": "/inputs/sbom/sbom.json (alpine 3.20.1)",
                    "Class": "os-pkgs",
                    "Type": "alpine",
                    "Vulnerabilities": [
                        {
                            "VulnerabilityID": "CVE-2024-0001",
                            "PkgName": "openssl",
                            "InstalledVersion": "3.0.1",
                        },
                        {
                            "VulnerabilityID": "CVE-2024-0001",
                            "PkgName": "libssl",
                            "InstalledVersion": "3.0.1",
                        },
                    ],
                }
            ],
        }
    ).encode()
    result = normalize_image_report(
        body,
        subject_asset="sbom@sha256:" + "c" * 64,
        exit_code=0,
        terminal_state="succeeded",
        native_ref="art_report",
    )
    assert result.coverage == "complete"
    assert {f.asset for f in result.findings} == {"sbom@sha256:" + "c" * 64}
    assert len(result.findings) == 2
    assert result.examined_assets == ["sbom@sha256:" + "c" * 64]
    malformed = normalize_image_report(
        b'{"SchemaVersion":2,"Results":[{"Target":"x"}]}',
        subject_asset="a",
        exit_code=0,
        terminal_state="succeeded",
        native_ref="r",
    )
    assert malformed.coverage == "partial"


# ------------------------------------------------------------ runner bounds


def fixture_trivy(tmp_path, monkeypatch, script: str):
    """Replace only the Trivy process; keep the runner's own bounds."""
    fixture = tmp_path / "trivy-fixture"
    fixture.write_text(
        f"#!{sys.executable}\nimport json, os, pathlib, sys, time\n"
        "args=sys.argv[1:]\n"
        "if args == ['--version']:\n print('Version: 0.58.1'); raise SystemExit(0)\n"
        "assert '--image-src' not in args or "
        "args[args.index('--image-src')+1] == 'remote'\n"
        "output=pathlib.Path(args[args.index('--output')+1])\n" + script
    )
    fixture.chmod(0o755)
    original = subprocess.Popen
    seen = []

    def popen(argv, *args, **kwargs):
        if len(argv) > 5 and argv[5] == "trivy":
            seen.append((list(argv[6:]), dict(kwargs["env"])))
            argv = [*argv[:5], str(fixture), *argv[6:]]
        return original(argv, *args, **kwargs)

    monkeypatch.setattr(subprocess, "Popen", popen)
    return seen


def feed(root):
    stage = root / "staging" / "fixture"
    (stage / "db").mkdir(parents=True)
    (root / "snapshots").mkdir(exist_ok=True)
    (stage / "db" / "trivy.db").write_text("{}")
    now = datetime.now(UTC).isoformat()
    (stage / "db" / "metadata.json").write_text(
        json.dumps({"Version": 2, "UpdatedAt": now, "DownloadedAt": now})
    )
    snapshot, _ = publish_snapshot(
        root,
        stage,
        components=["vulnerability"],
        acquisition="import",
        upstreams={"vulnerability": "artifact:art_fixture"},
        now=datetime.now(UTC),
    )
    return snapshot.snapshot_id


def image_tar(path):
    with tarfile.open(path, "w") as bundle:
        body = b"[]"
        info = tarfile.TarInfo("manifest.json")
        info.size = len(body)
        bundle.addfile(info, io.BytesIO(body))


REPORT = (
    "output.write_text(json.dumps({'SchemaVersion':2,'Metadata':{'ImageConfig':"
    "{'os':'linux','architecture':'amd64'}},'Results':[{'Target':'img','Class':"
    "'os-pkgs','Type':'alpine','Vulnerabilities':[]}]}))\n"
)


def archive_case(tmp_path, **overrides):
    inputs = tmp_path / "inputs"
    (inputs / "image").mkdir(parents=True)
    image_tar(inputs / "image" / "image.tar")
    digest = feed(tmp_path / "db")
    return inputs, archive(databaseDigest=digest, **overrides)


def test_archive_scan_uses_input_flag_without_daemon_or_ambient_config(
    tmp_path, monkeypatch
):
    inputs, assessment = archive_case(tmp_path)
    seen = fixture_trivy(tmp_path, monkeypatch, REPORT)
    monkeypatch.setenv("DOCKER_HOST", "unix:///var/run/docker.sock")
    output = tmp_path / "out"
    assert (
        runner.run_image_assessment(
            assessment, inputs=inputs, output=output, database=tmp_path / "db"
        )
        == 0
    )
    argv, env = seen[-1]
    assert argv[:3] == ["image", "--input", str(inputs / "image" / "image.tar")]
    assert "DOCKER_HOST" not in env
    assert env["DOCKER_CONFIG"] != runner.TARGET_AUTH_TARGET
    execution = json.loads((output / "execution.json").read_bytes())
    assert execution["subject"]["asset"].startswith("image-archive@sha256:")
    assert execution["subject"]["architecture"] == "amd64"
    assert execution["databaseDigest"] == assessment.database_digest


@pytest.mark.parametrize(
    "case, code",
    [("oversized", "input_bound_exceeded"), ("malformed", "image_archive_malformed")],
)
def test_archive_bounds_and_malformed_inputs_fail_before_scanning(
    tmp_path, monkeypatch, case, code
):
    inputs, assessment = archive_case(
        tmp_path, **({"maxInputBytes": 16} if case == "oversized" else {})
    )
    if case == "malformed":
        (inputs / "image" / "image.tar").write_bytes(b"not a tar archive")
    seen = fixture_trivy(tmp_path, monkeypatch, REPORT)
    output = tmp_path / "out"
    assert (
        runner.run_image_assessment(
            assessment, inputs=inputs, output=output, database=tmp_path / "db"
        )
        == 2
    )
    execution = json.loads((output / "execution.json").read_bytes())
    assert {code, "scanner_prerequisite_failed"} <= set(execution["limitations"])
    assert not seen and not (output / "report.json").exists()


@pytest.mark.parametrize("failure", ["timeout", "cancel"])
def test_timeout_and_cancellation_stop_the_scanner(tmp_path, monkeypatch, failure):
    inputs, assessment = archive_case(tmp_path, timeoutSeconds=1)
    script = (
        "import signal\nos.kill(os.getppid(), signal.SIGTERM)\n"
        if failure == "cancel"
        else ""
    ) + "time.sleep(30)\n"
    fixture_trivy(tmp_path, monkeypatch, script)
    output = tmp_path / "out"
    assert (
        runner.run_image_assessment(
            assessment, inputs=inputs, output=output, database=tmp_path / "db"
        )
        == 2
    )
    execution = json.loads((output / "execution.json").read_bytes())
    assert execution["state"] == ("timed_out" if failure == "timeout" else "canceled")
    assert "scanner_exit_failure" in execution["limitations"]


def test_oversized_native_report_is_not_released(tmp_path, monkeypatch):
    inputs, assessment = archive_case(tmp_path, maxReportBytes=128)
    fixture_trivy(
        tmp_path,
        monkeypatch,
        "output.write_text(json.dumps({'SchemaVersion':2,'Results':[],"
        "'pad':'x'*4096}))\n",
    )
    output = tmp_path / "out"
    runner.run_image_assessment(
        assessment, inputs=inputs, output=output, database=tmp_path / "db"
    )
    execution = json.loads((output / "execution.json").read_bytes())
    assert "native_report_exceeds_bound" in execution["limitations"] or (
        "missing_malformed_or_truncated_report" in execution["limitations"]
    )
    assert not (output / "report.json").exists()


def test_target_credential_reaches_trivy_only_as_docker_config(tmp_path, monkeypatch):
    auth = tmp_path / "auth"
    auth.mkdir()
    password = "fixture-target-password-5046"
    (auth / "config.json").write_text(
        json.dumps(
            {
                "auths": {
                    "registry.example.test:5443": {
                        "username": "reader",
                        "password": password,
                    }
                }
            }
        )
    )
    digest = feed(tmp_path / "db")
    # The fixture echoes the credential into its report: it must be withheld.
    seen = fixture_trivy(
        tmp_path,
        monkeypatch,
        "cfg=json.loads((pathlib.Path(os.environ['DOCKER_CONFIG'])/'config.json')"
        ".read_text())\n"
        "secret=next(iter(cfg['auths'].values()))['password']\n"
        "output.write_text(json.dumps({'SchemaVersion':2,'Results':[],"
        "'Leak':secret}))\n",
    )
    output = tmp_path / "out"
    assessment = registry(
        databaseDigest=digest, targetCredentialRef="secret://target-registry"
    )
    assert (
        runner.run_image_assessment(
            assessment,
            inputs=tmp_path,
            output=output,
            database=tmp_path / "db",
            auth=auth,
        )
        == 2
    )
    argv, env = seen[-1]
    assert password not in json.dumps(argv) and env["DOCKER_CONFIG"] == str(auth)
    assert all(password not in value for value in env.values())
    for path in output.iterdir():
        assert password.encode() not in path.read_bytes()
        assert base64.b64encode(password.encode()) not in path.read_bytes()
    execution = json.loads((output / "execution.json").read_bytes())
    assert execution["targetCredential"] == "delivered"
    assert "credential_material_withheld" in execution["limitations"]


@pytest.mark.parametrize(
    "stderr, code",
    [
        ("UNAUTHORIZED: authentication required", "target_registry_auth_failed"),
        (
            "DENIED: requested access to the resource is denied",
            "target_registry_access_denied",
        ),
        ("MANIFEST_UNKNOWN: manifest unknown", "target_not_found"),
    ],
)
def test_registry_failures_are_classified_without_retaining_text(
    tmp_path, monkeypatch, stderr, code
):
    digest = feed(tmp_path / "db")
    fixture_trivy(
        tmp_path,
        monkeypatch,
        f"print({stderr!r}, file=sys.stderr); raise SystemExit(1)\n",
    )
    output = tmp_path / "out"
    assert (
        runner.run_image_assessment(
            registry(databaseDigest=digest),
            inputs=tmp_path,
            output=output,
            database=tmp_path / "db",
        )
        == 2
    )
    body = (output / "execution.json").read_text()
    assert code in json.loads(body)["limitations"]
    assert stderr not in body and not (output / "report.json").exists()
    assert os.listdir(output) == ["execution.json"]


def test_archive_identity_reads_oci_layout_config_without_extraction(tmp_path):
    import hashlib

    config = json.dumps({"os": "linux", "architecture": "arm64", "variant": "v8"})
    config_digest = hashlib.sha256(config.encode()).hexdigest()
    manifest = json.dumps({"config": {"digest": "sha256:" + config_digest}})
    manifest_digest = hashlib.sha256(manifest.encode()).hexdigest()
    members = {
        "oci-layout": '{"imageLayoutVersion":"1.0.0"}',
        "index.json": json.dumps(
            {"manifests": [{"digest": "sha256:" + manifest_digest}]}
        ),
        f"blobs/sha256/{manifest_digest}": manifest,
        f"blobs/sha256/{config_digest}": config,
    }
    path = tmp_path / "image.tar"
    with tarfile.open(path, "w") as bundle:
        for name, body in members.items():
            info = tarfile.TarInfo(name)
            info.size = len(body.encode())
            bundle.addfile(info, io.BytesIO(body.encode()))
    assert runner._archive_identity(path) == {
        "image_id": "sha256:" + config_digest,
        "os": "linux",
        "architecture": "arm64",
        "variant": "v8",
    }
