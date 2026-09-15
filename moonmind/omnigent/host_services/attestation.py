"""Exact Docker, harness, credential, Skill, egress, and model attestation."""

from __future__ import annotations

import asyncio
import json
from typing import Any

from moonmind.omnigent.bridge_artifacts import OmnigentArtifactGateway
from moonmind.omnigent.harness_platform.failures import (
    HarnessPlatformError,
    HarnessPlatformFailure,
)
from moonmind.omnigent.harness_platform.host_classes import HostClass
from moonmind.omnigent.host_ports import HostLaunchSpec
from moonmind.omnigent.host_services.docker_backend import DockerCommandBackend
from moonmind.omnigent.host_services.github_credentials import (
    github_repository_from_request,
)
from moonmind.schemas.agent_runtime_models import AgentExecutionRequest
from moonmind.security.egress import (
    OMNIGENT_EGRESS_PROFILE,
    EgressAttestation,
    attest_docker_workload_egress,
)
from moonmind.workflows.adapters.omnigent_client import OmnigentClientError

# Resolve the upstream package layout inside the admitted image. Both 0.12 and
# 0.13 hosts can be digest-pinned by existing plans. Select before importing:
# a broken helper or dependency in the selected layout must fail attestation,
# never trigger an import-error fallback to a different implementation.
_OPENCODE_APP_SERVER_IMPORT = (
    "import importlib, importlib.util; "
    "opencode_app_server = importlib.import_module("
    "'omnigent.harnesses.opencode_native.app_server' "
    "if importlib.util.find_spec('omnigent.harnesses') is not None "
    "else 'omnigent.opencode_native_app_server'); "
)
# Reserved exit status meaning "the attestation helper itself is unavailable in
# the host image". The wrapper pairs it with the marker line below; only that
# pair is remapped, so a probed command that happens to exit 97 keeps its own
# authoritative contract status.
_PROBE_SUBSTRATE_UNAVAILABLE_EXIT_CODE = 97
_PROBE_SUBSTRATE_UNAVAILABLE_MARKER = "moonmind-attestation-substrate-unavailable"
_MODEL_ATTESTATION_MAX_ATTEMPTS = 3


def _substrate_guarded_probe(body: str) -> str:
    """Wrap one line of in-host probe statements so helper drift fails typed.

    A missing module, renamed helper, or changed helper signature exits with
    the reserved status and a marker line naming the exception. The probed
    command's own exit status still propagates unchanged.
    """

    return (
        "import sys\n"
        "try:\n"
        f"    {body}\n"
        "except (ImportError, AttributeError, TypeError) as exc:\n"
        f"    sys.stderr.write({_PROBE_SUBSTRATE_UNAVAILABLE_MARKER!r} + ': ' "
        "+ type(exc).__name__ + ': ' + str(exc) + '\\n')\n"
        f"    raise SystemExit({_PROBE_SUBSTRATE_UNAVAILABLE_EXIT_CODE})\n"
    )


def _raise_if_probe_substrate_unavailable(
    code: int, stderr: str, *, boundary: str
) -> None:
    """Translate the reserved probe status into an actionable build mismatch."""

    if code != _PROBE_SUBSTRATE_UNAVAILABLE_EXIT_CODE:
        return
    marker_line = next(
        (
            line
            for line in stderr.splitlines()
            if line.startswith(_PROBE_SUBSTRATE_UNAVAILABLE_MARKER)
        ),
        None,
    )
    if marker_line is None:
        # The reserved status without the wrapper's marker is the probed
        # command's own exit status: authoritative contract evidence, not a
        # substrate fault.
        return
    detail = marker_line[len(_PROBE_SUBSTRATE_UNAVAILABLE_MARKER) :].lstrip(": ")
    detail = detail.strip()[:200]
    message = f"{boundary} attestation helper is unavailable in the exact host image"
    if detail:
        message = f"{message}: {detail}"
    raise HarnessPlatformError(
        message, code=HarnessPlatformFailure.OMNIGENT_HARNESS_BUILD_MISMATCH
    )


def _model_ids(value: Any) -> set[str]:
    found: set[str] = set()
    if isinstance(value, str):
        if "/" in value:
            found.add(value)
    elif isinstance(value, dict):
        for key in ("qualifiedId", "id", "model", "value"):
            item = value.get(key)
            if isinstance(item, str) and "/" in item:
                found.add(item)
        for item in value.values():
            found.update(_model_ids(item))
    elif isinstance(value, list):
        for item in value:
            found.update(_model_ids(item))
    return found


