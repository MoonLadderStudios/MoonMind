"""Exact Docker, harness, credential, Skill, egress, and model attestation."""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from moonmind.omnigent.bridge_artifacts import OmnigentArtifactGateway
from moonmind.omnigent.codex_login import (
    CODEX_ACCOUNT_READ_PROBE,
    CODEX_LOGIN_SESSION_PROBE,
    record_ended_codex_login,
)
from moonmind.omnigent.harness_platform.failures import (
    HarnessPlatformError,
    HarnessPlatformFailure,
)
from moonmind.omnigent.harness_platform.host_classes import HostClass
from moonmind.omnigent.host_ports import HostLaunchSpec
from moonmind.omnigent.host_services import upstream_probes
from moonmind.omnigent.host_services.docker_backend import DockerCommandBackend
from moonmind.omnigent.host_services.github_credentials import (
    github_host_from_endpoint,
    github_repository_from_request,
)
from moonmind.omnigent.host_services.mounted_tools import classify_tool_attachment
from moonmind.omnigent.mounted_tool_preflight import (
    MountedToolPreflightError,
    preflight_github_access,
)
from moonmind.omnigent.workspace_intent import authored_github_mutation_required
from moonmind.schemas.agent_runtime_models import AgentExecutionRequest
from moonmind.security.egress import (
    OMNIGENT_EGRESS_PROFILE,
    EgressAttestation,
    attest_docker_workload_egress,
)
from moonmind.workflows.adapters.omnigent_client import OmnigentClientError

_MODEL_ATTESTATION_MAX_ATTEMPTS = 3

# Each Skill-projection invariant reports which one failed. A bare
# ``test`` chain exits 1 for all four, so an empty projection, a stripped
# environment, and a mismatched Step Execution all reported the same
# sentence and the operator had to reproduce the launch to tell them apart.
_SKILL_PROJECTION_PROBE_MARKER = "moonmind-skill-projection-check"
_SKILL_PROJECTION_PROBE_REASONS = {
    "active-skills-dir-env-mismatch": (
        "the runtime environment does not carry the delivered "
        "MOONMIND_ACTIVE_SKILLS_DIR"
    ),
    "projection-directory-missing": "the projection directory is not readable",
    "projection-manifest-missing": (
        "the projection directory is mounted but holds no readable "
        "_manifest.json, so the host received an empty or foreign directory"
    ),
    "step-execution-id-mismatch": (
        "the runtime environment does not carry the launched "
        "MOONMIND_STEP_EXECUTION_ID"
    ),
}
_SKILL_PROJECTION_PROBE_SCRIPT = (
    f'fail() {{ echo "{_SKILL_PROJECTION_PROBE_MARKER}:$1"; exit 1; }}; '
    'test "${MOONMIND_ACTIVE_SKILLS_DIR:-}" = "$1" '
    "|| fail active-skills-dir-env-mismatch; "
    'test -d "$1" || fail projection-directory-missing; '
    'test -r "$1/_manifest.json" || fail projection-manifest-missing; '
    'test "${MOONMIND_STEP_EXECUTION_ID:-}" = "$2" '
    "|| fail step-execution-id-mismatch"
)


def _skill_projection_probe_message(
    boundary: str, stdout: str, stderr: str, skill_target: str
) -> str:
    """Name the Skill-projection invariant the probed environment failed."""

    reason = next(
        (
            line.split(":", 1)[1].strip()
            for line in f"{stdout}\n{stderr}".splitlines()
            if line.strip().startswith(f"{_SKILL_PROJECTION_PROBE_MARKER}:")
        ),
        "",
    )
    message = (
        f"resolved Skill projection is unavailable in the {boundary} "
        f"at {skill_target}"
    )
    detail = _SKILL_PROJECTION_PROBE_REASONS.get(reason)
    if detail:
        return f"{message}: {detail} ({reason})"
    # The probe could not report a reason (transport failure, killed shell).
    # Carry its own diagnostics rather than a sentence that names none.
    raw = (stderr or stdout).strip()[:256]
    return f"{message}: {raw}" if raw else message


