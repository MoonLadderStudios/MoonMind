"""Top-level MoonMind CLI exposing worker utilities."""

from __future__ import annotations

import json
import os
from dataclasses import asdict
from pathlib import Path
from typing import Annotated

import typer

from moonmind.container_job_cli import (
    ContainerJobCliError,
    ContainerJobResult,
    load_container_job_spec,
    run_container_job,
    run_passive_scan_job,
    run_python_tests,
)
from moonmind.utils.logging import redact_sensitive_text

app = typer.Typer(
    help=(
        "MoonMind developer utilities (worker, container, workflow). "
        "The operator interface uses local transport or trusted ingress admission. "
        "`workflow run/status/logs/download` is the thin authenticated client for "
        "ordinary workflows (MoonLadderStudios/MoonMind#3939)."
    )
)
worker_app = typer.Typer(help="Worker runtime diagnostics.")
container_app = typer.Typer(help="Run work through MoonMind's Docker backend.")
runtime_resources_app = typer.Typer(
    help="Install reusable deployment runtime resources."
)
workflow_app = typer.Typer(
    help=(
        "Submit and observe ordinary workflows through the public execution API. "
        "Presets, defaults, model/profile selection, and publication "
        "normalization remain server-owned. Only run/status/logs/download exist here."
    )
)
app.add_typer(worker_app, name="worker")
app.add_typer(container_app, name="container")
app.add_typer(runtime_resources_app, name="runtime-resources")
app.add_typer(workflow_app, name="workflow")


@runtime_resources_app.command("install")
def runtime_resources_install(
    source: Path = typer.Argument(
        ..., help="Resource package directory or resources.json."
    ),
    project_dir: Path = typer.Option(
        Path.cwd(), "--project-dir", help="MoonMind deployment project directory."
    ),
) -> None:
    from moonmind.config.runtime_resources_install import install_runtime_resources

    try:
        installed = install_runtime_resources(source, project_dir)
    except (OSError, ValueError, RuntimeError) as exc:
        from moonmind.config.container_backend_settings import (
            ContainerBackendConfigError,
        )
        from moonmind.config.runtime_resources import RuntimeResourcesConfigError

        detail = (
            str(exc)
            if isinstance(
                exc, (ContainerBackendConfigError, RuntimeResourcesConfigError)
            )
            or type(exc) is ValueError
            else type(exc).__name__
        )
        typer.secho(
            f"Resource installation failed: {detail}", fg=typer.colors.RED, err=True
        )
        raise typer.Exit(code=1) from None
    typer.echo(f"Installed runtime resources: {installed}")
    typer.echo(
        "Activate through the standard deployment controller update; ordinary workflows then inherit these resources."
    )


def _print_container_job_result(result: ContainerJobResult) -> None:
    for line in result.log_tail:
        typer.echo(line)
    if result.log_error:
        typer.secho(
            f"Warning: terminal logs could not be read: {result.log_error}",
            fg=typer.colors.YELLOW,
            err=True,
        )
    if result.artifact_error:
        typer.secho(
            "Warning: output artifacts could not be read: "
            + redact_sensitive_text(result.artifact_error),
            fg=typer.colors.YELLOW,
            err=True,
        )
    failure_detail = ""
    if result.failure_class:
        failure_detail += f", failureClass={result.failure_class}"
    if result.message:
        message = (
            redact_sensitive_text(result.message)
            .replace("\r", " ")
            .replace("\n", " ")
        )
        failure_detail += f", message={message}"
    typer.echo(
        f"container job {result.job_id}: {result.state} "
        f"(exitCode={result.exit_code}{failure_detail}, logsRef={result.logs_ref}, "
        f"artifactsRef={result.artifacts_ref})"
    )
    if result.state != "succeeded" or result.exit_code not in {None, 0}:
        raise typer.Exit(code=1)


