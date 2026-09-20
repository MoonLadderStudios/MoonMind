"""Top-level MoonMind CLI exposing worker utilities."""

from __future__ import annotations

import os
from pathlib import Path

import typer

from moonmind.container_job_cli import (
    ContainerJobCliError,
    ContainerJobResult,
    load_container_job_spec,
    run_container_job,
    run_python_tests,
)
from moonmind.utils.logging import redact_sensitive_text

app = typer.Typer(
    help=(
        "MoonMind developer utilities (worker, container, workflow). "
        "Application authentication is selected by AUTH_PROVIDER "
        "(accounts, oidc, header, disabled; retired keycloak/default/google/local "
        "selectors are rejected) — see "
        "docs/Security/AuthenticationContracts.md. Container commands run as "
        "machine callers with the MOONMIND_CONTAINER_JOBS_BEARER_TOKEN "
        "family, never as browser login; the removed legacy worker-token "
        "path is rejected (410 worker_token_deprecated). The native "
        "Manifest/RAG ingestion product was retired "
        "(MoonLadderStudios/MoonMind#4192): there is no `manifest` command "
        "group and no retrieval/embedding inspection command. "
        "`workflow run/status/logs` is the thin authenticated client for "
        "ordinary workflows (MoonLadderStudios/MoonMind#3939)."
    )
)
worker_app = typer.Typer(help="Worker runtime diagnostics.")
container_app = typer.Typer(help="Run work through MoonMind's Docker backend.")
workflow_app = typer.Typer(
    help=(
        "Submit and observe ordinary workflows through the public execution API. "
        "Presets, defaults, model/profile selection, and publication "
        "normalization remain server-owned. Only run/status/logs exist here."
    )
)
app.add_typer(worker_app, name="worker")
app.add_typer(container_app, name="container")
app.add_typer(workflow_app, name="workflow")

accounts_app = typer.Typer(
    help=(
        "Built-in accounts lifecycle operations (MoonLadderStudios/MoonMind#4122). "
        "Operator-held, expiring, one-use bootstrap/recovery capabilities for "
        "protected first-owner setup and local administrator recovery. "
        "Capability tokens are printed once to this operator terminal and "
        "must be delivered out of band; they are never logged. The existing "
        "User UUID and audit trail are preserved."
    )
)
app.add_typer(accounts_app, name="accounts")


def _print_container_job_result(result: ContainerJobResult) -> None:
    for line in result.log_tail:
        typer.echo(line)
    if result.log_error:
        typer.secho(
            f"Warning: terminal logs could not be read: {result.log_error}",
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
        WorkflowCliError,
        WorkflowApiClient,
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
        None, "--agent-profile", help="Agent Profile selector (not a provider profile)."
    ),
    provider_profile: str | None = typer.Option(
        None, "--provider-profile", help="Provider Profile selector (not an agent profile)."
    ),
    publish_mode: str | None = typer.Option(
        None, "--publish-mode", help="Publication intent: auto, none, branch, or pr."
    ),
    param: list[str] | None = typer.Option(
        None, "--param", help="Extra task field as key=value (repeatable)."
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
                f"Error: {exc} (requestId={payload.get('idempotencyKey', effective_request_id)}; "
                "retry with the same --request-id to reconcile.)",
                fg=typer.colors.RED,
                err=True,
            )
            raise typer.Exit(code=1) from exc
        summary = summarize_execution(admitted)
        url = detail_url(base, summary.workflow_id)
        if as_json:
            typer.echo(
                format_execution_json({**admitted, "detailUrl": url, "requestId": payload["idempotencyKey"]})
            )
        else:
            typer.echo(f"workflow {summary.workflow_id}: {summary.status} (admitted)")
            typer.echo(f"details: {url}")
            typer.echo(f"requestId: {payload['idempotencyKey']}")
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