# A mounted-tool version probe attests the delivered build: that the pinned
# executable is present and reports its manifest-declared version. It must not
# inherit the host's runtime configuration, because a tool can be built exactly
# right and still refuse to run under a broken credential projection -- `gh`
# fails every command, including `--version`, while its config awaits the
# network-dependent multi-account migration. Probing under a cleared
# environment keeps that failure with its own owner instead of reporting a
# healthy build as OMNIGENT_HARNESS_BUILD_MISMATCH.
_PROBE_CLEARED_ENVIRONMENT = (
    'exec env -i PATH=/usr/local/bin:/usr/bin:/bin HOME=/nonexistent "$@"'
)


def mounted_tool_probe_argv(
    container_name: str, executable: str, probe: list[str]
) -> list[str]:
    """Return the exact-host command that attests one mounted tool build."""

    return [
        "docker",
        "exec",
        container_name,
        "/bin/sh",
        "-ceu",
        _PROBE_CLEARED_ENVIRONMENT,
        "--",
        executable,
        *probe,
    ]


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

    The host tunnel answers every harness except ``opencode-native``, whose
    catalog comes from the retained in-host probe rather than turning the
    tunnel's honest unsupported-harness response into a generic HTTP 502.
    """

    if harness_id == "opencode-native":
        payload = await upstream_probes.read_opencode_model_options(
            backend=backend, container_name=container_name
        )
        return payload, "exact-host-opencode-cli"
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
            code=HarnessPlatformFailure.OMNIGENT_HARNESS_CATALOG_UNAVAILABLE,
            transient=True,
        ) from exc
    return model_options, "omnigent-host-tunnel"


async def confirm_exact_host_model(
    *,
    backend: DockerCommandBackend,
    container_name: str,
    harness_id: str,
    model_options: Any,
    selected_model: str,
) -> tuple[list[str], bool]:
    """Return the host's advertised model ids and whether it serves the selection.

    Qualified catalogs (``provider/model``) confirm by exact membership. Codex
    ``model/list`` rows name bare Codex model slugs (``gpt-6.1-sol``) -- the
    provider is the launch configuration, not part of the id -- so they also
    confirm by exact membership, of the row spellings. Claude Code rows name
    picker aliases and bare Anthropic ids (``opus[1m]`` ->
    ``claude-opus-5-5[1m]``), and whether a launch of the selected id is served
    is Claude harness truth, so the exact host answers with its own upstream
    rule for exactly these rows instead of MoonMind re-deriving it.
    """

    if harness_id not in {"claude-native", "codex-native"}:
        available = sorted(_model_ids(model_options))
        return available, selected_model in available
    models = model_options.get("models") if isinstance(model_options, dict) else None
    rows = [row for row in models or [] if isinstance(row, dict)]
    available = sorted(
        {
            str(row[key])
            for row in rows
            for key in ("id", "model")
            if isinstance(row.get(key), str) and row[key]
        }
    )
    if harness_id == "codex-native":
        return available, selected_model in available
    if not rows:
        return available, False
    # The config-independent clauses of the upstream rule: an exact row (picker
    # id or wire model) always serves, and a non-exact id serves only as a
    # canonical ``claude-`` id. Only that family case needs the exact host.
    if any(
        row.get("id") == selected_model or row.get("model") == selected_model
        for row in rows
    ):
        return available, True
    if not selected_model.lower().startswith("claude-"):
        return available, False
    return available, await upstream_probes.claude_serves_model(
        backend=backend,
        container_name=container_name,
        rows=rows,
        selected_model=selected_model,
    )


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


async def credential_mount_access(
    backend: DockerCommandBackend,
    container_name: str,
    attachment: dict[str, Any],
) -> str | None:
    """Return the observed access of one exact credential mount, if present.

    ``run_runtime_command`` redacts credential-home paths such as
    ``/home/app/.claude`` from command output, so an echoed mount table cannot
    be compared against the attachment. Docker's template evaluator compares
    the exact source and destination and prints one bounded token instead.
    """

    source = json.dumps(str(attachment["sourceRef"]))
    destination = json.dumps(str(attachment["targetPath"]))
    template = (
        "{{range .Mounts}}"
        f"{{{{if and (or (eq .Name {source}) (eq .Source {source})) "
        f"(eq .Destination {destination})}}}}"
        "{{if .RW}}rw{{else}}ro{{end}}"
        "{{end}}{{end}}"
    )
    _code, observed, _err = await backend.run(
        ["docker", "container", "inspect", "--format", template, container_name],
        failure_code=HarnessPlatformFailure.OMNIGENT_CREDENTIAL_MATERIALIZATION_FAILED,
    )
    return {"rw": "read-write", "ro": "read-only"}.get(observed.strip())


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
        # Docker does not report a volume subpath on the container's mount
        # list, so the declared subpath is the only record of which directory
        # inside the volume the host received.
        **(
            {"subPath": str(attachment["subPath"])} if attachment.get("subPath") else {}
        ),
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
        # Rebuilt images may change digests and versions. A same-repository
        # image may be recovered only after its observed build provenance and
        # required runtime capabilities are attested below.
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
        inspect_ref = (
            str(
                (launch_result or {}).get("launchImageRef")
                or configured_image
                or host_class.imageRef
            ).strip()
            or host_class.imageRef
        )
        _code, image_json, _err = await self._backend.run(
            ["docker", "image", "inspect", inspect_ref],
            failure_code=HarnessPlatformFailure.OMNIGENT_HARNESS_BUILD_MISMATCH,
        )
        image_rows = json.loads(image_json)
        image = image_rows[0] if isinstance(image_rows, list) and image_rows else {}
        # Probe the executable version before judging build identity. A
        # release-number difference alone does not reject a selected host;
        # immutable image provenance and the live host checks remain required.
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
        logging.getLogger(__name__).info(
            "omnigent --version probe: expected=%r got=%r",
            host_class.omnigentVersion,
            omnigent_version.strip()[:200],
        )
        from moonmind.omnigent.compatibility import versions_compatible

        if not versions_compatible(host_class.omnigentVersion, omnigent_version):
            raise HarnessPlatformError(
                "host Omnigent version differs from the catalog build",
                code=HarnessPlatformFailure.OMNIGENT_HARNESS_BUILD_MISMATCH,
            )
        try:
            _assert_exact_omnigent_build(image, host_class.omnigentBuildDigest)
        except HarnessPlatformError:
            # Rebuilds may carry a different build digest. Accept that only
            # when the launched image drifted from the selected Host Class
            # image within the same repository. An unchanged image with a
            # different build label is
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
        # omnigent_version was probed above before the build-provenance check.
        runtime_versions: dict[str, str] = {}
        selected_entry = next(
            item
            for item in host_class.declaredHarnessImplementations
            if item.harnessId == plan.payload.harnessId
            and item.implementationRef == plan.payload.harnessImplementationRef
        )
        declared_runtime_versions: dict[str, str] = {}
        for dependency in selected_entry.runtimeDependencies:
            name = str(dependency.get("name") or "").strip()
            if not name:
                continue
            # The declared runtime must be installed and runnable. Its release
            # number is provenance: the runner, Skill, model, and login probes
            # below prove the behavior this harness needs, and a different
            # release alone cannot show that behavior is missing.
            _code, observed, _err = await self._backend.run(
                ["docker", "exec", launch_result["containerName"], name, "--version"],
                failure_code=HarnessPlatformFailure.OMNIGENT_VENDOR_RUNTIME_MISMATCH,
            )
            runtime_versions[name] = observed.strip()[:256]
            declared_runtime_versions[name] = str(
                dependency.get("version") or ""
            ).strip()
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
        code, out, err = await upstream_probes.run_runner_command(
            backend=self._backend,
            container_name=launch_result["containerName"],
            argv=[
                "/bin/sh",
                "-ceu",
                _SKILL_PROJECTION_PROBE_SCRIPT,
                "--",
                skill_target,
                spec.stepExecutionId,
            ],
        )
        if code != 0:
            raise HarnessPlatformError(
                _skill_projection_probe_message(
                    "exact runner environment", out, err, skill_target
                ),
                code=HarnessPlatformFailure.OMNIGENT_SKILL_SNAPSHOT_UNAVAILABLE,
            )
        if plan.payload.harnessId == "opencode-native":
            code, out, err = await upstream_probes.run_opencode_shell_command(
                backend=self._backend,
                container_name=launch_result["containerName"],
                argv=[
                    "/bin/sh",
                    "-ceu",
                    _SKILL_PROJECTION_PROBE_SCRIPT,
                    "--",
                    skill_target,
                    spec.stepExecutionId,
                ],
            )
            if code != 0:
                raise HarnessPlatformError(
                    _skill_projection_probe_message(
                        "OpenCode shell environment", out, err, skill_target
                    ),
                    code=HarnessPlatformFailure.OMNIGENT_SKILL_SNAPSHOT_UNAVAILABLE,
                )
        tool_mount_evidence: list[dict[str, Any]] = []
        for attachment in spec.toolAttachments:
            delivery = classify_tool_attachment(attachment)
            if delivery == "unsupported":
                raise HarnessPlatformError(
                    "resolved tool projection is not a supported delivery",
                    code=HarnessPlatformFailure.OMNIGENT_HARNESS_BUILD_MISMATCH,
                )
            if delivery == "legacy-volume-drain":
                # Persisted pre-cutover bindings are drained, never rewritten:
                # the recorded volume must still be present read-only.
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
            # Image-owned tools (MoonLadderStudios/MoonMind#4558) carry no
            # mount: the selected image owns /opt/moonmind-tools. The
            # executable, digest, and version-probe checks below still run
            # against the exact host; no mount evidence is fabricated.
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
                    mounted_tool_probe_argv(
                        launch_result["containerName"], executable, list(probe)
                    ),
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
                tool_evidence: dict[str, Any] = {
                    "name": str(tool.get("name") or ""),
                    "version": str(tool.get("version") or ""),
                    "path": executable,
                    "accessMode": "read-only",
                    "delivery": delivery,
                    "digestVerified": digest_verified,
                    "versionProbe": list(tool["versionProbe"]),
                    "probe": observed.strip()[:128],
                }
                if delivery == "image":
                    tool_evidence["imageRef"] = inspect_ref
                else:
                    tool_evidence["sourceRef"] = str(attachment.get("sourceRef") or "")
                tool_mount_evidence.append(tool_evidence)
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
            github_host = github_host_from_endpoint(
                "https://"
                + str(spec.githubCredentialAttachment.get("githubHost") or "github.com")
            )
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
                    f"https://{github_host}",
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

            async def host_runner(command: str):
                return await self._backend.run(
                    [
                        "docker",
                        "exec",
                        launch_result["containerName"],
                        "/bin/sh",
                        "-ceu",
                        command,
                    ],
                    timeout_seconds=30.0,
                    check=False,
                )

            async def runner_runner(command: str):
                return await upstream_probes.run_runner_command(
                    backend=self._backend,
                    container_name=launch_result["containerName"],
                    argv=["/bin/sh", "-ceu", command],
                )

            async def opencode_runner(command: str):
                return await upstream_probes.run_opencode_shell_command(
                    backend=self._backend,
                    container_name=launch_result["containerName"],
                    argv=["/bin/sh", "-ceu", command],
                )

            boundaries = {"host": host_runner, "runner": runner_runner}
            if plan.payload.harnessId == "opencode-native":
                boundaries["opencode_shell"] = opencode_runner
            try:
                access = await preflight_github_access(
                    repository=repository,
                    boundaries=boundaries,
                    mutation_required=authored_github_mutation_required(request),
                    github_host=github_host,
                )
            except MountedToolPreflightError as exc:
                # Keep bounded, redacted attempts in durable diagnostics even
                # when the host never qualifies and no session can start.
                detail = str(exc)
                try:
                    failure_ref = await self._artifacts.write_json(
                        request=request,
                        name="github-access-preflight-failure.json",
                        payload=exc.evidence,
                        link_type="evidence.github_access",
                    )
                    detail += f" (diagnosticsRef: {failure_ref})"
                except (
                    Exception
                ):  # noqa: BLE001 -- Preserve the original access failure.
                    logging.getLogger(__name__).warning(
                        "Could not persist GitHub preflight evidence; original failure: %s",
                        exc,
                    )
                # A GitHub outage or rate limit that outlasted in-place
                # recovery is not a host defect; no session exists yet, so it
                # authorizes a fresh Step Execution instead of ending the run.
                raise HarnessPlatformError(
                    detail,
                    code=(
                        HarnessPlatformFailure.OMNIGENT_EXTERNAL_SERVICE_UNAVAILABLE
                        if exc.transient
                        else HarnessPlatformFailure.OMNIGENT_EXACT_HOST_CAPABILITY_MISMATCH
                    ),
                ) from exc
            repository_probe = next(
                (
                    item
                    for item in access["probes"]
                    if item["probe"] == "repository_access"
                    and item["status"] == "ready"
                ),
                None,
            )
            permission = (
                repository_probe.get("repositoryPermission")
                if repository_probe is not None
                else None
            )
            github_mount_evidence = {
                "targetPath": target,
                "accessMode": "read-only",
                "credentialAvailable": True,
                "authenticated": True if repository else None,
                "repository": repository or None,
                "repositoryAuthorized": True if repository else None,
                "repositoryPermission": permission,
                "gitCredentialHelperConfigured": True,
                "secretValueRecorded": False,
                "preflight": access,
            }
        credential_mount_evidence: list[dict[str, Any]] = []
        for handle in credential_handles:
            for attachment in handle.get("attachments", []):
                observed_access = await credential_mount_access(
                    self._backend, launch_result["containerName"], attachment
                )
                # OAuth homes declare read-write so vendor token refresh persists;
                # all other credential attachments are read-only. Compare the
                # observed Docker RW flag against the declared accessMode instead
                # of unconditionally rejecting writable mounts.
                expected_writable = _declared_access_is_writable(
                    attachment.get("accessMode")
                )
                if observed_access is None:
                    raise HarnessPlatformError(
                        "credential volume mount is missing",
                        code=HarnessPlatformFailure.OMNIGENT_CREDENTIAL_MATERIALIZATION_FAILED,
                    )
                if (observed_access == "read-write") != expected_writable:
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
        # Volume presence and login status prove enrollment, not a usable
        # session: Codex still reports "logged in" for an ended ChatGPT login
        # and can answer model/list with its bundled catalog after a 401.
        # Observe the exact mounted home before trusting that model list.
        # Ordinary token expiry remains refreshable; only a proven ended
        # session requires a new sign-in. Never retain vendor login output.
        oauth_mounts = [
            (handle, attachment)
            for handle in credential_handles
            for attachment in handle.get("attachments", [])
            if _declared_access_is_writable(attachment.get("accessMode"))
        ]

        async def verify_codex_login_session(*, read_account: bool = False) -> None:
            if not oauth_mounts or plan.payload.harnessId != "codex-native":
                return
            for handle, attachment in oauth_mounts:
                code, verdict, _err = await self._backend.run(
                    [
                        "docker",
                        "exec",
                        launch_result["containerName"],
                        "/opt/venv/bin/python",
                        "-c",
                        CODEX_LOGIN_SESSION_PROBE,
                        str(attachment["targetPath"]),
                    ],
                    timeout_seconds=10.0,
                    check=False,
                )
                if code == 0 and verdict.strip() == "ended":
                    profile_id = str(handle.get("providerProfileRef") or "")
                    await record_ended_codex_login(
                        profile_id, int(handle["credentialGeneration"])
                    )
                    raise HarnessPlatformError(
                        f"The ChatGPT login stored for Provider Profile {profile_id} "
                        "has ended and cannot be refreshed. Reconnect OAuth in "
                        "Settings, then retry the failed step.",
                        code=HarnessPlatformFailure.OMNIGENT_HOST_HARNESS_NEEDS_AUTH,
                    )
                if not read_account:
                    continue
                code, verdict, _err = await self._backend.run(
                    [
                        "docker",
                        "exec",
                        launch_result["containerName"],
                        "/opt/venv/bin/python",
                        "-c",
                        CODEX_ACCOUNT_READ_PROBE,
                        str(attachment["targetPath"]),
                    ],
                    timeout_seconds=30.0,
                    check=False,
                )
                if code == 0 and verdict.strip() in {"unauthorized", "missing_login"}:
                    profile_id = str(handle.get("providerProfileRef") or "")
                    reason = (
                        "codex_account_read_unauthorized"
                        if verdict.strip() == "unauthorized"
                        else "codex_login_required"
                    )
                    await record_ended_codex_login(
                        profile_id, int(handle["credentialGeneration"]), reason=reason
                    )
                    raise HarnessPlatformError(
                        f"Codex account/read rejected the ChatGPT login for "
                        f"Provider Profile {profile_id} "
                        f"({'401' if verdict.strip() == 'unauthorized' else 'login required'}). "
                        "Reconnect OAuth in Settings, then retry the failed step.",
                        code=HarnessPlatformFailure.OMNIGENT_HOST_HARNESS_NEEDS_AUTH,
                    )
                if code != 0 or verdict.strip() != "ready":
                    raise HarnessPlatformError(
                        "Codex account/read readiness could not be verified on "
                        "the exact host; the stored login has not been invalidated.",
                        code=HarnessPlatformFailure.OMNIGENT_CREDENTIAL_MATERIALIZATION_FAILED,
                        transient=True,
                    )

        if oauth_mounts:
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
        await verify_codex_login_session(read_account=True)
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
                # workload matches what actually launched.
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
                available_models, selected_model_present = (
                    await confirm_exact_host_model(
                        backend=self._backend,
                        container_name=launch_result["containerName"],
                        harness_id=plan.payload.harnessId,
                        model_options=model_options,
                        selected_model=selected_model,
                    )
                )
                observation.update(
                    availableModels=available_models,
                    source=model_options_source,
                    selectedModelPresent=selected_model_present,
                )
                model_evidence.update(
                    availableModels=available_models, source=model_options_source
                )
            except HarnessPlatformError as exc:
                if (
                    exc.code
                    != HarnessPlatformFailure.OMNIGENT_HARNESS_CATALOG_UNAVAILABLE
                ):
                    raise
                # Do not retain raw provider CLI diagnostics in evidence.
                observation["errorCode"] = exc.code
            model_evidence["attempts"].append(observation)
            # Refresh can first prove the session ended during this read,
            # even when a bundled fallback includes the selected model.
            await verify_codex_login_session()
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
            if "source" not in model_evidence:
                # No read completed, so absence was never observed. Keep the
                # unavailable cause instead of asking for another model.
                failure = HarnessPlatformError(
                    f"model catalog for {selected_model} could not be read on the "
                    f"exact host in {_MODEL_ATTESTATION_MAX_ATTEMPTS} attempts",
                    code=HarnessPlatformFailure.OMNIGENT_HARNESS_CATALOG_UNAVAILABLE,
                    transient=True,
                )
            else:
                # Show exact observed suffix variants as choices, never aliases.
                # Keep the last successful observation if a later probe failed.
                related = [
                    model
                    for model in model_evidence.get("availableModels", [])
                    if model.startswith(selected_model + "-")
                ][:3]
                selection_hint = (
                    ". Related IDs reported by this host: "
                    + ", ".join(repr(model) for model in related)
                    + ". Select an exact model ID explicitly."
                    if related
                    else ""
                )
                failure = HarnessPlatformError(
                    f"selected model {selected_model} could not be confirmed on the "
                    f"exact host after {_MODEL_ATTESTATION_MAX_ATTEMPTS} catalog "
                    f"reads{selection_hint}",
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
            "declaredRuntimeVersions": declared_runtime_versions,
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
