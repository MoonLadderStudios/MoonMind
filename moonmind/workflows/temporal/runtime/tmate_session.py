"""Shared tmate session lifecycle manager.

Target location per TmateSessionArchitecture.md §4.1.

Provides:
  - ``TmateServerConfig``  – self-hosted relay configuration
  - ``TmateEndpoints``     – extracted session endpoints
  - ``TmateSessionManager`` – start / teardown / endpoint extraction
"""

from __future__ import annotations

import asyncio
import logging
import os
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# Config directive to prevent tmate from restarting the inner session.
_TMATE_FOREGROUND_RESTART_OFF = "set tmate-foreground-restart 0\n"

# Mapping from our endpoint field names to tmate format strings.
_ENDPOINT_KEYS: dict[str, str] = {
    "attach_ro": "tmate_ssh_ro",
    "attach_rw": "tmate_ssh",
    "web_ro": "tmate_web_ro",
    "web_rw": "tmate_web",
}


@dataclass
class TmateServerConfig:
    """Configuration for a self-hosted tmate relay server.

    When provided to ``TmateSessionManager``, the manager writes
    ``set-option`` directives into the per-session config file so
    sessions connect to the private relay instead of ``tmate.io``.
    """

    host: str
    port: int = 22
    rsa_fingerprint: str = ""
    ed25519_fingerprint: str = ""

    @classmethod
    def from_env(cls) -> TmateServerConfig | None:
        """Build config from ``MOONMIND_TMATE_SERVER_*`` env vars.

        Returns ``None`` when ``MOONMIND_TMATE_SERVER_HOST`` is unset or
        empty — callers should fall back to the public ``tmate.io``
        relay.
        """
        host = os.environ.get("MOONMIND_TMATE_SERVER_HOST", "").strip()
        if not host:
            return None
        return cls(
            host=host,
            port=int(os.environ.get("MOONMIND_TMATE_SERVER_PORT", "22")),
            rsa_fingerprint=os.environ.get(
                "MOONMIND_TMATE_SERVER_RSA_FINGERPRINT", ""
            ),
            ed25519_fingerprint=os.environ.get(
                "MOONMIND_TMATE_SERVER_ED25519_FINGERPRINT", ""
            ),
        )


@dataclass
class TmateEndpoints:
    """Extracted tmate session endpoints.

    Fields may be ``None`` if extraction failed — callers should treat
    partial endpoints gracefully (e.g., the session is usable even
    without ``web_ro``).
    """

    session_name: str
    socket_path: str
    attach_ro: Optional[str] = None
    attach_rw: Optional[str] = None
    web_ro: Optional[str] = None
    web_rw: Optional[str] = None


