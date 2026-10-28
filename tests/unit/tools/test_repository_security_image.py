"""Security image release selection, authentication and default promotion (#5039)."""

import hashlib
import io
import json
import subprocess
import tarfile
import urllib.error

import pytest

from tools.ci import qualify_repository_security_image as qualification
from tools.ci import repository_security_image as image
from tools.ci import test_repository_security_image_updates as updates


def release(tool, version="1.2.3", prerelease=False):
    names = {
        "opengrep": [
            f"opengrep_manylinux_{arch}{suffix}"
            for arch in ("x86", "aarch64")
            for suffix in ("", ".sig", ".cert")
        ],
        "gitleaks": [
            f"gitleaks_{version}_linux_{arch}.tar.gz" for arch in ("x64", "arm64")
        ]
        + [f"gitleaks_{version}_checksums.txt"],
        "trivy": [
            f"trivy_{version}_Linux-{arch}.tar.gz{suffix}"
            for arch in ("64bit", "ARM64")
            for suffix in ("", ".sigstore.json")
        ],
    }[tool]
    return {
        "tag_name": f"v{version}",
        "prerelease": prerelease,
        "draft": False,
        "id": 123,
        "assets": [
            {
                "name": name,
                "id": index,
                "browser_download_url": f"https://github.com/{image.REPOSITORIES[tool]}/releases/download/v{version}/{name}",
            }
            for index, name in enumerate(names)
        ],
    }


@pytest.fixture
def releases(monkeypatch):
    calls = []

    def fetch(url):
        calls.append(url)
        tool = next(t for t, repo in image.REPOSITORIES.items() if f"/{repo}/" in url)
        version = url.rsplit("/", 1)[-1]
        return release(
            tool, "1.2.3" if version == "latest" else version.removeprefix("v")
        )

    monkeypatch.setattr(image, "fetch_json", fetch)
    return calls


def test_defaults_resolve_stable_inputs_for_both_architectures(releases):
    inputs = image.resolve_inputs()
    assert inputs["baseImage"] == image.DEFAULT_BASE
    for tool in image.REPOSITORIES:
        assert inputs["tools"][tool]["version"] == "v1.2.3"
        assert set(inputs["tools"][tool]["assets"]) == {"amd64", "arm64"}
    assert len(releases) == 3 and all(url.endswith("/latest") for url in releases)


def test_explicit_versions_and_base_are_resolved_without_compatibility_gates(releases):
    inputs = image.resolve_inputs(
        opengrep="9.8.7", trivy="v4.3.2", base_image="python:3.13-slim"
    )
    assert inputs["baseImage"] == "python:3.13-slim"
    assert inputs["tools"]["opengrep"]["version"] == "v9.8.7"
    assert inputs["tools"]["trivy"]["version"] == "v4.3.2"
    assert any(url.endswith("/tags/v9.8.7") for url in releases)


@pytest.mark.parametrize("flag", ["prerelease", "draft"])
def test_default_does_not_admit_unstable_release(monkeypatch, flag):
    data = release("opengrep")
    data[flag] = True
    monkeypatch.setattr(image, "fetch_json", lambda url: data)
    with pytest.raises(image.ImageError, match="stable"):
        image.resolve_inputs()


@pytest.mark.parametrize(
    "missing",
    [
        "opengrep_manylinux_aarch64",
        "opengrep_manylinux_x86.sig",
        "opengrep_manylinux_x86.cert",
    ],
)
def test_missing_architecture_or_required_verification_material_fails_closed(
    monkeypatch, missing
):
    data = release("opengrep")
    data["assets"] = [a for a in data["assets"] if a["name"] != missing]
    monkeypatch.setattr(image, "fetch_json", lambda url: data)
    with pytest.raises(image.ImageError, match=missing):
        image.resolve_inputs()


def test_upstream_resolution_failure_has_no_fallback(monkeypatch):
    monkeypatch.setattr(
        image,
        "fetch_json",
        lambda url: (_ for _ in ()).throw(OSError("upstream unavailable")),
    )
    with pytest.raises(OSError, match="upstream unavailable"):
        image.resolve_inputs()


@pytest.mark.parametrize("status", [401, 403])
def test_public_release_metadata_recovers_from_rejected_optional_token(
    monkeypatch, tmp_path, status
):
    monkeypatch.setenv("GH_TOKEN", "test-credential")
    monkeypatch.setattr(image.time, "sleep", lambda _: None)
    requests = []

    def open_url(request, **kwargs):
        requests.append(request)
        if request.has_header("Authorization"):
            raise urllib.error.HTTPError(
                request.full_url,
                status,
                "Forbidden",
                {},
                io.BytesIO(b'{"message":"Resource not accessible by integration"}'),
            )
        return io.BytesIO(b'{"tag_name":"v1.2.3"}')

    monkeypatch.setattr(image.urllib.request, "urlopen", open_url)
    destination = tmp_path / "release.json"
    image.download(
        "https://api.github.com/repos/opengrep/opengrep/releases/latest", destination
    )
    assert json.loads(destination.read_text()) == {"tag_name": "v1.2.3"}
    assert len(requests) == 2
    assert requests[0].has_header("Authorization")
    assert not requests[1].has_header("Authorization")


