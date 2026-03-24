"""Unit tests for TmateSessionManager abstraction.

Tests cover:
  - TmateEndpoints construction
  - TmateServerConfig.from_env() with various env combos
  - Config file generation with and without server config
  - is_available() with and without tmate on PATH
  - teardown() cleanup logic
  - Endpoint key constants
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from moonmind.workflows.temporal.runtime.tmate_session import (
    TmateEndpoints,
    TmateServerConfig,
    TmateSessionManager,
    _ENDPOINT_KEYS,
    _TMATE_FOREGROUND_RESTART_OFF,
)


# ---------------------------------------------------------------------------
# TmateEndpoints
# ---------------------------------------------------------------------------


class TestTmateEndpoints:
    def test_construction_full(self):
        ep = TmateEndpoints(
            session_name="mm-abc123",
            socket_path="/tmp/moonmind/tmate/mm-abc123.sock",
            attach_ro="ssh ro@example.com",
            attach_rw="ssh rw@example.com",
            web_ro="https://tmate.io/t/ro-token",
            web_rw="https://tmate.io/t/rw-token",
        )
        assert ep.session_name == "mm-abc123"
        assert ep.socket_path == "/tmp/moonmind/tmate/mm-abc123.sock"
        assert ep.attach_ro == "ssh ro@example.com"
        assert ep.attach_rw == "ssh rw@example.com"
        assert ep.web_ro == "https://tmate.io/t/ro-token"
        assert ep.web_rw == "https://tmate.io/t/rw-token"

    def test_construction_partial(self):
        ep = TmateEndpoints(
            session_name="mm-test",
            socket_path="/tmp/test.sock",
        )
        assert ep.session_name == "mm-test"
        assert ep.attach_ro is None
        assert ep.web_rw is None

    def test_has_all_six_fields(self):
        """DOC-REQ-002: Endpoints must have all 6 fields."""
        import dataclasses

        field_names = {f.name for f in dataclasses.fields(TmateEndpoints)}
        assert field_names == {
            "session_name",
            "socket_path",
            "attach_ro",
            "attach_rw",
            "web_ro",
            "web_rw",
        }


# ---------------------------------------------------------------------------
# TmateServerConfig
# ---------------------------------------------------------------------------


class TestTmateServerConfig:
    def test_from_env_none_when_unset(self):
        """DOC-REQ-013: Returns None when host env var is unset."""
        with patch.dict(os.environ, {}, clear=True):
            assert TmateServerConfig.from_env() is None

    def test_from_env_none_when_empty(self):
        with patch.dict(os.environ, {"MOONMIND_TMATE_SERVER_HOST": ""}, clear=True):
            assert TmateServerConfig.from_env() is None

    def test_from_env_host_only(self):
        with patch.dict(
            os.environ,
            {"MOONMIND_TMATE_SERVER_HOST": "tmate.example.com"},
            clear=True,
        ):
            cfg = TmateServerConfig.from_env()
            assert cfg is not None
            assert cfg.host == "tmate.example.com"
            assert cfg.port == 22
            assert cfg.rsa_fingerprint == ""
            assert cfg.ed25519_fingerprint == ""

    def test_from_env_full_config(self):
        with patch.dict(
            os.environ,
            {
                "MOONMIND_TMATE_SERVER_HOST": "tmate.internal",
                "MOONMIND_TMATE_SERVER_PORT": "2222",
                "MOONMIND_TMATE_SERVER_RSA_FINGERPRINT": "SHA256:rsa-abc",
                "MOONMIND_TMATE_SERVER_ED25519_FINGERPRINT": "SHA256:ed-xyz",
            },
            clear=True,
        ):
            cfg = TmateServerConfig.from_env()
            assert cfg is not None
            assert cfg.host == "tmate.internal"
            assert cfg.port == 2222
            assert cfg.rsa_fingerprint == "SHA256:rsa-abc"
            assert cfg.ed25519_fingerprint == "SHA256:ed-xyz"


# ---------------------------------------------------------------------------
# Config file generation
# ---------------------------------------------------------------------------


class TestConfigGeneration:
    def test_writes_foreground_restart_off(self, tmp_path: Path):
        """DOC-REQ-006: Config must contain foreground restart directive."""
        conf = tmp_path / "test.conf"
        TmateSessionManager._write_config(conf, None)
        content = conf.read_text()
        assert _TMATE_FOREGROUND_RESTART_OFF in content

    def test_writes_server_config_directives(self, tmp_path: Path):
        """DOC-REQ-005, DOC-REQ-006: Server set-option directives."""
        conf = tmp_path / "test.conf"
        server = TmateServerConfig(
            host="tmate.internal",
            port=2222,
            rsa_fingerprint="SHA256:rsa",
            ed25519_fingerprint="SHA256:ed",
        )
        TmateSessionManager._write_config(conf, server)
        content = conf.read_text()
        assert "set -g tmate-server-host tmate.internal" in content
        assert "set -g tmate-server-port 2222" in content
        assert "set -g tmate-server-rsa-fingerprint SHA256:rsa" in content
        assert "set -g tmate-server-ed25519-fingerprint SHA256:ed" in content

    def test_omits_empty_fingerprints(self, tmp_path: Path):
        conf = tmp_path / "test.conf"
        server = TmateServerConfig(host="tmate.internal")
        TmateSessionManager._write_config(conf, server)
        content = conf.read_text()
        assert "tmate-server-host" in content
        assert "tmate-server-rsa-fingerprint" not in content
        assert "tmate-server-ed25519-fingerprint" not in content


# ---------------------------------------------------------------------------
# is_available
# ---------------------------------------------------------------------------


class TestIsAvailable:
    def test_available_when_tmate_on_path(self):
        with patch("shutil.which", return_value="/usr/bin/tmate"):
            assert TmateSessionManager.is_available() is True

    def test_not_available_when_tmate_missing(self):
        with patch("shutil.which", return_value=None):
            assert TmateSessionManager.is_available() is False


# ---------------------------------------------------------------------------
# TmateSessionManager properties
# ---------------------------------------------------------------------------


class TestManagerProperties:
    def test_socket_and_config_paths(self):
        mgr = TmateSessionManager(
            session_name="mm-test",
            socket_dir=Path("/tmp/test"),
        )
        assert mgr.socket_path == Path("/tmp/test/mm-test.sock")
        assert mgr.config_path == Path("/tmp/test/mm-test.conf")

    def test_endpoints_none_before_start(self):
        mgr = TmateSessionManager(session_name="mm-test")
        assert mgr.endpoints is None

    def test_exit_code_path_none_before_start(self):
        mgr = TmateSessionManager(session_name="mm-test")
        assert mgr.exit_code_path is None


# ---------------------------------------------------------------------------
# teardown
# ---------------------------------------------------------------------------


class TestTeardown:
    @pytest.mark.asyncio
    async def test_teardown_removes_files(self, tmp_path: Path):
        """DOC-REQ-010: teardown removes socket, config, exit-code files."""
        mgr = TmateSessionManager(
            session_name="mm-clean",
            socket_dir=tmp_path,
        )
        # Create the files that teardown should clean up.
        sock = tmp_path / "mm-clean.sock"
        conf = tmp_path / "mm-clean.conf"
        exit_file = tmp_path / "mm-clean.exit"
        for f in (sock, conf, exit_file):
            f.write_text("test")
        mgr._exit_code_path_value = exit_file

        await mgr.teardown()

        assert not sock.exists()
        assert not conf.exists()
        assert not exit_file.exists()

    @pytest.mark.asyncio
    async def test_teardown_no_op_when_not_started(self, tmp_path: Path):
        """Teardown on unstarted session should be a no-op."""
        mgr = TmateSessionManager(
            session_name="mm-noop",
            socket_dir=tmp_path,
        )
        # Should not raise
        await mgr.teardown()

    @pytest.mark.asyncio
    async def test_teardown_terminates_process(self, tmp_path: Path):
        mgr = TmateSessionManager(
            session_name="mm-kill",
            socket_dir=tmp_path,
        )
        mock_proc = MagicMock()
        mock_proc.returncode = None
        mock_proc.terminate = MagicMock()
        mock_proc.kill = MagicMock()
        mock_proc.wait = AsyncMock(return_value=0)
        mgr._process = mock_proc

        await mgr.teardown()

        mock_proc.terminate.assert_called_once()
        assert mgr._process is None


# ---------------------------------------------------------------------------
# Endpoint keys
# ---------------------------------------------------------------------------


class TestEndpointKeys:
    def test_endpoint_keys_mapping(self):
        """Verify the shared endpoint key mapping is correct."""
        assert _ENDPOINT_KEYS == {
            "attach_ro": "tmate_ssh_ro",
            "attach_rw": "tmate_ssh",
            "web_ro": "tmate_web_ro",
            "web_rw": "tmate_web",
        }

    def test_all_four_endpoint_types(self):
        """DOC-REQ-004: Must extract all four endpoint types."""
        assert len(_ENDPOINT_KEYS) == 4


# ---------------------------------------------------------------------------
# start() orchestration
# ---------------------------------------------------------------------------


class TestStart:
    @pytest.mark.asyncio
    async def test_start_orchestration(self, tmp_path: Path):
        """Verify start() creates config, launches subprocess, extracts endpoints."""
        mgr = TmateSessionManager(
            session_name="mm-orch",
            socket_dir=tmp_path,
        )

        fake_proc = MagicMock()
        fake_proc.pid = 42
        fake_proc.returncode = None

        endpoint_values = {
            "attach_ro": "ssh ro@host",
            "attach_rw": "ssh rw@host",
            "web_ro": "https://host/ro",
            "web_rw": "https://host/rw",
        }

        with (
            patch(
                "moonmind.workflows.temporal.runtime.tmate_session.asyncio.create_subprocess_exec",
                new_callable=AsyncMock,
                return_value=fake_proc,
            ),
            patch.object(
                TmateSessionManager,
                "_wait_ready",
                new_callable=AsyncMock,
            ),
            patch.object(
                TmateSessionManager,
                "_extract_endpoints",
                new_callable=AsyncMock,
                return_value=endpoint_values,
            ),
        ):
            caller_env = {"EXISTING": "val"}
            endpoints = await mgr.start(
                command=["echo", "hello"],
                env=caller_env,
                cwd=tmp_path,
            )

        # Config file should have been written.
        assert mgr.config_path.exists()

        # Endpoints should be populated.
        assert endpoints.session_name == "mm-orch"
        assert endpoints.attach_ro == "ssh ro@host"
        assert endpoints.web_rw == "https://host/rw"

        # The process property should expose the subprocess.
        assert mgr.process is fake_proc

        # Exit code path should be set (exit_code_capture defaults to True).
        assert mgr.exit_code_path is not None
        assert str(mgr.exit_code_path).endswith(".exit")

        # Caller's env dict must NOT have been mutated.
        assert "MM_EXIT_FILE" not in caller_env

    @pytest.mark.asyncio
    async def test_start_graceful_on_endpoint_failure(self, tmp_path: Path):
        """start() should return partial endpoints when extraction fails."""
        mgr = TmateSessionManager(
            session_name="mm-fail",
            socket_dir=tmp_path,
        )

        fake_proc = MagicMock()
        fake_proc.pid = 43
        fake_proc.returncode = None

        with (
            patch(
                "moonmind.workflows.temporal.runtime.tmate_session.asyncio.create_subprocess_exec",
                new_callable=AsyncMock,
                return_value=fake_proc,
            ),
            patch.object(
                TmateSessionManager,
                "_wait_ready",
                new_callable=AsyncMock,
                side_effect=RuntimeError("tmate wait failed"),
            ),
        ):
            endpoints = await mgr.start(command="echo test", cwd=tmp_path)

        # Should still return endpoints, just with None values.
        assert endpoints.session_name == "mm-fail"
        assert endpoints.attach_ro is None
        assert endpoints.web_ro is None

    @pytest.mark.asyncio
    async def test_wait_ready_raises_on_nonzero_exit(self):
        """_wait_ready raises RuntimeError when tmate exits non-zero."""
        fake_proc = MagicMock()
        fake_proc.wait = AsyncMock(return_value=1)

        with patch(
            "moonmind.workflows.temporal.runtime.tmate_session.asyncio.create_subprocess_exec",
            new_callable=AsyncMock,
            return_value=fake_proc,
        ):
            with pytest.raises(RuntimeError, match="exited with code 1"):
                await TmateSessionManager._wait_ready(
                    Path("/tmp/test.sock"), timeout=5.0
                )

