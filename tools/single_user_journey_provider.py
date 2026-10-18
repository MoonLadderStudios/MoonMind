#!/usr/bin/env python3
"""Test-only deterministic provider for the real first-run Compose journey.

The external-provider catalog observation and turn are controlled. Admission,
profile resolution, host attestation, workspace saves and finalization remain
owned by the normal production worker.
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import re
import shutil
from contextvars import ContextVar
from pathlib import Path
from typing import Any


PHASE = "firstRunGenerated"
_CONTROLLED_CATALOG: ContextVar[bool] = ContextVar("first_run_catalog", default=False)
_MARKER = re.compile(r"(?<![\w-])first-run-generated:[0-9a-f]{32}(?![\w-])")

# Compute inside the admitted host, rather than uploading expected output bytes.
# The workspace receipt closes the command/DB-commit crash window. Retried
# deliveries verify an accepted computation instead of running it a second time.
_COMPUTE = r"""
import fcntl, hashlib, json, os, pathlib, sys
marker = sys.argv[1]
report = pathlib.Path("report.md")
sentinel = pathlib.Path(".first-run-generated.json")
with open(".first-run-generated.lock", "a") as lock:
    fcntl.flock(lock, fcntl.LOCK_EX)
    if report.is_symlink() or sentinel.is_symlink():
        raise RuntimeError("generated output must not be a symlink")
    if sentinel.exists():
        saved = json.loads(sentinel.read_text())
        if saved["marker"] != marker or hashlib.sha256(report.read_bytes()).hexdigest() != saved["sha256"]:
            raise RuntimeError("accepted computation no longer matches the workspace")
    else:
        digest = hashlib.sha256(marker.encode()).hexdigest()
        payload = f"# Generated scratch report\n\nRequest: {marker}\nSHA-256: {digest}\n".encode()
        report.write_bytes(payload)
        receipt = {"marker": marker, "sha256": hashlib.sha256(report.read_bytes()).hexdigest()}
        temporary = pathlib.Path(".first-run-generated.pending")
        with temporary.open("w") as out:
            json.dump(receipt, out)
            out.flush()
            os.fsync(out.fileno())
        os.replace(temporary, sentinel)