@pytest.mark.parametrize("with_token", [False, True])
def test_public_upstream_denial_stays_bounded_and_diagnostic(
    monkeypatch, tmp_path, with_token
):
    if with_token:
        monkeypatch.setenv("GH_TOKEN", "test-credential")
    else:
        monkeypatch.delenv("GH_TOKEN", raising=False)
    monkeypatch.setattr(image.time, "sleep", lambda _: None)
    requests = []
    url = "https://api.github.com/repos/opengrep/opengrep/releases/latest"

    def open_url(request, **kwargs):
        requests.append(request)
        message = "API rate limit exceeded"
        if with_token:
            message += " for test-credential"
        raise urllib.error.HTTPError(
            request.full_url,
            403,
            "Forbidden",
            {},
            io.BytesIO(json.dumps({"message": message}).encode()),
        )

    monkeypatch.setattr(image.urllib.request, "urlopen", open_url)
    with pytest.raises(image.ImageError, match="API rate limit exceeded") as failure:
        image.download(url, tmp_path / "release.json")
    assert url in str(failure.value)
    assert "403" in str(failure.value)
    assert "test-credential" not in str(failure.value)
    assert len(requests) == 3


def test_release_asset_download_never_receives_api_token(monkeypatch, tmp_path):
    monkeypatch.setenv("GH_TOKEN", "test-credential")
    requests = []

    def open_url(request, **kwargs):
        requests.append(request)
        return io.BytesIO(b"verified separately before installation")

    monkeypatch.setattr(image.urllib.request, "urlopen", open_url)
    image.download(
        "https://github.com/opengrep/opengrep/releases/download/v1.2.3/artifact",
        tmp_path / "artifact",
    )
    assert not requests[0].has_header("Authorization")


@pytest.mark.parametrize("tool", ["opengrep", "trivy"])
@pytest.mark.parametrize(
    "failure", [None, "invalid signature or identity", "missing verifier"]
)
def test_signature_verification_is_required_and_uses_the_upstream_identity(
    monkeypatch, tmp_path, tool, failure
):
    calls = []

    def command(*args):
        calls.append(args)
        if failure == "missing verifier":
            raise FileNotFoundError("cosign")
        if failure:
            raise subprocess.CalledProcessError(1, args, stderr=failure)
        return "Verified OK"

    monkeypatch.setattr(image, "command", command)
    binary = tmp_path / "artifact"
    binary.write_bytes(b"test artifact")
    if failure:
        with pytest.raises((subprocess.CalledProcessError, FileNotFoundError)):
            image.verify_signature(
                tool,
                "v1.2.3",
                binary,
                {
                    "signature": tmp_path / "signature",
                    "certificate": tmp_path / "certificate",
                    "bundle": tmp_path / "bundle",
                },
            )
    else:
        image.verify_signature(
            tool,
            "v1.2.3",
            binary,
            {
                "signature": tmp_path / "signature",
                "certificate": tmp_path / "certificate",
                "bundle": tmp_path / "bundle",
            },
        )
    argv = calls[0]
    assert argv[:2] == ("cosign", "verify-blob")
    assert argv[argv.index("--certificate-oidc-issuer") + 1] == (
        "https://token.actions.githubusercontent.com"
    )
    ref = (
        r"refs/(?:tags/v1\.2\.3|heads/release-v1\.2\.3)"
        if tool == "opengrep"
        else r"refs/tags/v1\.2\.3"
    )
    assert argv[argv.index("--certificate-identity-regexp") + 1] == (
        rf"^https://github.com/{image.REPOSITORIES[tool]}/\.github/workflows/[^/]+\.ya?ml@{ref}$"
    )
    assert not any("insecure" in str(a) or "ignore" in str(a) for a in argv)


def test_checksums_reject_modified_or_missing_archive(tmp_path):
    binary = tmp_path / "gitleaks.tar.gz"
    binary.write_bytes(b"known bytes")
    checksum = hashlib.sha256(binary.read_bytes()).hexdigest()
    image.verify_checksum(binary, f"{checksum}  {binary.name}\n")
    with pytest.raises(image.ImageError, match="checksum"):
        image.verify_checksum(binary, f"{'0' * 64}  {binary.name}\n")
    with pytest.raises(image.ImageError, match="checksum"):
        image.verify_checksum(binary, f"{checksum}  other.tar.gz\n")


