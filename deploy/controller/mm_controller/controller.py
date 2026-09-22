"""Controller orchestration over injected runners (stdlib only).

``DockerRunner`` is a small interface the host wires to real subprocess
calls; tests inject fakes. The controller itself never imports Docker SDKs,
MoonMind modules, DB, Temporal, or artifact services.

Lifecycle:
  validate request -> acquire installation-local kernel lock ->
  reconcile competing Compose child -> stage images (pull --policy always) ->
  persist prepared target -> apply (up changed services) ->
  verify after apply -> persist installed (only on mandatory success) ->
  record attempt (original errors preserved, redacted tail surfaced)

Restart recovery uses :meth:`OperationStore.converge_on_restart`: a lost
result never repeats a completed apply; an unfinished operation converges
toward the same prepared target.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Protocol

from . import compose_plan, lifecycle
from .compose_plan import PlanRequest
from .lifecycle import CheckResult
from .redact import bound_tail, redact_text
from .store import MAX_AUTO_ATTEMPTS, OperationRecord, OperationStore


class ControllerError(RuntimeError):
    def __init__(self, message: str, *, exit_code: int | None = None, log_tail: str = "") -> None:
        super().__init__(message)
        self.exit_code = exit_code
        self.log_tail = log_tail


class DockerRunner(Protocol):
    """Injected side-effect boundary (subprocess in production, fake in tests)."""

    def pull(self, command: tuple[str, ...]) -> tuple[int, str]:
        """Run the pull command. Returns (exit_code, combined_output)."""

    def up(self, command: tuple[str, ...]) -> tuple[int, str]:
        """Run the up command. Returns (exit_code, combined_output)."""

    def running_images(self) -> Mapping[str, str]:
        """Observed running service -> image mapping for convergence checks."""

    def reconcile_child(self) -> str:
        """Reconcile or stop an existing competing Compose child before launch.
        Returns a short description of what was done (possibly 'none')."""


@dataclass(slots=True)
class UpdateResult:
    operation_id: str
    status: str
    exit_code: int | None = None
    log_tail: str = ""
    checks: tuple[CheckResult, ...] = ()


class Controller:
    def __init__(self, *, store: OperationStore, runner: DockerRunner) -> None:
        self._store = store
        self._runner = runner

    # -- update ------------------------------------------------------

    def update(
        self,
        request: PlanRequest,
        *,
        pre: lifecycle.PreApplyVerdict,
        post_checks: tuple[CheckResult, ...] | None = None,
    ) -> UpdateResult:
        errors = compose_plan.validate_request(request)
        if errors:
            raise ControllerError("; ".join(errors))
        if not pre.allowed:
            failed = lifecycle.summarize(pre.checks).get("mandatoryFailed", [])
            raise ControllerError(f"pre-apply validation failed: {failed}")

        record = self._store.explicit_retry(target_image=request.target_image)
        automatic = [a for a in record.attempts if not a.get("explicitRetry")]
        # Count only real execution attempts (phase pull/apply), not retry markers.
        executions = [a for a in automatic if a.get("phase") in ("pull", "apply")]
        if len(executions) >= MAX_AUTO_ATTEMPTS:
            record.status = "FAILED"
            self._store.save_operation(record)
            raise ControllerError(
                "automatic attempt budget exhausted; an explicit Retry is required"
            )

        plan = compose_plan.build_plan(services=request.services, mode=request.mode)

        reconcile_note = self._runner.reconcile_child()
        self._store.append_log(f"reconcile: {reconcile_note}\n")

        # Stage all images before apply: pull failure leaves containers untouched.
        exit_code, output = self._runner.pull(plan.pull_command)
        if exit_code != 0:
            tail = redact_text(bound_tail(output))
            self._store.record_attempt(record, phase="pull", ok=False, exit_code=exit_code, log_tail=tail)
            record.status = "FAILED"
            self._store.save_operation(record)
            raise ControllerError(
                f"image staging failed with exit {exit_code}",
                exit_code=exit_code,
                log_tail=tail,
            )
        self._store.record_attempt(record, phase="pull", ok=True, exit_code=0, log_tail=output)
        self._store.mark_prepared(
            target_image=request.target_image,
            resolved_images={request.target_image: request.target_image},
        )

        exit_code, output = self._runner.up(plan.up_command)
        if exit_code != 0:
            tail = redact_text(bound_tail(output))
            self._store.record_attempt(record, phase="apply", ok=False, exit_code=exit_code, log_tail=tail)
            record.status = "FAILED"
            self._store.save_operation(record)
            raise ControllerError(
                f"apply failed with exit {exit_code}",
                exit_code=exit_code,
                log_tail=tail,
            )
        self._store.record_attempt(record, phase="apply", ok=True, exit_code=0, log_tail=output)

        verdict = lifecycle.verify_after_apply(checks=tuple(post_checks or ()))
        if verdict.status == "SUCCEEDED":
            self._store.mark_installed(
                target_image=request.target_image,
                resolved_images={request.target_image: request.target_image},
                operation_id=record.operation_id,
            )
        # PARTIALLY_VERIFIED / FAILED: installed is NOT overwritten, the
        # failure stays explicit, and the controller remains usable.
        record.status = verdict.status
        self._store.save_operation(record)
        return UpdateResult(
            operation_id=record.operation_id,
            status=verdict.status,
            exit_code=0,
            checks=verdict.checks,
        )

    # -- restart recovery ----------------------------------------------

    def recover_on_restart(self) -> str:
        """Converge unfinished work toward the same prepared target.

        Never repeats a completed apply: when installed already matches the
        prepared target the operation is settled without touching Compose.
        """
        return self._store.converge_on_restart(
            observed_running=dict(self._runner.running_images())
        )


def extract_release_config(artifact: Mapping[str, Any]) -> dict[str, Any]:
    """Extract release configuration from a selected trusted artifact as data.

    The artifact is plain JSON-serializable data (e.g. a pinned release
    manifest); this never imports the target application's Python bootstrap.
    Only allowlisted keys are carried forward.
    """
    if not isinstance(artifact, Mapping):
        raise ControllerError("trusted release artifact must be a mapping")
    allowed = ("targetImage", "target_image", "services", "projectName", "project_name")
    extracted = {k: artifact[k] for k in allowed if k in artifact}
    image = str(extracted.get("targetImage") or extracted.get("target_image") or "").strip()
    if not image:
        raise ControllerError("trusted release artifact carries no target image")
    services = extracted.get("services") or []
    return {
        "targetImage": image,
        "services": tuple(str(s) for s in services) if isinstance(services, list) else (),
        "projectName": str(extracted.get("projectName") or extracted.get("project_name") or "moonmind"),
    }
