"""Controller apply orchestration for the standalone controller.

Stdlib-only. This module wires the existing building blocks (compose command
builders, crash-safe state, restart convergence, kernel lock, mount adapter,
redaction) into one bounded apply path:

- stage ALL requested images (``pull --policy always``) before apply;
- apply changed services with ``--pull never --no-build --remove-orphans
  --wait`` under bounded timeouts;
- preserve installed infrastructure image/config selections unless an
  infrastructure change is intentional;
- keep required ``init-db`` gating in Compose (never a second migration
  runner);
- surface exit status + redacted log tail on failure;
- full ``down`` and force-recreate are explicit repair only, never automatic;
- no volume/image pruning;
- persist prepared target + last installed config + compatibility-gated
  previous release;
- pre-apply validation and post-apply verification keep mandatory failures
  explicit while leaving the controller usable;
- reporting/cleanup never erases a confirmed installation.

Actual subprocess execution is injectable (``run`` callable) so unit tests
exercise the orchestration without Docker.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass

from moonmind_controller import compose, lock, mounts, redact, service, state

# Infrastructure services whose image/config selections are preserved unless
# the requested update intentionally changes them. Application-release churn
# must not leak into these definitions.
INFRA_SERVICES = ("postgres", "temporal", "minio", "temporal-ui")

# Required init-db gating stays in Compose: the orchestrator refuses to apply
# a target that drops it when the installed configuration requires it.
INIT_DB_SERVICE = "init-db"


@dataclass
class CommandResult:
    returncode: int
    output: str


def _default_run(
    command: tuple[str, ...], *, timeout: int, env: dict | None = None
) -> CommandResult:
    """Run one Compose command, capturing merged output for diagnostics.

    ``env`` carries the requested release images (see
    :func:`compose.image_env`); it is layered over the process environment
    so Compose interpolation recreates the requested release instead of
    whatever the ambient configuration already names.
    """
    import os

    merged_env = dict(os.environ)
    merged_env.update(dict(env or {}))
    try:
        completed = subprocess.run(
            list(command),
            capture_output=True,
            text=True,
            timeout=timeout,
            env=merged_env,
        )
    except subprocess.TimeoutExpired as exc:
        partial = ""
        if exc.stdout:
            partial += str(exc.stdout)
        if exc.stderr:
            partial += ("\n" if partial else "") + str(exc.stderr)
        raise TimeoutError(
            f"Compose command timed out after {timeout}s: {' '.join(command)}"
        ) from exc
    merged = (completed.stdout or "").strip()
    if completed.stderr:
        merged += ("\n" if merged else "") + (completed.stderr or "").strip()
    return CommandResult(returncode=completed.returncode, output=merged)


def _failure_report(command: tuple[str, ...], result: CommandResult) -> str:
    tail = redact.log_tail(result.output)
    return (
        f"{' '.join(command)} failed (exit {result.returncode}); "
        "deployment remains on its previous containers"
        + (f"\nDiagnostics (redacted):\n{tail}" if tail else "\nDiagnostics: no output captured.")
    )


def preserve_infra_images(
    *,
    installed_images: dict,
    requested_images: dict,
    infra_changed_intentionally: bool = False,
) -> dict:
    """Keep installed infra selections unless the change is intentional."""
    merged = dict(requested_images or {})
    if infra_changed_intentionally:
        return merged
    for name in INFRA_SERVICES:
        if name in (installed_images or {}) and name not in merged:
            merged[name] = installed_images[name]
    return merged


def validate_target(
    *,
    target: dict,
    installed_config: dict | None = None,
    required_host_sources: tuple[str, ...] = (),
    host_source_exists=None,
) -> list[str]:
    """Pre-apply validation: config, authorization, storage, access.

    Returns a list of blocking errors (empty means valid). The old API/worker
    health is never consulted here: it is diagnostic, not admission to repair.
    """
    errors: list[str] = []
    if not isinstance(target, dict) or not target:
        return ["Target configuration is empty; refusing to apply."]
    if not target.get("targetImage") and not target.get("concreteImages"):
        errors.append("Target names no image; refusing to apply.")
    if not target.get("services"):
        errors.append("Target names no services; refusing to apply.")
    auth = target.get("authorization")
    if auth is not None and not auth:
        errors.append("Target authorization is empty; refusing to apply.")
    storage = target.get("storage")
    if storage is not None and not storage:
        errors.append("Target persistent storage is unconfigured; refusing to apply.")
    # Preservation of access settings: overrides/ports/ingress must survive.
    access = target.get("accessSettings")
    if access is not None and not access:
        errors.append("Target drops operator access settings; refusing to apply.")
    # init-db gating stays in Compose when the installed config requires it.
    installed_services = set((installed_config or {}).get("services") or [])
    target_services = set(target.get("services") or [])
    if INIT_DB_SERVICE in installed_services and INIT_DB_SERVICE not in target_services:
        errors.append(
            "Target drops required init-db gating; refusing to apply "
            "without an explicit repair operation."
        )
    exists = host_source_exists or (lambda _path: True)
    for source in required_host_sources or ():
        try:
            # ``exists=False`` raises; missing sources must never become
            # empty auto-created bind directories.
            mounts.require_host_source(source, exists=bool(exists(source)))
        except FileNotFoundError as exc:
            errors.append(str(exc))
    return errors


def verify_post_apply(
    *,
    service_statuses: dict | None,
    operator_access_ok: bool | None,
    artifact_store_ok: bool | None = None,
    expected_services: tuple[str, ...] = (),
    require_operator_access: bool = False,
    require_artifact_store: bool = False,
) -> list[str]:
    """Post-apply verification: service, dispatch, operator access.

    Every expected service needs a ``running`` observation: missing or
    non-running entries fail instead of passing silently. Operator and
    artifact-store checks are mandatory when the target declares them
    (``accessSettings``/``storage``); an unavailable observation for a
    declared check is unverified and fails. Undeclared checks stay absent,
    mirroring pre-apply validation. Failed or unavailable mandatory checks
    are returned explicitly; the caller records them without erasing a
    confirmed installation.
    """
    failures: list[str] = []
    statuses = dict(service_statuses or {})
    for name in expected_services or ():
        if statuses.get(name) != "running":
            failures.append(
                f"Service {name} is {statuses.get(name) or 'unverified'} after apply."
            )
    for name, status in statuses.items():
        if status != "running" and name not in (expected_services or ()):
            failures.append(f"Service {name} is {status or 'unknown'} after apply.")
    if operator_access_ok is False:
        failures.append("Operator access verification failed after apply.")
    elif operator_access_ok is None and require_operator_access:
        failures.append("Operator access verification is unavailable after apply.")
    if artifact_store_ok is False:
        failures.append("Artifact-store verification failed after apply.")
    elif artifact_store_ok is None and require_artifact_store:
        failures.append("Artifact-store verification is unavailable after apply.")
    return failures


def stage_all_images(
    *,
    images: dict,
    services: tuple[str, ...] = (),
    target_image: str | None = None,
    env: dict | None = None,
    project: str | None = None,
    project_directory: str | None = None,
    compose_files: tuple[str, ...] = (),
    run=None,
) -> CommandResult:
    """Stage every image needed for the requested update before apply.

    The requested ``images`` mapping is honored through the Compose
    environment (see :func:`compose.image_env`): an explicit ``env`` wins,
    otherwise the environment is derived from ``target_image`` plus the
    requested mapping, so staging pulls the requested release instead of
    whatever the ambient configuration already names.
    """
    runner = run or _default_run
    effective_env = (
        dict(env)
        if env is not None
        else compose.image_env(target_image=target_image, concrete_images=images)
    )
    command = compose.build_pull_command(
        services=services,
        project=project,
        project_directory=project_directory,
        compose_files=compose_files,
    )
    result = runner(
        command, timeout=compose.PULL_TIMEOUT_SECONDS, env=effective_env or None
    )
    if result.returncode:
        raise RuntimeError(_failure_report(command, result))
    return result


def apply_changed_services(
    *,
    services: tuple[str, ...],
    run=None,
    force_recreate: bool = False,
    env: dict | None = None,
    project: str | None = None,
    project_directory: str | None = None,
    compose_files: tuple[str, ...] = (),
) -> CommandResult:
    """Recreate changed services; force-recreate is explicit repair only."""
    if force_recreate:
        raise ValueError(
            "force-recreate is an explicit repair operation; "
            "use the explicit repair path instead of the default apply."
        )
    runner = run or _default_run
    command = compose.build_up_command(
        services=tuple(services),
        project=project,
        project_directory=project_directory,
        compose_files=compose_files,
    )
    result = runner(command, timeout=compose.UP_TIMEOUT_SECONDS, env=env or None)
    if result.returncode:
        raise RuntimeError(_failure_report(command, result))
    return result


def prepare_apply_record(
    *,
    record: dict,
    target: dict,
    installed_images: dict | None = None,
    infra_changed_intentionally: bool = False,
) -> tuple[dict, dict]:
    """Derive staged images and record the prepared target (no side effects).

    Returns ``(updated_record, staged_images)``. The caller persists the
    updated record before the first Compose side effect so interruption
    reconciles against the selected inputs instead of the original record.
    """
    requested_images = dict((target or {}).get("concreteImages") or {})
    staged_images = preserve_infra_images(
        installed_images=dict(installed_images or {}),
        requested_images=requested_images,
        infra_changed_intentionally=infra_changed_intentionally,
    )
    prepared = dict(target or {})
    prepared["concreteImages"] = staged_images
    updated = state.record_prepared_target(record, target=prepared)
    updated = state.record_concrete_images(updated, staged_images)
    return updated, staged_images


def orchestrate_apply(
    *,
    record: dict,
    target: dict,
    installed_images: dict | None = None,
    installed_config: dict | None = None,
    required_host_sources: tuple[str, ...] = (),
    host_source_exists=None,
    is_desktop_daemon: bool | None = None,
    infra_changed_intentionally: bool = False,
    service_statuses: dict | None = None,
    operator_access_ok: bool | None = None,
    artifact_store_ok: bool | None = None,
    run=None,
    previous_release: dict | None = None,
    previous_compatible: bool = True,
) -> dict:
    """Run the bounded controller apply for one operation record.

    Pure orchestration over injectable boundaries: ``run`` executes Compose
    commands, ``host_source_exists`` answers bind-source presence, and
    ``service_statuses``/``operator_access_ok``/``artifact_store_ok`` carry
    post-apply observations (``None`` means unobserved, which fails every
    mandatory check instead of passing silently).
    Returns the updated record (installed on success; attempt error recorded
    by the caller on exception).
    """
    if state.apply_already_complete(record):
        return record
    # Bind sources are derived from the selected daemon's actual mounts:
    # Desktop spellings resolve only on positive Desktop evidence, and every
    # required source must exist (never an empty auto-created bind dir).
    resolved_sources = tuple(
        mounts.resolve_bind_source(source, is_desktop_daemon=is_desktop_daemon)
        for source in (required_host_sources or ())
    )
    errors = validate_target(
        target=dict(target or {}),
        installed_config=installed_config,
        required_host_sources=resolved_sources,
        host_source_exists=host_source_exists,
    )
    if errors:
        raise ValueError("; ".join(errors))

    updated, staged_images = prepare_apply_record(
        record=record,
        target=target,
        installed_images=installed_images,
        infra_changed_intentionally=infra_changed_intentionally,
    )

    services = tuple((target or {}).get("services") or ())
    project = (target or {}).get("stack")
    project_directory = (target or {}).get("projectDirectory")
    compose_files = tuple((target or {}).get("composeFiles") or ())
    image_env = compose.image_env(
        target_image=(target or {}).get("targetImage"),
        concrete_images=staged_images,
    )
    # Reconcile or stop an existing Compose child before launching anew.
    # The caller supplies liveness via service.reconcile_child; here the
    # decision point is explicit so a competing child is never ignored.
    child_state = (target or {}).get("child") or {}
    decision = service.reconcile_child(
        child_running=bool(child_state.get("running", False)),
        child_owner_matches=bool(child_state.get("ownerMatches", True)),
    )
    if decision == "stop_competing":
        raise RuntimeError(
            "A competing Compose child is running; stop or reconcile it "
            "before launching a new apply."
        )

    stage_all_images(
        images=staged_images,
        services=services,
        target_image=(target or {}).get("targetImage"),
        env=image_env or None,
        project=project,
        project_directory=project_directory,
        compose_files=compose_files,
        run=run,
    )
    apply_changed_services(
        services=services,
        run=run,
        env=image_env or None,
        project=project,
        project_directory=project_directory,
        compose_files=compose_files,
    )

    failures = verify_post_apply(
        service_statuses=service_statuses,
        operator_access_ok=operator_access_ok,
        artifact_store_ok=artifact_store_ok,
        expected_services=services,
        require_operator_access="accessSettings" in (target or {}),
        require_artifact_store="storage" in (target or {}),
    )
    if failures:
        raise RuntimeError("; ".join(failures))

    desired_image = (updated.get("desired") or {}).get("targetImage") or target.get(
        "targetImage"
    )
    updated = state.mark_installed(
        updated,
        installed_image=str(desired_image or ""),
        service_images=staged_images,
    )
    updated = state.record_installed_config(
        updated, config=dict(installed_config or {"services": list(services)})
    )
    if previous_release is not None:
        updated = state.record_previous_release(
            updated, previous=dict(previous_release), compatible=previous_compatible
        )
    return updated


def locked_orchestrate_apply(*, lock_dir, stack: str, **kwargs) -> dict:
    """Hold the installation-local kernel lock across one apply.

    The lock reuses the ``moonmind.deployment-kernel-lock.v1`` contract: a
    legacy owner blocks cutover explicitly, a live holder blocks a second
    writer, and two independent installations on one daemon hold independent
    locks because ``lock_dir`` is installation-local.
    """
    with lock.hold(lock_dir, stack=stack):
        return orchestrate_apply(**kwargs)
