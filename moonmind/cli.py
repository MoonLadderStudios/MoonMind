"""Top-level MoonMind CLI exposing worker utilities."""

from __future__ import annotations

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
        "MoonMind developer utilities (worker, container). "
        "Application authentication is selected by AUTH_PROVIDER "
        "(accounts, oidc, header, disabled; retired keycloak/default/google/local "
        "selectors are rejected) — see "
        "docs/Security/AuthenticationContracts.md. Container commands run as "
        "machine callers with the MOONMIND_CONTAINER_JOBS_BEARER_TOKEN "
        "family, never as browser login; the removed legacy worker-token "
        "path is rejected (410 worker_token_deprecated). The native "
        "Manifest/RAG ingestion product was retired "
        "(MoonLadderStudios/MoonMind#4192): there is no `manifest` command "
        "group and no retrieval/embedding inspection command."
    )
)
worker_app = typer.Typer(help="Worker runtime diagnostics.")
container_app = typer.Typer(help="Run work through MoonMind's Docker backend.")
app.add_typer(worker_app, name="worker")
app.add_typer(container_app, name="container")


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
        "container job."
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


def main() -> None:
    app()


if __name__ == "__main__":  # pragma: no cover
    main()