def test_archive_installs_only_regular_selected_binary(tmp_path):
    archive = tmp_path / "tool.tar.gz"
    with tarfile.open(archive, "w:gz") as out:
        binary = tarfile.TarInfo("gitleaks")
        binary.size = 4
        out.addfile(binary, io.BytesIO(b"tool"))
        extra = tarfile.TarInfo("../../unwanted")
        extra.size = 3
        out.addfile(extra, io.BytesIO(b"bad"))
    destination = tmp_path / "bin"
    destination.mkdir()
    image.install_binary("gitleaks", archive, destination)
    assert (destination / "gitleaks").read_bytes() == b"tool"
    assert list(destination.iterdir()) == [destination / "gitleaks"]


def records():
    return [
        {
            "architecture": arch,
            "qualification": "passed",
            "sbomSha256": "b" * 64,
            "image": f"localhost:5000/security@sha256:{digest * 64}",
            "inventory": {"tools": {"opengrep": {"version": "v1.2.4"}}},
        }
        for arch, digest in [("amd64", "a"), ("arm64", "c")]
    ]


def test_successful_update_promotes_both_qualified_variants_in_one_manifest_write(
    monkeypatch,
):
    calls = []
    monkeypatch.setattr(image, "command", lambda *args: calls.append(args) or "")
    candidate = records()
    image.promote(candidate, "localhost:5000/security")
    assert calls == [
        (
            "docker",
            "buildx",
            "imagetools",
            "create",
            "--tag",
            "localhost:5000/security:latest",
            *(r["image"] for r in candidate),
        )
    ]


@pytest.mark.parametrize(
    "problem",
    [
        "failed",
        "missing",
        "duplicate",
        "missing-sbom",
        "mutable-image",
        "mixed-versions",
    ],
)
def test_failed_update_does_not_write_the_working_default(monkeypatch, problem):
    calls = []
    monkeypatch.setattr(image, "command", lambda *args: calls.append(args) or "")
    candidate = records()
    if problem == "failed":
        candidate[1]["qualification"] = "failed"
    if problem == "missing":
        candidate.pop()
    if problem == "duplicate":
        candidate[1]["architecture"] = "amd64"
    if problem == "missing-sbom":
        candidate[1].pop("sbomSha256")
    if problem == "mutable-image":
        candidate[1]["image"] = "localhost:5000/security:new"
    if problem == "mixed-versions":
        candidate[1]["inventory"]["tools"]["opengrep"]["version"] = "v1.2.5"
    with pytest.raises(image.ImageError):
        image.promote(candidate, "localhost:5000/security")
    assert not calls