def _accounts_lifecycle_key() -> bytes:
    """Resolve the operator-held lifecycle HMAC key for capability minting.

    Prefers the explicit ``MOONMIND_ACCOUNTS_KEY`` (at least 32 bytes);
    otherwise derives a domain-separated key from the durable session
    secret, mirroring the API boundary in
    ``api_service/api/routers/accounts_4122.py``.
    """
    from moonmind.security.account_lifecycle_4122 import MIN_KEY_BYTES

    explicit = (os.environ.get("MOONMIND_ACCOUNTS_KEY") or "").strip()
    if explicit:
        raw = explicit.encode("utf-8")
        if len(raw) < MIN_KEY_BYTES:
            typer.secho("Error: MOONMIND_ACCOUNTS_KEY must be at least 32 bytes.", fg=typer.colors.RED, err=True)
            raise typer.Exit(code=1)
        return raw
    try:
        from moonmind.security.auth_modes_4120 import (
            default_session_key_path,
            resolve_session_secret,
        )

        secret = resolve_session_secret(
            # Mirror the API boundary (build_moonmind_control_plane_config):
            # the supported JWT_SECRET legacy alias resolves when
            # MOONMIND_SESSION_SECRET is absent so CLI-minted capabilities
            # redeem with the same lifecycle key the API verifies with.
            explicit_secret=(os.environ.get("MOONMIND_SESSION_SECRET") or "").strip()
            or (os.environ.get("JWT_SECRET") or "").strip()
            or None,
            key_path=default_session_key_path(),
            allow_generate=False,
            for_remote_production=False,
        )
    except Exception as exc:
        typer.secho(f"Error: no lifecycle key material available: {exc}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1) from exc
    from moonmind.security.account_lifecycle_4122 import derive_lifecycle_key

    return derive_lifecycle_key(bytes(secret))


@accounts_app.command(
    "mint-bootstrap",
    help="Mint an operator-held first-owner bootstrap capability for LOGIN.",
)
def accounts_mint_bootstrap(
    login: str = typer.Argument(..., help="Login name the capability is bound to."),
    ttl_seconds: int = typer.Option(1800, "--ttl-seconds", min=60, max=86400),
) -> None:
    """Print a single-use bootstrap capability (operator delivery only)."""
    from moonmind.security.account_lifecycle_4122 import mint_bootstrap_capability

    clean = (login or "").strip()
    if not clean:
        typer.secho("Error: login is required.", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1)
    try:
        token = mint_bootstrap_capability(clean, key=_accounts_lifecycle_key(), ttl_seconds=ttl_seconds)
    except Exception as exc:
        typer.secho(f"Error: cannot mint bootstrap capability: {exc}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1) from exc
    typer.echo(token)


@accounts_app.command(
    "mint-recovery",
    help="Mint an operator-held recovery capability for LOGIN.",
)
def accounts_mint_recovery(
    login: str = typer.Argument(..., help="Login name the capability is bound to."),
    ttl_seconds: int = typer.Option(900, "--ttl-seconds", min=60, max=86400),
) -> None:
    """Print a single-use recovery capability (operator delivery only)."""
    from moonmind.security.account_lifecycle_4122 import mint_recovery_capability

    clean = (login or "").strip()
    if not clean:
        typer.secho("Error: login is required.", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1)
    try:
        token = mint_recovery_capability(clean, key=_accounts_lifecycle_key(), ttl_seconds=ttl_seconds)
    except Exception as exc:
        typer.secho(f"Error: cannot mint recovery capability: {exc}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1) from exc
    typer.echo(token)


@accounts_app.command(
    "restore-access",
    help="Local operator recovery: reactivate and promote LOGIN (preserves UUID).",
)
def accounts_restore_access(
    login: str = typer.Argument(..., help="Existing login to restore administrator access for."),
    recovery_token: str | None = typer.Option(
        None,
        "--recovery-token",
        help="Operator-held recovery capability. Prefer the hidden prompt or "
        "MOONMIND_RECOVERY_TOKEN env / --recovery-token-file; argv exposes "
        "secrets via process listings and shell history.",
    ),
    new_password: str | None = typer.Option(
        None,
        "--new-password",
        help="Optional replacement password (min 12 chars); otherwise credentials "
        "are retained. Prefer the hidden prompt or MOONMIND_NEW_PASSWORD env / "
        "--new-password-file; argv exposes secrets via process listings.",
    ),
    recovery_token_file: str | None = typer.Option(
        None,
        "--recovery-token-file",
        help="Read the recovery capability from a protected file descriptor "
        "instead of argv (e.g., --recovery-token-file <(printf '%s' \"$TOKEN\")).",
    ),
    new_password_file: str | None = typer.Option(
        None,
        "--new-password-file",
        help="Read the replacement password from a protected file instead of argv.",
    ),
) -> None:
    """Redeem a recovery capability locally and restore administrator access.

    The tested recovery path the last-admin refusal points at: consumes
    the one-use nonce and, in the same transaction, reactivates and
    promotes the login so a stranded deployment regains an
    administrator. The existing ``User`` row (UUID, ownership records)
    is preserved; only a redacted audit event is recorded and only the
    outcome (never the capability) is printed.
    """
    import asyncio as _asyncio

    clean = (login or "").strip()
    if not clean:
        typer.secho("Error: login is required.", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1)

    def _read_secret_file(path: str | None, *, what: str) -> str:
        if not path:
            return ""
        try:
            return Path(path).read_text(encoding="utf-8").strip()
        except OSError as exc:
            typer.secho(f"Error: cannot read {what} file: {exc}", fg=typer.colors.RED, err=True)
            raise typer.Exit(code=1) from exc

    # Prefer protected descriptors over argv: file > env > argv, then a
    # hidden prompt for the required capability so routine operator use
    # never places recovery material in process listings or shell history.
    resolved_token = (
        _read_secret_file(recovery_token_file, what="recovery capability")
        or (os.environ.get("MOONMIND_RECOVERY_TOKEN") or "").strip()
        or (recovery_token or "").strip()
    )
    if not resolved_token:
        try:
            import getpass as _getpass

            resolved_token = _getpass.getpass("Recovery capability: ").strip()
        except (EOFError, KeyboardInterrupt) as exc:
            typer.secho("Error: recovery capability is required.", fg=typer.colors.RED, err=True)
            raise typer.Exit(code=1) from exc
    if not resolved_token:
        typer.secho("Error: recovery capability is required.", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1)
    resolved_password = (
        _read_secret_file(new_password_file, what="replacement password")
        or (os.environ.get("MOONMIND_NEW_PASSWORD") or "")
        or (new_password or "")
    )
    # Do not echo the secret back into argv-derived state.
    if resolved_password and not (12 <= len(resolved_password) <= 256):
        typer.secho("Error: replacement password must be 12-256 chars.", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1)

    async def _run() -> str:
        from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
        from sqlalchemy.orm import sessionmaker

        from api_service.db.base import DATABASE_URL
        from api_service.services.account_lifecycle_store_4122 import (
            redeem_recovery_and_restore_access,
        )

        engine = create_async_engine(DATABASE_URL, future=True)
        maker = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        try:
            async with maker() as db_session:
                hashed: str | None = None
                if resolved_password:
                    from moonmind.security.omnigent_auth_qualification import (
                        qualify_password_hash,
                    )

                    hashed = await _asyncio.to_thread(qualify_password_hash, resolved_password)
                user = await redeem_recovery_and_restore_access(
                    db_session,
                    token=resolved_token,
                    key=_accounts_lifecycle_key(),
                    login=clean,
                    hashed_password=hashed,
                )
                return str(user.id)
        finally:
            await engine.dispose()

    try:
        user_id = _asyncio.run(_run())
    except Exception as exc:
        typer.secho(
            f"Error: recovery failed ({getattr(exc, 'code', type(exc).__name__)}).",
            fg=typer.colors.RED,
            err=True,
        )
        raise typer.Exit(code=1) from exc
    from moonmind.security.account_lifecycle_4122 import redacted_lifecycle_event

    redacted_lifecycle_event("recovery", action="restore_access", login=clean)
    typer.echo(f"restored administrator access for {clean} (user_id={user_id})")


def main() -> None:
    app()


if __name__ == "__main__":  # pragma: no cover
    main()