@container_app.command(
    "run",
    help=(
        "Run a validated JSON workload spec in the active managed workspace "
        "through MoonMind's durable Docker backend."
    ),
)
def container_run(
    spec: Path = typer.Option(
        ...,
        "--spec",
        exists=True,
        dir_okay=False,
        readable=True,
        help="JSON file containing ContainerJobSpec workload fields.",
    ),
    as_json: bool = typer.Option(
        False, "--json", help="Emit the ContainerJobResult as JSON."
    ),
    request_id: str | None = typer.Option(
        None,
        "--request-id",
        help="Stable caller request id for idempotent retries.",
    ),
) -> None:
    try:
        result = run_container_job(
            load_container_job_spec(spec),
            request_id=request_id,
        )
    except ContainerJobCliError as exc:
        typer.secho(f"Error: {exc}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1) from exc
    if as_json:
        payload = asdict(result)
        for field in ("message", "log_error", "artifact_error"):
            if payload[field]:
                payload[field] = redact_sensitive_text(payload[field])
        payload["log_tail"] = [redact_sensitive_text(line) for line in result.log_tail]
        typer.echo(json.dumps(payload))
        if result.state != "succeeded" or result.exit_code not in {None, 0}:
            raise typer.Exit(code=1)
    else:
        _print_container_job_result(result)


@container_app.command(
    "python-tests",
    help=(
        "Run Python unit tests in the active managed workspace through a durable "
        "container job. The CLI waits a bounded interval "
        "(--timeout-seconds plus a 120s terminal-read grace) for the job to "
        "reach a terminal state instead of waiting indefinitely; a stuck "
        "`moonmind container python-tests` call inside an idle Omnigent host "
        "(MoonLadderStudios/MoonMind#4226) therefore surfaces as a CLI error "
        "rather than a silent hang."
    ),
)
def container_python_tests(
    targets: list[str] | None = typer.Argument(
        None, help="Optional pytest paths or node ids; defaults to tests/unit."
    ),
    timeout_seconds: int = typer.Option(3600, "--timeout-seconds", min=1, max=86400),
) -> None:
    try:
        result = run_python_tests(targets or [], timeout_seconds=timeout_seconds)
    except ContainerJobCliError as exc:
        typer.secho(f"Error: {exc}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1) from exc
    _print_container_job_result(result)


@container_app.command(
    "passive-scan",
    help=(
        "Run the supported passive repository secret-exposure scan "
        "(MoonLadderStudios/MoonMind#3970) in the active managed workspace "
        "through a durable container job, without hand-authoring a JSON "
        "workload. Prints the terminal job state plus the collected logs and "
        "artifacts references for the retained native report and summary."
    ),
)
def container_passive_scan(
    snapshot: str = typer.Option(
        ".",
        "--snapshot",
        help="Workspace-relative snapshot directory to scan; defaults to the mounted workspace root.",
    ),
    report: str = typer.Option(
        "artifacts/passive-scan-report.json",
        "--report",
        help="Workspace-relative path for the retained native JSON report.",
    ),
    summary: str = typer.Option(
        "artifacts/passive-scan-summary.md",
        "--summary",
        help="Workspace-relative path for the retained Markdown summary.",
    ),
    timeout_seconds: int = typer.Option(1800, "--timeout-seconds", min=1, max=86400),
) -> None:
    try:
        result = run_passive_scan_job(
            snapshot_relative_path=snapshot,
            report_relative_path=report,
            summary_relative_path=summary,
            timeout_seconds=timeout_seconds,
        )
    except ContainerJobCliError as exc:
        typer.secho(f"Error: {exc}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1) from exc
    _print_container_job_result(result)


@container_app.command(
    "repository-scan",
    help="Run one admitted repository scanner and retain sanitized evidence.",
)
def container_repository_scan(
    admission: Annotated[
        Path | None,
        typer.Option("--admission", exists=True, dir_okay=False, readable=True),
    ] = None,
    scanner: str | None = typer.Option(
        None,
        "--scanner",
        help="gitleaks, opengrep or trivy; uses image rules and prepared feeds.",
    ),
    snapshot: str | None = typer.Option(None, "--snapshot"),
    output: str | None = typer.Option(None, "--output"),
    database_cache: str | None = typer.Option(None, "--database-cache"),
    database_digest: str | None = typer.Option(None, "--database-digest"),
    image_source: str | None = typer.Option(None, "--image-source"),
    image: str | None = typer.Option(None, "--image"),
) -> None:
    from moonmind.security.repository_scanners import RepositoryScan, build_scanner_job

    try:
        if image is not None and image_source is not None:
            raise ValueError("choose image or image-source")
        if admission is not None:
            if any(
                value is not None
                for value in (
                    scanner,
                    snapshot,
                    output,
                    database_cache,
                    database_digest,
                )
            ):
                raise ValueError("choose admission or scan options")
            if admission.stat().st_size > 32 * 1024:
                raise ValueError("scanner admission exceeds the input bound")
            scan = RepositoryScan.model_validate_json(admission.read_bytes())
        else:
            scan = RepositoryScan(
                scanner=scanner,
                inputPath=snapshot if snapshot is not None else ".",
                outputDirectory=output if output is not None else f"artifacts/{scanner}",
                databaseCacheRef=database_cache,
                databaseDigest=database_digest,
            )
        workload = build_scanner_job(
            scan, image_source_ref=image_source or "repository-security"
        )
        if image is not None:
            workload.pop("imageSourceRef")
            workload["image"] = image
        result = run_container_job(workload)
    except (ContainerJobCliError, ValueError, OSError) as exc:
        # Do not echo supplied admission strings or service diagnostics.
        typer.secho(
            "Scanner unavailable. Choose --scanner gitleaks|opengrep|trivy or --admission, and check Settings Operations for image/feed authority.",
            fg=typer.colors.RED,
            err=True,
        )
        raise typer.Exit(code=1) from exc
    _print_container_job_result(result)


@container_app.command(
    "feed-refresh",
    help="Prepare selected Trivy feeds without repository content or assessment credentials.",
)
def container_feed_refresh(
    admission: Annotated[
        Path | None,
        typer.Option("--admission", exists=True, dir_okay=False, readable=True),
    ] = None,
    image_source: str = typer.Option("repository-security", "--image-source"),
    cache: str | None = typer.Option(None, "--cache"),
    egress_policy: str | None = typer.Option(None, "--egress-policy"),
    archive: str | None = typer.Option(
        None,
        "--archive",
        help="Retained artifact ref for an offline Trivy cache tar archive.",
    ),
    component: list[str] | None = typer.Option(
        None,
        "--component",
        help="Repeat vulnerability, java or checks. Default: vulnerability and checks.",
    ),
    max_age_hours: int | None = typer.Option(None, "--max-age-hours", min=1, max=8760),
) -> None:
    from moonmind.security.scanner_feeds import FeedRefresh, build_feed_refresh_job

    try:
        if admission is not None:
            if any(
                value is not None
                for value in (cache, egress_policy, archive, component, max_age_hours)
            ):
                raise ValueError("choose admission or refresh options")
            if admission.stat().st_size > 32 * 1024:
                raise ValueError("feed refresh admission exceeds the input bound")
            refresh = FeedRefresh.model_validate_json(admission.read_bytes())
        else:
            refresh = FeedRefresh(
                acquisition="import" if archive else "download",
                feedCacheRef=cache if cache is not None else "scanner-feeds",
                egressPolicyRef=egress_policy,
                archiveArtifactRef=archive,
                components=(
                    component if component is not None else ["vulnerability", "checks"]
                ),
                maxAgeHours=max_age_hours if max_age_hours is not None else 72,
            )
        result = run_container_job(
            build_feed_refresh_job(refresh, image_source_ref=image_source)
        )
    except (ContainerJobCliError, ValueError, OSError) as exc:
        typer.secho(
            "Feed preparation unavailable. Choose an authorized --egress-policy for downloads or --archive for offline import (or --admission), and check Settings Operations for cache/image authority.",
            fg=typer.colors.RED,
            err=True,
        )
        raise typer.Exit(code=1) from exc
    _print_container_job_result(result)


@container_app.command(
    "image-assessment",
    help="Assess an image or archive, generate an SBOM, or reassess an SBOM.",
)
def container_image_assessment(
    admission: Annotated[
        Path, typer.Option("--admission", exists=True, dir_okay=False, readable=True)
    ],
    image_source: str = typer.Option("repository-security", "--image-source"),
) -> None:
    from moonmind.schemas.repository_scanner_models import ImageAssessment
    from moonmind.security.image_assessments import build_image_assessment_job

    try:
        if admission.stat().st_size > 32 * 1024:
            raise ValueError("image assessment admission exceeds the input bound")
        assessment = ImageAssessment.model_validate_json(admission.read_bytes())
        result = run_container_job(
            build_image_assessment_job(assessment, image_source_ref=image_source)
        )
    except (ContainerJobCliError, ValueError, OSError) as exc:
        typer.secho(
            "Invalid image assessment admission or container job unavailable.",
            fg=typer.colors.RED,
            err=True,
        )
        raise typer.Exit(code=1) from exc
    _print_container_job_result(result)


@container_app.command(
    "cloud-assessment",
    help="Run a read-only Prowler posture assessment of an admitted account.",
)
def container_cloud_assessment(
    admission: Annotated[
        Path, typer.Option("--admission", exists=True, dir_okay=False, readable=True)
    ],
    image_source: str = typer.Option("cloud-posture", "--image-source"),
) -> None:
    from moonmind.schemas.repository_scanner_models import CloudAssessment
    from moonmind.security.cloud_assessments import build_cloud_assessment_job

    try:
        if admission.stat().st_size > 32 * 1024:
            raise ValueError("cloud assessment admission exceeds the input bound")
        assessment = CloudAssessment.model_validate_json(admission.read_bytes())
        result = run_container_job(
            build_cloud_assessment_job(assessment, image_source_ref=image_source)
        )
    except (ContainerJobCliError, ValueError, OSError) as exc:
        typer.secho(
            f"Invalid cloud assessment admission or container job unavailable: {exc}",
            fg=typer.colors.RED,
            err=True,
        )
        raise typer.Exit(code=1) from exc
    _print_container_job_result(result)


@container_app.command(
    "web-assessment",
    help="Run a bounded ZAP assessment of one admitted web origin.",
)
def container_web_assessment(
    admission: Annotated[
        Path, typer.Option("--admission", exists=True, dir_okay=False, readable=True)
    ],
    image_source: str = typer.Option("web-assessment", "--image-source"),
) -> None:
    from moonmind.schemas.repository_scanner_models import WebAssessment
    from moonmind.security.web_assessments import build_web_assessment_job

    try:
        if admission.stat().st_size > 32 * 1024:
            raise ValueError("web assessment admission exceeds the input bound")
        assessment = WebAssessment.model_validate_json(admission.read_bytes())
        result = run_container_job(
            build_web_assessment_job(assessment, image_source_ref=image_source)
        )
    except (ContainerJobCliError, ValueError, OSError) as exc:
        # Admission can carry identity names and routes; do not echo it.
        typer.secho(
            "Invalid web assessment admission or container job unavailable.",
            fg=typer.colors.RED,
            err=True,
        )
        raise typer.Exit(code=1) from exc
    _print_container_job_result(result)


@container_app.command(
    "tls-assessment", help="Inspect TLS at one admitted HTTPS origin."
)
def container_tls_assessment(
    admission: Annotated[
        Path, typer.Option("--admission", exists=True, dir_okay=False, readable=True)
    ],
    image_source: str = typer.Option("repository-security", "--image-source"),
) -> None:
    from moonmind.security.tls_assessments import (
        TLSAssessment,
        build_tls_assessment_job,
    )

    try:
        if admission.stat().st_size > 32 * 1024:
            raise ValueError("TLS admission exceeds the input bound")
        assessment = TLSAssessment.model_validate_json(admission.read_bytes())
        result = run_container_job(
            build_tls_assessment_job(assessment, image_source_ref=image_source)
        )
    except (ContainerJobCliError, ValueError, OSError) as exc:
        typer.secho(
            "Invalid TLS admission or container job unavailable.",
            fg=typer.colors.RED,
            err=True,
        )
        raise typer.Exit(code=1) from exc
    _print_container_job_result(result)


@container_app.command(
    "service-inventory", help="Observe services through an admitted native TCP profile."
)
def container_service_inventory(
    admission: Annotated[
        Path, typer.Option("--admission", exists=True, dir_okay=False, readable=True)
    ],
    cpu_millis: int = typer.Option(1000, "--cpu-millis", min=1, max=128000),
    memory_mib: int = typer.Option(256, "--memory-mib", min=16, max=1048576),
    timeout_seconds: int | None = typer.Option(
        None, "--timeout-seconds", min=1, max=86400
    ),
) -> None:
    from moonmind.schemas.container_job_models import (
        ContainerJobSpec,
        WorkloadProfileSelection,
    )
    from moonmind.security.service_inventory import build_service_inventory_plan

    try:
        with admission.open("rb") as stream:
            raw = stream.read(96 * 1024 + 1)
        if len(raw) > 96 * 1024:
            raise ValueError("inventory admission exceeds the input bound")
        selected = WorkloadProfileSelection.model_validate_json(raw)
        if (
            selected.native_network is None
            or selected.service_inventory is None
            or selected.arguments
            or selected.configuration_exceptions
            or selected.credentials
        ):
            raise ValueError("inventory requires a typed native profile selection")
        build_service_inventory_plan(
            selected.native_network, selected.service_inventory
        )
        duration = selected.native_network.limits.max_duration_seconds
        timeout = (
            timeout_seconds
            if timeout_seconds is not None
            else min(86400, duration + 15)
        )
        if timeout < duration:
            raise ValueError("job timeout cannot shorten admitted native duration")
        spec = ContainerJobSpec.model_validate(
            {
                "workloadProfile": selected.model_dump(mode="json", by_alias=True),
                "inputAttachments": [],
                "resources": {
                    "cpuMillis": cpu_millis,
                    "memoryMiB": memory_mib,
                    "pids": 64,
                },
                "timeoutSeconds": timeout,
            }
        )
        result = run_container_job(spec.model_dump(mode="json", by_alias=True))
    except (ContainerJobCliError, ValueError, OSError) as exc:
        typer.secho(
            "Invalid service inventory admission or container job unavailable.",
            fg=typer.colors.RED,
            err=True,
        )
        raise typer.Exit(code=1) from exc
    _print_container_job_result(result)


@worker_app.command("doctor", help="Verify worker prerequisites.")
def worker_doctor() -> None:
    # MoonLadderStudios/MoonMind#4192: the native Manifest/RAG ingestion
    # product (including the vector-free manifest pipeline and the RAG
    # retrieval guardrail) is retired. The doctor reports the static
    # retirement state instead of probing a retrieval backend.
    typer.secho(
        "Worker prerequisites satisfied (native Manifest/RAG ingestion retired).",
        fg=typer.colors.GREEN,
    )


@worker_app.command(
    "code-readiness",
    help=(
        "Compare each worker's startup-recorded code identity with the "
        "checkout on disk and report stale_code (MoonLadderStudios/MoonMind#4224). "
        "A host git pull alone is not a deployment: restart stale workers "
        "before new runs are admitted."
    ),
)
def worker_code_readiness(
    url: list[str] | None = typer.Option(
        None,
        "--url",
        help="Worker /readyz endpoint to probe (repeatable). Defaults to "
        "MOONMIND_WORKER_READINESS_URLS / TEMPORAL_WORKFLOW_READINESS_URL.",
    ),
) -> None:
    from moonmind.workflows.temporal.worker_code_identity import (
        collect_worker_code_freshness,
        format_stale_code_message,
        readiness_urls_from_env,
        resolve_checkout_code_identity,
    )

    targets = [(item, item) for item in (url or []) if item.strip()]
    if not targets:
        targets = readiness_urls_from_env()
    checkout = resolve_checkout_code_identity()
    typer.echo(
        f"checkout revision: {checkout.revision or 'unknown'} "
        f"(source={checkout.source})"
    )
    if not targets:
        typer.secho(
            "No worker readiness endpoints configured: set "
            "MOONMIND_WORKER_READINESS_URLS or TEMPORAL_WORKFLOW_READINESS_URL.",
            fg=typer.colors.YELLOW,
        )
        return
    freshness = collect_worker_code_freshness(targets, current=checkout)
    stale = [item for item in freshness if item.status == "stale"]
    for item in freshness:
        color = (
            typer.colors.GREEN
            if item.status == "healthy"
            else typer.colors.RED
            if item.status == "stale"
            else typer.colors.YELLOW
        )
        typer.secho(
            f"{item.name}: {item.status} "
            f"(running={item.startup_revision} checkout={item.current_revision})",
            fg=color,
        )
    if stale:
        typer.secho(format_stale_code_message(stale), fg=typer.colors.RED, err=True)
        raise typer.Exit(code=2)


def _workflow_client(api_base: str | None = None):
    """Build the thin executions client from protected configuration only."""
    from moonmind.workflow_cli import (
        WorkflowApiClient,
        WorkflowCliError,
        require_secure_transport,
        resolve_api_base,
        resolve_bearer_token,
    )

    env = dict(os.environ)
    base = (api_base or "").strip() or resolve_api_base(env)
    try:
        token = resolve_bearer_token(env)
    except WorkflowCliError as exc:
        typer.secho(f"Error: {exc}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1) from exc
    try:
        require_secure_transport(base, has_token=bool(token), env=env)
    except WorkflowCliError as exc:
        typer.secho(f"Error: {exc}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1) from exc
    return WorkflowApiClient(base_url=base, bearer_token=token), base


def _workflow_fail(message: str) -> None:
    typer.secho(f"Error: {message}", fg=typer.colors.RED, err=True)
    raise typer.Exit(code=1)


@workflow_app.command(
    "run",
    help=(
        "Submit one ordinary workflow via POST /api/executions and print the "
        "admitted workflow ID plus dashboard detail URL. Preset expansion, "
        "profile selection, authorization, and publication normalization are "
        "server-owned. Pass either --preset (preset slug) or --skill (Skill "
        "name), never both; identify Agent vs Provider profiles with "
        "--agent-profile / --provider-profile (there is no --profile). "
        "Secrets come from MOONMIND_API_TOKEN(_FILE) only. A lost POST "
        "acknowledgment is reconciled by retrying with the same --request-id; "
        "a new intentional request needs a fresh --request-id. Ctrl-C stops "
        "local waiting only, never the remote workflow. "
        "Exit codes: 0 admitted (or completed with --wait), "
        "1 submission/auth/transport/usage error, 2 remote work failed/canceled, "
        "3 still running after --wait timeout."
    ),
)
def workflow_run(
    instructions: str | None = typer.Option(
        None, "--instructions", help="Task instructions/goal for the workflow."
    ),
    preset: str | None = typer.Option(
        None, "--preset", help="Preset slug (distinct from a Skill name)."
    ),
    skill: str | None = typer.Option(
        None, "--skill", help="Skill name (distinct from a preset slug)."
    ),
    title: str | None = typer.Option(None, "--title", help="Workflow title."),
    repository: str | None = typer.Option(
        None,
        "--repository",
        help="Backend-admitted source (owner/repo or https URL). Local paths are rejected.",
    ),
    agent_profile: str | None = typer.Option(
        None,
        "--agent-profile",
        help=(
            "Agent Profile selector (not a provider profile). Pair it with "
            "--provider-profile, the account the Agent Profile runs on."
        ),
    ),
    provider_profile: str | None = typer.Option(
        None, "--provider-profile", help="Provider Profile selector (not an agent profile)."
    ),
    publish_mode: str | None = typer.Option(
        None, "--publish-mode", help="Publication intent: auto, none, branch, or pr."
    ),
    param: list[str] | None = typer.Option(
        None, "--param", help="Extra task input as key=value (repeatable)."
    ),
    request_id: str | None = typer.Option(
        None,
        "--request-id",
        help="Stable idempotency key; reuse after a lost acknowledgment, renew for a new intent.",
    ),
    api_base: str | None = typer.Option(
        None, "--api-base", help="API base URL (default MOONMIND_API_BASE/MOONMIND_URL or local)."
    ),
    as_json: bool = typer.Option(False, "--json", help="Emit machine-readable JSON on stdout."),
    wait: bool = typer.Option(False, "--wait", help="Wait (bounded) for terminal status."),
    timeout_seconds: float = typer.Option(
        300.0, "--timeout-seconds", min=1.0, max=86400.0, help="Bounded wait budget."
    ),
) -> None:
    from moonmind.workflow_cli import (
        EXIT_STILL_RUNNING,
        WorkflowCliError,
        build_execution_payload,
        detail_url,
        exit_code_for_status,
        format_execution_json,
        new_request_id,
        parse_extra_params,
        sanitize_terminal_text,
        summarize_execution,
        wait_for_terminal,
    )

    try:
        extras = parse_extra_params(param)
        effective_request_id = (request_id or "").strip() or new_request_id()
        payload = build_execution_payload(
            instructions=instructions,
            preset=preset,
            skill=skill,
            title=title,
            repository=repository,
            agent_profile=agent_profile,
            provider_profile=provider_profile,
            publish_mode=publish_mode,
            extra_params=extras,
            idempotency_key=effective_request_id,
        )
    except WorkflowCliError as exc:
        _workflow_fail(str(exc))
        return
    client, base = _workflow_client(api_base)
    try:
        try:
            admitted = client.submit_execution(payload)
        except WorkflowCliError as exc:
            typer.secho(
                f"Error: {exc} (requestId={effective_request_id}; "
                "retry with the same --request-id to reconcile.)",
                fg=typer.colors.RED,
                err=True,
            )
            raise typer.Exit(code=1) from exc
        summary = summarize_execution(admitted)
        url = detail_url(base, summary.workflow_id)
        if as_json:
            typer.echo(
                format_execution_json({**admitted, "detailUrl": url, "requestId": effective_request_id})
            )
        else:
            typer.echo(f"workflow {summary.workflow_id}: {summary.status} (admitted)")
            typer.echo(f"details: {url}")
            typer.echo(f"requestId: {effective_request_id}")
            if summary.title:
                typer.echo(f"title: {sanitize_terminal_text(summary.title, max_chars=500)}")
        if not wait:
            return
        try:
            observed = wait_for_terminal(
                client, summary.workflow_id, timeout_seconds=timeout_seconds
            )
        except WorkflowCliError as exc:
            typer.secho(f"Error: {exc}", fg=typer.colors.RED, err=True)
            raise typer.Exit(code=1) from exc
        except KeyboardInterrupt as exc:
            typer.secho(
                "Stopped following; the remote workflow continues (cancellation "
                "requires an explicit authorized action).",
                fg=typer.colors.YELLOW,
                err=True,
            )
            raise typer.Exit(code=EXIT_STILL_RUNNING) from exc
        final = summarize_execution(observed)
        timed_out = final.status not in {"completed", "failed", "canceled"}
        if as_json:
            typer.echo(format_execution_json({**observed, "detailUrl": url}))
        elif timed_out:
            typer.secho(
                f"workflow {final.workflow_id}: still {final.status} after bounded wait; "
                "remote work continues.",
                fg=typer.colors.YELLOW,
            )
        else:
            typer.echo(f"workflow {final.workflow_id}: {final.status}")
        raise typer.Exit(code=exit_code_for_status(final.status, timed_out=timed_out))
    except KeyboardInterrupt as exc:
        typer.secho(
            "Stopped following; the remote workflow continues (cancellation "
            "requires an explicit authorized action).",
            fg=typer.colors.YELLOW,
            err=True,
        )
        raise typer.Exit(code=EXIT_STILL_RUNNING) from exc
    finally:
        client.close()


@workflow_app.command(
    "status",
    help=(
        "Read one workflow via GET /api/executions/{workflowId}. Optional "
        "bounded --wait polls the authorized read contract until terminal or "
        "timeout. Stream loss is a read retry, never workflow failure. "
        "Ctrl-C stops local following only. "
        "Exit codes: 0 completed, 1 read/auth/transport error, "
        "2 failed/canceled, 3 still running."
    ),
)
def workflow_status(
    workflow_id: str = typer.Argument(..., help="Workflow ID to describe."),
    api_base: str | None = typer.Option(None, "--api-base", help="API base URL."),
    as_json: bool = typer.Option(False, "--json", help="Emit machine-readable JSON."),
    wait: bool = typer.Option(False, "--wait", help="Wait (bounded) for terminal status."),
    timeout_seconds: float = typer.Option(300.0, "--timeout-seconds", min=1.0, max=86400.0),
) -> None:
    from moonmind.workflow_cli import (
        EXIT_STILL_RUNNING,
        WorkflowCliError,
        exit_code_for_status,
        format_execution_json,
        sanitize_terminal_text,
        summarize_execution,
        wait_for_terminal,
    )

    client, base = _workflow_client(api_base)
    try:
        try:
            if wait:
                try:
                    body = wait_for_terminal(
                        client, workflow_id, timeout_seconds=timeout_seconds
                    )
                except KeyboardInterrupt as exc:
                    typer.secho(
                        "Stopped following; the remote workflow continues.",
                        fg=typer.colors.YELLOW,
                        err=True,
                    )
                    raise typer.Exit(code=EXIT_STILL_RUNNING) from exc
            else:
                body = client.describe_execution(workflow_id)
        except WorkflowCliError as exc:
            typer.secho(f"Error: {exc}", fg=typer.colors.RED, err=True)
            raise typer.Exit(code=1) from exc
        summary = summarize_execution(body)
        timed_out = wait and summary.status not in {"completed", "failed", "canceled"}
        if as_json:
            from moonmind.workflow_cli import detail_url as _detail_url

            typer.echo(
                format_execution_json(
                    {**body, "detailUrl": _detail_url(base, summary.workflow_id)}
                )
            )
        else:
            typer.echo(f"workflow {summary.workflow_id}: {summary.status} (state={summary.state})")
            if summary.title:
                typer.echo(f"title: {sanitize_terminal_text(summary.title, max_chars=500)}")
            if timed_out:
                typer.secho(
                    "still running after bounded wait; remote work continues.",
                    fg=typer.colors.YELLOW,
                )
        raise typer.Exit(code=exit_code_for_status(summary.status, timed_out=timed_out))
    except KeyboardInterrupt as exc:
        typer.secho(
            "Stopped following; the remote workflow continues.",
            fg=typer.colors.YELLOW,
            err=True,
        )
        raise typer.Exit(code=EXIT_STILL_RUNNING) from exc
    finally:
        client.close()


@workflow_app.command(
    "logs",
    help=(
        "Read available logs/evidence via GET /api/executions/{workflowId}/"
        "captured-evidence and .../steps. Terminal evidence stays readable; "
        "missing auxiliary logs are reported honestly and never become "
        "workflow failure. Ctrl-C stops local following only."
    ),
)
def workflow_logs(
    workflow_id: str = typer.Argument(..., help="Workflow ID to read."),
    api_base: str | None = typer.Option(None, "--api-base", help="API base URL."),
    as_json: bool = typer.Option(False, "--json", help="Emit machine-readable JSON."),
    follow: bool = typer.Option(False, "--follow", help="Follow until terminal or timeout."),
    timeout_seconds: float = typer.Option(300.0, "--timeout-seconds", min=1.0, max=86400.0),
    max_lines: int = typer.Option(200, "--max-lines", min=1, max=1000),
) -> None:
    import time as _time

    from moonmind.workflow_cli import (
        WorkflowCliError,
        format_execution_json,
        render_log_lines,
        sanitize_terminal_text,
        summarize_execution,
    )

    client, _base = _workflow_client(api_base)
    try:
        deadline = _time.monotonic() + max(1.0, timeout_seconds)
        while True:
            try:
                evidence = client.captured_evidence(workflow_id)
                ledger = client.step_ledger(workflow_id)
                current: dict = {}
                try:
                    current = client.describe_execution(workflow_id)
                    summary = summarize_execution(current)
                    terminal = summary.status in {"completed", "failed", "canceled"}
                except WorkflowCliError:
                    summary = None
                    terminal = True
            except WorkflowCliError as exc:
                typer.secho(f"Error: {exc}", fg=typer.colors.RED, err=True)
                raise typer.Exit(code=1) from exc
            lines, gap = render_log_lines(evidence, ledger, max_lines=max_lines)
            if as_json:
                typer.echo(
                    format_execution_json(
                        {
                            "workflowId": workflow_id.strip(),
                            "lines": lines,
                            "gap": gap,
                            "status": summary.status if summary else "unknown",
                        }
                    )
                )
            else:
                for line in lines:
                    typer.echo(line)
                if gap:
                    typer.secho(
                        f"Note: {sanitize_terminal_text(gap, max_chars=500)}",
                        fg=typer.colors.YELLOW,
                        err=True,
                    )
            if not follow or terminal or _time.monotonic() >= deadline:
                if follow and not terminal:
                    typer.secho(
                        "Follow timeout reached; remote work continues.",
                        fg=typer.colors.YELLOW,
                        err=True,
                    )
                return
            _time.sleep(2.0)
    except KeyboardInterrupt as exc:
        typer.secho(
            "Stopped following; the remote workflow continues.",
            fg=typer.colors.YELLOW,
            err=True,
        )
        raise typer.Exit(code=3) from exc
    finally:
        client.close()


@workflow_app.command(
    "download",
    help=(
        "Download one saved captured-evidence artifact via "
        "GET /api/executions/{workflowId}/captured-evidence/download?ref=... — "
        "the same authorized evidence refs the Workflow Detail page links "
        "(MoonLadderStudios/MoonMind#3926). Requires --ref (an authorized "
        "artifact ref; list them with `moonmind workflow logs --json`) and "
        "--out (destination file). An existing file is never silently "
        "replaced (pass --overwrite). Ctrl-C stops the local download only, "
        "never the remote workflow."
    ),
)
def workflow_download(
    workflow_id: str = typer.Argument(..., help="Workflow ID that owns the evidence."),
    ref: str | None = typer.Option(
        None, "--ref", help="Authorized artifact ref to download."
    ),
    out: Path | None = typer.Option(
        None, "--out", help="Destination file path for the saved bytes."
    ),
    overwrite: bool = typer.Option(
        False, "--overwrite", help="Replace the --out file when it already exists."
    ),
    api_base: str | None = typer.Option(None, "--api-base", help="API base URL."),
) -> None:
    from moonmind.workflow_cli import WorkflowCliError
    from moonmind.workflow_cli import detail_url as _detail_url
    from moonmind.workflow_cli import sanitize_terminal_text, save_evidence_download

    clean_ref = (ref or "").strip()
    if not clean_ref:
        _workflow_fail(
            "an artifact --ref is required; pick one from "
            "`moonmind workflow logs --json <workflow-id>`."
        )
        return
    if out is None or not str(out).strip():
        _workflow_fail(
            "an --out file path is required so saved bytes land explicitly."
        )
        return
    clean_workflow_id = workflow_id.strip()
    if not clean_workflow_id:
        _workflow_fail("a workflow ID is required.")
        return
    client, base = _workflow_client(api_base)
    try:
        try:
            downloaded = client.download_captured_evidence(clean_workflow_id, clean_ref)
        except WorkflowCliError as exc:
            typer.secho(f"Error: {exc}", fg=typer.colors.RED, err=True)
            raise typer.Exit(code=1) from exc
        try:
            saved = save_evidence_download(out, downloaded.content, overwrite=overwrite)
        except WorkflowCliError as exc:
            typer.secho(f"Error: {exc}", fg=typer.colors.RED, err=True)
            raise typer.Exit(code=1) from exc
        url = _detail_url(base, clean_workflow_id)
        typer.echo(
            f"saved {len(downloaded.content)} bytes to {saved} "
            f"(ref {sanitize_terminal_text(clean_ref, max_chars=500)})"
        )
        typer.echo(f"details: {url}")
    except KeyboardInterrupt as exc:
        typer.secho(
            "Stopped download; the remote workflow continues.",
            fg=typer.colors.YELLOW,
            err=True,
        )
        raise typer.Exit(code=1) from exc
    finally:
        client.close()


def main() -> None:
    app()


if __name__ == "__main__":  # pragma: no cover
    main()