"""


def require_test_project() -> str:
    project = os.environ.get("FIRST_RUN_JOURNEY_PROJECT", "")
    if not re.fullmatch(r"moonmind-test(?:-[a-z0-9][a-z0-9_-]*)?", project):
        raise RuntimeError(
            "FIRST_RUN_JOURNEY_PROJECT must identify an isolated moonmind-test project"
        )
    return project


def _expected_report(marker: str) -> bytes:
    digest = hashlib.sha256(marker.encode()).hexdigest()
    return (
        f"# Generated scratch report\n\nRequest: {marker}\nSHA-256: {digest}\n".encode()
    )


async def _marker(request: Any, artifacts: Any, *, required: bool = True) -> str | None:
    from moonmind.omnigent.execute import (
        _build_omnigent_first_message,
        _first_message_text,
    )

    omni = request.parameters.get("omnigent", {})
    message = await _build_omnigent_first_message(
        request=request, prompt=omni.get("prompt", {}), artifact_gateway=artifacts
    )
    markers = set(_MARKER.findall(_first_message_text(message)))
    if len(markers) != 1:
        if not required:
            return None
        raise RuntimeError(
            "Controlled first-run provider requires exactly one first-run-generated:<32 lowercase hex> prompt marker; use the normal worker for other requests"
        )
    return markers.pop()


async def _owned_lease(binding: Any, leases: Any) -> Any:
    lease = await leases.get(binding.hostLeaseRef)
    if (
        lease is None
        or lease.runtimeBindingId != binding.bindingId
        or lease.omnigentHostId != binding.omnigentHostId
        or lease.launchGeneration != binding.hostLeaseGeneration
    ):
        raise RuntimeError("Host lease does not belong to the admitted runtime binding")
    if not (lease.cleanupHandle or {}).get("containerName"):
        raise RuntimeError("Admitted host lease has no authoritative container name")
    return lease


def install_controlled_catalog() -> None:
    """Pair only marked first-run turns with their fixed external-provider catalog.

    The production attestor still owns every host probe, membership check,
    retry and evidence artifact. Only the test provider's catalog observation
    changes, and it must execute successfully inside that same exact host.
    """
    from moonmind.omnigent import production
    from moonmind.omnigent.bootstrap.opencode import ZEN_FREE_QUALIFIED
    from moonmind.omnigent.harness_platform.failures import (
        HarnessPlatformError,
        HarnessPlatformFailure,
    )
    from moonmind.omnigent.host_services import attestation

    require_test_project()
    original_reader = attestation._read_exact_host_model_options

    async def read_model_options(**kwargs: Any):
        if not _CONTROLLED_CATALOG.get() or kwargs["harness_id"] != "opencode-native":
            return await original_reader(**kwargs)
        require_test_project()
        # This literal advertises the controlled provider's supported model.
        # It never reads the request's selected model or upstream catalog.
        fixed_catalog = {"models": [{"id": ZEN_FREE_QUALIFIED}]}
        probe = attestation._substrate_guarded_probe(
            "import json; print(json.dumps(" + repr(fixed_catalog) + "))"
        )
        code, stdout, stderr = await kwargs["backend"].run(
            [
                "docker",
                "exec",
                kwargs["container_name"],
                "/opt/venv/bin/python",
                "-c",
                probe,
            ],
            timeout_seconds=45.0,
            check=False,
        )
        attestation._raise_if_probe_substrate_unavailable(
            code, stderr, boundary="Controlled first-run provider model catalog"
        )
        if code != 0:
            raise HarnessPlatformError(
                "exact host controlled provider catalog probe failed",
                code=HarnessPlatformFailure.OMNIGENT_MODEL_UNAVAILABLE,
            )
        try:
            payload = json.loads(stdout)
        except (TypeError, json.JSONDecodeError) as exc:
            raise HarnessPlatformError(
                "exact host controlled provider catalog returned invalid JSON",
                code=HarnessPlatformFailure.OMNIGENT_MODEL_UNAVAILABLE,
            ) from exc
        if not isinstance(payload, dict) or not isinstance(payload.get("models"), list):
            raise HarnessPlatformError(
                "exact host controlled provider probe returned an invalid catalog",
                code=HarnessPlatformFailure.OMNIGENT_MODEL_UNAVAILABLE,
            )
        return payload, "exact-host-controlled-first-run-provider"

    class JourneyScopedAttestor(attestation.DockerOmnigentHostAttestor):
        async def attest(self, **kwargs: Any):
            controlled = (
                kwargs["plan"].payload.harnessId == "opencode-native"
                and await _marker(kwargs["request"], self._artifacts, required=False)
                is not None
            )
            token = _CONTROLLED_CATALOG.set(controlled)
            try:
                return await super().attest(**kwargs)
            finally:
                _CONTROLLED_CATALOG.reset(token)

    attestation._read_exact_host_model_options = read_model_options
    production.DockerOmnigentHostAttestor = JourneyScopedAttestor


class ControlledProvider:
    def __init__(self, client: Any, leases: Any, docker: Any):
        self.client, self.leases, self.docker = client, leases, docker

    async def __call__(
        self,
        request: Any,
        *,
        session_authority_sink: Any,
        artifact_gateway: Any,
        **_continuation: Any,
    ):
        from moonmind.schemas.agent_runtime_models import AgentRunResult
        from moonmind.omnigent.bootstrap.opencode import ZEN_FREE_QUALIFIED

        require_test_project()
        marker = await _marker(request, artifact_gateway)
        sink = session_authority_sink
        binding = sink.binding
        step = request.step_execution
        if step is None:
            raise RuntimeError(
                "Controlled provider requires an admitted Step Execution"
            )
        omni = request.parameters["omnigent"]
        session_options = omni["session"]
        if (
            request.parameters.get("model") != ZEN_FREE_QUALIFIED
            or session_options.get("modelOverride") != ZEN_FREE_QUALIFIED
            or omni["agent"].get("harnessOverride") != "opencode-native"
        ):
            raise RuntimeError(
                "Controlled first-run execution requires its supported model "
                f"{ZEN_FREE_QUALIFIED} and opencode-native harness"
            )
        locator = request.workspace_spec.get("workspaceLocator", {})
        if session_options["hostId"] != binding.omnigentHostId or not locator.get(
            "workspaceId"
        ):
            raise RuntimeError("Request does not name the admitted host and workspace")
        identity = {
            "marker": marker,
            "model": ZEN_FREE_QUALIFIED,
            "provider": "controlled-first-run-provider",
            "bindingId": binding.bindingId,
            "hostId": binding.omnigentHostId,
            "workspaceId": locator["workspaceId"],
            "workflowId": step.workflow_id,
            "runId": step.run_id,
            "logicalStepId": step.logical_step_id,
            "executionOrdinal": step.execution_ordinal,
        }
        prior = (binding.phaseResults or {}).get(PHASE)
        if prior is not None:
            if (
                any(prior.get(key) != value for key, value in identity.items())
                or prior.get("sessionId") != binding.omnigentSessionId
            ):
                raise RuntimeError(
                    "Accepted first-run computation belongs to another request"
                )
            evidence = prior
        else:
            lease = await _owned_lease(binding, self.leases)
            if lease.status != "ready":
                raise RuntimeError("Admitted host lease is not ready for computation")
            container = lease.cleanupHandle["containerName"]
            observed = await self.docker.inspect_container(container)
            labels = observed.get("Config", {}).get("Labels", {})
            expected = {
                "moonmind.runtime_binding_id": binding.bindingId,
                "moonmind.host_lease_ref": binding.hostLeaseRef,
                "moonmind.host_lease_generation": str(binding.hostLeaseGeneration),
            }
            if any(labels.get(key) != value for key, value in expected.items()):
                raise RuntimeError(
                    "Container ownership does not match admitted host lease"
                )
            session = binding.omnigentSessionId
            if not session:
                created = await self.client.create_session(
                    {
                        "idempotency_key": "first-run-generated-session:"
                        + hashlib.sha256(binding.bindingId.encode()).hexdigest(),
                        "agent_id": omni["agent"]["agentId"],
                        "host_type": session_options["hostType"],
                        "host_id": session_options["hostId"],
                        "workspace": session_options["workspace"],
                        "harness_override": omni["agent"]["harnessOverride"],
                        "initial_items": [],
                        "title": "MoonMind generated first-run journey",
                    }
                )
                session = created["id"]
                await sink.session_created(session)
            command = [
                "docker",
                "exec",
                "-w",
                session_options["workspace"],
                container,
                "python3",
            ]
            code, _out, err = await self.docker.run(
                command + ["-c", _COMPUTE, marker], check=False
            )
            if code != 0:
                raise RuntimeError(
                    f"Admitted host report computation exited {code}: {err[:500]}"
                )
            read_code, report, read_error = await self.docker.run(
                command
                + [
                    "-c",
                    "from pathlib import Path; print(Path('report.md').read_text(), end='')",
                ],
                check=False,
            )
            if read_code != 0 or report.encode() != _expected_report(marker):
                raise RuntimeError(
                    f"Admitted host report readback failed verification: {read_error[:500]}"
                )
            evidence = {
                **identity,
                "sessionId": session,
                "outputPath": "report.md",
                "sha256": hashlib.sha256(report.encode()).hexdigest(),
                "exitCode": code,
            }
            await sink.record_phase(PHASE, evidence)
        if not (sink.binding.phaseResults or {}).get("firstRunGeneratedEvidence"):
            ref = await artifact_gateway.write_json(
                request=request,
                name="first-run-generated-compute.json",
                payload=evidence,
                link_type="evidence.first_run.compute",
            )
            await sink.record_phase("firstRunGeneratedEvidence", {"artifactRef": ref})
        return AgentRunResult(
            summary="Generated and verified report.md in the admitted host.",
            metadata={"omnigentSessionId": evidence["sessionId"]},
        )


async def controlled_provider(request: Any, **kwargs: Any):
    from api_service.db.base import async_session_maker
    from moonmind.omnigent.host_leases import DbOmnigentHostLeaseRepository
    from moonmind.omnigent.host_services.docker_backend import DockerCommandBackend
    from moonmind.omnigent.settings import (
        resolved_api_token,
        resolved_proxy_forward_headers,
        resolved_server_url,
    )
    from moonmind.workflows.adapters.omnigent_client import OmnigentHttpClient

    client = OmnigentHttpClient(
        base_url=resolved_server_url(),
        api_token=resolved_api_token(),
        client=kwargs["transport_pool"].client(),
        upstream_header_allowlist=resolved_proxy_forward_headers(),
    )
    return await ControlledProvider(
        client,
        DbOmnigentHostLeaseRepository(async_session_maker),
        DockerCommandBackend(),
    )(request, **kwargs)


async def cleanup_saved_workspace(
    state: dict[str, Any],
    *,
    bindings: Any,
    leases: Any,
    docker: Any,
    artifacts: Any,
    workspace_root: Path,
) -> dict[str, Any]:
    """Discard one proven saved workspace after production has cleaned its host."""
    from moonmind.workflows.temporal.runtime.workspace_locators import (
        SandboxWorkspaceRecordStore,
    )

    require_test_project()
    selected = state.get("generatedOutput") or {}
    binding = await bindings.get(selected.get("bindingId", ""))
    if binding is None or binding.state != "cleaned":
        raise RuntimeError(
            "Workspace cleanup requires a cleaned production runtime binding"
        )
    phases = binding.phaseResults or {}
    evidence, saved = phases.get(PHASE), phases.get("saved")
    if (
        not evidence
        or not phases.get("compute")
        or not saved
        or phases["compute"].get("failureClass")
    ):
        raise RuntimeError(
            "Workspace cleanup requires separate successful compute and saved phases"
        )
    for key in (
        "bindingId",
        "workspaceId",
        "workflowId",
        "runId",
        "marker",
        "sha256",
        "sessionId",
        "hostId",
    ):
        if selected.get(key) != evidence.get(key) or not evidence.get(key):
            raise RuntimeError(f"Cleanup evidence identity mismatch: {key}")
    if (
        evidence.get("exitCode") != 0
        or evidence.get("outputPath") != "report.md"
        or evidence["sessionId"] != binding.omnigentSessionId
        or evidence["hostId"] != binding.omnigentHostId
        or evidence["bindingId"] != binding.bindingId
    ):
        raise RuntimeError(
            "Cleanup requires accepted computation from this binding's session"
        )
    for key in ("checkpointRef", "archiveRef", "archiveDigest"):
        if not saved.get(key):
            raise RuntimeError(f"Saved checkpoint identity mismatch: {key}")
    # A retried save can publish equivalent content under a new artifact ID.
    # The durable binding owns the references; content identity fences deletion.
    if selected.get("archiveDigest") != saved["archiveDigest"]:
        raise RuntimeError("Saved checkpoint identity mismatch: archiveDigest")
    checkpoint = json.loads(await artifacts.read_bytes(saved["checkpointRef"]))
    if (
        checkpoint.get("boundary") != "after_execution"
        or any(
            checkpoint.get("source", {}).get(key) != evidence.get(key)
            for key in ("workflowId", "runId", "logicalStepId", "executionOrdinal")
        )
        or any(
            checkpoint.get("workspace", {}).get(key) != saved[key]
            for key in ("archiveRef", "archiveDigest")
        )
    ):
        raise RuntimeError(
            "Saved checkpoint does not identify the accepted computation and archive"
        )
    archive = await artifacts.read_bytes(saved["archiveRef"])
    if hashlib.sha256(archive).hexdigest() != saved["archiveDigest"].removeprefix(
        "sha256:"
    ):
        raise RuntimeError(
            "Durable saved archive failed readback verification; workspace retained"
        )
    lease = await _owned_lease(binding, leases)
    if lease.status != "cleaned":
        raise RuntimeError("Host lease has not completed production cleanup")
    container = lease.cleanupHandle["containerName"]
    if not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_.-]*", container):
        raise RuntimeError("Invalid authoritative host container name")
    code, found, error = await docker.run(
        [
            "docker",
            "ps",
            "-a",
            "--filter",
            f"name=^/{re.escape(container)}$",
            "--format",
            "{{.ID}}",
        ],
        check=False,
    )
    if code != 0 or found.strip():
        raise RuntimeError(
            f"Cannot confirm admitted host removal: {error[:300] or found.strip()}"
        )
    workspace = phases.get("workspace", {})
    locator = workspace.get("workspaceSpec", {}).get("workspaceLocator", {})
    step = workspace.get("stepExecution", {})
    workspace_id = evidence["workspaceId"]
    expected_id = hashlib.sha256(
        f"{evidence['workflowId']}:{step.get('stepExecutionId', '')}".encode()
    ).hexdigest()[:24]
    if (
        not re.fullmatch(r"[0-9a-f]{24}", workspace_id)
        or workspace_id != expected_id
        or locator.get("kind") != "sandbox"
        or locator.get("workspaceId") != workspace_id
        or locator.get("relativePath", "repo") != "repo"
        or step.get("workflowId") != evidence["workflowId"]
    ):
        raise RuntimeError("Workspace does not belong to the admitted execution")
    root = workspace_root.resolve()
    authority = root / "temporal_sandbox"
    target = authority / workspace_id
    if (
        authority.is_symlink()
        or (authority / ".workspace_records").is_symlink()
        or target.is_symlink()
        or target.resolve().parent != authority
    ):
        raise RuntimeError(
            "Workspace cleanup cannot follow symlinks or leave its authority"
        )
    records = SandboxWorkspaceRecordStore(root)

    def remove_owned_workspace() -> bool:
        # Use the same persistent inode as grant admission. Keep all mutable
        # ownership/retention checks and deletion in one synchronous critical
        # section, off the event loop; discard deliberately preserves the lock.
        with records.claims_locked(workspace_id):
            owner = records.load(workspace_id)
            if target.exists() and (
                owner is None
                or owner.workflow_id != evidence["workflowId"]
                or owner.step_execution_id != step["stepExecutionId"]
                or owner.relative_path != "repo"
            ):
                raise RuntimeError(
                    "Workspace owner record does not match the saved binding"
                )
            if records.read_unsaved(workspace_id) or records.active_claim_grantees(
                workspace_id
            ):
                raise RuntimeError(
                    "Workspace is retained for unsaved work or an active reader"
                )
            if target.exists():
                shutil.rmtree(target)
            records.discard(workspace_id)
            return not target.exists()

    workspace_removed = await asyncio.to_thread(remove_owned_workspace)
    return {
        "bindingId": binding.bindingId,
        "workspaceId": workspace_id,
        "workspaceRemoved": workspace_removed,
        "hostRemoved": True,
    }


async def _cleanup_from_file(path: Path) -> dict[str, Any]:
    from api_service.db.base import async_session_maker
    from moonmind.omnigent.bridge_artifacts import TemporalOmnigentArtifactGateway
    from moonmind.omnigent.host_leases import DbOmnigentHostLeaseRepository
    from moonmind.omnigent.host_services.docker_backend import DockerCommandBackend
    from moonmind.omnigent.runtime_bindings import DbRuntimeBindingStore

    root = os.environ.get("WORKFLOW_WORKSPACE_ROOT")
    if not root:
        raise RuntimeError("Cleanup requires an explicit WORKFLOW_WORKSPACE_ROOT")
    return await cleanup_saved_workspace(
        json.loads(path.read_text()),
        bindings=DbRuntimeBindingStore(async_session_maker),
        leases=DbOmnigentHostLeaseRepository(async_session_maker),
        docker=DockerCommandBackend(),
        artifacts=TemporalOmnigentArtifactGateway(async_session_maker),
        workspace_root=Path(root),
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["worker", "cleanup"])
    parser.add_argument("--state-file", type=Path)
    parser.add_argument("--receipt-file", type=Path)
    args = parser.parse_args()
    require_test_project()
    if args.command == "cleanup":
        if not args.state_file:
            parser.error("cleanup requires --state-file")
        receipt = asyncio.run(_cleanup_from_file(args.state_file))
        if args.receipt_file:
            args.receipt_file.write_text(json.dumps(receipt, sort_keys=True) + "\n")
        print(json.dumps(receipt, sort_keys=True))
    else:
        # Follow the normal worker bootstrap before loading provider services.
        # isort: off
        from moonmind.workflows.temporal import worker_runtime
        from moonmind.omnigent import production

        # isort: on

        production.run_omnigent_execution = controlled_provider
        install_controlled_catalog()
        worker_runtime._configure_worker_logging(
            enable_opentelemetry=os.environ.get("MOONMIND_ENABLE_OPENTELEMETRY", "0")
            == "1"
        )
        asyncio.run(worker_runtime.main_async())


if __name__ == "__main__":
    main()
