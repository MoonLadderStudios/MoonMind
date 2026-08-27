"""Exact Docker, harness, credential, Skill, egress, and model attestation."""

from __future__ import annotations

import json
from typing import Any

from moonmind.omnigent.bridge_artifacts import OmnigentArtifactGateway
from moonmind.omnigent.harness_platform.failures import (
    HarnessPlatformError,
    HarnessPlatformFailure,
)
from moonmind.omnigent.harness_platform.host_classes import HostClass
from moonmind.omnigent.host_services.docker_backend import DockerCommandBackend
from moonmind.omnigent.host_services.github_credentials import (
    github_repository_from_request,
)
from moonmind.omnigent.host_services.launcher import HostLaunchSpec
from moonmind.schemas.agent_runtime_models import AgentExecutionRequest
from moonmind.security.egress import (
    OMNIGENT_EGRESS_PROFILE,
    EgressAttestation,
    attest_docker_workload_egress,
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
        return (
            await client.get_host_model_options(omnigent_host_id, harness_id),
            "omnigent-host-tunnel",
        )

    probe = (
        "import json; "
        "from omnigent.opencode_native_app_server import "
        "list_opencode_cli_model_options; "
        "print(json.dumps({'models': list_opencode_cli_model_options()}))"
    )
    code, stdout, _stderr = await backend.run(
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
    expected_writable = attachment.get("accessMode") == "read-write"
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
        if configured_image != host_class.imageRef:
            raise HarnessPlatformError(
                "launched host image ref differs from the selected Host Class",
                code=HarnessPlatformFailure.OMNIGENT_HARNESS_BUILD_MISMATCH,
            )
        _code, image_json, _err = await self._backend.run(
            ["docker", "image", "inspect", host_class.imageRef],
            failure_code=HarnessPlatformFailure.OMNIGENT_HARNESS_BUILD_MISMATCH,
        )
        image_rows = json.loads(image_json)
        image = image_rows[0] if isinstance(image_rows, list) and image_rows else {}
        _assert_exact_omnigent_build(image, host_class.omnigentBuildDigest)
        repo_digests = set(image.get("RepoDigests") or [])
        if (
            host_class.imageRef not in repo_digests
            and configured_image not in repo_digests
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
        if host_class.omnigentVersion not in omnigent_version:
            raise HarnessPlatformError(
                "host Omnigent version differs from the catalog build",
                code=HarnessPlatformFailure.OMNIGENT_HARNESS_BUILD_MISMATCH,
            )
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
            if version and version not in observed:
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
        await self._backend.run(
            [
                "docker",
                "exec",
                launch_result["containerName"],
                "/bin/sh",
                "-ceu",
                'test -d "$1"; test -r "$1"',
                "--",
                str(spec.skillAttachment["targetPath"]),
            ],
            failure_code=HarnessPlatformFailure.OMNIGENT_HARNESS_BUILD_MISMATCH,
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
                executable = (
                    str(attachment["targetPath"]).rstrip("/")
                    + "/"
                    + str(tool.get("path") or "").lstrip("/")
                )
                digests = ",".join(tool.get("executableDigests") or [])
                verify_tool_script = "".join(
                    (
                        'test -x "$1"; actual=$(sha256sum "$1" | awk \'{print $1}\'); ',
                        'case ",$2," in *,"$actual",*) :;; *) exit 1;; esac; ',
                        '"$1" --version >/dev/null',
                    )
                )
                _code, observed, _err = await self._backend.run(
                    [
                        "docker",
                        "exec",
                        launch_result["containerName"],
                        "/bin/sh",
                        "-ceu",
                        verify_tool_script,
                        "--",
                        executable,
                        digests,
                    ],
                    failure_code=HarnessPlatformFailure.OMNIGENT_HARNESS_BUILD_MISMATCH,
                )
                tool_mount_evidence.append(
                    {
                        "name": str(tool.get("name") or ""),
                        "version": str(tool.get("version") or ""),
                        "path": executable,
                        "accessMode": "read-only",
                        "digestVerified": bool(digests),
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
            expected_helper = "!/opt/moonmind-tools/bin/gh auth git-credential"
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
                if matched is None or bool(matched.get("RW")):
                    raise HarnessPlatformError(
                        "credential volume mount is missing or writable",
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
                        "accessMode": "read-only",
                        "owner": "1000:1000",
                        "directoryMode": "0700",
                        "fileMode": "0600",
                        "generation": generation,
                    }
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
                expected_image_ref=host_class.imageRef,
            )
        except (RuntimeError, ValueError) as exc:
            raise HarnessPlatformError(
                "exact host restricted-egress attachment could not be attested",
                code=HarnessPlatformFailure.OMNIGENT_HARNESS_BUILD_MISMATCH,
            ) from exc
        model_options, model_options_source = await _read_exact_host_model_options(
            backend=self._backend,
            client=self._client,
            container_name=launch_result["containerName"],
            omnigent_host_id=host_id,
            harness_id=plan.payload.harnessId,
        )
        selected_model = plan.payload.modelConfig.qualifiedId
        if selected_model not in _model_ids(model_options):
            raise HarnessPlatformError(
                f"selected model {selected_model} is unavailable on the exact host",
                code=HarnessPlatformFailure.OMNIGENT_MODEL_UNAVAILABLE,
            )
        host_evidence = {
            "schemaVersion": "moonmind.omnigent-exact-host-attestation.v1",
            "containerId": str(container.get("Id") or ""),
            "containerName": launch_result["containerName"],
            "omnigentHostId": host_id,
            "hostOwner": registration["host"].get("owner"),
            "imageRef": host_class.imageRef,
            "architecture": architecture,
            "omnigentVersion": omnigent_version.strip()[:256],
            "omnigentBuildDigest": host_class.omnigentBuildDigest,
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
        model_ref = await self._artifacts.write_json(
            request=request,
            name="generic-host-model-options.json",
            payload={
                "omnigentHostId": host_id,
                "harnessId": plan.payload.harnessId,
                "selectedModel": selected_model,
                "availableModels": sorted(_model_ids(model_options)),
                "source": model_options_source,
            },
            link_type="evidence.model_options",
        )
        return {
            "hostHarnessAttestationRef": host_ref,
            "modelOptionAttestationRef": model_ref,
            "skillDeliveryAttestationRef": spec.skillAttachment.get("deliveryRef"),
            "egressAttestationRef": egress_attestation["attestationRef"],
            "omnigentHostId": host_id,
        }


__all__ = ["DockerOmnigentHostAttestor", "_read_exact_host_model_options"]
