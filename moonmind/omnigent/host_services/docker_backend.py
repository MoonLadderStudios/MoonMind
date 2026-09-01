"""Bounded Docker CLI substrate shared by Omnigent host services."""

from __future__ import annotations

import json
from typing import Any

from moonmind.omnigent.harness_platform.failures import (
    HarnessPlatformError,
    HarnessPlatformFailure,
)
from moonmind.workflows.temporal.runtime.command_runner import run_runtime_command


_DEFAULT_OUTPUT_LIMIT_BYTES = 32_768


class DockerCommandBackend:
    async def run(
        self,
        argv: list[str],
        *,
        input_bytes: bytes | None = None,
        timeout_seconds: float = 600.0,
        failure_code: HarnessPlatformFailure = HarnessPlatformFailure.OMNIGENT_HOST_LAUNCH_FAILED,
        check: bool = True,
        output_limit_bytes: int | None = _DEFAULT_OUTPUT_LIMIT_BYTES,
    ) -> tuple[int, str, str]:
        # ``None`` disables the head-truncating retention bound for callers
        # that bound the output themselves (for example a log tail that must
        # keep the newest bytes). The subprocess is still line-bounded by the
        # command and fully read either way.
        code, stdout, stderr = await run_runtime_command(
            argv,
            input_bytes=input_bytes,
            timeout_seconds=timeout_seconds,
            output_limit_bytes=(
                None if output_limit_bytes is None else max(0, int(output_limit_bytes))
            ),
        )
        out = stdout.decode("utf-8", errors="replace")
        err = stderr.decode("utf-8", errors="replace")
        if check and code != 0:
            raise HarnessPlatformError(
                f"Docker host operation failed: {(err or out)[:512]}",
                code=failure_code,
            )
        return code, out, err

    async def inspect_container(self, container_name: str) -> dict[str, Any]:
        """Return the subset of container inspect data needed for attestation.

        Uses targeted ``--format`` probes instead of ``{{json .}}`` because
        WSL2 Docker's JSON serialisation breaks on containers whose Args
        contain shell scripts with backslash-escaped quotes and variable
        expansions.
        """

        probes = {
            "Config.Image": "{{.Config.Image}}",
            "Config.Labels": "{{json .Config.Labels}}",
            "Config.Env": "{{json .Config.Env}}",
            "Mounts": "{{json .Mounts}}",
        }
        result: dict[str, Any] = {"Config": {}}
        for key, fmt in probes.items():
            _code, out, _err = await self.run(
                ["docker", "inspect", "--format", fmt, container_name],
                failure_code=HarnessPlatformFailure.OMNIGENT_HARNESS_BUILD_MISMATCH,
            )
            text = out.strip()
            if key == "Config.Image":
                result["Config"]["Image"] = text
            elif key == "Config.Labels":
                try:
                    result["Config"]["Labels"] = json.loads(text) or {}
                except json.JSONDecodeError:
                    result["Config"]["Labels"] = {}
            elif key == "Config.Env":
                try:
                    env_list = json.loads(text) or []
                    env_dict: dict[str, str] = {}
                    for item in env_list:
                        k, _, v = str(item).partition("=")
                        env_dict[k] = v
                    result["Config"]["Env"] = env_dict
                    result["Config"]["_EnvList"] = env_list
                except json.JSONDecodeError:
                    result["Config"]["Env"] = {}
                    result["Config"]["_EnvList"] = []
            elif key == "Mounts":
                try:
                    result["Mounts"] = json.loads(text) or []
                except json.JSONDecodeError:
                    result["Mounts"] = []
        return result


__all__ = ["DockerCommandBackend"]