async def _read_exact_host_model_options(
    *,
    backend: DockerCommandBackend,
    client: Any,
    container_name: str,
    omnigent_host_id: str,
    harness_id: str,
) -> tuple[Any, str]:
    """Read model options from the exact host through its supported substrate.

    Omnigent's host tunnel does not expose pre-launch model options for
    ``opencode-native``. The upstream package does expose one portable catalog
    helper, and the selected host already owns the exact image, environment,
    egress policy, and materialized credential that helper must inspect. Run
    that helper inside the host instead of turning the tunnel's honest
    unsupported-harness response into a generic HTTP 502.
    """

    if harness_id != "opencode-native":
        try:
            model_options = await client.get_host_model_options(
                omnigent_host_id, harness_id
            )
        except OmnigentClientError as exc:
            status = exc.status_code
            transient = exc.failure_class == "integration_error" and (
                status is None or status in {408, 429} or 500 <= status < 600
            )
            if not transient:
                raise
            # Normalize at the catalog boundary so the same bounded policy
            # owns CLI and tunnel read recovery. Auth/input failures retain
            # their original authority; provider diagnostics stay private.
            raise HarnessPlatformError(
                "exact host model catalog tunnel read failed",
                code=HarnessPlatformFailure.OMNIGENT_MODEL_UNAVAILABLE,
            ) from exc
        return model_options, "omnigent-host-tunnel"

    probe = _substrate_guarded_probe(
        "import json; " + _OPENCODE_APP_SERVER_IMPORT + "print(json.dumps({'models': "
        "opencode_app_server.list_opencode_cli_model_options()}))"
    )
    code, stdout, stderr = await backend.run(
        [
            "docker",
            "exec",
            container_name,
            "/opt/venv/bin/python",
            "-c",
            probe,
        ],
        timeout_seconds=45.0,
        check=False,
    )
    _raise_if_probe_substrate_unavailable(
        code, stderr, boundary="OpenCode model catalog"
    )
    if code != 0:
        # Provider CLI diagnostics can include credential-sensitive context.
        # Exact-host evidence needs only the typed failure, never raw output.
        raise HarnessPlatformError(
            "exact host OpenCode model catalog probe failed",
            code=HarnessPlatformFailure.OMNIGENT_MODEL_UNAVAILABLE,
        )
    try:
        payload = json.loads(stdout)
    except (TypeError, json.JSONDecodeError) as exc:
        raise HarnessPlatformError(
            "exact host OpenCode model catalog probe returned invalid JSON",
            code=HarnessPlatformFailure.OMNIGENT_MODEL_UNAVAILABLE,
        ) from exc
    if not isinstance(payload, dict) or not isinstance(payload.get("models"), list):
        raise HarnessPlatformError(
            "exact host OpenCode model catalog probe returned an invalid catalog",
            code=HarnessPlatformFailure.OMNIGENT_MODEL_UNAVAILABLE,
        )
    return payload, "exact-host-opencode-cli"


async def _run_exact_host_runner_command(
    *,
    backend: DockerCommandBackend,
    container_name: str,
    argv: list[str],
    timeout_seconds: float = 30.0,
) -> tuple[int, str, str]:
    """Execute a probe through the stock host's runner environment builder."""

    runner_probe = _substrate_guarded_probe(
        "import os, subprocess, sys; "
        "from omnigent.host.connect import _build_runner_env; "
        "env = _build_runner_env(os.environ, "
        "server_url=os.environ.get('OMNIGENT_SERVER_URL', ''), "
        "runner_id='moonmind-attestation', "
        "binding_token='moonmind-attestation', "
        "workspace='/workspaces/run', parent_pid=os.getpid()); "
        "result = subprocess.run(sys.argv[1:], env=env, text=True, "
        "capture_output=True); "
        "sys.stdout.write(result.stdout); sys.stderr.write(result.stderr); "
        "raise SystemExit(result.returncode)"
    )
    code, stdout, stderr = await backend.run(
        [
            "docker",
            "exec",
            container_name,
            "/opt/venv/bin/python",
            "-c",
            runner_probe,
            *argv,
        ],
        timeout_seconds=timeout_seconds,
        check=False,
    )
    _raise_if_probe_substrate_unavailable(code, stderr, boundary="runner environment")
    return code, stdout, stderr


async def _run_exact_host_opencode_command(
    *,
    backend: DockerCommandBackend,
    container_name: str,
    argv: list[str],
    timeout_seconds: float = 30.0,
) -> tuple[int, str, str]:
    """Execute through the runner, OpenCode filter, and MoonMind context shim."""

    filtered_child_probe = _substrate_guarded_probe(
        "import subprocess, sys; from pathlib import Path; "
        + _OPENCODE_APP_SERVER_IMPORT
        + "env = opencode_app_server.filtered_server_env("
        "bridge_dir=Path('/tmp/moonmind-opencode-attestation'), "
        "auth_secret='moonmind-attestation'); "
        "result = subprocess.run("
        "['/home/app/.omnigent/moonmind/bin/moonmind-context', "
        "*sys.argv[1:]], "
        "env=env, text=True, capture_output=True); "
        "sys.stdout.write(result.stdout); sys.stderr.write(result.stderr); "
        "raise SystemExit(result.returncode)"
    )
    opencode_probe = _substrate_guarded_probe(
        "import os, subprocess, sys; "
        "from omnigent.host.connect import _build_runner_env; "
        "runner_env = _build_runner_env(os.environ, "
        "server_url=os.environ.get('OMNIGENT_SERVER_URL', ''), "
        "runner_id='moonmind-attestation', "
        "binding_token='moonmind-attestation', "
        "workspace='/workspaces/run', parent_pid=os.getpid()); "
        f"child = {filtered_child_probe!r}; "
        "result = subprocess.run([sys.executable, '-c', child, *sys.argv[1:]], "
        "env=runner_env, text=True, "
        "capture_output=True); sys.stdout.write(result.stdout); "
        "sys.stderr.write(result.stderr); raise SystemExit(result.returncode)"
    )
    code, stdout, stderr = await backend.run(
        [
            "docker",
            "exec",
            container_name,
            "/opt/venv/bin/python",
            "-c",
            opencode_probe,
            *argv,
        ],
        timeout_seconds=timeout_seconds,
        check=False,
    )
    _raise_if_probe_substrate_unavailable(
        code, stderr, boundary="OpenCode shell environment"
    )
    return code, stdout, stderr


def _assert_exact_omnigent_build(
    image: dict[str, Any], expected_build_digest: str
) -> None:
    labels = image.get("Config", {}).get("Labels", {}) or {}
    if str(labels.get("moonmind.omnigent.build_digest") or "") != (
        expected_build_digest
    ):
        raise HarnessPlatformError(
            "host image Omnigent build identity differs from the catalog",
            code=HarnessPlatformFailure.OMNIGENT_HARNESS_BUILD_MISMATCH,
        )


def _declared_access_is_writable(access_mode: Any) -> bool:
    """Return whether a declared attachment accessMode requires a writable mount.

    OAuth homes declare ``read-write`` (hyphen) while the generic credential
    helper emits ``read_write`` (underscore); both mean the Docker mount must
    be RW so vendor token refresh persists. All other credential attachments
    are read-only.
    """

    return str(access_mode or "").strip().replace("_", "-").lower() == "read-write"


