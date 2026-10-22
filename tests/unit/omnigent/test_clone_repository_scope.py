"""Local-only regression coverage for admitted clone transport boundaries."""

import asyncio
import os
import subprocess
from pathlib import Path

import pytest

from moonmind.omnigent.host_failures import OmnigentOAuthHostError
from moonmind.omnigent.host_services.workspace import build_daemon_git_clone_argv
from moonmind.workflows.temporal.runtime.git_auth import build_isolated_git_environment
from tests.helpers import git_transport
from tests.unit.omnigent.test_oauth_profile_lifecycle import _runtime_for, _sandbox_id


@pytest.mark.parametrize(
    ("url", "allowed"),
    [
        ("https://github.com/owner/repo.git", True),
        ("https://GITHUB.COM/owner/repo.git/", True),
        ("https://github.com/owner/repo.git/info/lfs", True),
        ("https://github.com/owner/repo", True),
        ("https://github.com/other/repo.git", False),
        ("https://github.com/owner/repo.git-extra", False),
        ("https://github.com/owner/repo.git/other", False),
        ("https://github.com/owner/repo.git/info/lfs-other", False),
        ("https://example.invalid/owner/repo.git", False),
        ("http://github.com/owner/repo.git", False),
    ],
)
@pytest.mark.parametrize(
    "repository_url",
    [
        "https://github.com/owner/repo.git",
        "https://GITHUB.COM/owner/repo.git/",
        "https://github.com/%6fwner/repo.git",
    ],
)
def test_profile_credential_reader_enforces_repository(url, allowed, repository_url):
    environment = build_isolated_git_environment(
        "syntheticSelectedB",
        repository_url=repository_url,
        base_env={"PATH": os.environ["PATH"], "GITHUB_TOKEN": "syntheticAmbientA"},
    )
    result = subprocess.run(
        ["git", "credential", "fill"],
        input=f"url={url}\n\n",
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )
    assert ("password=syntheticSelectedB" in result.stdout) is allowed
    assert (result.returncode == 0) is allowed
    assert "syntheticAmbientA" not in result.stdout