@dataclass
class TmateSessionManager:
    """Manages a single tmate session lifecycle.

    Usage::

        mgr = TmateSessionManager(session_name="mm-abc123")
        endpoints = await mgr.start(["gemini", "--yolo"])
        # … agent runs inside tmate …
        await mgr.teardown()
    """

    session_name: str
    socket_dir: Path = field(default_factory=lambda: Path("/tmp/moonmind/tmate"))
    server_config: TmateServerConfig | None = field(default=None)

    # Internal state — not part of the public constructor.
    _process: Optional[asyncio.subprocess.Process] = field(
        default=None, init=False, repr=False
    )
    _endpoints: Optional[TmateEndpoints] = field(
        default=None, init=False, repr=False
    )
    _exit_code_path_value: Optional[Path] = field(
        default=None, init=False, repr=False
    )

    # -- Public API -----------------------------------------------------------

    @staticmethod
    def is_available() -> bool:
        """Return ``True`` if the ``tmate`` binary is on PATH."""
        return shutil.which("tmate") is not None

    @property
    def endpoints(self) -> TmateEndpoints | None:
        """Last extracted endpoints, or ``None`` if not yet started."""
        return self._endpoints

    @property
    def exit_code_path(self) -> Path | None:
        """Path to the exit code file when ``exit_code_capture`` is enabled."""
        return self._exit_code_path_value

    @property
    def socket_path(self) -> Path:
        """Computed socket file path."""
        return self.socket_dir / f"{self.session_name}.sock"

    @property
    def config_path(self) -> Path:
        """Computed config file path."""
        return self.socket_dir / f"{self.session_name}.conf"

    async def start(
        self,
        command: list[str] | str | None = None,
        *,
        env: dict[str, str] | None = None,
        cwd: Path | str | None = None,
        exit_code_capture: bool = True,
        timeout_seconds: float = 30.0,
    ) -> TmateEndpoints:
        """Start a tmate session wrapping the given command.

        1. Creates socket dir and config file.
        2. Launches ``tmate -S <sock> -f <conf> -F new-session …``.
        3. Waits for readiness via ``tmate wait tmate-ready``.
        4. Extracts all four endpoint types.
        5. Returns ``TmateEndpoints`` (partial if extraction fails).
        """
        # Resolve server config from env if not explicitly provided.
        server_config = self.server_config or TmateServerConfig.from_env()

        # Ensure socket directory exists.
        self.socket_dir.mkdir(parents=True, exist_ok=True)

        # Clean up any stale artifacts.
        sock = self.socket_path
        conf = self.config_path
        exit_file = self.socket_dir / f"{self.session_name}.exit"

        for path in (sock, conf, exit_file):
            path.unlink(missing_ok=True)

        # Write config file.
        self._write_config(conf, server_config)

        if exit_code_capture:
            self._exit_code_path_value = exit_file
            if env is None:
                env = dict(os.environ)
            env["MM_EXIT_FILE"] = str(exit_file)

        # Build the inner command string.
        import shlex

        if command is None:
            inner_cmd = "bash"
        elif isinstance(command, str):
            inner_cmd = command
        else:
            inner_cmd = shlex.join(command)

        # Wrap the command to capture exit code.
        if exit_code_capture:
            wrapped = (
                f"{inner_cmd}\n"
                f"rc=$?\n"
                f"printf '%s\\n' \"$rc\" > \"$MM_EXIT_FILE\"\n"
                f"exit 0\n"
            )
        else:
            wrapped = inner_cmd

        tmate_cmd = [
            "tmate",
            "-S", str(sock),
            "-f", str(conf),
            "-F",
            "new-session",
            "-A",
            "-s", self.session_name,
            wrapped,
        ]

        self._process = await asyncio.create_subprocess_exec(
            *tmate_cmd,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
            cwd=str(cwd) if cwd else None,
        )

        # Wait for tmate readiness.
        endpoints_dict: dict[str, str | None] = {}
        try:
            await self._wait_ready(sock, timeout_seconds)
            endpoints_dict = await self._extract_endpoints(sock)
        except Exception:
            logger.warning(
                "Failed to fetch tmate endpoints for session %s",
                self.session_name,
                exc_info=True,
            )

        self._endpoints = TmateEndpoints(
            session_name=self.session_name,
            socket_path=str(sock),
            attach_ro=endpoints_dict.get("attach_ro"),
            attach_rw=endpoints_dict.get("attach_rw"),
            web_ro=endpoints_dict.get("web_ro"),
            web_rw=endpoints_dict.get("web_rw"),
        )

        logger.info(
            "Tmate session %s started: web_ro=%s",
            self.session_name,
            self._endpoints.web_ro or "(pending)",
        )

        return self._endpoints

    async def teardown(self) -> None:
        """Kill the tmate session and clean up socket/config/exit-code files."""
        # Kill the tmate process if still running.
        if self._process is not None and self._process.returncode is None:
            try:
                self._process.terminate()
                try:
                    await asyncio.wait_for(self._process.wait(), timeout=5.0)
                except asyncio.TimeoutError:
                    self._process.kill()
            except ProcessLookupError:
                pass
            except Exception:
                logger.warning(
                    "Failed to terminate tmate process for session %s",
                    self.session_name,
                    exc_info=True,
                )

        # Clean up filesystem artifacts.
        for path in (self.socket_path, self.config_path):
            try:
                path.unlink(missing_ok=True)
            except Exception:
                logger.debug("Failed to remove %s", path, exc_info=True)

        if self._exit_code_path_value is not None:
            try:
                self._exit_code_path_value.unlink(missing_ok=True)
            except Exception:
                logger.debug(
                    "Failed to remove %s",
                    self._exit_code_path_value,
                    exc_info=True,
                )

        self._process = None
        logger.info("Tmate session %s torn down", self.session_name)

    # -- Internal helpers -----------------------------------------------------

    @staticmethod
    def _write_config(
        config_path: Path,
        server_config: TmateServerConfig | None,
    ) -> None:
        """Write the per-session tmate config file."""
        lines = [_TMATE_FOREGROUND_RESTART_OFF]

        if server_config is not None:
            lines.append(
                f"set -g tmate-server-host {server_config.host}\n"
            )
            lines.append(
                f"set -g tmate-server-port {server_config.port}\n"
            )
            if server_config.rsa_fingerprint:
                lines.append(
                    f"set -g tmate-server-rsa-fingerprint {server_config.rsa_fingerprint}\n"
                )
            if server_config.ed25519_fingerprint:
                lines.append(
                    f"set -g tmate-server-ed25519-fingerprint {server_config.ed25519_fingerprint}\n"
                )

        config_path.write_text("".join(lines), encoding="utf-8")

    @staticmethod
    async def _wait_ready(socket_path: Path, timeout: float) -> None:
        """Block until ``tmate wait tmate-ready`` succeeds or timeout."""
        async def _inner() -> None:
            proc = await asyncio.create_subprocess_exec(
                "tmate", "-S", str(socket_path), "wait", "tmate-ready",
            )
            await proc.wait()

        await asyncio.wait_for(_inner(), timeout=timeout)

    @staticmethod
    async def _extract_endpoints(
        socket_path: Path,
    ) -> dict[str, str | None]:
        """Extract all four endpoint types from a ready tmate session."""
        results: dict[str, str | None] = {}

        for our_key, tmate_key in _ENDPOINT_KEYS.items():
            try:
                proc = await asyncio.create_subprocess_exec(
                    "tmate",
                    "-S", str(socket_path),
                    "display", "-p", f"#{{{tmate_key}}}",
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                stdout, _ = await proc.communicate()
                value = stdout.decode("utf-8", errors="replace").strip()
                results[our_key] = value if value else None
            except Exception:
                logger.debug(
                    "Failed to extract endpoint %s", our_key, exc_info=True
                )
                results[our_key] = None

        return results
