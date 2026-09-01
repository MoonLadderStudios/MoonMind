"""Cleanup-boundary tests for the generic Omnigent host container service."""

from __future__ import annotations

import pytest

from moonmind.omnigent.harness_platform.failures import (
    HarnessPlatformError,
    HarnessPlatformFailure,
)
from moonmind.omnigent.host_services.cleanup import (
    _HOST_LOG_READ_LIMIT_BYTES,
    _HOST_LOG_RETAINED_BYTES,
    _HOST_LOG_TAIL_LINES,
    _HOST_LOG_TRUNCATION_MARKER,
    DockerOmnigentHostCleanupService,
)

_LEASE = "omnigent-host-lease:sha256:" + "a" * 64
_GENERATION = 3


class _Backend:
    """Deterministic Docker CLI double keyed on the leading argv words."""

    def __init__(
        self,
        *,
        owner: str = f"{_LEASE}|{_GENERATION}",
        logs: str = "",
        logs_stderr: str = "",
        logs_exit_code: int = 0,
        logs_raises: Exception | None = None,
    ) -> None:
        self.owner = owner
        self.logs = logs
        self.logs_stderr = logs_stderr
        self.logs_exit_code = logs_exit_code
        self.logs_raises = logs_raises
        self.commands: list[list[str]] = []
        self.kwargs: list[dict[str, object]] = []

    async def run(self, argv, **kwargs):
        self.commands.append(list(argv))
        self.kwargs.append(dict(kwargs))
        if argv[:2] == ["docker", "inspect"]:
            return 0, self.owner + "\n", ""
        if argv[:3] == ["docker", "volume", "inspect"]:
            return 0, self.owner + "\n", ""
        if argv[:2] == ["docker", "logs"]:
            if self.logs_raises is not None:
                raise self.logs_raises
            return self.logs_exit_code, self.logs, self.logs_stderr
        return 0, "", ""


async def _cleanup(backend: _Backend) -> dict[str, object]:
    return await DockerOmnigentHostCleanupService(backend).cleanup(
        container_name="mm-host-1",
        host_lease_ref=_LEASE,
        host_lease_generation=_GENERATION,
        state_volume_ref="mm-state-1",
        control_volume_ref="mm-control-1",
    )


@pytest.mark.asyncio
async def test_cleanup_captures_bounded_log_tail_before_removing_container() -> None:
    backend = _Backend(logs="runner: turn accepted\nopencode: exited 1\n")

    evidence = await _cleanup(backend)

    kinds = [tuple(command[:3]) for command in backend.commands]
    logs_index = kinds.index(("docker", "logs", "--tail"))
    remove_index = kinds.index(("docker", "rm", "-f"))
    assert logs_index < remove_index, "logs must be read before the container is gone"
    assert backend.commands[logs_index] == [
        "docker",
        "logs",
        "--tail",
        str(_HOST_LOG_TAIL_LINES),
        "mm-host-1",
    ]
    logs_kwargs = backend.kwargs[logs_index]
    assert logs_kwargs["check"] is False
    assert logs_kwargs["output_limit_bytes"] == _HOST_LOG_READ_LIMIT_BYTES
    assert evidence["containerRemoved"] is True
    assert evidence["hostLogs"] == "runner: turn accepted\nopencode: exited 1\n"
    assert evidence["hostLogsTruncated"] is False
    assert "hostLogsCaptureError" not in evidence


@pytest.mark.asyncio
async def test_cleanup_keeps_stderr_and_retains_only_the_most_recent_bytes() -> None:
    oldest = "first line that must be dropped\n"
    body = "".join(f"line {index}\n" for index in range(20_000))
    backend = _Backend(logs=oldest + body, logs_stderr="harness stderr tail\n")

    evidence = await _cleanup(backend)

    logs = str(evidence["hostLogs"])
    assert evidence["hostLogsTruncated"] is True
    assert logs.startswith(_HOST_LOG_TRUNCATION_MARKER)
    assert oldest not in logs
    assert logs.endswith("harness stderr tail\n")
    assert len(logs.encode("utf-8")) <= _HOST_LOG_RETAINED_BYTES + len(
        _HOST_LOG_TRUNCATION_MARKER
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "backend",
    [
        _Backend(logs_exit_code=1, logs_stderr="Error: No such container"),
        _Backend(logs_raises=TimeoutError("docker logs hung")),
    ],
    ids=["nonzero-exit", "raised"],
)
async def test_cleanup_records_capture_failure_without_blocking_removal(
    backend: _Backend,
) -> None:
    evidence = await _cleanup(backend)

    kinds = [tuple(command[:3]) for command in backend.commands]
    assert ("docker", "rm", "-f") in kinds
    assert ("docker", "volume", "rm") in kinds
    assert evidence["containerRemoved"] is True
    assert evidence["stateVolumeRemoved"] is True
    assert "hostLogs" not in evidence
    assert str(evidence["hostLogsCaptureError"])


@pytest.mark.asyncio
async def test_cleanup_stale_owner_is_fenced_before_any_log_read_or_removal() -> None:
    backend = _Backend(owner=f"{_LEASE}|{_GENERATION + 1}", logs="newer owner")

    with pytest.raises(HarnessPlatformError) as exc:
        await _cleanup(backend)

    assert (
        exc.value.code == HarnessPlatformFailure.OMNIGENT_RUNTIME_BINDING_CONFLICT.value
    )
    kinds = [tuple(command[:2]) for command in backend.commands]
    assert ("docker", "logs") not in kinds
    assert ("docker", "rm") not in kinds