@pytest.mark.parametrize(
    "repository_url", [None, "", "http://github.com/owner/repo.git"]
)
def test_profile_credential_requires_admitted_https_repository(repository_url):
    with pytest.raises(ValueError, match="admitted HTTPS repository"):
        build_isolated_git_environment(
            "syntheticSelectedB", repository_url=repository_url
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("consumer", ["profile", "daemon-script"])
@pytest.mark.parametrize("after_authentication", [False, True])
async def test_clone_rejects_redirect_before_contacting_new_repository(
    tmp_path: Path, monkeypatch, consumer, after_authentication
):
    """Real Git, loopback HTTPS and fake credentials; no provider account involved."""
    original_factory = git_transport._dumb_http_handler

    def redirect_factory(transport):
        original = original_factory(transport)

        class Handler(original):
            def do_GET(self):
                if self.path.startswith("/owner/repo.git/") and (
                    not after_authentication
                    or self.headers.get("Authorization")
                    == git_transport.basic_authorization("syntheticSelectedB")
                ):
                    transport.requests.append(
                        git_transport.RecordedRequest(
                            self.path,
                            {key.lower(): value for key, value in self.headers.items()},
                        )
                    )
                    self.send_response(302)
                    # A fixed fixture target avoids reflecting request bytes into
                    # headers while still redirecting to a different repository.
                    self.send_header("Location", "/other/repo.git/info/refs")
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
                super().do_GET()

        return Handler

    monkeypatch.setattr(git_transport, "_dumb_http_handler", redirect_factory)
    generator = git_transport.start_synthetic_github(
        tmp_path / "transport", required_token="syntheticSelectedB"
    )
    transport = next(generator)
    try:
        if consumer == "profile":
            monkeypatch.setenv("HTTPS_PROXY", transport.proxy_url)
            monkeypatch.setenv("https_proxy", transport.proxy_url)
            monkeypatch.setenv("GIT_SSL_CAINFO", str(transport.ca_path))
            with pytest.raises(OmnigentOAuthHostError, match="materialization failed"):
                await _runtime_for(tmp_path)._prepare_workspace(
                    workspace_locator={"kind": "sandbox", "workspaceId": _sandbox_id()},
                    current_workflow_id="workflow-1",
                    current_step_execution_id="step-1",
                    repository_source="https://github.com/owner/repo.git",
                    starting_branch="main",
                    github_token="syntheticSelectedB",
                )
        else:
            argv = build_daemon_git_clone_argv(
                volume="agent_workspaces",
                target_in_volume="run/repo",
                source="https://github.com/owner/repo.git",
                branch="main",
                image="fixture-unused",
                git_user_name="Fixture",
                git_user_email="fixture@example.invalid",
            )
            positional = argv[argv.index("--") + 1 :]
            target = tmp_path / "clone"
            shim = git_transport.write_proxy_git_shim(tmp_path, transport)
            result = await asyncio.to_thread(
                subprocess.run,
                [
                    "/bin/sh",
                    "-ceu",
                    argv[argv.index("-ceu") + 1],
                    "--",
                    positional[0],
                    positional[1],
                    str(target),
                    *positional[3:],
                ],
                input=b"syntheticSelectedB",
                env={"PATH": f"{shim}:{os.environ['PATH']}"},
                capture_output=True,
                check=False,
            )
            assert result.returncode != 0
        assert transport.requests
        assert not any(
            request.path.startswith("/other/") for request in transport.requests
        )
        if after_authentication:
            assert transport.sent_authorizations()
    finally:
        generator.close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "repository_url",
    [
        "https://github.com/owner/repo.git",
        "https://GITHUB.COM/owner/repo.git/",
        "https://github.com/owner/repo",
    ],
)
async def test_profile_clone_materializes_remote_lfs_with_scoped_credential(
    tmp_path, monkeypatch, repository_url
):
    """Exercise the real LFS batch reader and download using only loopback HTTPS."""
    import hashlib
    import json
    import shutil

    if shutil.which("git-lfs") is None:
        pytest.skip("requires Git LFS")
    payload = b"synthetic remote LFS content\n"
    oid = hashlib.sha256(payload).hexdigest()
    original_factory = git_transport._dumb_http_handler
    batch_authorized = []

    def lfs_factory(transport):
        original = original_factory(transport)

        class Handler(original):
            def do_POST(self):
                self.rfile.read(int(self.headers.get("Content-Length", "0")))
                transport.requests.append(
                    git_transport.RecordedRequest(
                        self.path,
                        {key.lower(): value for key, value in self.headers.items()},
                    )
                )
                if self.headers.get(
                    "Authorization"
                ) != git_transport.basic_authorization("syntheticSelectedB"):
                    self.send_response(401)
                    self.send_header("WWW-Authenticate", 'Basic realm="GitHub"')
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
                batch_authorized.append(self.path)
                body = json.dumps(
                    {
                        "objects": [
                            {
                                "oid": oid,
                                "size": len(payload),
                                "actions": {
                                    "download": {
                                        "href": "https://github.com/owner/repo.git/lfs-data"
                                    }
                                },
                            }
                        ]
                    }
                ).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/vnd.git-lfs+json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self):
                if self.path == "/owner/repo.git/lfs-data":
                    self.send_response(200)
                    self.send_header("Content-Length", str(len(payload)))
                    self.end_headers()
                    self.wfile.write(payload)
                    return
                super().do_GET()

        return Handler

    monkeypatch.setattr(git_transport, "_dumb_http_handler", lfs_factory)
    generator = git_transport.start_synthetic_github(
        tmp_path / "transport", required_token="syntheticSelectedB"
    )
    transport = next(generator)
    (transport.repository_root / "owner/repo").symlink_to(
        "repo.git", target_is_directory=True
    )
    try:
        seed = tmp_path / "transport/repository/seed"
        (seed / ".gitattributes").write_text(
            "asset.bin filter=lfs diff=lfs merge=lfs -text\n"
        )
        (seed / "asset.bin").write_text(
            f"version https://git-lfs.github.com/spec/v1\noid sha256:{oid}\nsize {len(payload)}\n"
        )
        environment = build_isolated_git_environment(
            None, base_env={"PATH": os.environ["PATH"]}
        )
        for args in [
            ["add", "."],
            [
                "-c",
                "user.name=Fixture",
                "-c",
                "user.email=fixture@example.invalid",
                "commit",
                "-qm",
                "LFS pointer",
            ],
            [
                "-c",
                "core.hooksPath=/dev/null",
                "push",
                str(transport.repository_root / "owner/repo.git"),
                "main",
            ],
        ]:
            await asyncio.to_thread(
                subprocess.run,
                ["git", "-C", str(seed), *args],
                env=environment,
                check=True,
                capture_output=True,
            )
        await asyncio.to_thread(
            subprocess.run,
            [
                "git",
                "-C",
                str(transport.repository_root / "owner/repo.git"),
                "update-server-info",
            ],
            check=True,
            capture_output=True,
        )
        monkeypatch.setenv("HTTPS_PROXY", transport.proxy_url)
        monkeypatch.setenv("https_proxy", transport.proxy_url)
        monkeypatch.setenv("GIT_SSL_CAINFO", str(transport.ca_path))
        monkeypatch.setenv("SSL_CERT_FILE", str(transport.ca_path))
        workspace = await _runtime_for(tmp_path)._prepare_workspace(
            workspace_locator={"kind": "sandbox", "workspaceId": _sandbox_id()},
            current_workflow_id="workflow-1",
            current_step_execution_id="step-1",
            repository_source=repository_url,
            starting_branch="main",
            github_token="syntheticSelectedB",
        )
        assert (workspace / "asset.bin").read_bytes() == payload
        assert batch_authorized == ["/owner/repo.git/info/lfs/objects/batch"]
    finally:
        generator.close()


@pytest.mark.asyncio
async def test_profile_invalid_credential_target_fails_before_git(
    tmp_path, monkeypatch
):
    from unittest.mock import AsyncMock

    runtime = _runtime_for(tmp_path)
    run = AsyncMock()
    monkeypatch.setattr(runtime, "_run", run)
    with pytest.raises(
        OmnigentOAuthHostError, match="credential target is invalid"
    ) as error:
        await runtime._materialize_repository(
            tmp_path / "workspace",
            repository_source="http://github.com/owner/repo.git",
            starting_branch="main",
            target_branch=None,
            checkout_commit=None,
            github_token="syntheticSelectedB",
        )
    assert error.value.code == "OMNIGENT_WORKSPACE_MATERIALIZATION_FAILED"
    run.assert_not_awaited()
