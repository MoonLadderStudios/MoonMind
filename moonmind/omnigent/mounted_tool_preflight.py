"""Required mounted-tool readiness at the Omnigent host/runner boundary (MM-1215)."""

from __future__ import annotations

import asyncio
import json
import re
import shlex
import time
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from moonmind.utils.logging import redact_sensitive_text

CommandRunner = Callable[..., Awaitable[tuple[int, str, str]]]
MAX_EVIDENCE_CHARS = 512
REMOTE_PROBE_MAX_ATTEMPTS = 4
REMOTE_PROBE_RETRY_DELAYS_SECONDS = (1.0, 2.0, 4.0)
# GitHub's reset time is free to read, so a rate-limited probe waits once for
# the reported reset instead of failing a launch that waited for capacity. The
# wait is capped well inside the launch activity's budget and the host it
# holds; a longer outage is reported as transient so the parent's bounded Step
# Execution retry waits out the rest. A secondary limit reports no exhausted
# primary resource; GitHub asks clients to wait at least a minute.
RATE_LIMIT_MAX_WAITS = 1
RATE_LIMIT_DEFAULT_WAIT_SECONDS = 60.0
RATE_LIMIT_MAX_WAIT_SECONDS = 900.0
# ``/rate_limit`` can report an untouched budget while every request is
# rejected (observed 2026-10-01), so the reset is read from the headers of one
# REST and one GraphQL request. A rejected request costs nothing; the probes
# run under ``sh -e``, so each tolerates its own non-zero exit.
RATE_LIMIT_LOOKUP_COMMAND = (
    "gh api --include --silent user || true; "
    "gh api graphql --include --silent -f query='{rateLimit{remaining}}' || true"
)
_RATE_LIMIT_HEADER = re.compile(
    r"^(x-ratelimit-remaining|x-ratelimit-reset|retry-after):\s*(\d+)\s*$",
    re.IGNORECASE | re.MULTILINE,
)