def _attest_workspace_mount(
    mounts: list[dict[str, Any]], attachment: dict[str, Any]
) -> dict[str, Any]:
    matched = next(
        (
            mount
            for mount in mounts
            if str(mount.get("Name") or mount.get("Source") or "")
            == str(attachment["sourceRef"])
            and str(mount.get("Destination") or "") == str(attachment["targetPath"])
        ),
        None,
    )
    expected_writable = _declared_access_is_writable(attachment.get("accessMode"))
    if matched is None or bool(matched.get("RW")) != expected_writable:
        raise HarnessPlatformError(
            "workspace projection does not match the selected mutation policy",
            code=HarnessPlatformFailure.OMNIGENT_HARNESS_BUILD_MISMATCH,
        )
    return {
        "sourceRef": attachment["sourceRef"],
        "targetPath": attachment["targetPath"],
        "accessMode": attachment["accessMode"],
    }


class DockerOmnigentHostAttestor:
    def __init__(
        self,
        *,
        backend: DockerCommandBackend,
        client: Any,
        artifacts: OmnigentArtifactGateway,
    ) -> None:
        self._backend = backend
        self._client = client
        self._artifacts = artifacts

    async def attest(
        self,
        *,
        request: AgentExecutionRequest,
        plan: Any,
        spec: HostLaunchSpec,
        host_class: HostClass,
        launch_result: dict[str, Any],
        registration: dict[str, Any],
        credential_handles: list[dict[str, Any]],
        egress_attestation: dict[str, Any],
    ) -> dict[str, Any]:
        container = await self._backend.inspect_container(
            launch_result["containerName"]
        )
        labels = container.get("Config", {}).get("Labels", {}) or {}
        expected_labels = spec.labels
        if any(
            str(labels.get(key) or "") != value
            for key, value in expected_labels.items()
        ):
            raise HarnessPlatformError(
                "launched host ownership labels do not match the host lease",
                code=HarnessPlatformFailure.OMNIGENT_HARNESS_BUILD_MISMATCH,
            )
        configured_image = str(container.get("Config", {}).get("Image") or "")
        # SHA/patch drift: rebuilt images change digests while keeping
        # major.minor. Exact equality is required when possible, but a
        # same-repository image is acceptable drift -- downstream version gates
        # (omnigent major.minor, vendor major.minor) still enforce release
        # compatibility. Different repositories are never compatible drift.
        if configured_image != host_class.imageRef:
            try:
                from moonmind.omnigent.host_image_drift import (
                    is_compatible_image_drift,
                )

                drift_ok = is_compatible_image_drift(
                    host_class.imageRef, configured_image
                )
            except Exception:
                drift_ok = False
            if not drift_ok:
                raise HarnessPlatformError(
                    "launched host image ref differs from the selected Host Class",
                    code=HarnessPlatformFailure.OMNIGENT_HARNESS_BUILD_MISMATCH,
                )
        # Inspect the actually launched image (fallback-aware): the launcher
        # records the effective digest it created the container from. Falling
        # back to the Host Class ref preserves historical behavior when no
        # drift occurred.
        inspect_ref = str(
            (launch_result or {}).get("launchImageRef")
            or configured_image
            or host_class.imageRef
        ).strip() or host_class.imageRef
        _code, image_json, _err = await self._backend.run(
            ["docker", "image", "inspect", inspect_ref],
            failure_code=HarnessPlatformFailure.OMNIGENT_HARNESS_BUILD_MISMATCH,
        )
        image_rows = json.loads(image_json)
        image = image_rows[0] if isinstance(image_rows, list) and image_rows else {}
        # Probe the executable omnigent version before judging build identity:
        # rebuilt images change SHA/patch while keeping major.minor, and
        # independently built hosts share a release series. Exact build match
        # remains required when versions differ; same-series drift with no
        # operator pin is acceptable (bootstrap judges the same way).
        _code, omnigent_version, _err = await self._backend.run(
            [
                "docker",
                "exec",
                launch_result["containerName"],
                "/opt/venv/bin/omnigent",
                "--version",
            ],
            failure_code=HarnessPlatformFailure.OMNIGENT_HARNESS_BUILD_MISMATCH,
        )
        import logging

        logging.getLogger(__name__).info(
            "omnigent --version probe: expected=%r got=%r",
            host_class.omnigentVersion,
            omnigent_version.strip()[:200],
        )
        from moonmind.omnigent.compatibility import (
            vendor_versions_compatible,
            versions_compatible,
        )

        if not versions_compatible(host_class.omnigentVersion, omnigent_version):
            raise HarnessPlatformError(
                "host Omnigent version differs from the catalog build",
                code=HarnessPlatformFailure.OMNIGENT_HARNESS_BUILD_MISMATCH,
            )
        try:
            _assert_exact_omnigent_build(image, host_class.omnigentBuildDigest)
        except HarnessPlatformError:
            # Same-series rebuilds may carry a different build digest while
            # reporting a compatible major.minor (proven by the live probe
            # above). Accept that only when the launched image actually
            # drifted from the selected Host Class image within the same
            # repository: an unchanged image with a different build label is
            # tampering, not a rebuild. Selection-time pin authority stays in
            # host_class.omnigentBuildDigest and both digests are recorded in
            # evidence below; worker-environment pins are never consulted here
            # so a post-admission pin change cannot silently reinterpret the
            # admitted plan.
            try:
                from moonmind.omnigent.host_image_drift import (
                    is_compatible_image_drift,
                )

                drifted = inspect_ref != host_class.imageRef and bool(
                    is_compatible_image_drift(host_class.imageRef, inspect_ref)
                )
            except Exception:
                drifted = False
            if not drifted:
                raise
            logging.getLogger(__name__).info(
                "host build drift: accepting compatible same-repo build "
                "(expected=%s actual=%s)",
                str(host_class.omnigentBuildDigest)[:19],
                str(
                    (image.get("Config", {}).get("Labels", {}) or {}).get(
                        "moonmind.omnigent.build_digest"
                    )
                    or ""
                )[:19]
                or "unknown",
            )
        repo_digests = set(image.get("RepoDigests") or [])
        if (
            host_class.imageRef not in repo_digests
            and configured_image not in repo_digests
            and inspect_ref not in repo_digests
        ):
            # Docker may omit RepoDigests only for a content-addressed local image;
            # in that case the configured immutable ref remains the exact authority.
            if "@sha256:" not in configured_image:
                raise HarnessPlatformError(
                    "host image repository digest could not be attested",
                    code=HarnessPlatformFailure.OMNIGENT_HARNESS_BUILD_MISMATCH,
                )
        architecture = (
            f"{str(image.get('Os') or 'linux')}/{str(image.get('Architecture') or '')}"
        )
        if architecture not in host_class.architectures:
            raise HarnessPlatformError(
                f"host architecture {architecture} is not admitted",
                code=HarnessPlatformFailure.OMNIGENT_HARNESS_BUILD_MISMATCH,
            )
        # omnigent_version was probed above (before the tolerant build check)
        # so build drift can be judged against the same major.minor series.
        runtime_versions: dict[str, str] = {}
        selected_entry = next(
            item
            for item in host_class.declaredHarnessImplementations
            if item.harnessId == plan.payload.harnessId
            and item.implementationRef == plan.payload.harnessImplementationRef
        )
        for dependency in selected_entry.runtimeDependencies:
            name = str(dependency.get("name") or "").strip()
            version = str(dependency.get("version") or "").strip()
            if not name:
                continue
            _code, observed, _err = await self._backend.run(
                ["docker", "exec", launch_result["containerName"], name, "--version"],
                failure_code=HarnessPlatformFailure.OMNIGENT_VENDOR_RUNTIME_MISMATCH,
            )
            # Patch may evolve (1.18.11 -> 1.18.12); only major.minor steers
            # compatibility. Exact-substring matching fails every vendor patch
            # update even when the image is functionally identical.
            if version and not vendor_versions_compatible(version, observed):
                raise HarnessPlatformError(
                    f"host {name} version does not match the Host Class",
                    code=HarnessPlatformFailure.OMNIGENT_VENDOR_RUNTIME_MISMATCH,
                )
            runtime_versions[name] = observed.strip()[:256]
        mounts = container.get("Mounts") or []
        workspace_mount_evidence = _attest_workspace_mount(
            mounts, spec.workspaceAttachment
        )
        skill_mount = next(
            (
                mount
                for mount in mounts
                if str(mount.get("Name") or mount.get("Source") or "")
                == str(spec.skillAttachment["sourceRef"])
                and str(mount.get("Destination") or "")
                == str(spec.skillAttachment["targetPath"])
            ),
            None,
        )
        if skill_mount is None or bool(skill_mount.get("RW")):
            raise HarnessPlatformError(
                "resolved Skill projection is missing or writable on the exact host",
                code=HarnessPlatformFailure.OMNIGENT_HARNESS_BUILD_MISMATCH,
            )
        skill_target = str(spec.skillAttachment["targetPath"])
        code, out, err = await self._backend.run(
            [
                "docker",
                "exec",
                launch_result["containerName"],
                "/bin/sh",
                "-ceu",
                'test -d "$1"; test -r "$1"',
                "--",
                skill_target,
            ],
            failure_code=HarnessPlatformFailure.OMNIGENT_HARNESS_BUILD_MISMATCH,
            check=False,
        )
        if code != 0:
            detail = (err or out).strip()[:512]
            if detail:
                # Docker transport/container failure (missing container,
                # daemon unavailable, rejected request): preserve the
                # backend diagnostic instead of misdirecting to Skill repair.
                raise HarnessPlatformError(
                    f"Skill projection check failed at Docker boundary for "
                    f"{skill_target}: {detail}",
                    code=HarnessPlatformFailure.OMNIGENT_HARNESS_BUILD_MISMATCH,
                )
            # Silent nonzero from shell ``test``: name the missing projection
            # so the operator does not see an empty Docker failure.
            raise HarnessPlatformError(
                f"resolved Skill projection is missing or unreadable at {skill_target}",
                code=HarnessPlatformFailure.OMNIGENT_HARNESS_BUILD_MISMATCH,
            )
        code, _out, _err = await _run_exact_host_runner_command(
            backend=self._backend,
            container_name=launch_result["containerName"],
            argv=[
                "/bin/sh",
                "-ceu",
                (
                    'test "${MOONMIND_ACTIVE_SKILLS_DIR:-}" = "$1"; '
                    'test -d "$1"; test -r "$1/_manifest.json"; '
                    'test "${MOONMIND_STEP_EXECUTION_ID:-}" = "$2"'
                ),
                "--",
                skill_target,
                spec.stepExecutionId,
            ],
        )
        if code != 0:
            raise HarnessPlatformError(
                "resolved Skill projection is unavailable in the exact runner environment",
                code=HarnessPlatformFailure.OMNIGENT_SKILL_SNAPSHOT_UNAVAILABLE,
            )
        if plan.payload.harnessId == "opencode-native":
            code, _out, _err = await _run_exact_host_opencode_command(
                backend=self._backend,
                container_name=launch_result["containerName"],
                argv=[
                    "/bin/sh",
                    "-ceu",
                    (
                        'test "${MOONMIND_ACTIVE_SKILLS_DIR:-}" = "$1"; '
                        'test -d "$1"; test -r "$1/_manifest.json"; '
                        'test "${MOONMIND_STEP_EXECUTION_ID:-}" = "$2"'
                    ),
                    "--",
                    skill_target,
                    spec.stepExecutionId,
                ],
            )
            if code != 0:
                raise HarnessPlatformError(
                    "resolved Skill projection is unavailable in the OpenCode shell environment",
                    code=HarnessPlatformFailure.OMNIGENT_SKILL_SNAPSHOT_UNAVAILABLE,
                )
        tool_mount_evidence: list[dict[str, Any]] = []
        for attachment in spec.toolAttachments:
            matched = next(
                (
                    mount
                    for mount in mounts
                    if str(mount.get("Name") or mount.get("Source") or "")
                    == str(attachment["sourceRef"])
                    and str(mount.get("Destination") or "")
                    == str(attachment["targetPath"])
                ),
                None,
            )
            if matched is None or bool(matched.get("RW")):
                raise HarnessPlatformError(
                    "resolved tool projection is missing or writable on the exact host",
                    code=HarnessPlatformFailure.OMNIGENT_HARNESS_BUILD_MISMATCH,
                )
            for tool in attachment.get("tools", []):
                probe = tool.get("versionProbe")
                if (
                    not isinstance(probe, list)
                    or not probe
                    or not all(
                        isinstance(arg, str) and arg and "\x00" not in arg
                        for arg in probe
                    )
                ):
                    raise HarnessPlatformError(
                        "mounted tool has no valid manifest-declared version probe",
                        code=HarnessPlatformFailure.OMNIGENT_HARNESS_BUILD_MISMATCH,
                    )
                tool_name = str(tool.get("name") or "tool")
                executable = (
                    str(attachment["targetPath"]).rstrip("/")
                    + "/"
                    + str(tool.get("path") or "").lstrip("/")
                )
                expected_digests = {
                    str(item).strip().lower()
                    for item in (tool.get("executableDigests") or [])
                    if str(item).strip()
                }
                # Serious and probable: the executable is absent or not
                # executable, or its version probe fails. Digest drift alone
                # (rebuild, deployment skew) is advisory only when the probe
                # also proves the manifest-declared version; arbitrary
                # mismatched bytes that merely exit 0 remain blocked.
                code, _out, _err = await self._backend.run(
                    [
                        "docker",
                        "exec",
                        launch_result["containerName"],
                        "/bin/sh",
                        "-ceu",
                        'test -x "$1"',
                        "--",
                        executable,
                    ],
                    timeout_seconds=10.0,
                    check=False,
                )
                if code != 0:
                    detail = (_err or _out).strip()[:200]
                    suffix = f": {detail}" if detail else ""
                    raise HarnessPlatformError(
                        f"mounted tool {tool_name} is not executable at "
                        f"{executable}{suffix}",
                        code=HarnessPlatformFailure.OMNIGENT_HARNESS_BUILD_MISMATCH,
                    )
                actual_digest = ""
                code, out, _err = await self._backend.run(
                    [
                        "docker",
                        "exec",
                        launch_result["containerName"],
                        "/bin/sh",
                        "-ceu",
                        'sha256sum "$1"',
                        "--",
                        executable,
                    ],
                    timeout_seconds=10.0,
                    check=False,
                )
                if code == 0:
                    actual_digest = (out.strip().split() or [""])[0].lower()[:128]
                digest_verified = bool(
                    actual_digest and actual_digest in expected_digests
                )
                code, observed, err = await self._backend.run(
                    [
                        "docker",
                        "exec",
                        launch_result["containerName"],
                        executable,
                        *probe,
                    ],
                    timeout_seconds=10.0,
                    check=False,
                )
                if code != 0:
                    detail = (err or observed).strip().replace("\n", " ")[:200]
                    suffix = f": {detail}" if detail else ""
                    raise HarnessPlatformError(
                        f"mounted tool {tool_name} version probe "
                        f"{' '.join(probe)[:80]} failed "
                        f"(exit {code}){suffix}",
                        code=HarnessPlatformFailure.OMNIGENT_HARNESS_BUILD_MISMATCH,
                    )
                if expected_digests and not digest_verified:
                    import logging

                    expected_version = str(tool.get("version") or "").strip()
                    probe_text = f"{observed}\n{err}".strip()
                    if expected_version and expected_version != "container-v1":
                        version_ok = expected_version in probe_text
                    else:
                        # The container CLI declares no semantic version in
                        # --help; require substantive help output, not merely
                        # exit 0, so a stub executable cannot attest readiness.
                        lowered = probe_text.lower()
                        version_ok = bool(probe_text) and (
                            "usage:" in lowered
                            or "moonmind" in lowered
                            or len(probe_text) > 20
                        )
                    if not version_ok:
                        raise HarnessPlatformError(
                            f"mounted tool {tool_name} digest mismatch and "
                            f"version evidence does not match manifest "
                            f"({expected_version or 'unknown version'})",
                            code=HarnessPlatformFailure.OMNIGENT_HARNESS_BUILD_MISMATCH,
                        )
                    logging.getLogger(__name__).info(
                        "mounted tool digest drift with matching version "
                        "(advisory): tool=%r executable=%r actual=%r "
                        "version=%r",
                        tool_name,
                        executable,
                        actual_digest[:19] or "unknown",
                        expected_version or "unknown",
                    )
                tool_mount_evidence.append(
                    {
                        "name": str(tool.get("name") or ""),
                        "version": str(tool.get("version") or ""),
                        "path": executable,
                        "accessMode": "read-only",
                        "digestVerified": digest_verified,
                        "versionProbe": list(tool["versionProbe"]),
                        "probe": observed.strip()[:128],
                    }
                )
        control_mount_evidence: dict[str, Any] | None = None
        if spec.controlAttachment is not None:
            control_mount = next(
                (
                    mount
                    for mount in mounts
                    if str(mount.get("Name") or mount.get("Source") or "")
                    == str(spec.controlAttachment["sourceRef"])
                    and str(mount.get("Destination") or "")
                    == str(spec.controlAttachment["targetPath"])
                ),
                None,
            )
            if control_mount is None or bool(control_mount.get("RW")):
                raise HarnessPlatformError(
                    "host control credential mount is missing or writable",
                    code=HarnessPlatformFailure.OMNIGENT_HARNESS_BUILD_MISMATCH,
                )
            control_mount_evidence = {
                "targetPath": spec.controlAttachment["targetPath"],
                "accessMode": "read-only",
                "secretValueRecorded": False,
            }
        github_mount_evidence: dict[str, Any] | None = None
        if spec.githubCredentialAttachment is not None:
            github_mount = next(
                (
                    mount
                    for mount in mounts
                    if str(mount.get("Name") or mount.get("Source") or "")
                    == str(spec.githubCredentialAttachment["sourceRef"])
                    and str(mount.get("Destination") or "")
                    == str(spec.githubCredentialAttachment["targetPath"])
                ),
                None,
            )
            if github_mount is None or bool(github_mount.get("RW")):
                raise HarnessPlatformError(
                    "GitHub credential projection is missing or writable",
                    code=(
                        HarnessPlatformFailure.OMNIGENT_CREDENTIAL_MATERIALIZATION_FAILED
                    ),
                )
            target = str(spec.githubCredentialAttachment["targetPath"])
            verify_github_config_script = (
                'test "$(stat -c %u:%g "$1")" = "$2:$3"; '
                'test "$(stat -c %a "$1")" = 700; '
                'test "$(stat -c %u:%g "$1/hosts.yml")" = "$2:$3"; '
                'test "$(stat -c %a "$1/hosts.yml")" = 600'
            )
            code, _out, _err = await self._backend.run(
                [
                    "docker",
                    "exec",
                    launch_result["containerName"],
                    "/bin/sh",
                    "-ceu",
                    verify_github_config_script,
                    "--",
                    target,
                    str(host_class.runtime.get("uid", 1000)),
                    str(host_class.runtime.get("gid", 1000)),
                ],
                check=False,
            )
            if code != 0:
                raise HarnessPlatformError(
                    "GitHub credential projection ownership or mode is invalid",
                    code=(
                        HarnessPlatformFailure.OMNIGENT_CREDENTIAL_MATERIALIZATION_FAILED
                    ),
                )
            code, _out, _err = await self._backend.run(
                [
                    "docker",
                    "exec",
                    launch_result["containerName"],
                    "gh",
                    "auth",
                    "status",
                    "--hostname",
                    "github.com",
                ],
                timeout_seconds=30.0,
                check=False,
            )
            if code != 0:
                raise HarnessPlatformError(
                    "GitHub CLI authentication failed on the exact host",
                    code=(
                        HarnessPlatformFailure.OMNIGENT_CREDENTIAL_MATERIALIZATION_FAILED
                    ),
                )
            code, _out, _err = await _run_exact_host_runner_command(
                backend=self._backend,
                container_name=launch_result["containerName"],
                argv=["gh", "auth", "status", "--hostname", "github.com"],
            )
            if code != 0:
                raise HarnessPlatformError(
                    "GitHub CLI authentication failed in the exact runner environment",
                    code=(
                        HarnessPlatformFailure.OMNIGENT_CREDENTIAL_MATERIALIZATION_FAILED
                    ),
                )
            if plan.payload.harnessId == "opencode-native":
                code, _out, _err = await _run_exact_host_opencode_command(
                    backend=self._backend,
                    container_name=launch_result["containerName"],
                    argv=["gh", "auth", "status", "--hostname", "github.com"],
                )
                if code != 0:
                    raise HarnessPlatformError(
                        "GitHub CLI authentication failed in the OpenCode shell environment",
                        code=(
                            HarnessPlatformFailure.OMNIGENT_CREDENTIAL_MATERIALIZATION_FAILED
                        ),
                    )
            expected_helper = "!/home/app/.omnigent/moonmind/bin/gh auth git-credential"
            code, observed, _err = await self._backend.run(
                [
                    "docker",
                    "exec",
                    launch_result["containerName"],
                    "git",
                    "config",
                    "--get-urlmatch",
                    "credential.helper",
                    "https://github.com",
                ],
                check=False,
            )
            if code != 0 or observed.strip() != expected_helper:
                raise HarnessPlatformError(
                    "GitHub git credential helper is not active on the exact host",
                    code=(
                        HarnessPlatformFailure.OMNIGENT_CREDENTIAL_MATERIALIZATION_FAILED
                    ),
                )
            repository = github_repository_from_request(request)
            repository_authorized: bool | None = None
            repository_permission: str | None = None
            if repository:
                code, observed, _err = await self._backend.run(
                    [
                        "docker",
                        "exec",
                        launch_result["containerName"],
                        "gh",
                        "repo",
                        "view",
                        repository,
                        "--json",
                        "nameWithOwner,viewerPermission",
                        "--jq",
                        "[.nameWithOwner, .viewerPermission] | @tsv",
                    ],
                    timeout_seconds=30.0,
                    check=False,
                )
                parts = observed.strip().split("\t", 1)
                observed_repository = parts[0] if parts else ""
                repository_permission = parts[1] if len(parts) == 2 else None
                repository_authorized = code == 0 and (
                    observed_repository.casefold() == repository.casefold()
                )
                if not repository_authorized:
                    raise HarnessPlatformError(
                        "GitHub credential cannot access the admitted repository",
                        code=(
                            HarnessPlatformFailure.OMNIGENT_CREDENTIAL_MATERIALIZATION_FAILED
                        ),
                    )
                code, runner_observed, _err = await _run_exact_host_runner_command(
                    backend=self._backend,
                    container_name=launch_result["containerName"],
                    argv=[
                        "gh",
                        "repo",
                        "view",
                        repository,
                        "--json",
                        "nameWithOwner,viewerPermission",
                        "--jq",
                        "[.nameWithOwner, .viewerPermission] | @tsv",
                    ],
                )
                runner_parts = runner_observed.strip().split("\t", 1)
                runner_repository = runner_parts[0] if runner_parts else ""
                if code != 0 or runner_repository.casefold() != repository.casefold():
                    raise HarnessPlatformError(
                        "GitHub credential cannot access the admitted repository "
                        "from the exact runner environment",
                        code=(
                            HarnessPlatformFailure.OMNIGENT_CREDENTIAL_MATERIALIZATION_FAILED
                        ),
                    )
            github_mount_evidence = {
                "targetPath": target,
                "accessMode": "read-only",
                "authenticated": True,
                "repository": repository or None,
                "repositoryAuthorized": repository_authorized,
                "repositoryPermission": repository_permission,
                "gitCredentialHelperConfigured": True,
                "secretValueRecorded": False,
            }
        credential_mount_evidence: list[dict[str, Any]] = []
        for handle in credential_handles:
            for attachment in handle.get("attachments", []):
                matched = next(
                    (
                        mount
                        for mount in mounts
                        if str(mount.get("Name") or mount.get("Source") or "")
                        == str(attachment["sourceRef"])
                        and str(mount.get("Destination") or "")
                        == str(attachment["targetPath"])
                    ),
                    None,
                )
                # OAuth homes declare read-write so vendor token refresh persists;
                # all other credential attachments are read-only. Compare the
                # observed Docker RW flag against the declared accessMode instead
                # of unconditionally rejecting writable mounts.
                expected_writable = _declared_access_is_writable(
                    attachment.get("accessMode")
                )
                if matched is None:
                    raise HarnessPlatformError(
                        "credential volume mount is missing",
                        code=HarnessPlatformFailure.OMNIGENT_CREDENTIAL_MATERIALIZATION_FAILED,
                    )
                if bool(matched.get("RW")) != expected_writable:
                    raise HarnessPlatformError(
                        "credential volume mount mode does not match "
                        "the declared accessMode",
                        code=HarnessPlatformFailure.OMNIGENT_CREDENTIAL_MATERIALIZATION_FAILED,
                    )
                generation = int(handle["credentialGeneration"])
                target = str(attachment["targetPath"])
                verify_credential_script = "".join(
                    (
                        'test "$(stat -c %u:%g "$1")" = 1000:1000; ',
                        'test "$(stat -c %a "$1")" = 700; ',
                        'test "$(stat -c %u:%g "$1/.moonmind-generation")" = 1000:1000; ',
                        'test "$(stat -c %a "$1/.moonmind-generation")" = 600; ',
                        'count=0; for file in "$1"/*; do ',
                        'test -f "$file" || continue; ',
                        'test "$(stat -c %u:%g "$file")" = 1000:1000; ',
                        'test "$(stat -c %a "$file")" = 600; ',
                        'count=$((count + 1)); done; test "$count" -ge 1; ',
                        'cat "$1/.moonmind-generation"',
                    )
                )
                _code, observed, _err = await self._backend.run(
                    [
                        "docker",
                        "exec",
                        launch_result["containerName"],
                        "/bin/sh",
                        "-ceu",
                        verify_credential_script,
                        "--",
                        target,
                    ],
                    failure_code=HarnessPlatformFailure.OMNIGENT_CREDENTIAL_MATERIALIZATION_FAILED,
                )
                if observed.strip() != str(generation):
                    raise HarnessPlatformError(
                        "credential generation sidecar differs on the exact host",
                        code=HarnessPlatformFailure.OMNIGENT_CREDENTIAL_GENERATION_FENCED,
                    )
                credential_mount_evidence.append(
                    {
                        "credentialRuntimeRef": handle["credentialRuntimeRef"],
                        "targetPath": target,
                        "accessMode": (
                            "read-write" if expected_writable else "read-only"
                        ),
                        "owner": "1000:1000",
                        "directoryMode": "0700",
                        "fileMode": "0600",
                        "generation": generation,
                    }
                )
        # Execute the declared OAuth login-status preflight inside the exact
        # runner environment before advancing host authority. Volume presence
        # and generation only prove enrollment; an expired or revoked token
        # since validation would otherwise surface only on the first provider
        # call. Vendor CLI output may carry credential context, so failures
        # carry only the typed error, never raw output.
        has_writable_oauth_mount = any(
            _declared_access_is_writable(attachment.get("accessMode"))
            for handle in credential_handles
            for attachment in handle.get("attachments", [])
        )
        if has_writable_oauth_mount:
            harness_id = ""
            try:
                harness_id = str(plan.payload.harnessId or "")
            except Exception:
                harness_id = ""
            login_command: tuple[str, ...] | None = None
            if harness_id == "codex-native":
                login_command = ("codex", "login", "status")
            elif harness_id == "claude-native":
                login_command = ("claude", "auth", "status")
            if login_command is not None:
                login_code, _login_out, _login_err = await self._backend.run(
                    [
                        "docker",
                        "exec",
                        launch_result["containerName"],
                        *login_command,
                    ],
                    failure_code=HarnessPlatformFailure.OMNIGENT_CREDENTIAL_MATERIALIZATION_FAILED,
                    check=False,
                )
                if login_code != 0:
                    raise HarnessPlatformError(
                        "OAuth credential login status failed on the exact host",
                        code=HarnessPlatformFailure.OMNIGENT_CREDENTIAL_MATERIALIZATION_FAILED,
                    )
        host_id = registration["omnigentHostId"]
        try:

            async def egress_runner(args):
                code, out, err = await self._backend.run(["docker", *args], check=False)
                return code, out.encode(), err.encode()

            workload_egress = await attest_docker_workload_egress(
                runner=egress_runner,
                profile=OMNIGENT_EGRESS_PROFILE,
                attestation=EgressAttestation.model_validate(
                    {
                        key: value
                        for key, value in egress_attestation.items()
                        if key
                        in {
                            field.alias or name
                            for name, field in EgressAttestation.model_fields.items()
                        }
                    }
                ),
                attachment_identity=launch_result["containerName"],
                # The container was created from the effective image
                # (drift-resolved above), so egress must prove the running
                # workload matches what actually launched. Series qualification
                # of that effective image was proven by the version gates.
                expected_image_ref=inspect_ref,
            )
        except (RuntimeError, ValueError) as exc:
            raise HarnessPlatformError(
                "exact host restricted-egress attachment could not be attested",
                code=HarnessPlatformFailure.OMNIGENT_HARNESS_BUILD_MISMATCH,
            ) from exc
        selected_model = plan.payload.modelConfig.qualifiedId
        model_evidence: dict[str, Any] = {
            "omnigentHostId": host_id,
            "harnessId": plan.payload.harnessId,
            "selectedModel": selected_model,
            "attempts": [],
        }
        # A cold catalog is an observation, not permanent model authority.
        # OpenCode can exit zero with its bundled catalog after a failed
        # refresh. Re-read before releasing this exact host/credential lease;
        # do not substitute a model or re-admit another host to recover a read.
        for attempt in range(1, _MODEL_ATTESTATION_MAX_ATTEMPTS + 1):
            observation: dict[str, Any] = {
                "attempt": attempt,
                "selectedModelPresent": False,
            }
            try:
                model_options, model_options_source = (
                    await _read_exact_host_model_options(
                        backend=self._backend,
                        client=self._client,
                        container_name=launch_result["containerName"],
                        omnigent_host_id=host_id,
                        harness_id=plan.payload.harnessId,
                    )
                )
                available_models = sorted(_model_ids(model_options))
                observation.update(
                    availableModels=available_models,
                    source=model_options_source,
                    selectedModelPresent=selected_model in available_models,
                )
                model_evidence.update(
                    availableModels=available_models, source=model_options_source
                )
            except HarnessPlatformError as exc:
                if exc.code != HarnessPlatformFailure.OMNIGENT_MODEL_UNAVAILABLE:
                    raise
                # Do not retain raw provider CLI diagnostics in evidence.
                observation["errorCode"] = exc.code
            model_evidence["attempts"].append(observation)
            if observation["selectedModelPresent"]:
                break
            if attempt < _MODEL_ATTESTATION_MAX_ATTEMPTS:
                await asyncio.sleep(attempt)

        async def write_model_evidence() -> str:
            return await self._artifacts.write_json(
                request=request,
                name="generic-host-model-options.json",
                payload=model_evidence,
                link_type="evidence.model_options",
            )

        if not model_evidence["attempts"][-1]["selectedModelPresent"]:
            failure = HarnessPlatformError(
                f"selected model {selected_model} could not be confirmed on the exact host "
                f"after {_MODEL_ATTESTATION_MAX_ATTEMPTS} catalog reads",
                code=HarnessPlatformFailure.OMNIGENT_MODEL_UNAVAILABLE,
            )
            try:
                await write_model_evidence()
            except Exception:  # noqa: BLE001 - preserve primary validation failure
                # Evidence storage is auxiliary to the primary validation
                # failure. The runtime still owns fenced host cleanup.
                logging.getLogger(__name__).warning(
                    "Failed to retain exhausted exact-host model catalog evidence"
                )
            raise failure
        observed_build_digest = str(
            (image.get("Config", {}).get("Labels", {}) or {}).get(
                "moonmind.omnigent.build_digest"
            )
            or ""
        )
        host_evidence = {
            "schemaVersion": "moonmind.omnigent-exact-host-attestation.v1",
            "containerId": str(container.get("Id") or ""),
            "containerName": launch_result["containerName"],
            "omnigentHostId": host_id,
            "hostOwner": registration["host"].get("owner"),
            # The image and build that actually ran (drift-resolved). When no
            # drift occurred these equal the selected Host Class values; the
            # expected (planned) values are retained alongside for audit and
            # retry revalidation.
            "imageRef": inspect_ref,
            "expectedImageRef": host_class.imageRef,
            "architecture": architecture,
            "omnigentVersion": omnigent_version.strip()[:256],
            "omnigentBuildDigest": observed_build_digest,
            "expectedOmnigentBuildDigest": host_class.omnigentBuildDigest,
            "harnessId": plan.payload.harnessId,
            "harnessImplementationRef": plan.payload.harnessImplementationRef,
            "runtimeVersions": runtime_versions,
            "credentialGenerations": {
                item["providerProfileRef"]: item["credentialGeneration"]
                for item in credential_handles
            },
            "credentialMounts": credential_mount_evidence,
            "workspaceMount": workspace_mount_evidence,
            "controlCredentialMount": control_mount_evidence,
            "githubCredentialMount": github_mount_evidence,
            "skillDeliveryRef": spec.skillAttachment.get("deliveryRef"),
            "toolDeliveryRefs": [
                item.get("toolDeliveryRef") for item in spec.toolAttachments
            ],
            "toolMounts": tool_mount_evidence,
            "egressAttestationRef": egress_attestation["attestationRef"],
            "egressAttachment": workload_egress,
            "harnessReady": registration["harnessReady"],
        }
        host_ref = await self._artifacts.write_json(
            request=request,
            name="generic-host-attestation.json",
            payload=host_evidence,
            link_type="evidence.host_attestation",
        )
        model_ref = await write_model_evidence()
        return {
            "hostHarnessAttestationRef": host_ref,
            "modelOptionAttestationRef": model_ref,
            "skillDeliveryAttestationRef": spec.skillAttachment.get("deliveryRef"),
            "egressAttestationRef": egress_attestation["attestationRef"],
            "omnigentHostId": host_id,
        }


__all__ = ["DockerOmnigentHostAttestor", "_read_exact_host_model_options"]
