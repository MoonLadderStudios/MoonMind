"""Build-time image-owned tools installer (MoonLadderStudios/MoonMind#4558).

The shared host image owns ``gh`` and ``moonmind``. The Dockerfile runs this
helper at build time against the single pin source
(``services/omnigent/tools/manifest.lock.json``); host launch never downloads,
installs, or copies shared tool binaries.
"""

from __future__ import annotations

import hashlib
import importlib.util
import io
import json
import ssl
import stat
import tarfile
import time
import urllib.error
import urllib.request
from pathlib import Path
from unittest.mock import Mock, call
from urllib.parse import urlsplit

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
HELPER = REPO_ROOT / "services/omnigent/moonmind-host/install_moonmind_tools.py"
REAL_MANIFEST = REPO_ROOT / "services/omnigent/tools/manifest.lock.json"
REAL_CLI = REPO_ROOT / "services/omnigent/scripts/moonmind-container-cli.py"
REAL_PROFILE = REPO_ROOT / "services/omnigent/scripts/moonmind-tools.sh"


def _helper():
    spec = importlib.util.spec_from_file_location(
        "install_moonmind_tools", HELPER
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _assert_download_requests(request, url, timeouts):
    assert len(request.call_args_list) == len(timeouts)
    for request_call, timeout in zip(request.call_args_list, timeouts, strict=True):
        assert len(request_call.args) == 1
        download_request = request_call.args[0]
        assert isinstance(download_request, urllib.request.Request)
        assert download_request.full_url == url
        assert download_request.get_method() == "GET"
        assert download_request.get_header("Accept") == "application/octet-stream"
        assert download_request.get_header("Authorization") is None
        assert request_call.kwargs == {"timeout": timeout}


def _fixture_tree(tmp_path: Path, *, version: str = "9.9.9"):
    """Manifest + archive + CLI fixture mirroring manifest.lock.json shape."""
    gh_binary = b"#!/bin/sh\necho \"gh version " + version.encode() + b" (test)\"\n"
    archive = tmp_path / "gh.tar.gz"
    with tarfile.open(archive, "w:gz") as tar:
        info = tarfile.TarInfo("gh/bin/gh")
        info.size = len(gh_binary)
        tar.addfile(info, io.BytesIO(gh_binary))
    cli = b"#!/bin/sh\necho \"usage: moonmind [options] (test cli)\"\n"
    cli_source = tmp_path / "moonmind-container-cli.py"
    cli_source.write_bytes(cli)
    manifest = {
        "schemaVersion": 1,
        "bundleVersion": f"gh-{version}-container-v1",
        "tools": [
            {
                "name": "gh",
                "version": version,
                "platforms": {
                    "linux/amd64": {
                        "url": archive.as_uri(),
                        "sha256": hashlib.sha256(archive.read_bytes()).hexdigest(),
                        "executableSha256": hashlib.sha256(gh_binary).hexdigest(),
                        "archivePath": "gh/bin/gh",
                    }
                },
                "path": "bin/gh",
                "versionProbe": ["--version"],
            },
            {
                "name": "docker",
                "version": "container-v1",
                "sourcePath": "moonmind-container-cli.py",
                "platforms": {
                    "linux/amd64": {
                        "executableSha256": hashlib.sha256(cli).hexdigest()
                    }
                },
                "path": "bin/moonmind",
                "versionProbe": ["--help"],
            },
        ],
    }
    manifest_path = tmp_path / "manifest.lock.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    return manifest_path, cli_source


def test_platform_selection_matches_manifest_keys() -> None:
    helper = _helper()
    assert helper.platform_key("x86_64") == "linux/amd64"
    assert helper.platform_key("aarch64") == "linux/arm64"
    assert helper.platform_key("arm64") == "linux/arm64"
    with pytest.raises(RuntimeError):
        helper.platform_key("riscv64")


@pytest.mark.parametrize("status", [408, 429, 500, 502, 503, 504])
def test_download_recovers_from_transient_http_errors(tmp_path, monkeypatch, status):
    helper = _helper()
    url = "https://example.invalid/gh.tar.gz"
    payload = b"verified archive"
    error = urllib.error.HTTPError(url, status, "temporary failure", {}, None)
    request = Mock(side_effect=[error, io.BytesIO(payload)])
    sleep = Mock()
    monkeypatch.setattr(helper.urllib.request, "urlopen", request)
    monkeypatch.setattr(time, "sleep", sleep)
    destination = tmp_path / "gh.tar.gz"

    helper._download(url, destination, hashlib.sha256(payload).hexdigest())

    assert destination.read_bytes() == payload
    _assert_download_requests(request, url, [60, 60])
    sleep.assert_called_once_with(1)


@pytest.mark.parametrize(
    "error",
    [
        TimeoutError("timed out"),
        urllib.error.URLError(TimeoutError("timed out")),
        ConnectionResetError("connection reset"),
    ],
)
def test_download_discards_partial_bytes_before_retry(tmp_path, monkeypatch, error):
    helper = _helper()
    payload = b"verified archive"
    destination = tmp_path / "gh.tar.gz"

    class InterruptedResponse(io.BytesIO):
        def read(self, size):
            if self.tell():
                raise error
            return super().read(size)

        read1 = read

    request = Mock(side_effect=[InterruptedResponse(b"partial"), io.BytesIO(payload)])

    def backoff(delay):
        assert delay == 1
        assert not destination.exists()

    monkeypatch.setattr(helper.urllib.request, "urlopen", request)
    monkeypatch.setattr(time, "sleep", backoff)

    helper._download(
        "https://example.invalid/gh.tar.gz",
        destination,
        hashlib.sha256(payload).hexdigest(),
    )

    assert destination.read_bytes() == payload
    assert request.call_count == 2


def test_download_stops_after_three_transient_failures(tmp_path, monkeypatch, capsys):
    helper = _helper()
    url = "https://example.invalid/gh.tar.gz"
    errors = [
        urllib.error.HTTPError(url, 504, "Gateway Time-out", {}, None) for _ in range(3)
    ]
    request = Mock(side_effect=errors)
    sleep = Mock()
    monkeypatch.setattr(helper.urllib.request, "urlopen", request)
    monkeypatch.setattr(time, "sleep", sleep)
    destination = tmp_path / "gh.tar.gz"

    with pytest.raises(urllib.error.HTTPError) as raised:
        helper._download(url, destination, "0" * 64)

    assert raised.value is errors[-1]
    _assert_download_requests(request, url, [60, 60, 60])
    assert sleep.call_args_list == [call(1), call(2)]
    assert not destination.exists()
    assert "504" in capsys.readouterr().err


def test_download_deadline_stops_a_slow_stream(tmp_path, monkeypatch):
    helper = _helper()
    elapsed = [0]

    class SlowResponse(io.BytesIO):
        def read1(self, size):
            elapsed[0] += 60
            return super().read(1)

        def read(self, size):
            pytest.fail(
                "read() may perform repeated socket reads without a deadline check"
            )

    request = Mock(return_value=SlowResponse(b"slow archive"))
    sleep = Mock()
    monkeypatch.setattr(helper.urllib.request, "urlopen", request)
    monkeypatch.setattr(time, "monotonic", lambda: elapsed[0])
    monkeypatch.setattr(time, "sleep", sleep)
    destination = tmp_path / "gh.tar.gz"

    with pytest.raises(TimeoutError, match="download.*budget"):
        helper._download("https://example.invalid/gh.tar.gz", destination, "0" * 64)

    assert elapsed[0] == 180
    assert request.call_count == 1
    sleep.assert_not_called()
    assert not destination.exists()


def test_download_caps_retry_timeout_to_remaining_budget(tmp_path, monkeypatch):
    helper = _helper()
    url = "https://example.invalid/gh.tar.gz"
    payload = b"verified archive"
    elapsed = [0]

    def respond(*args, **kwargs):
        if elapsed[0] == 0:
            elapsed[0] = 160
            raise urllib.error.HTTPError(url, 504, "Gateway Time-out", {}, None)
        return io.BytesIO(payload)

    def backoff(delay):
        elapsed[0] += delay

    request = Mock(side_effect=respond)
    monkeypatch.setattr(helper.urllib.request, "urlopen", request)
    monkeypatch.setattr(time, "monotonic", lambda: elapsed[0])
    monkeypatch.setattr(time, "sleep", backoff)
    destination = tmp_path / "gh.tar.gz"

    helper._download(url, destination, hashlib.sha256(payload).hexdigest())

    _assert_download_requests(request, url, [60, 19])
    assert destination.read_bytes() == payload


def test_download_preserves_last_error_when_elapsed_budget_expires(
    tmp_path, monkeypatch
):
    helper = _helper()
    url = "https://example.invalid/gh.tar.gz"
    error = urllib.error.HTTPError(url, 504, "Gateway Time-out", {}, None)
    elapsed = [0]

    def respond(*args, **kwargs):
        elapsed[0] = 180
        raise error

    request = Mock(side_effect=respond)
    sleep = Mock()
    monkeypatch.setattr(helper.urllib.request, "urlopen", request)
    monkeypatch.setattr(time, "monotonic", lambda: elapsed[0])
    monkeypatch.setattr(time, "sleep", sleep)

    with pytest.raises(urllib.error.HTTPError) as raised:
        helper._download(url, tmp_path / "gh.tar.gz", "0" * 64)

    assert raised.value is error
    assert request.call_count == 1
    sleep.assert_not_called()


@pytest.mark.parametrize("status", [400, 401, 403, 404, 501])
def test_download_does_not_retry_permanent_http_errors(tmp_path, monkeypatch, status):
    helper = _helper()
    url = "https://example.invalid/gh.tar.gz"
    error = urllib.error.HTTPError(url, status, "permanent failure", {}, None)
    request = Mock(side_effect=error)
    sleep = Mock()
    monkeypatch.setattr(helper.urllib.request, "urlopen", request)
    monkeypatch.setattr(time, "sleep", sleep)

    with pytest.raises(urllib.error.HTTPError) as raised:
        helper._download(url, tmp_path / "gh.tar.gz", "0" * 64)

    assert raised.value is error
    assert request.call_count == 1
    sleep.assert_not_called()


@pytest.mark.parametrize(
    "error",
    [
        urllib.error.URLError(ssl.SSLCertVerificationError("invalid certificate")),
        urllib.error.URLError("unknown URL type"),
        PermissionError("access denied"),
    ],
)
def test_download_does_not_retry_non_transient_errors(tmp_path, monkeypatch, error):
    helper = _helper()
    request = Mock(side_effect=error)
    sleep = Mock()
    monkeypatch.setattr(helper.urllib.request, "urlopen", request)
    monkeypatch.setattr(time, "sleep", sleep)

    with pytest.raises(type(error)) as raised:
        helper._download(
            "https://example.invalid/gh.tar.gz", tmp_path / "gh.tar.gz", "0" * 64
        )

    assert raised.value is error
    assert request.call_count == 1
    sleep.assert_not_called()


@pytest.mark.parametrize("failure", ["checksum", "size"])
def test_download_does_not_retry_or_retain_invalid_artifacts(
    tmp_path, monkeypatch, failure
):
    helper = _helper()
    payload = b"unverified archive"
    request = Mock(return_value=io.BytesIO(payload))
    sleep = Mock()
    monkeypatch.setattr(helper.urllib.request, "urlopen", request)
    monkeypatch.setattr(time, "sleep", sleep)
    if failure == "size":
        monkeypatch.setattr(helper, "MAX_ARTIFACT_BYTES", len(payload) - 1)
    destination = tmp_path / "gh.tar.gz"

    with pytest.raises(RuntimeError, match="SHA-256 mismatch|artifact exceeds"):
        helper._download("https://example.invalid/gh.tar.gz", destination, "0" * 64)

    assert request.call_count == 1
    sleep.assert_not_called()
    assert not destination.exists()


def test_install_produces_image_owned_read_only_layout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    helper = _helper()
    manifest_path, cli_source = _fixture_tree(tmp_path)
    monkeypatch.setattr(helper, "platform_key", lambda: "linux/amd64")
    output = tmp_path / "opt" / "moonmind-tools"
    profile_target = tmp_path / "etc-profile-d" / "moonmind-tools.sh"

    helper.install(
        manifest_path,
        output,
        cli_source=cli_source,
        profile_source=REAL_PROFILE,
        profile_target=profile_target,
    )

    gh = output / "bin" / "gh"
    moonmind = output / "bin" / "moonmind"
    assert gh.is_file() and moonmind.is_file()
    assert stat.S_IMODE(gh.stat().st_mode) == 0o555
    assert stat.S_IMODE(moonmind.stat().st_mode) == 0o555
    assert stat.S_IMODE(profile_target.stat().st_mode) == 0o444
    image_manifest = json.loads((output / "manifest.json").read_text(encoding="utf-8"))
    assert image_manifest["bundleVersion"] == "gh-9.9.9-container-v1"
    assert image_manifest["tools"][0] == {
        "name": "gh",
        "version": "9.9.9",
        "path": "bin/gh",
        "versionProbe": ["--version"],
    }
    assert image_manifest["tools"][1]["path"] == "bin/moonmind"


def test_install_rejects_archive_hash_mismatch(tmp_path: Path, monkeypatch) -> None:
    helper = _helper()
    manifest_path, cli_source = _fixture_tree(tmp_path)
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    payload["tools"][0]["platforms"]["linux/amd64"]["sha256"] = "0" * 64
    manifest_path.write_text(json.dumps(payload), encoding="utf-8")
    monkeypatch.setattr(helper, "platform_key", lambda: "linux/amd64")
    with pytest.raises(RuntimeError, match="[Ss][Hh][Aa].*mismatch|hash"):
        helper.install(
            manifest_path,
            tmp_path / "out",
            cli_source=cli_source,
            profile_source=REAL_PROFILE,
            profile_target=tmp_path / "profile.sh",
        )


def test_install_rejects_cli_source_drift(tmp_path: Path, monkeypatch) -> None:
    helper = _helper()
    manifest_path, cli_source = _fixture_tree(tmp_path)
    cli_source.write_bytes(b"#!/usr/bin/env python3\nprint('tampered')\n")
    monkeypatch.setattr(helper, "platform_key", lambda: "linux/amd64")
    with pytest.raises(RuntimeError, match="[Ss][Hh][Aa].*mismatch|drift"):
        helper.install(
            manifest_path,
            tmp_path / "out",
            cli_source=cli_source,
            profile_source=REAL_PROFILE,
            profile_target=tmp_path / "profile.sh",
        )


def test_version_override_must_match_manifest(tmp_path: Path, monkeypatch) -> None:
    helper = _helper()
    manifest_path, cli_source = _fixture_tree(tmp_path, version="9.9.9")
    monkeypatch.setattr(helper, "platform_key", lambda: "linux/amd64")
    with pytest.raises(RuntimeError, match="[Dd]rift|does not match"):
        helper.install(
            manifest_path,
            tmp_path / "out",
            cli_source=cli_source,
            profile_source=REAL_PROFILE,
            profile_target=tmp_path / "profile.sh",
            gh_version_override="1.2.3",
        )


def test_real_manifest_and_cli_sources_are_installable_inputs() -> None:
    """The repository's actual pins stay usable: both arches pinned, CLI present."""
    manifest = json.loads(REAL_MANIFEST.read_text(encoding="utf-8"))
    gh = next(item for item in manifest["tools"] if item["name"] == "gh")
    assert set(gh["platforms"]) == {"linux/amd64", "linux/arm64"}
    asset_urls = {pin["url"] for pin in gh["platforms"].values()}
    assert len(asset_urls) == 2
    for asset_url in asset_urls:
        parsed = urlsplit(asset_url)
        assert parsed.scheme == "https"
        assert parsed.netloc == "api.github.com"
        assert parsed.path.startswith("/repos/cli/cli/releases/assets/")
        assert parsed.path.removeprefix("/repos/cli/cli/releases/assets/").isdigit()
        assert not parsed.query and not parsed.fragment
    docker_tool = next(item for item in manifest["tools"] if item["name"] == "docker")
    assert docker_tool["path"] == "bin/moonmind"
    assert REAL_CLI.is_file()
    assert REAL_PROFILE.is_file()
