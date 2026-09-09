"""Top-level MoonMind CLI exposing worker utilities."""

from __future__ import annotations

from pathlib import Path
from typing import Optional

import typer

from moonmind.container_job_cli import (
    ContainerJobCliError,
    ContainerJobResult,
    load_container_job_spec,
    run_container_job,
    run_python_tests,
)
from moonmind.manifest import manifest_cli
from moonmind.rag.guardrails import GuardrailError, ensure_rag_ready
from moonmind.rag.settings import RagRuntimeSettings
from moonmind.utils.logging import redact_sensitive_text

app = typer.Typer(help="MoonMind developer utilities.")
worker_app = typer.Typer(help="Worker runtime diagnostics.")
manifest_app = typer.Typer(help="Manifest schema validation and pipeline commands.")
container_app = typer.Typer(help="Run work through MoonMind's Docker backend.")
app.add_typer(worker_app, name="worker")
app.add_typer(manifest_app, name="manifest")
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


@worker_app.command("doctor", help="Verify worker prerequisites (vector-free).")
def worker_doctor() -> None:
    # MoonLadderStudios/MoonMind#4112: native vector backend retired. The
    # doctor delegates to the vector-free guardrail (optional RetrievalGateway
    # health probe only; never probes Qdrant) so gateway failures surface as
    # exit-code failures instead of a false healthy result.
    settings = RagRuntimeSettings.from_env()
    try:
        ensure_rag_ready(settings)
    except GuardrailError as exc:
        typer.secho(f"Worker prerequisite check failed: {exc}", fg=typer.colors.RED)
        raise typer.Exit(code=1) from exc
    typer.secho("Worker prerequisites satisfied.", fg=typer.colors.GREEN)

# ----- manifest commands -----

@manifest_app.command("validate", help="Validate a manifest YAML against the v0 schema.")
def manifest_validate(
    file: Path = typer.Option(..., "-f", "--file", help="Path to manifest YAML."),
) -> None:
    result = manifest_cli.run_validate(manifest_path=str(file))
    for issue in result.issues:
        color = typer.colors.RED if issue.severity == "ERROR" else typer.colors.YELLOW
        typer.secho(f"[{issue.severity}] {issue.field}: {issue.message}", fg=color)
    typer.secho(result.summary(), fg=typer.colors.GREEN if result.valid else typer.colors.RED)
    if not result.valid:
        raise typer.Exit(code=1)

@manifest_app.command("plan", help="Dry-run: estimate scope without side effects.")
def manifest_plan(
    file: Path = typer.Option(..., "-f", "--file", help="Path to manifest YAML."),
) -> None:
    import json as _json

    try:
        summary = manifest_cli.run_plan(manifest_path=str(file))
    except manifest_cli.ManifestCliError as exc:
        typer.secho(f"Error: {exc}", fg=typer.colors.RED)
        raise typer.Exit(code=1) from exc
    typer.echo(_json.dumps(summary, indent=2))

@manifest_app.command("run", help="Execute full manifest pipeline: fetch → chunk → embed → upsert.")
def manifest_run(
    file: Path = typer.Option(..., "-f", "--file", help="Path to manifest YAML."),
) -> None:
    import json as _json

    try:
        result = manifest_cli.run_manifest(manifest_path=str(file))
    except manifest_cli.ManifestCliError as exc:
        typer.secho(f"Error: {exc}", fg=typer.colors.RED)
        raise typer.Exit(code=1) from exc
    typer.echo(_json.dumps(result, indent=2))

@manifest_app.command("evaluate", help="Evaluate retrieval quality against golden datasets.")
def manifest_evaluate(
    file: Path = typer.Option(..., "-f", "--file", help="Path to manifest YAML."),
    dataset: Optional[str] = typer.Option(None, "--dataset", help="Filter to specific dataset name."),
) -> None:
    import json as _json

    try:
        result = manifest_cli.run_evaluate(manifest_path=str(file), dataset=dataset)
    except manifest_cli.ManifestCliError as exc:
        typer.secho(f"Error: {exc}", fg=typer.colors.RED)
        raise typer.Exit(code=1) from exc
    passed = result.get("passed", False)
    typer.echo(_json.dumps(result, indent=2))
    if not passed:
        raise typer.Exit(code=1)

def main() -> None:
    app()

if __name__ == "__main__":  # pragma: no cover
    main()
