"""Bounded Docker CLI substrate shared by Omnigent host services."""

from __future__ import annotations

import json
from typing import Any

from moonmind.omnigent.harness_platform.failures import (
    HarnessPlatformError,
    HarnessPlatformFailure,
)
from moonmind.workflows.temporal.runtime.command_runner import run_runtime_command


class DockerCommandBackend:
    async def run(
        self,
        argv: list[str],
        *,
        input_bytes: bytes | None = None,
        timeout_seconds: float = 600.0,
        failure_code: HarnessPlatformFailure = HarnessPlatformFailure.OMNIGENT_HOST_LAUNCH_FAILED,
        check: bool = True,
    ) -> tuple[int, str, str]:
        code, stdout, stderr = await run_runtime_command(
            argv,
            input_bytes=input_bytes,
            timeout_seconds=timeout_seconds,
            output_limit_bytes=32_768,
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
        _code, out, _err = await self.run(
            ["docker", "inspect", container_name],
            failure_code=HarnessPlatformFailure.OMNIGENT_HARNESS_BUILD_MISMATCH,
        )
        try:
            parsed = json.loads(out)
            if (
                not isinstance(parsed, list)
                or len(parsed) != 1
                or not isinstance(parsed[0], dict)
            ):
                raise ValueError("unexpected inspect shape")
            return parsed[0]
        except (json.JSONDecodeError, ValueError) as exc:
            raise HarnessPlatformError(
                "Docker returned malformed host inspection evidence",
                code=HarnessPlatformFailure.OMNIGENT_HARNESS_BUILD_MISMATCH,
            ) from exc


__all__ = ["DockerCommandBackend"]