# Every job that defaults to the repository-security alias runs one of these.
@pytest.mark.parametrize(
    "entrypoint",
    [
        "moonmind.security.repository_scanner_runner",
        "moonmind.security.scanner_feeds",
        "moonmind.security.image_assessment_runner",
    ],
)
def test_packaged_runner_imports_with_only_its_declared_python_dependency(
    tmp_path, entrypoint
):
    image.stage_runner(tmp_path)
    result = subprocess.run(
        [
            "python",
            "-I",
            "-c",
            f"import sys; from pathlib import Path; sys.path.insert(0, {str(tmp_path)!r}); import {entrypoint} as runner; assert Path(runner.__file__).is_relative_to({str(tmp_path)!r})",
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize(
    "scanner,body,count",
    [
        ("gitleaks", [], 0),
        ("gitleaks", [{"RuleID": "fixture"}], 1),
        ("opengrep", {"results": [], "errors": []}, 0),
        ("opengrep", {"results": [{"check_id": "fixture"}], "errors": []}, 1),
        ("trivy", {"SchemaVersion": 2, "Results": []}, 0),
        (
            "trivy",
            {"SchemaVersion": 2, "Results": [{"Secrets": [{"RuleID": "fixture"}]}]},
            1,
        ),
        (
            "trivy",
            {
                "SchemaVersion": 2,
                "Results": [{"Vulnerabilities": [{"VulnerabilityID": "fixture"}]}],
            },
            1,
        ),
    ],
)
def test_qualification_distinguishes_real_finding_and_clean_native_reports(
    scanner, body, count
):
    assert qualification.count_findings(scanner, body) == count


@pytest.mark.parametrize(
    "scanner,body",
    [
        ("gitleaks", {}),
        ("opengrep", {"results": [], "errors": ["scan failed"]}),
        ("trivy", {"SchemaVersion": 2}),
    ],
)
def test_qualification_does_not_count_missing_or_incomplete_evidence_as_clean(
    scanner, body
):
    with pytest.raises(image.ImageError):
        qualification.count_findings(scanner, body)


def test_qualification_failure_exposes_diagnostics_and_retains_full_evidence(
    monkeypatch, tmp_path
):
    output = "original diagnostic\n" * 1000 + "scanner failed to load its engine\n"
    monkeypatch.setattr(
        qualification.subprocess,
        "run",
        lambda argv, **kwargs: subprocess.CompletedProcess(argv, 2, stdout=output),
    )
    evidence = tmp_path / "failure.json"
    with pytest.raises(
        image.ImageError, match="scanner failed to load its engine"
    ) as failure:
        qualification.invoke(["opengrep", "scan"], evidence)
    assert len(str(failure.value)) < len(output)
    assert json.loads(evidence.read_text()) == {
        "command": ["opengrep", "scan"],
        "exitCode": 2,
        "output": output,
    }


def test_qualification_runtime_has_read_only_inputs_and_only_declared_writes(tmp_path):
    argv = qualification.container(
        "image",
        tmp_path / "source",
        tmp_path / "output",
        "--version",
        entrypoint="opengrep",
    )
    assert "--read-only" in argv
    assert argv[argv.index("--network") + 1] == "none"
    scratch = argv[argv.index("--tmpfs") + 1]
    assert "exec" in scratch.split(":", 1)[1].split(",")
    assert "noexec" not in scratch.split(":", 1)[1].split(",")
    mounts = [argv[i + 1] for i, a in enumerate(argv) if a == "--mount"]
    assert all(m.endswith(",readonly") for m in mounts if "dst=/scan-output" not in m)
    assert len([m for m in mounts if not m.endswith(",readonly")]) == 1
    assert not any("docker.sock" in arg or "--env" in arg for arg in argv)


def test_qualification_setup_supports_the_ci_host_and_preserves_runtime_ownership(
    monkeypatch, tmp_path
):
    monkeypatch.setattr(qualification.os, "getuid", lambda: 0)
    ownership = []
    monkeypatch.setattr(
        qualification.os,
        "chown",
        lambda path, uid, gid: ownership.append((path, uid, gid)),
    )
    output = tmp_path / "scan-output"
    qualification.owned_directory(output)
    assert output.is_dir()
    assert ownership == [(output, 1000, 1000)]
    monkeypatch.setattr(qualification.os, "getuid", lambda: 1001)
    with pytest.raises(image.ImageError, match="host qualification harness"):
        qualification.owned_directory(tmp_path / "unsupported")


def test_prepare_verifies_before_installing_any_binary(releases, monkeypatch, tmp_path):
    inputs = image.resolve_inputs()

    def download(url, path):
        path.write_bytes(b"fixture upstream bytes")

    monkeypatch.setattr(image, "download", download)
    monkeypatch.setattr(
        image,
        "verify_signature",
        lambda *args: (_ for _ in ()).throw(image.ImageError("invalid signature")),
    )
    installs = []
    monkeypatch.setattr(image, "install_binary", lambda *args: installs.append(args))
    with pytest.raises(image.ImageError, match="invalid signature"):
        image.prepare(inputs, "amd64", tmp_path / "build", tmp_path / "evidence")
    assert not installs
    assert not (tmp_path / "build/inventory.json").exists()


def test_negative_verification_checks_preserve_original_artifacts(
    releases, monkeypatch, tmp_path
):
    inputs = image.resolve_inputs()
    for tool in ("opengrep", "trivy"):
        (tmp_path / tool).mkdir()
        for asset in inputs["tools"][tool]["assets"]["amd64"].values():
            (tmp_path / tool / asset["name"]).write_bytes(b"original")
    calls = []

    def reject(tool, tag, binary, material):
        calls.append((tool, tag, binary, material))
        raise subprocess.CalledProcessError(1, ["cosign"])

    monkeypatch.setattr(image, "verify_signature", reject)
    image.verification_negatives(inputs, "amd64", tmp_path)
    assert len(calls) == 8
    assert len(json.loads((tmp_path / "verification-negatives.json").read_text())) == 8
    assert all(
        path.read_bytes() == b"original"
        for folder in (tmp_path / "opengrep", tmp_path / "trivy")
        for path in folder.iterdir()
    )


def test_update_fixture_cleanup_preserves_the_original_failure_and_attempts_removal(
    monkeypatch, tmp_path
):
    calls = []
    original = OSError("registry port unavailable")

    def command(*args):
        calls.append(args)
        if args[1] == "port":
            raise original
        if args[1] == "logs":
            raise OSError("log collection unavailable")
        return "fixture"

    monkeypatch.setattr(updates, "command", command)
    with pytest.raises(OSError) as caught:
        updates.exercise_updates(tmp_path)
    assert caught.value is original
    assert calls[-1][:3] == ("docker", "rm", "--force")
    assert (
        "log collection unavailable" in (tmp_path / "cleanup-errors.json").read_text()
    )