class MountedToolPreflightError(RuntimeError):
    """Stable, bounded failure raised before an Omnigent session is created.

    ``transient`` marks a remote failure that persisted through in-place
    recovery (transport errors, HTTP 5xx, rate limits) rather than a rejected
    credential, identity, or permission; nothing has run, so a fresh attempt
    is safe.
    """

    def __init__(
        self,
        message: str,
        *,
        code: str,
        evidence: Mapping[str, Any],
        transient: bool = False,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.evidence = dict(evidence)
        self.transient = transient


@dataclass(frozen=True)
class Probe:
    name: str
    command: str
    failure_code: str
    remote: bool = False


def _bounded(value: str) -> str:
    return redact_sensitive_text(str(value or ""))[:MAX_EVIDENCE_CHARS]


def _repository_name(repository: str) -> str:
    value = repository.strip().removesuffix(".git")
    if value.startswith("git@github.com:"):
        value = value.split(":", 1)[1]
    elif "://" in value:
        parsed = urlparse(value)
        if parsed.hostname != "github.com":
            value = ""
        else:
            value = parsed.path
    value = value.strip("/")
    parts = value.split("/")
    if len(parts) != 2 or not all(parts):
        raise MountedToolPreflightError(
            "GitHub capability requires an owner/repository target",
            code="github_repository_unauthorized",
            evidence={"phase": "authorization", "repository": _bounded(repository)},
        )
    return "/".join(parts)


def _trusted_gh_digest_checks() -> str:
    path = Path(__file__).resolve().parents[2] / "services/omnigent/tools/manifest.lock.json"
    manifest = json.loads(path.read_text(encoding="utf-8"))
    tool = next(item for item in manifest["tools"] if item["name"] == "gh")
    digests = {item["executableSha256"] for item in tool["platforms"].values()}
    executable = f'/opt/moonmind-tools/{tool["path"]}'
    return _digest_check_command(executable, sorted(digests))


def _digest_check_command(executable: str, digests: Sequence[str]) -> str:
    quoted_executable = shlex.quote(executable)
    return " || ".join(
        f'''test "$(sha256sum {quoted_executable} | awk '{{print $1}}')" = "{digest}"'''
        for digest in sorted(digests)
    )


def _github_access_probes(repository: str) -> tuple[Probe, ...]:
    # Token lookup is local and its value must never cross the command boundary.
    # Account-status probes add unrelated user/scope API calls and turn provider
    # outages into misleading "invalid credential" errors. Prove the requested
    # repository operation instead, once per execution environment.
    probes = [
        Probe(
            "authentication",
            "gh auth token --hostname github.com >/dev/null",
            "github_auth_unavailable",
        ),
    ]
    if repository:
        probes.append(
            Probe(
                "repository_access",
                f"gh repo view {shlex.quote(_repository_name(repository))} "
                "--json nameWithOwner,viewerPermission",
                "github_repository_unauthorized",
                remote=True,
            )
        )
    return tuple(probes)


_TRANSIENT_GITHUB_FAILURE = re.compile(
    r"HTTP 5\d\d|(?:connection|network) (?:reset|refused|unreachable|failure)|"
    r"temporary (?:failure|provider connection failure)|"
    r"no such host|i/o timeout|TLS handshake timeout|command timed out|unexpected EOF|"
    r"context deadline exceeded|timeout exceeded while awaiting headers|"
    r"connection timed out|network is unreachable",
    re.IGNORECASE,
)


_RATE_LIMITED_GITHUB_FAILURE = re.compile(
    r"rate limit|HTTP 429|abuse detection", re.IGNORECASE
)


async def _rate_limit_wait_seconds(command_runner: CommandRunner) -> float:
    """Seconds until GitHub's reported reset for the exhausted resource.

    ``Retry-After`` (secondary limits) wins; otherwise the latest reset of a
    resource reporting no remaining budget. Any lookup failure or a response
    with neither falls back to the bounded default wait.
    """

    try:
        _rc, stdout, _stderr = await command_runner(RATE_LIMIT_LOOKUP_COMMAND)
    except TimeoutError:
        return RATE_LIMIT_DEFAULT_WAIT_SECONDS
    waits: list[float] = []
    for response in re.split(r"^HTTP/", stdout or "", flags=re.MULTILINE)[1:]:
        headers = {
            name.lower(): int(value)
            for name, value in _RATE_LIMIT_HEADER.findall(response)
        }
        if "retry-after" in headers:
            waits.append(float(headers["retry-after"]))
        elif (
            headers.get("x-ratelimit-remaining") == 0
            and "x-ratelimit-reset" in headers
        ):
            waits.append(headers["x-ratelimit-reset"] - time.time() + 1.0)
    if not waits:
        return RATE_LIMIT_DEFAULT_WAIT_SECONDS
    return min(max(max(waits), 1.0), RATE_LIMIT_MAX_WAIT_SECONDS)


async def _run_probes(
    probes: Sequence[Probe],
    boundaries: Mapping[str, CommandRunner],
    *,
    repository: str,
    mutation_required: bool,
) -> dict[str, Any]:
    evidence: list[dict[str, Any]] = []
    for boundary, command_runner in boundaries.items():
        for probe in probes:
            max_attempts = REMOTE_PROBE_MAX_ATTEMPTS if probe.remote else 1
            attempt = transient_retries = rate_limit_waits = 0
            while True:
                attempt += 1
                try:
                    rc, stdout, stderr = await command_runner(probe.command)
                except TimeoutError:
                    rc, stdout, stderr = 124, "", "command timed out"
                rate_limited = (
                    rc != 0
                    and probe.remote
                    and bool(_RATE_LIMITED_GITHUB_FAILURE.search(stderr or stdout))
                )
                retryable = (
                    rc != 0
                    and probe.remote
                    and not rate_limited
                    and bool(_TRANSIENT_GITHUB_FAILURE.search(stderr or stdout))
                )
                if rc == 0 and probe.name == "repository_access":
                    try:
                        result = json.loads(stdout)
                    except (TypeError, ValueError):
                        result = None
                    if (
                        not isinstance(result, dict)
                        or str(result.get("nameWithOwner", "")).casefold()
                        != _repository_name(repository).casefold()
                    ):
                        rc, stderr = (
                            1,
                            "GitHub returned no matching repository identity",
                        )
                    elif mutation_required and result.get("viewerPermission") not in {
                        "ADMIN",
                        "MAINTAIN",
                        "WRITE",
                    }:
                        rc, stderr = (
                            1,
                            "GitHub credential lacks repository write permission",
                        )
                item = {
                    "boundary": boundary,
                    "probe": probe.name,
                    "attempt": attempt,
                    "status": (
                        "ready"
                        if rc == 0
                        else "rate_limited" if rate_limited else "failed"
                    ),
                }
                if stdout:
                    item["output"] = _bounded(stdout)
                if stderr and rc != 0:
                    item["error"] = _bounded(stderr)
                if rc == 0 and probe.name == "repository_access":
                    item["repositoryPermission"] = result.get("viewerPermission")
                evidence.append(item)
                if rc == 0:
                    break
                if rate_limited and rate_limit_waits < RATE_LIMIT_MAX_WAITS:
                    rate_limit_waits += 1
                    wait_seconds = await _rate_limit_wait_seconds(command_runner)
                    item["retryAfterSeconds"] = wait_seconds
                    await asyncio.sleep(wait_seconds)
                    continue
                if retryable and transient_retries < max_attempts - 1:
                    await asyncio.sleep(
                        REMOTE_PROBE_RETRY_DELAYS_SECONDS[transient_retries]
                    )
                    transient_retries += 1
                    continue
                detail = _bounded(stderr or stdout) or f"exit {rc}, no output"
                raise MountedToolPreflightError(
                    f"GitHub preflight failed during {boundary} {probe.name} "
                    f"after {attempt} attempt(s): {detail}",
                    code="github_rate_limited" if rate_limited else probe.failure_code,
                    evidence={"tool": "gh", "phase": probe.name, "probes": evidence},
                    transient=rate_limited or retryable,
                )
    return {"status": "ready", "tool": "gh", "probes": evidence}


async def preflight_github_access(
    *,
    repository: str,
    boundaries: Mapping[str, CommandRunner],
    mutation_required: bool = False,
) -> dict[str, Any]:
    """Check projected credentials and requested access on the existing host.

    A repository-free tool projection proves only local credential availability;
    it does not claim remote authorization. Transport retries never rematerialize
    credentials, replace the host, or change repository authority.
    """
    return await _run_probes(
        _github_access_probes(repository),
        boundaries,
        repository=repository,
        mutation_required=mutation_required,
    )


async def preflight_mounted_tools(
    *,
    required_capabilities: Sequence[str],
    repository: str,
    mutation_required: bool,
    host_runner: CommandRunner,
    runner_runner: CommandRunner,
) -> dict[str, Any]:
    """Probe only declared tools through both real shell construction paths."""

    capabilities = {str(value).strip().lower() for value in required_capabilities}
    if "gh" not in capabilities:
        return {"status": "not_required", "boundaries": []}

    repository = _repository_name(repository)
    return await _run_probes(
        (
            Probe("manifest", _trusted_gh_digest_checks(), "tool_manifest_mismatch"),
            Probe("lookup", "command -v gh", "tool_not_visible_in_login_shell"),
            Probe("version", "gh --version", "tool_manifest_mismatch"),
            *_github_access_probes(repository),
        ),
        {"host": host_runner, "runner": runner_runner},
        repository=repository,
        mutation_required=mutation_required,
    )


__all__ = [
    "MountedToolPreflightError",
    "preflight_github_access",
    "preflight_mounted_tools",
]
