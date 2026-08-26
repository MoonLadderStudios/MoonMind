"""Docker-backed workload launcher for control-plane owned workload containers."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import posixpath
import shlex
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Mapping, Protocol, Sequence

from moonmind.schemas.workload_models import (
    RunnerProfile,
    UnrestrictedContainerRequest,
    UnrestrictedDockerRequest,
    ValidatedWorkloadRequest,
    WorkloadMount,
    WorkloadResourceOverrides,
    WorkloadResult,
)
from moonmind.security.egress import (
    DEFAULT_EGRESS_PROFILE,
    EGRESS_NETWORK_REF,
    EgressAttestation,
    attest_docker_egress,
    attest_docker_workload_egress,
    restricted_proxy_env,
)
from moonmind.security.egress_conformance_evidence import (
    parse_and_verify_conformance_evidence,
    serialize_conformance_evidence,
)
from moonmind.utils.logging import redact_sensitive_payload, redact_sensitive_text
from moonmind.workloads.gpu import gpu_device_request_args, gpu_launch_observations

_MAX_CAPTURED_STREAM_CHARS = 64_000
_MAX_CAPTURED_STREAM_BYTES = 64_000
_DEFAULT_TIMEOUT_SECONDS = 300
_DEFAULT_KILL_GRACE_SECONDS = 30
# Upper bound on generically collected workspace files per run. Collection is
# glob-driven and project-agnostic; this guard keeps a pathological glob (for
# example ``**/*``) from publishing an unbounded number of refs. When the bound
# is hit the remaining matches are reported as truncated rather than dropped
# silently.
_MAX_COLLECTED_ARTIFACTS = 512
_UNRESTRICTED_RUNNER_PROFILE = RunnerProfile.model_validate(
    {
        "id": "unrestricted",
        "kind": "one_shot",
        "image": "busybox:1.0",
        "workdirTemplate": "/tmp",
        "requiredMounts": [
            {
                "type": "volume",
                "source": "tmp",
                "target": "/tmp",
            }
        ],
        "envAllowlist": [],
        "networkPolicy": "none",
        "resources": {},
        "timeoutSeconds": _DEFAULT_TIMEOUT_SECONDS,
    }
)

class DockerWorkloadLauncherError(RuntimeError):
    """Raised when the Docker workload launcher cannot execute a request."""

class _DockerMount(Protocol):
    type: str
    source: str
    target: str
    read_only: bool

class _ConcurrencyLease:
    def __init__(
        self,
        limiter: "DockerWorkloadConcurrencyLimiter",
        profile_id: str,
    ) -> None:
        self._limiter = limiter
        self._profile_id = profile_id
        self._released = False

    async def release(self) -> None:
        if self._released:
            return
        self._released = True
        await self._limiter.release(self._profile_id)

class DockerWorkloadConcurrencyLimiter:
    """Fail-fast in-process concurrency guard for Docker workload launches."""

    def __init__(self, *, fleet_limit: int | None = None) -> None:
        if fleet_limit is not None and fleet_limit < 1:
            raise ValueError("fleet_limit must be positive")
        self._fleet_limit = fleet_limit
        self._active_total = 0
        self._active_by_profile: dict[str, int] = {}
        self._lock = asyncio.Lock()

    async def acquire(
        self,
        request: ValidatedWorkloadRequest,
    ) -> _ConcurrencyLease:
        profile_id = request.profile.id if request.profile is not None else request.request.tool_name
        max_concurrency = (
            request.profile.max_concurrency if request.profile is not None else None
        )
        async with self._lock:
            active_for_profile = self._active_by_profile.get(profile_id, 0)
            if max_concurrency is not None and active_for_profile >= max_concurrency:
                raise DockerWorkloadLauncherError(
                    "workload concurrency limit exceeded for profile "
                    f"{profile_id}"
                )
            if self._fleet_limit is not None and self._active_total >= self._fleet_limit:
                raise DockerWorkloadLauncherError(
                    "workload concurrency limit exceeded for docker_workload fleet"
                )
            self._active_total += 1
            self._active_by_profile[profile_id] = active_for_profile + 1
        return _ConcurrencyLease(self, profile_id)

    async def release(self, profile_id: str) -> None:
        async with self._lock:
            active_for_profile = self._active_by_profile.get(profile_id, 0)
            if active_for_profile <= 1:
                self._active_by_profile.pop(profile_id, None)
            else:
                self._active_by_profile[profile_id] = active_for_profile - 1
            if self._active_total > 0:
                self._active_total -= 1

def _decode_stream(data: bytes) -> str:
    text = data.decode("utf-8", errors="replace")
    if len(text) <= _MAX_CAPTURED_STREAM_CHARS:
        return text
    return text[-_MAX_CAPTURED_STREAM_CHARS:]

def _append_limited(buffer: bytearray, chunk: bytes) -> None:
    buffer.extend(chunk)
    overflow = len(buffer) - _MAX_CAPTURED_STREAM_BYTES
    if overflow > 0:
        del buffer[:overflow]

async def _read_limited_stream(
    stream: asyncio.StreamReader | None,
    buffer: bytearray,
) -> None:
    if stream is None:
        return
    while True:
        chunk = await stream.read(8192)
        if not chunk:
            return
        _append_limited(buffer, chunk)

async def _wait_with_limited_output(
    process: asyncio.subprocess.Process,
    *,
    stdout_buffer: bytearray,
    stderr_buffer: bytearray,
) -> int:
    await asyncio.gather(
        _read_limited_stream(process.stdout, stdout_buffer),
        _read_limited_stream(process.stderr, stderr_buffer),
    )
    return await process.wait()

async def _kill_and_reap_process(process: asyncio.subprocess.Process) -> None:
    if process.returncode is None:
        process.kill()
    await process.wait()

def _docker_env(*, docker_host: str | None = None) -> dict[str, str]:
    env = dict(os.environ)
    if docker_host:
        env["DOCKER_HOST"] = docker_host
    return env

def structured_container_security_args() -> list[str]:
    """Return the non-overridable Docker hardening flags for owned containers.

    This is the single definition of the ``--privileged=false`` / capability
    drop / ``no-new-privileges`` protections applied to every MoonMind-owned
    container. It is reused by the workload launcher below and by the
    deployment-selected container-job backend so the two launch paths cannot
    drift apart.
    """

    return [
        "--privileged=false",
        "--cap-drop",
        "ALL",
        "--security-opt",
        "no-new-privileges",
    ]

def _mount_arg(mount: _DockerMount) -> str:
    parts = [
        f"type={mount.type}",
        f"source={mount.source}",
        f"target={mount.target}",
    ]
    if mount.read_only:
        parts.append("readonly")
    return ",".join(parts)

def _effective_resources(
    *,
    profile: RunnerProfile,
    overrides: WorkloadResourceOverrides,
) -> dict[str, str]:
    resources: dict[str, str] = {}
    cpu = overrides.cpu or profile.resources.cpu
    memory = overrides.memory or profile.resources.memory
    shm_size = overrides.shm_size or profile.resources.shm_size
    if cpu:
        resources["--cpus"] = cpu
    if memory:
        resources["--memory"] = memory
    if shm_size:
        resources["--shm-size"] = shm_size
    return resources

def _workload_command_args(
    *,
    command_wrapper: Sequence[str],
    workload_command: Sequence[str],
) -> list[str]:
    if command_wrapper and command_wrapper[-1] in {"-c", "-lc"}:
        if len(workload_command) == 1:
            return [workload_command[0]]
        return [shlex.join(workload_command)]
    return list(workload_command)


def _profile_network_args(network_policy: str) -> tuple[str, list[str]]:
    if network_policy == "none":
        return "none", []
    if network_policy == "restricted_egress":
        args = [
            "--label",
            f"moonmind.egress.profile={DEFAULT_EGRESS_PROFILE.ref}",
            "--label",
            f"moonmind.egress.profile_digest={DEFAULT_EGRESS_PROFILE.digest}",
        ]
        for value in restricted_proxy_env():
            args.extend(("--env", value))
        return EGRESS_NETWORK_REF, args
    if network_policy == "docker_proxy":
        return (
            os.environ.get(
                "MOONMIND_DOCKER_PROXY_NETWORK",
                "moonmind_docker-proxy-network",
            ),
            [],
        )
    raise DockerWorkloadLauncherError("unsupported workload network policy")


def _egress_launch_binding_args(
    attestation: EgressAttestation | None,
) -> list[str]:
    if attestation is None:
        return []
    return [
        "--label",
        f"moonmind.egress.applied_rule_digest={attestation.applied_rule_digest}",
    ]


def _path_is_under_mount(path: str, mounts: Sequence[WorkloadMount]) -> bool:
    normalized = posixpath.normpath(path)
    for mount in mounts:
        target = posixpath.normpath(mount.target)
        if normalized == target or normalized.startswith(f"{target}/"):
            return True
    return False

def _isoformat(value: datetime | None) -> str | None:
    if value is None:
        return None
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")

def _parse_iso_datetime(value: str) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = f"{text[:-1]}+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)

def _session_context(request: ValidatedWorkloadRequest) -> dict[str, object] | None:
    workload = request.request
    if workload.session_id is None:
        return None
    context: dict[str, object] = {"sessionId": workload.session_id}
    if workload.session_epoch is not None:
        context["sessionEpoch"] = workload.session_epoch
    if workload.source_turn_id is not None:
        context["sourceTurnId"] = workload.source_turn_id
    return context

def _operational_labels(request: ValidatedWorkloadRequest) -> dict[str, str]:
    if request.profile is not None and request.profile.kind == "bounded_service":
        ttl_seconds = (
            request.request.ttl_seconds
            or request.profile.helper_ttl_seconds
            or request.profile.timeout_seconds
        )
        expires_at = datetime.now(UTC) + timedelta(seconds=ttl_seconds)
        return {
            **request.ownership.labels,
            "moonmind.expires_at": _isoformat(expires_at) or "",
            "moonmind.helper_ttl_seconds": str(ttl_seconds),
        }
    timeout_seconds = _request_timeout_seconds(request)
    kill_grace_seconds = _request_kill_grace_seconds(request)
    expires_at = datetime.now(UTC) + timedelta(seconds=timeout_seconds + kill_grace_seconds)
    return {
        **request.ownership.labels,
        "moonmind.expires_at": _isoformat(expires_at) or "",
    }

def _workload_metadata(
    request: ValidatedWorkloadRequest,
    *,
    status: str,
    exit_code: int | None,
    started_at: datetime,
    completed_at: datetime,
    duration_seconds: float,
    timeout_reason: str | None,
    stderr: str = "",
) -> dict[str, object]:
    image_ref = request.profile.image if request.profile is not None else getattr(request.request, "image", None)
    profile_id = request.profile.id if request.profile is not None else None
    workload_access = request.ownership.workload_access
    return {
        "agentRunId": request.request.agent_run_id,
        "stepId": request.request.step_id,
        "attempt": request.request.attempt,
        "toolName": request.request.tool_name,
        "profileId": profile_id,
        "workflowDockerMode": request.ownership.workflow_docker_mode,
        "workloadAccess": workload_access,
        "unrestrictedContainer": workload_access == "unrestricted_container",
        "unrestrictedDocker": workload_access == "unrestricted_docker_cli",
        "imageRef": image_ref,
        "containerName": request.container_name,
        "identityKind": request.ownership.kind,
        "status": status,
        "exitCode": exit_code,
        "startedAt": _isoformat(started_at),
        "completedAt": _isoformat(completed_at),
        "durationSeconds": duration_seconds,
        "timeoutReason": timeout_reason,
        "labels": dict(request.ownership.labels),
        "artifactsDir": request.request.artifacts_dir,
        "sessionContext": _session_context(request),
        # Generic GPU observations for a caller-supplied device request. Absent
        # (``None``) for every CPU-only request, which keeps existing CPU-only
        # behavior unchanged.
        "gpu": gpu_launch_observations(
            gpu=request.request.resources.gpu,
            exit_code=exit_code,
            stderr=stderr,
        ),
    }

def _helper_metadata(
    request: ValidatedWorkloadRequest,
    *,
    status: str,
    started_at: datetime,
    completed_at: datetime,
    duration_seconds: float,
    readiness: Mapping[str, object] | None = None,
    teardown: Mapping[str, object] | None = None,
) -> dict[str, object]:
    ttl_seconds = request.request.ttl_seconds or request.profile.helper_ttl_seconds
    workload_access = request.ownership.workload_access
    return {
        "agentRunId": request.request.agent_run_id,
        "stepId": request.request.step_id,
        "attempt": request.request.attempt,
        "toolName": request.request.tool_name,
        "profileId": request.profile.id,
        "workflowDockerMode": request.ownership.workflow_docker_mode,
        "workloadAccess": workload_access,
        "unrestrictedContainer": workload_access == "unrestricted_container",
        "unrestrictedDocker": workload_access == "unrestricted_docker_cli",
        "imageRef": request.profile.image,
        "containerName": request.container_name,
        "identityKind": request.ownership.kind,
        "status": status,
        "startedAt": _isoformat(started_at),
        "completedAt": _isoformat(completed_at),
        "durationSeconds": duration_seconds,
        "ttlSeconds": ttl_seconds,
        "labels": dict(request.ownership.labels),
        "artifactsDir": request.request.artifacts_dir,
        "sessionContext": _session_context(request),
        "readiness": dict(readiness or {}),
        "teardown": dict(teardown or {}),
    }

def _report_publication_metadata(
    request: ValidatedWorkloadRequest,
    *,
    declared_output_refs: Mapping[str, str],
    missing_declared_outputs: Mapping[str, str],
) -> dict[str, object]:
    report_keys = {"output.primary", "output.summary"}
    declared_outputs = request.request.declared_outputs or {}
    declared_keys = report_keys.intersection(declared_outputs)
    if not declared_keys:
        return {"status": "not_requested"}

    metadata: dict[str, object] = {
        "status": "configured",
        "primaryDeclared": "output.primary" in declared_keys,
        "summaryDeclared": "output.summary" in declared_keys,
    }
    published_refs = {
        key: declared_output_refs[key]
        for key in declared_keys
        if key in declared_output_refs
    }
    if published_refs:
        metadata["publishedRefs"] = published_refs
    missing_outputs = {
        key: missing_declared_outputs[key]
        for key in declared_keys
        if key in missing_declared_outputs
    }
    if missing_outputs:
        metadata["missingOutputs"] = missing_outputs
    return metadata


def _write_text_artifact(path: Path, payload: str) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(payload, encoding="utf-8")
    return str(path)


def _egress_conformance_artifact(
    request: ValidatedWorkloadRequest,
    diagnostics: Mapping[str, object],
) -> tuple[str, str] | None:
    raw_evidence = diagnostics.get("egressWorkloadEvidence")
    if not isinstance(raw_evidence, Mapping):
        return None
    status = str(diagnostics.get("status") or "unknown")
    row = (
        "managed_helper"
        if request.profile is not None and request.profile.kind == "bounded_service"
        else "generic_workload"
    )
    runner_profile_digest = None
    if request.profile is not None:
        profile_payload = request.profile.model_dump(by_alias=True, mode="json")
        runner_profile_digest = "sha256:" + hashlib.sha256(
            json.dumps(
                profile_payload, sort_keys=True, separators=(",", ":")
            ).encode()
        ).hexdigest()
    payload = {
        "schemaVersion": 1,
        "kind": "restricted-egress-workload-conformance",
        "conformanceRow": row,
        "workloadClass": row,
        "hostMode": "managed_helper" if row == "managed_helper" else None,
        "runtimeProvenance": "docker_workload_launcher/docker-engine",
        "egressProfileVersion": DEFAULT_EGRESS_PROFILE.version,
        "securityPolicyRef": DEFAULT_EGRESS_PROFILE.security_review_ref,
        "securityPolicyVersion": DEFAULT_EGRESS_PROFILE.version,
        # Workload runner profiles are the immutable launch-profile authority
        # for this plane.  Bind both the selected identity and its canonical
        # digest so a later registry change cannot be mistaken for this launch.
        "agentProfileRef": (
            request.profile.id if request.profile is not None else None
        ),
        "agentProfileVersion": runner_profile_digest,
        "runnerProfileRef": (
            request.profile.id if request.profile is not None else None
        ),
        "runnerProfileDigest": runner_profile_digest,
        "networkPolicy": (
            request.profile.network_policy if request.profile is not None else None
        ),
        "workloadStatus": status,
        **dict(raw_evidence),
    }
    serialized = serialize_conformance_evidence(
        payload,
        location=f"workload-egress:{request.container_name}:{status}",
    )
    path = (
        Path(request.request.artifacts_dir)
        / "workload"
        / request.container_name
        / f"egress-conformance-{status}.json"
    )
    return str(path), serialized.decode("utf-8") + "\n"


def _helper_egress_authority_path(
    request: ValidatedWorkloadRequest,
    *,
    state: str,
) -> Path:
    return (
        Path(request.request.artifacts_dir)
        / "workload"
        / request.container_name
        / f"egress-helper-authority-{state}.json"
    )


def _runner_profile_digest(request: ValidatedWorkloadRequest) -> str:
    payload = request.profile.model_dump(by_alias=True, mode="json")
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return "sha256:" + hashlib.sha256(canonical).hexdigest()


def _persist_helper_egress_authority(
    request: ValidatedWorkloadRequest,
    *,
    state: str,
    attestation: EgressAttestation,
    workload_evidence: Mapping[str, object],
    started_at: datetime,
    cleanup_evidence: Mapping[str, object] | None = None,
    lease_release_result: str = "held",
) -> str:
    """Persist restart-safe helper ownership and its immutable egress chain."""

    payload = {
        "schemaVersion": 1,
        "kind": "restricted-egress-helper-authority",
        "conformanceRow": "managed_helper",
        "workloadClass": "managed_helper",
        "hostMode": "managed_helper",
        "runtimeProvenance": "docker_workload_launcher/docker-engine",
        "state": state,
        "containerName": request.container_name,
        "ownershipLabels": dict(request.ownership.labels),
        "runnerProfileRef": request.profile.id,
        "runnerProfileDigest": _runner_profile_digest(request),
        "egressProfileVersion": DEFAULT_EGRESS_PROFILE.version,
        "securityPolicyRef": DEFAULT_EGRESS_PROFILE.security_review_ref,
        "securityPolicyVersion": DEFAULT_EGRESS_PROFILE.version,
        "agentProfileRef": request.profile.id,
        "agentProfileVersion": _runner_profile_digest(request),
        "startedAt": _isoformat(started_at),
        "leaseAuthority": {
            "owner": request.container_name,
            "state": (
                "released"
                if lease_release_result
                in {
                    "released",
                    "released_after_interrupted_start",
                    "released_after_reconciliation",
                }
                else "held"
            ),
            "releaseResult": lease_release_result,
        },
        "reconciliationOwner": (
            {
                "toolName": "container.stop_helper",
                "containerName": request.container_name,
                "agentRunId": request.request.agent_run_id,
                "stepId": request.request.step_id,
                "attempt": request.request.attempt,
            }
            if state in {"cleanup_failed", "cleanup_validated"}
            else None
        ),
        "attestation": attestation.model_dump(by_alias=True, mode="json"),
        **dict(workload_evidence),
        **dict(cleanup_evidence or {}),
    }
    serialized = serialize_conformance_evidence(
        payload,
        location=f"helper-egress-authority:{request.container_name}:{state}",
    )
    path = _helper_egress_authority_path(request, state=state)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(serialized + b"\n")
    return str(path)


def _load_helper_egress_authority(
    request: ValidatedWorkloadRequest,
) -> tuple[EgressAttestation, dict[str, object], datetime, str] | None:
    """Recover attached helper authority after an Activity worker restart."""

    candidates = [
        candidate
        for state in ("attached", "cleanup_failed", "cleanup_validated", "stopped")
        if (candidate := _helper_egress_authority_path(request, state=state)).is_file()
    ]
    path = (
        max(candidates, key=lambda candidate: candidate.stat().st_mtime_ns)
        if candidates
        else None
    )
    if path is None:
        return None
    raw = path.read_bytes()
    payload = parse_and_verify_conformance_evidence(
        raw,
        location=f"helper-egress-authority:{request.container_name}:{path.stem}",
    )
    if (
        payload.get("containerName") != request.container_name
        or payload.get("runnerProfileRef") != request.profile.id
        or payload.get("runnerProfileDigest") != _runner_profile_digest(request)
    ):
        raise DockerWorkloadLauncherError(
            "durable helper egress authority does not match the stop request"
        )
    lease_authority = payload.get("leaseAuthority")
    if payload.get("state") in {"cleanup_failed", "cleanup_validated"} and (
        not isinstance(lease_authority, Mapping)
        or lease_authority.get("state") != "held"
    ):
        raise DockerWorkloadLauncherError(
            "failed helper cleanup did not retain durable lease authority"
        )
    attestation_payload = payload.get("attestation")
    if not isinstance(attestation_payload, Mapping):
        raise DockerWorkloadLauncherError(
            "durable helper egress authority is missing its launch attestation"
        )
    started_at = _parse_iso_datetime(str(payload.get("startedAt") or ""))
    if started_at is None:
        raise DockerWorkloadLauncherError(
            "durable helper egress authority is missing its launch time"
        )
    workload_evidence = {
        key: value
        for key, value in payload.items()
        if key
        not in {
            "evidenceDigest",
            "schemaVersion",
            "kind",
            "conformanceRow",
            "workloadClass",
            "hostMode",
            "runtimeProvenance",
            "state",
            "containerName",
            "ownershipLabels",
            "runnerProfileRef",
            "runnerProfileDigest",
            "egressProfileVersion",
            "securityPolicyRef",
            "securityPolicyVersion",
            "agentProfileRef",
            "agentProfileVersion",
            "startedAt",
            "leaseAuthority",
            "reconciliationOwner",
            "attestation",
        }
    }
    return (
        EgressAttestation.model_validate(attestation_payload),
        workload_evidence,
        started_at,
        str(path),
    )

def _declared_output_refs(
    request: ValidatedWorkloadRequest,
) -> tuple[dict[str, str], dict[str, str]]:
    artifact_root = Path(request.request.artifacts_dir)
    refs: dict[str, str] = {}
    missing: dict[str, str] = {}
    for artifact_class, relative_path in request.request.declared_outputs.items():
        output_path = (artifact_root / relative_path).resolve()
        try:
            output_path.relative_to(artifact_root.resolve())
        except ValueError:
            missing[artifact_class] = relative_path
            continue
        if output_path.is_file():
            refs[artifact_class] = str(output_path)
        else:
            missing[artifact_class] = relative_path
    return refs, missing

def _collect_workspace_artifacts(
    request: ValidatedWorkloadRequest,
) -> tuple[dict[str, str], list[dict[str, object]]]:
    """Collect repo-written workspace files matching caller-supplied globs.

    This is the generic, path/glob-based collection of whatever a repo's
    scripts write into the workspace after a managed Docker run (logs, gate
    files, reports). It is intentionally project- and engine-agnostic: the only
    inputs are the caller-supplied ``collectGlobs`` patterns, resolved relative
    to the run repo directory (the container workdir where repo scripts run).
    MoonMind hardcodes no project- or engine-specific paths.

    Returns ``(refs, diagnostics)`` where ``refs`` maps a deterministic
    ``collected:<relative-posix-path>`` artifact class to the absolute file
    path, and ``diagnostics`` records, per requested pattern, what matched or
    why a candidate was skipped.
    """

    collect_globs = tuple(getattr(request.request, "collect_globs", ()) or ())
    if not collect_globs:
        return {}, []

    base = Path(request.request.repo_dir).resolve()
    refs: dict[str, str] = {}
    diagnostics: list[dict[str, object]] = []
    seen: set[str] = set()

    for pattern in collect_globs:
        if len(refs) >= _MAX_COLLECTED_ARTIFACTS:
            # The cap is already reached, so any remaining pattern would only
            # produce results we discard. Skip globbing entirely; a broad
            # pattern over a large workspace must not trigger unbounded post-run
            # I/O just to throw the matches away.
            diagnostics.append(
                {
                    "pattern": pattern,
                    "status": "truncated",
                    "truncated": True,
                    "matched": [],
                }
            )
            continue
        matched: list[str] = []
        skipped: list[str] = []
        truncated = False
        try:
            # Iterate lazily and stop once the cap is reached instead of
            # materializing and sorting every candidate up front. A broad
            # pattern (for example ``**/*``) over a large workspace would
            # otherwise spend unbounded I/O and memory before the cap applies.
            for candidate in base.glob(pattern):
                if len(refs) >= _MAX_COLLECTED_ARTIFACTS:
                    truncated = True
                    break
                try:
                    resolved = candidate.resolve()
                except OSError:
                    continue
                if not resolved.is_file():
                    continue
                try:
                    relative = resolved.relative_to(base)
                except ValueError:
                    # A symlink (or matched entry) resolving outside the repo
                    # workspace must never be published as a collected artifact.
                    skipped.append(str(candidate))
                    continue
                key = relative.as_posix()
                if key in seen:
                    continue
                seen.add(key)
                refs[f"collected:{key}"] = str(resolved)
                matched.append(key)
        except (NotImplementedError, OSError, ValueError) as exc:
            diagnostics.append(
                {"pattern": pattern, "status": "error", "error": str(exc)}
            )
            continue
        # Sort only the bounded collected/skipped subsets for deterministic
        # diagnostics, never the full (possibly unbounded) candidate set.
        matched.sort()
        skipped.sort()
        entry: dict[str, object] = {
            "pattern": pattern,
            "status": "matched" if matched else "empty",
            "matched": matched,
        }
        if skipped:
            entry["skippedOutsideWorkspace"] = skipped
        if truncated:
            entry["status"] = "truncated"
            entry["truncated"] = True
        diagnostics.append(entry)

    return refs, diagnostics

def _publish_workload_artifacts(
    request: ValidatedWorkloadRequest,
    *,
    stdout: str,
    stderr: str,
    diagnostics: Mapping[str, object],
    declared_output_refs: Mapping[str, str],
    collected_output_refs: Mapping[str, str] | None = None,
) -> tuple[str | None, str | None, str | None, dict[str, str], dict[str, object]]:
    artifact_root = Path(request.request.artifacts_dir)
    workload_root = artifact_root / "workload" / request.container_name
    errors: dict[str, str] = {}

    def _write(class_name: str, path: Path, payload: str) -> str | None:
        try:
            return _write_text_artifact(path, payload)
        except OSError as exc:
            errors[class_name] = str(exc)
            return None

    sanitized_stdout = redact_sensitive_text(stdout)
    sanitized_stderr = redact_sensitive_text(stderr)
    diagnostics_payload = redact_sensitive_payload(diagnostics)
    # Build and high-security scan the restricted-egress artifact before any
    # diagnostics containing the evidence are persisted.
    egress_artifact = _egress_conformance_artifact(request, diagnostics_payload)
    stdout_ref = _write(
        "runtime.stdout",
        workload_root / "runtime.stdout.log",
        sanitized_stdout,
    )
    stderr_ref = _write(
        "runtime.stderr",
        workload_root / "runtime.stderr.log",
        sanitized_stderr,
    )
    diagnostics_payload["artifactPublication"] = (
        {
            "status": "failed",
            "error": next(iter(errors.values())),
            "errors": dict(errors),
        }
        if errors
        else {"status": "complete"}
    )
    diagnostics_ref = _write(
        "runtime.diagnostics",
        workload_root / "runtime.diagnostics.json",
        json.dumps(diagnostics_payload, sort_keys=True, indent=2) + "\n",
    )
    egress_ref: str | None = None
    if egress_artifact is not None:
        egress_ref = _write(
            "security.egress",
            Path(egress_artifact[0]),
            egress_artifact[1],
        )
    output_refs: dict[str, str] = {}
    if stdout_ref is not None:
        output_refs["runtime.stdout"] = stdout_ref
        output_refs["output.logs"] = stdout_ref
    if stderr_ref is not None:
        output_refs["runtime.stderr"] = stderr_ref
    if diagnostics_ref is not None:
        output_refs["runtime.diagnostics"] = diagnostics_ref
    if egress_ref is not None:
        output_refs["security.egress"] = egress_ref
    output_refs.update(declared_output_refs)
    if collected_output_refs:
        output_refs.update(collected_output_refs)
    publication: dict[str, object]
    if errors:
        publication = {
            "status": "failed",
            "error": next(iter(errors.values())),
            "errors": errors,
        }
    else:
        publication = {"status": "complete"}
    return stdout_ref, stderr_ref, diagnostics_ref, output_refs, publication

def _request_timeout_seconds(request: ValidatedWorkloadRequest) -> int | float:
    return request.request.timeout_seconds or (
        request.profile.timeout_seconds
        if request.profile is not None
        else _DEFAULT_TIMEOUT_SECONDS
    )

def _request_kill_grace_seconds(request: ValidatedWorkloadRequest) -> int:
    return (
        request.profile.cleanup.kill_grace_seconds
        if request.profile is not None
        else _DEFAULT_KILL_GRACE_SECONDS
    )

def _removes_container_on_exit(request: ValidatedWorkloadRequest) -> bool:
    """Return whether job cleanup owns and removes the launched container.

    Cleanup is scoped to the container MoonMind named and launched. Profile
    workloads follow their profile cleanup policy. On the unrestricted path,
    only a device-bearing container is run-owned for cleanup: a CPU-only
    unrestricted request keeps the retained-container semantics its already
    recorded ``workload.run`` history was launched with, so a replayed or
    retried in-flight attempt cannot start deleting a container it previously
    kept. A raw docker-CLI request owns no MoonMind-named container, so it
    removes nothing. Images and named cache volumes are never job-owned and are
    never removed here.
    """

    if request.profile is not None:
        return request.profile.cleanup.remove_container_on_exit
    return (
        isinstance(request.request, UnrestrictedContainerRequest)
        and request.request.resources.gpu is not None
    )

def _request_cleanup_policy(request: ValidatedWorkloadRequest) -> dict[str, object]:
    if request.profile is not None:
        return request.profile.cleanup.model_dump(mode="json", by_alias=True)
    return {
        "removeContainerOnExit": _removes_container_on_exit(request),
        "killGraceSeconds": _DEFAULT_KILL_GRACE_SECONDS,
    }

def _build_unrestricted_run_args(
    *,
    docker_binary: str,
    request: ValidatedWorkloadRequest,
) -> list[str]:
    workload = request.request
    if isinstance(workload, UnrestrictedDockerRequest):
        return [docker_binary, *workload.command[1:]]
    if not isinstance(workload, UnrestrictedContainerRequest):
        raise DockerWorkloadLauncherError("unsupported unrestricted workload request")
    args = [
        docker_binary,
        "run",
        "--name",
        request.container_name,
        "--workdir",
        workload.workdir or workload.repo_dir,
        "--network",
        workload.network_mode,
        *structured_container_security_args(),
    ]
    for key, value in _operational_labels(request).items():
        args.extend(["--label", f"{key}={value}"])
    args.extend(["--mount", f"type=bind,source={workload.repo_dir},target={workload.repo_dir}"])
    args.extend(["--mount", f"type=bind,source={workload.artifacts_dir},target={workload.artifacts_dir}"])
    args.extend(["--mount", f"type=bind,source={workload.scratch_dir},target={workload.scratch_dir}"])
    for mount in workload.cache_mounts:
        suffix = ",readonly" if mount.read_only else ""
        args.extend(["--mount", f"type=volume,source={mount.source},target={mount.target}{suffix}"])
    for key, value in workload.env_overrides.items():
        args.extend(["--env", f"{key}={value}"])
    for flag, value in _effective_resources(
        profile=_UNRESTRICTED_RUNNER_PROFILE,
        overrides=workload.resources,
    ).items():
        args.extend([flag, value])
    if workload.resources.gpu is not None:
        # The caller owns the GPU resource request; MoonMind only realizes it as
        # the vendor device request. No profile device policy is consulted on
        # the unrestricted container path.
        args.extend(gpu_device_request_args(workload.resources.gpu))
    if workload.entrypoint:
        args.extend(["--entrypoint", workload.entrypoint[0]])
    args.append(workload.image)
    if workload.entrypoint[1:]:
        args.extend(workload.entrypoint[1:])
    args.extend(workload.command)
    return args

def _ensure_paths_are_mounted(request: ValidatedWorkloadRequest) -> None:
    if request.profile is None:
        return
    mounts = (*request.profile.required_mounts, *request.profile.optional_mounts)
    workload = request.request
    if not _path_is_under_mount(workload.repo_dir, mounts):
        raise DockerWorkloadLauncherError(
            f"repoDir is not covered by approved profile mounts: {workload.repo_dir}"
        )
    if not _path_is_under_mount(workload.artifacts_dir, mounts):
        raise DockerWorkloadLauncherError(
            "artifactsDir is not covered by approved profile mounts: "
            f"{workload.artifacts_dir}"
        )

class DockerContainerJanitor:
    """Small Docker cleanup helper for workload containers."""

    def __init__(
        self,
        *,
        docker_binary: str = "docker",
        docker_host: str | None = None,
    ) -> None:
        self._docker_binary = docker_binary
        self._docker_host = docker_host

    async def stop(self, container_name: str, *, grace_seconds: int) -> None:
        await self._run_control(
            ["stop", "-t", str(max(0, grace_seconds)), container_name],
        )

    async def kill(self, container_name: str) -> None:
        await self._run_control(["kill", container_name])

    async def remove(self, container_name: str) -> None:
        await self._run_control(["rm", "-f", container_name])

    async def find_by_labels(self, labels: Mapping[str, str]) -> tuple[str, ...]:
        args = ["ps", "-a"]
        for key, value in labels.items():
            args.extend(["--filter", f"label={key}={value}"])
        args.extend(["--format", "{{.ID}}"])
        result = await self._run_control(args)
        return tuple(
            line.strip()
            for line in _decode_stream(result[0]).splitlines()
            if line.strip()
        )

    async def sweep_expired_workloads(
        self,
        *,
        now_iso: str | None = None,
    ) -> tuple[str, ...]:
        """Remove orphaned workload containers whose TTL label has expired."""

        return await self._sweep_expired_kind(
            kind="workload",
            now_iso=now_iso,
        )

    async def sweep_expired_helpers(
        self,
        *,
        now_iso: str | None = None,
    ) -> tuple[str, ...]:
        """Remove orphaned bounded helper containers whose TTL label has expired."""

        return await self._sweep_expired_kind(
            kind="bounded_service",
            now_iso=now_iso,
        )

    async def _sweep_expired_kind(
        self,
        *,
        kind: str,
        now_iso: str | None = None,
    ) -> tuple[str, ...]:
        now = _parse_iso_datetime(now_iso or _isoformat(datetime.now(UTC)) or "")
        if now is None:
            now = datetime.now(UTC)
        stdout, _stderr, _returncode = await self._run_control(
            [
                "ps",
                "-a",
                "--filter",
                f"label=moonmind.kind={kind}",
                "--format",
                '{{.ID}}\t{{.Names}}\t{{.Label "moonmind.expires_at"}}',
            ]
        )
        expired: list[str] = []
        for line in _decode_stream(stdout).splitlines():
            parts = line.split("\t")
            if len(parts) < 3:
                continue
            container_id = parts[0].strip()
            expires_at = _parse_iso_datetime(parts[2])
            if container_id and expires_at is not None and expires_at <= now:
                await self.remove(container_id)
                expired.append(container_id)
        return tuple(expired)

    async def _run_control(self, args: Sequence[str]) -> tuple[bytes, bytes, int]:
        process = await asyncio.create_subprocess_exec(
            self._docker_binary,
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=_docker_env(docker_host=self._docker_host),
        )
        stdout, stderr = await process.communicate()
        return stdout, stderr, int(process.returncode or 0)

class DockerWorkloadLauncher:
    """Turn a validated workload request into one bounded Docker execution."""

    def __init__(
        self,
        *,
        docker_binary: str = "docker",
        docker_host: str | None = None,
        janitor: DockerContainerJanitor | None = None,
        concurrency_limiter: DockerWorkloadConcurrencyLimiter | None = None,
    ) -> None:
        self._docker_binary = docker_binary
        self._docker_host = docker_host
        self._janitor = janitor or DockerContainerJanitor(
            docker_binary=docker_binary,
            docker_host=docker_host,
        )
        self._concurrency_limiter = (
            concurrency_limiter or DockerWorkloadConcurrencyLimiter()
        )
        self._helper_leases: dict[str, _ConcurrencyLease] = {}
        self._helper_egress_evidence: dict[
            str, tuple[EgressAttestation, dict[str, object], datetime]
        ] = {}

    async def _attest_egress_before_launch(
        self, request: ValidatedWorkloadRequest
    ) -> EgressAttestation | None:
        """Fail closed at the shared process-creation boundary.

        Argument construction is deliberately side-effect free, but a
        restricted-egress profile must not become a Docker process based on
        declared network metadata alone. Both one-shot and helper launches
        pass this boundary.

        The proven attestation is returned to the caller so each workload
        lifecycle can publish its durable evidence (profile/applied-rule digest
        and validation time) instead of discarding it.
        """

        if (
            request.profile is None
            or request.profile.network_policy != "restricted_egress"
        ):
            return None

        async def runner(args: Sequence[str]) -> tuple[int, bytes, bytes]:
            stdout, stderr, code = await self._janitor._run_control(args)
            return code, stdout, stderr

        return await attest_docker_egress(
            runner=runner,
            profile=DEFAULT_EGRESS_PROFILE,
            backend_ref="docker-workload-launcher",
        )

    async def _attest_workload_egress_after_launch(
        self,
        request: ValidatedWorkloadRequest,
        *,
        attestation: EgressAttestation,
        started_at: datetime,
        finished_at: datetime | None = None,
    ) -> dict[str, object]:
        async def runner(args: Sequence[str]) -> tuple[int, bytes, bytes]:
            stdout, stderr, code = await self._janitor._run_control(args)
            return code, stdout, stderr

        return await attest_docker_workload_egress(
            runner=runner,
            profile=DEFAULT_EGRESS_PROFILE,
            attestation=attestation,
            attachment_identity=request.container_name,
            expected_image_ref=(
                request.profile.image
                if request.profile is not None
                else str(request.request.image or "")
            ),
            started_at=started_at,
            finished_at=finished_at,
        )

    async def _verify_container_cleanup(
        self,
        request: ValidatedWorkloadRequest,
    ) -> dict[str, object]:
        if (
            request.profile is None
        ):
            raise DockerWorkloadLauncherError(
                "helper cleanup requires a resolved runner profile"
            )
        if not request.profile.cleanup.remove_container_on_exit:
            stdout, _stderr, code = await self._janitor._run_control(
                (
                    "inspect",
                    "--format",
                    "{{.State.Running}}",
                    request.container_name,
                )
            )
            if code == 0 and stdout.strip().lower() == "true":
                raise DockerWorkloadLauncherError(
                    "retained helper cleanup could not be verified"
                )
            return {
                "cleanupResult": "retained_by_policy",
                "reconciliationResult": "succeeded",
            }
        stdout, _stderr, code = await self._janitor._run_control(
            (
                "ps",
                "-a",
                "--filter",
                f"name=^/{request.container_name}$",
                "--format",
                "{{.Names}}",
            )
        )
        if code != 0 or stdout.strip():
            raise DockerWorkloadLauncherError(
                "helper workload cleanup could not be verified"
            )
        return {
            "cleanupResult": "succeeded",
            "reconciliationResult": "succeeded",
        }

    @staticmethod
    def _egress_attestation_evidence(
        egress_attestation: EgressAttestation | None,
    ) -> dict[str, object] | None:
        if egress_attestation is None:
            return None
        return egress_attestation.model_dump(by_alias=True, mode="json")

    def build_run_args(
        self,
        request: ValidatedWorkloadRequest,
        *,
        egress_attestation: EgressAttestation | None = None,
    ) -> list[str]:
        _ensure_paths_are_mounted(request)
        profile = request.profile
        workload = request.request
        if profile is None:
            return _build_unrestricted_run_args(
                docker_binary=self._docker_binary,
                request=request,
            )
        network_ref, egress_args = _profile_network_args(profile.network_policy)
        args = [
            self._docker_binary,
            "run",
            "--name",
            request.container_name,
            "--workdir",
            workload.repo_dir,
            "--network",
            network_ref,
            *structured_container_security_args(),
            *egress_args,
            *_egress_launch_binding_args(egress_attestation),
        ]

        for key, value in _operational_labels(request).items():
            args.extend(["--label", f"{key}={value}"])
        for mount in (*profile.required_mounts, *profile.optional_mounts):
            args.extend(["--mount", _mount_arg(mount)])
        for mount in profile.credential_mounts:
            args.extend(["--mount", _mount_arg(mount)])
        for key, value in workload.env_overrides.items():
            args.extend(["--env", f"{key}={value}"])
        for flag, value in _effective_resources(
            profile=profile,
            overrides=workload.resources,
        ).items():
            args.extend([flag, value])
        if profile.entrypoint:
            args.extend(["--entrypoint", profile.entrypoint[0]])

        args.append(profile.image)
        if len(profile.entrypoint) > 1:
            args.extend(profile.entrypoint[1:])
        args.extend(profile.command_wrapper)
        args.extend(
            _workload_command_args(
                command_wrapper=profile.command_wrapper,
                workload_command=workload.command,
            )
        )
        return args

    def build_helper_run_args(
        self,
        request: ValidatedWorkloadRequest,
        *,
        egress_attestation: EgressAttestation | None = None,
    ) -> list[str]:
        profile = request.profile
        workload = request.request
        if profile is None:
            return _build_unrestricted_run_args(
                docker_binary=self._docker_binary,
                request=request,
            )
        if profile.kind != "bounded_service":
            raise DockerWorkloadLauncherError(
                "start_helper requires a bounded_service runner profile"
            )
        _ensure_paths_are_mounted(request)
        network_ref, egress_args = _profile_network_args(profile.network_policy)
        args = [
            self._docker_binary,
            "run",
            "--detach",
            "--name",
            request.container_name,
            "--workdir",
            workload.repo_dir,
            "--network",
            network_ref,
            *structured_container_security_args(),
            *egress_args,
            *_egress_launch_binding_args(egress_attestation),
        ]
        for key, value in _operational_labels(request).items():
            args.extend(["--label", f"{key}={value}"])
        for mount in (*profile.required_mounts, *profile.optional_mounts):
            args.extend(["--mount", _mount_arg(mount)])
        for mount in profile.credential_mounts:
            args.extend(["--mount", _mount_arg(mount)])
        for key, value in workload.env_overrides.items():
            args.extend(["--env", f"{key}={value}"])
        for flag, value in _effective_resources(
            profile=profile,
            overrides=workload.resources,
        ).items():
            args.extend([flag, value])
        if profile.entrypoint:
            args.extend(["--entrypoint", profile.entrypoint[0]])
        args.append(profile.image)
        if len(profile.entrypoint) > 1:
            args.extend(profile.entrypoint[1:])
        args.extend(profile.command_wrapper)
        args.extend(
            _workload_command_args(
                command_wrapper=profile.command_wrapper,
                workload_command=workload.command,
            )
        )
        return args

    async def run(
        self,
        request: ValidatedWorkloadRequest,
        *,
        timeout_seconds: float | None = None,
    ) -> WorkloadResult:
        started_at = datetime.now(UTC)
        stdout_buffer = bytearray()
        stderr_buffer = bytearray()
        exit_code: int | None = None
        timeout_reason: str | None = None
        configured_timeout = (
            timeout_seconds
            if timeout_seconds is not None
            else _request_timeout_seconds(request)
        )
        lease = await self._concurrency_limiter.acquire(request)
        egress_attestation: EgressAttestation | None = None
        egress_workload_evidence: dict[str, object] | None = None

        try:
            egress_attestation = await self._attest_egress_before_launch(request)
            process = await asyncio.create_subprocess_exec(
                *self.build_run_args(
                    request, egress_attestation=egress_attestation
                ),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=_docker_env(docker_host=self._docker_host),
            )
            try:
                exit_code = await asyncio.wait_for(
                    _wait_with_limited_output(
                        process,
                        stdout_buffer=stdout_buffer,
                        stderr_buffer=stderr_buffer,
                    ),
                    timeout=configured_timeout,
                )
                status = "succeeded" if exit_code == 0 else "failed"
            except asyncio.TimeoutError:
                status = "timed_out"
                timeout_reason = "workload exceeded timeoutSeconds"
                await self._terminate_container(request)
                try:
                    await asyncio.wait_for(
                        _wait_with_limited_output(
                            process,
                            stdout_buffer=stdout_buffer,
                            stderr_buffer=stderr_buffer,
                        ),
                        timeout=max(1, _request_kill_grace_seconds(request)),
                    )
                except asyncio.TimeoutError:
                    process.kill()
                    await _wait_with_limited_output(
                        process,
                        stdout_buffer=stdout_buffer,
                        stderr_buffer=stderr_buffer,
                    )
                exit_code = process.returncode
            except asyncio.CancelledError:
                await self._terminate_container(request)
                if process.returncode is None:
                    process.terminate()
                    try:
                        await asyncio.wait_for(
                            _wait_with_limited_output(
                                process,
                                stdout_buffer=stdout_buffer,
                                stderr_buffer=stderr_buffer,
                            ),
                            timeout=max(1, _request_kill_grace_seconds(request)),
                        )
                    except asyncio.TimeoutError:
                        process.kill()
                        await _wait_with_limited_output(
                            process,
                            stdout_buffer=stdout_buffer,
                            stderr_buffer=stderr_buffer,
                        )
                raise
            finally:
                try:
                    if (
                        egress_attestation is not None
                        and process.returncode is not None
                    ):
                        egress_workload_evidence = (
                            await self._attest_workload_egress_after_launch(
                                request,
                                attestation=egress_attestation,
                                started_at=started_at,
                                finished_at=datetime.now(UTC),
                            )
                        )
                finally:
                    if _removes_container_on_exit(request):
                        await self._janitor.remove(request.container_name)
                    if egress_workload_evidence is not None:
                        cleanup_evidence = await self._verify_container_cleanup(
                            request
                        )
                        egress_workload_evidence.update(cleanup_evidence)
                        egress_workload_evidence["cleanupValidatedAt"] = _isoformat(
                            datetime.now(UTC)
                        )
        finally:
            await lease.release()

        completed_at = datetime.now(UTC)
        duration_seconds = (completed_at - started_at).total_seconds()
        stdout = redact_sensitive_text(_decode_stream(bytes(stdout_buffer)))
        stderr = redact_sensitive_text(_decode_stream(bytes(stderr_buffer)))
        workload_metadata = _workload_metadata(
            request,
            status=status,
            exit_code=exit_code,
            started_at=started_at,
            completed_at=completed_at,
            duration_seconds=duration_seconds,
            timeout_reason=timeout_reason,
            stderr=stderr,
        )
        diagnostics = {
            **workload_metadata,
            "command": list(request.request.command),
            "envOverrideKeys": sorted(request.request.env_overrides),
            "declaredOutputs": dict(request.request.declared_outputs),
            "collectGlobs": list(getattr(request.request, "collect_globs", ()) or ()),
            "resourceOverrides": request.request.resources.model_dump(
                mode="json",
                by_alias=True,
                exclude_none=True,
            ),
            "cleanup": _request_cleanup_policy(request),
        }
        declared_refs, missing_declared_outputs = _declared_output_refs(request)
        collected_refs, collected_outputs = _collect_workspace_artifacts(request)
        report_publication = _report_publication_metadata(
            request,
            declared_output_refs=declared_refs,
            missing_declared_outputs=missing_declared_outputs,
        )
        diagnostics["declaredOutputRefs"] = dict(declared_refs)
        diagnostics["missingDeclaredOutputs"] = dict(missing_declared_outputs)
        diagnostics["collectedOutputRefs"] = dict(collected_refs)
        diagnostics["collectedOutputs"] = collected_outputs
        diagnostics["reportPublication"] = report_publication
        egress_evidence = self._egress_attestation_evidence(egress_attestation)
        diagnostics["egressAttestation"] = egress_evidence
        diagnostics["egressWorkloadEvidence"] = egress_workload_evidence
        workload_metadata["egressAttestation"] = egress_evidence
        workload_metadata["egressWorkloadEvidence"] = egress_workload_evidence
        stdout_ref, stderr_ref, diagnostics_ref, output_refs, artifact_publication = (
            _publish_workload_artifacts(
                request,
                stdout=stdout,
                stderr=stderr,
                diagnostics=diagnostics,
                declared_output_refs=declared_refs,
                collected_output_refs=collected_refs,
            )
        )
        workload_metadata["artifactPublication"] = artifact_publication
        workload_metadata["reportPublication"] = report_publication
        metadata = redact_sensitive_payload({
            "containerName": request.container_name,
            "image": request.profile.image if request.profile is not None else getattr(request.request, "image", None),
            "imageRef": request.profile.image if request.profile is not None else getattr(request.request, "image", None),
            "dockerHost": self._docker_host or os.environ.get("DOCKER_HOST", ""),
            "artifactsDir": request.request.artifacts_dir,
            "stdout": stdout,
            "stderr": stderr,
            "workload": workload_metadata,
            "artifactPublication": artifact_publication,
            "reportPublication": report_publication,
        })
        return WorkloadResult(
            requestId=request.container_name,
            profileId=request.profile.id if request.profile is not None else request.request.tool_name,
            status=status,
            labels=request.ownership.labels,
            exitCode=exit_code,
            startedAt=started_at,
            completedAt=completed_at,
            durationSeconds=duration_seconds,
            timeoutReason=timeout_reason,
            stdoutRef=stdout_ref,
            stderrRef=stderr_ref,
            diagnosticsRef=diagnostics_ref,
            outputRefs=output_refs,
            metadata=metadata,
        )

    async def start_helper(
        self,
        request: ValidatedWorkloadRequest,
    ) -> WorkloadResult:
        started_at = datetime.now(UTC)
        lease = await self._concurrency_limiter.acquire(request)
        egress_attestation: EgressAttestation | None = None
        egress_workload_evidence: dict[str, object] | None = None
        egress_authority_ref: str | None = None
        try:
            egress_attestation = await self._attest_egress_before_launch(request)
            process = await asyncio.create_subprocess_exec(
                *self.build_helper_run_args(
                    request, egress_attestation=egress_attestation
                ),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=_docker_env(docker_host=self._docker_host),
            )
            stdout, stderr = await process.communicate()
            if int(process.returncode or 0) != 0:
                completed_at = datetime.now(UTC)
                return self._helper_result(
                    request,
                    status="failed",
                    started_at=started_at,
                    completed_at=completed_at,
                    stdout=_decode_stream(stdout),
                    stderr=_decode_stream(stderr),
                    readiness={
                        "status": "not_started",
                        "reason": "docker run failed",
                    },
                    egress_attestation=egress_attestation,
                )
            self._helper_leases[request.container_name] = lease
            lease = None
            if egress_attestation is not None:
                egress_workload_evidence = (
                    await self._attest_workload_egress_after_launch(
                        request,
                        attestation=egress_attestation,
                        started_at=started_at,
                        finished_at=datetime.now(UTC),
                    )
                )
                # Persist ownership immediately after attachment, before a
                # readiness await can be cancelled.  The artifact directory is
                # workflow-owned shared state, so a replacement Activity worker
                # can recover the exact attestation and clean the same helper.
                egress_authority_ref = _persist_helper_egress_authority(
                    request,
                    state="attached",
                    attestation=egress_attestation,
                    workload_evidence=egress_workload_evidence,
                    started_at=started_at,
                )
                self._helper_egress_evidence[request.container_name] = (
                    egress_attestation,
                    egress_workload_evidence,
                    started_at,
                )
            readiness = await self._wait_for_helper_readiness(request)
        except (Exception, asyncio.CancelledError):
            helper_lease = self._helper_leases.get(request.container_name)
            if helper_lease is not None:
                cleanup_error: BaseException | None = None
                cleanup_evidence: dict[str, object] = {}
                try:
                    await asyncio.shield(self._janitor.remove(request.container_name))
                    cleanup_evidence = await asyncio.shield(
                        self._verify_container_cleanup(request)
                    )
                except BaseException as exc:
                    cleanup_error = exc
                    cleanup_evidence = {
                        "cleanupResult": "failed",
                        "reconciliationResult": "required",
                        "cleanupErrorCode": type(exc).__name__,
                    }
                authority_workload_evidence = egress_workload_evidence
                if egress_attestation is not None:
                    if authority_workload_evidence is None:
                        authority_workload_evidence = {
                            **egress_attestation.model_dump(
                                by_alias=True, mode="json"
                            ),
                            "attachmentIdentity": request.container_name,
                            "attachmentRef": f"container:{request.container_name}",
                            "workloadValidationResult": "not_completed",
                        }
                    _persist_helper_egress_authority(
                        request,
                        state=(
                            "cleanup_failed"
                            if cleanup_error is not None
                            else "cancelled"
                        ),
                        attestation=egress_attestation,
                        workload_evidence=authority_workload_evidence,
                        started_at=started_at,
                        cleanup_evidence={
                            **cleanup_evidence,
                            "cleanupValidatedAt": _isoformat(datetime.now(UTC)),
                        },
                        lease_release_result=(
                            "held_for_reconciliation"
                            if cleanup_error is not None
                            else "released_after_interrupted_start"
                        ),
                    )
                    self._helper_egress_evidence[request.container_name] = (
                        egress_attestation,
                        authority_workload_evidence,
                        started_at,
                    )
                if cleanup_error is None:
                    self._helper_leases.pop(request.container_name, None)
                    self._helper_egress_evidence.pop(request.container_name, None)
                    await asyncio.shield(helper_lease.release())
            raise
        finally:
            if lease is not None:
                await lease.release()

        completed_at = datetime.now(UTC)
        status = "ready" if readiness.get("status") == "ready" else "unhealthy"
        return self._helper_result(
            request,
            status=status,
            started_at=started_at,
            completed_at=completed_at,
            stdout=_decode_stream(stdout),
            stderr=_decode_stream(stderr),
            readiness=readiness,
            egress_attestation=egress_attestation,
            egress_workload_evidence=egress_workload_evidence,
            egress_authority_ref=egress_authority_ref,
        )

    async def stop_helper(
        self,
        request: ValidatedWorkloadRequest,
        *,
        reason: str = "bounded_window_complete",
    ) -> WorkloadResult:
        if request.profile.kind != "bounded_service":
            raise DockerWorkloadLauncherError(
                "stop_helper requires a bounded_service runner profile"
        )
        started_at = datetime.now(UTC)
        egress_state = self._helper_egress_evidence.get(request.container_name)
        had_in_memory_authority = egress_state is not None
        durable_state = None
        if request.profile.network_policy == "restricted_egress":
            durable_state = _load_helper_egress_authority(request)
            if egress_state is None and durable_state is not None:
                egress_state = durable_state[:3]
        stdout, stderr = await self._collect_container_logs(request.container_name)
        egress_attestation = egress_state[0] if egress_state is not None else None
        egress_workload_evidence = egress_state[1] if egress_state is not None else None
        terminal_validation_error: Exception | None = (
            DockerWorkloadLauncherError(
                "restricted-egress helper authority is unavailable"
            )
            if request.profile.network_policy == "restricted_egress"
            and egress_state is None
            else None
        )
        cleanup_error: Exception | None = None
        cleanup_evidence: dict[str, object] = {}
        if egress_attestation is not None and egress_state is not None:
            try:
                egress_workload_evidence = (
                    await self._attest_workload_egress_after_launch(
                        request,
                        attestation=egress_attestation,
                        started_at=egress_state[2],
                        finished_at=datetime.now(UTC),
                    )
                )
            except Exception as exc:
                terminal_validation_error = exc
        try:
            await self._terminate_container(request)
        except Exception:
            # Removal and objective reconciliation remain authoritative.
            pass
        try:
            if request.profile.cleanup.remove_container_on_exit:
                await self._janitor.remove(request.container_name)
        except Exception:
            # A failed remove command is auxiliary if reconciliation proves
            # the owned attachment is already absent.
            pass
        try:
            cleanup_evidence = await self._verify_container_cleanup(request)
        except Exception as exc:
            cleanup_error = exc
            cleanup_evidence = {
                "cleanupResult": "failed",
                "reconciliationResult": "required",
                "cleanupErrorCode": type(exc).__name__,
            }
        completed_at = datetime.now(UTC)
        egress_workload_evidence = (
            {
                **egress_workload_evidence,
                **cleanup_evidence,
                "cleanupValidatedAt": _isoformat(completed_at),
                "terminalValidationResult": (
                    "failed" if terminal_validation_error is not None else "passed"
                ),
                **(
                    {
                        "terminalValidationErrorCode": type(
                            terminal_validation_error
                        ).__name__
                    }
                    if terminal_validation_error is not None
                    else {}
                ),
            }
            if egress_workload_evidence is not None
            else None
        )
        egress_authority_ref = None
        if egress_attestation is not None and egress_workload_evidence is not None:
            egress_authority_ref = _persist_helper_egress_authority(
                request,
                state=(
                    "cleanup_failed"
                    if cleanup_error is not None
                    else "cleanup_validated"
                ),
                attestation=egress_attestation,
                workload_evidence=egress_workload_evidence,
                started_at=egress_state[2],
                cleanup_evidence={
                    "cleanupResult": egress_workload_evidence.get("cleanupResult"),
                    "reconciliationResult": egress_workload_evidence.get(
                        "reconciliationResult"
                    ),
                    "cleanupValidatedAt": egress_workload_evidence.get(
                        "cleanupValidatedAt"
                    ),
                },
                lease_release_result=(
                    "held_for_reconciliation"
                    if cleanup_error is not None
                    else "held_until_evidence_persisted"
                ),
            )
        if cleanup_error is None:
            lease = self._helper_leases.pop(request.container_name, None)
            if lease is not None:
                await lease.release()
            if egress_attestation is not None and egress_workload_evidence is not None:
                egress_authority_ref = _persist_helper_egress_authority(
                    request,
                    state="stopped",
                    attestation=egress_attestation,
                    workload_evidence=egress_workload_evidence,
                    started_at=egress_state[2],
                    cleanup_evidence={
                        "cleanupResult": egress_workload_evidence.get(
                            "cleanupResult"
                        ),
                        "reconciliationResult": egress_workload_evidence.get(
                            "reconciliationResult"
                        ),
                        "cleanupValidatedAt": egress_workload_evidence.get(
                            "cleanupValidatedAt"
                        ),
                    },
                    lease_release_result=(
                        "released_after_reconciliation"
                        if durable_state is not None
                        and (
                            not had_in_memory_authority
                            or any(
                                marker in durable_state[3]
                                for marker in (
                                    "cleanup_failed",
                                    "cleanup_validated",
                                )
                            )
                        )
                        else "released"
                    ),
                )
            self._helper_egress_evidence.pop(request.container_name, None)
        if cleanup_error is not None:
            raise DockerWorkloadLauncherError(
                "helper cleanup requires reconciliation; "
                f"evidence={egress_authority_ref or 'unavailable'}"
            ) from cleanup_error
        if terminal_validation_error is not None:
            raise DockerWorkloadLauncherError(
                "restricted-egress helper terminal attestation failed after cleanup; "
                f"evidence={egress_authority_ref or 'unavailable'}"
            ) from terminal_validation_error
        return self._helper_result(
            request,
            status="stopped",
            started_at=started_at,
            completed_at=completed_at,
            stdout=stdout,
            stderr=stderr,
            readiness={},
            teardown={
                "status": "complete",
                "reason": reason,
                "removeContainerOnExit": request.profile.cleanup.remove_container_on_exit,
            },
            egress_attestation=egress_attestation,
            egress_workload_evidence=egress_workload_evidence,
            egress_authority_ref=egress_authority_ref,
        )

    async def _wait_for_helper_readiness(
        self,
        request: ValidatedWorkloadRequest,
    ) -> dict[str, object]:
        probe = request.profile.readiness_probe
        if probe is None:
            raise DockerWorkloadLauncherError(
                "bounded_service profiles must define a readinessProbe"
            )
        last_stdout = ""
        last_stderr = ""
        for attempt in range(1, probe.retries + 1):
            process = await asyncio.create_subprocess_exec(
                self._docker_binary,
                "exec",
                request.container_name,
                *probe.command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=_docker_env(docker_host=self._docker_host),
            )
            try:
                stdout_buffer = bytearray()
                stderr_buffer = bytearray()
                await asyncio.wait_for(
                    _wait_with_limited_output(
                        process,
                        stdout_buffer=stdout_buffer,
                        stderr_buffer=stderr_buffer,
                    ),
                    timeout=probe.timeout_seconds,
                )
            except asyncio.TimeoutError:
                await _kill_and_reap_process(process)
                last_stdout = ""
                last_stderr = "readiness probe timed out"
                if attempt < probe.retries and probe.interval_seconds:
                    await asyncio.sleep(probe.interval_seconds)
                continue
            last_stdout = _decode_stream(bytes(stdout_buffer))
            last_stderr = _decode_stream(bytes(stderr_buffer))
            if int(process.returncode or 0) == 0:
                return {
                    "status": "ready",
                    "attempts": attempt,
                    "command": list(probe.command),
                    "stdoutBytes": len(last_stdout.encode("utf-8")),
                    "stderrBytes": len(last_stderr.encode("utf-8")),
                }
            if attempt < probe.retries and probe.interval_seconds:
                await asyncio.sleep(probe.interval_seconds)
        return {
            "status": "unhealthy",
            "attempts": probe.retries,
            "command": list(probe.command),
            "stdoutBytes": len(last_stdout.encode("utf-8")),
            "stderrBytes": len(last_stderr.encode("utf-8")),
        }

    def _helper_result(
        self,
        request: ValidatedWorkloadRequest,
        *,
        status: str,
        started_at: datetime,
        completed_at: datetime,
        stdout: str,
        stderr: str,
        readiness: Mapping[str, object],
        teardown: Mapping[str, object] | None = None,
        egress_attestation: EgressAttestation | None = None,
        egress_workload_evidence: Mapping[str, object] | None = None,
        egress_authority_ref: str | None = None,
    ) -> WorkloadResult:
        duration_seconds = (completed_at - started_at).total_seconds()
        helper_metadata = _helper_metadata(
            request,
            status=status,
            started_at=started_at,
            completed_at=completed_at,
            duration_seconds=duration_seconds,
            readiness=readiness,
            teardown=teardown,
        )
        diagnostics = {
            **helper_metadata,
            "command": list(request.request.command),
            "envOverrideKeys": sorted(request.request.env_overrides),
            "collectGlobs": list(getattr(request.request, "collect_globs", ()) or ()),
            "resourceOverrides": request.request.resources.model_dump(
                mode="json",
                by_alias=True,
                exclude_none=True,
            ),
            "cleanup": (request.profile.cleanup.model_dump(mode="json", by_alias=True) if request.profile is not None else {"removeContainerOnExit": False, "killGraceSeconds": 30}),
        }
        declared_refs, missing_declared_outputs = _declared_output_refs(request)
        collected_refs, collected_outputs = _collect_workspace_artifacts(request)
        report_publication = _report_publication_metadata(
            request,
            declared_output_refs=declared_refs,
            missing_declared_outputs=missing_declared_outputs,
        )
        diagnostics["declaredOutputRefs"] = dict(declared_refs)
        diagnostics["missingDeclaredOutputs"] = dict(missing_declared_outputs)
        diagnostics["collectedOutputRefs"] = dict(collected_refs)
        diagnostics["collectedOutputs"] = collected_outputs
        diagnostics["reportPublication"] = report_publication
        egress_evidence = self._egress_attestation_evidence(egress_attestation)
        diagnostics["egressAttestation"] = egress_evidence
        diagnostics["egressWorkloadEvidence"] = (
            dict(egress_workload_evidence)
            if egress_workload_evidence is not None
            else None
        )
        helper_metadata["egressAttestation"] = egress_evidence
        helper_metadata["egressWorkloadEvidence"] = diagnostics[
            "egressWorkloadEvidence"
        ]
        helper_metadata["egressAuthorityRef"] = egress_authority_ref
        diagnostics["egressAuthorityRef"] = egress_authority_ref
        stdout_ref, stderr_ref, diagnostics_ref, output_refs, artifact_publication = (
            _publish_workload_artifacts(
                request,
                stdout=stdout,
                stderr=stderr,
                diagnostics=diagnostics,
                declared_output_refs=declared_refs,
                collected_output_refs=collected_refs,
            )
        )
        helper_metadata["artifactPublication"] = artifact_publication
        helper_metadata["reportPublication"] = report_publication
        if egress_authority_ref is not None:
            output_refs["security.egress.authority"] = egress_authority_ref
        metadata = redact_sensitive_payload({
            "containerName": request.container_name,
            "image": request.profile.image if request.profile is not None else getattr(request.request, "image", None),
            "imageRef": request.profile.image if request.profile is not None else getattr(request.request, "image", None),
            "dockerHost": self._docker_host or os.environ.get("DOCKER_HOST", ""),
            "artifactsDir": request.request.artifacts_dir,
            "stdout": stdout,
            "stderr": stderr,
            "helper": helper_metadata,
            "artifactPublication": artifact_publication,
            "reportPublication": report_publication,
        })
        return WorkloadResult(
            requestId=request.container_name,
            profileId=request.profile.id if request.profile is not None else request.request.tool_name,
            status=status,
            labels=request.ownership.labels,
            exitCode=None,
            startedAt=started_at,
            completedAt=completed_at,
            durationSeconds=duration_seconds,
            stdoutRef=stdout_ref,
            stderrRef=stderr_ref,
            diagnosticsRef=diagnostics_ref,
            outputRefs=output_refs,
            metadata=metadata,
        )

    async def _collect_container_logs(self, container_name: str) -> tuple[str, str]:
        stdout_buffer = bytearray()
        stderr_buffer = bytearray()
        process = await asyncio.create_subprocess_exec(
            self._docker_binary,
            "logs",
            container_name,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=_docker_env(docker_host=self._docker_host),
        )
        await _wait_with_limited_output(
            process,
            stdout_buffer=stdout_buffer,
            stderr_buffer=stderr_buffer,
        )
        return _decode_stream(bytes(stdout_buffer)), _decode_stream(bytes(stderr_buffer))

    async def _terminate_container(self, request: ValidatedWorkloadRequest) -> None:
        await self._janitor.stop(
            request.container_name,
            grace_seconds=request.profile.cleanup.kill_grace_seconds if request.profile is not None else 30,
        )
        await self._janitor.kill(request.container_name)
