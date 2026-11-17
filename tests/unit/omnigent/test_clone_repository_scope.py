"""Local-only regression coverage for admitted clone transport boundaries."""

import asyncio
import os
import subprocess
from pathlib import Path

import pytest

from moonmind.omnigent.host_services.workspace import build_daemon_git_clone_argv
from moonmind.workflows.temporal.runtime.git_auth import build_isolated_git_environment
from tests.helpers import git_transport


@pytest.mark.parametrize(
    ("url", "allowed"),
    [
        ("https://github.com/owner/repo.git", True),
        ("https://GITHUB.COM/owner/repo.git/", True),
        ("https://github.com/owner/repo.git/info/lfs", True),
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
@pytest.mark.parametrize("after_authentication", [False, True])
async def test_clone_rejects_redirect_before_contacting_new_repository(
    tmp_path: Path, monkeypatch, after_authentication
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
