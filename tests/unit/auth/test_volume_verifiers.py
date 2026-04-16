"""Tests for volume_verifiers module."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from moonmind.workflows.temporal.runtime.providers.volume_verifiers import (
    PROVIDER_CREDENTIAL_PATHS,
    verify_volume_credentials,
)


class TestProviderCredentialPaths:
    """Verify per-provider credential path definitions."""

    def test_gemini_paths_defined(self) -> None:
        assert "gemini_cli" in PROVIDER_CREDENTIAL_PATHS
        paths = PROVIDER_CREDENTIAL_PATHS["gemini_cli"]
        assert len(paths) >= 1
        assert any("gemini" in p or "google-cloud-sdk" in p for p in paths)

    def test_codex_paths_defined(self) -> None:
        assert "codex_cli" in PROVIDER_CREDENTIAL_PATHS
        paths = PROVIDER_CREDENTIAL_PATHS["codex_cli"]
        assert paths == ("auth.json", "config.toml")

    def test_claude_paths_defined(self) -> None:
        assert "claude_code" in PROVIDER_CREDENTIAL_PATHS
        paths = PROVIDER_CREDENTIAL_PATHS["claude_code"]
        assert len(paths) >= 1
        assert any("claude" in p for p in paths)


class TestVerifyVolumeCredentials:
    """Test volume credential verification logic."""

    @pytest.mark.asyncio
    async def test_unknown_runtime_returns_verified(self) -> None:
        """Unknown runtimes skip verification and return verified=True."""
        result = await verify_volume_credentials(
            runtime_id="unknown_runtime",
            volume_ref="test_volume",
        )
        assert result["verified"] is True
        assert result["status"] == "skipped"
        assert result["reason"] == "no_credential_paths_defined"

    @pytest.mark.asyncio
    async def test_missing_volume_ref_returns_not_verified(self) -> None:
        result = await verify_volume_credentials(
            runtime_id="gemini_cli",
            volume_ref="",
        )
        assert result["verified"] is False
        assert result["reason"] == "no_volume_ref"

    @pytest.mark.asyncio
    async def test_docker_not_available(self) -> None:
        """When Docker isn't installed, returns not verified with reason."""
        with patch(
            "asyncio.create_subprocess_exec",
            side_effect=FileNotFoundError("docker not found"),
        ):
            result = await verify_volume_credentials(
                runtime_id="gemini_cli",
                volume_ref="test_volume",
            )
        assert result["verified"] is False
        assert result["reason"] == "docker_not_available"

    @pytest.mark.asyncio
    async def test_docker_timeout(self) -> None:
        async def slow_exec(*args, **kwargs):
            mock_proc = AsyncMock()
            mock_proc.communicate = AsyncMock(
                side_effect=asyncio.TimeoutError()
            )
            return mock_proc

        with patch(
            "asyncio.create_subprocess_exec",
            side_effect=asyncio.TimeoutError(),
        ):
            result = await verify_volume_credentials(
                runtime_id="gemini_cli",
                volume_ref="test_volume",
            )
        assert result["verified"] is False
        assert result["reason"] == "timeout"

    @pytest.mark.asyncio
    async def test_successful_verification(self) -> None:
        """Simulate Docker run finding credentials."""
        mock_process = AsyncMock()
        mock_process.communicate = MagicMock(return_value="dummy")
        mock_process.returncode = 0

        with patch(
            "moonmind.workflows.temporal.runtime.providers.volume_verifiers.asyncio.create_subprocess_exec",
            return_value=mock_process,
        ), patch(
            "moonmind.workflows.temporal.runtime.providers.volume_verifiers.asyncio.wait_for",
            new_callable=AsyncMock,
            return_value=(
                b"FOUND:.config/gemini/credentials.json\nMISSING:.config/google-cloud-sdk/application_default_credentials.json\n",
                b"",
            )
        ):
            result = await verify_volume_credentials(
                runtime_id="gemini_cli",
                volume_ref="gemini_auth_volume",
            )

        assert result["verified"] is True
        assert result["status"] == "verified"
        assert result["credentials_found_count"] == 1
        assert result["credentials_missing_count"] == 1
        assert "found" not in result
        assert "missing" not in result

    @pytest.mark.asyncio
    async def test_codex_verification_checks_volume_root_when_mounted_as_codex_home(
        self,
    ) -> None:
        mock_process = AsyncMock()
        mock_process.communicate = MagicMock(return_value="dummy")
        mock_process.returncode = 0

        with patch(
            "moonmind.workflows.temporal.runtime.providers.volume_verifiers.asyncio.create_subprocess_exec",
            return_value=mock_process,
        ) as exec_mock, patch(
            "moonmind.workflows.temporal.runtime.providers.volume_verifiers.asyncio.wait_for",
            new_callable=AsyncMock,
            return_value=(
                b"FOUND:auth.json\nMISSING:config.toml\n",
                b"",
            ),
        ):
            result = await verify_volume_credentials(
                runtime_id="codex_cli",
                volume_ref="codex_auth_volume",
                volume_mount_path="/home/app/.codex",
            )

        assert result["verified"] is True
        assert result["status"] == "verified"
        assert result["credentials_found_count"] == 1
        assert result["credentials_missing_count"] == 1
        assert "found" not in result
        assert "missing" not in result
        docker_args = exec_mock.call_args.args
        assert "-v" in docker_args
        assert "codex_auth_volume:/home/app/.codex:ro" in docker_args

    @pytest.mark.asyncio
    async def test_no_credentials_found(self) -> None:
        """Simulate Docker run finding no credentials."""
        mock_process = AsyncMock()
        mock_process.communicate = MagicMock(return_value="dummy")
        mock_process.returncode = 0

        with patch(
            "moonmind.workflows.temporal.runtime.providers.volume_verifiers.asyncio.create_subprocess_exec",
            return_value=mock_process,
        ), patch(
            "moonmind.workflows.temporal.runtime.providers.volume_verifiers.asyncio.wait_for",
            new_callable=AsyncMock,
            return_value=(
                b"MISSING:.config/gemini/credentials.json\nMISSING:.config/google-cloud-sdk/application_default_credentials.json\n",
                b"",
            )
        ):
            result = await verify_volume_credentials(
                runtime_id="gemini_cli",
                volume_ref="gemini_auth_volume",
            )

        assert result["verified"] is False
        assert result["status"] == "failed"
        assert result["reason"] == "no_credentials_found"
        assert result["credentials_found_count"] == 0
        assert result["credentials_missing_count"] == 2
        assert "found" not in result
        assert "missing" not in result
