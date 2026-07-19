"""Top-level MoonMind CLI exposing worker utilities."""

from __future__ import annotations

from pathlib import Path
from typing import List, Optional

import typer

from moonmind.container_job_cli import ContainerJobCliError, run_python_tests
from moonmind.manifest import manifest_cli
from moonmind.rag import cli as rag_cli
from moonmind.rag.guardrails import GuardrailError, ensure_rag_ready
from moonmind.rag.settings import RagRuntimeSettings

app = typer.Typer(help="MoonMind developer utilities.")
rag_app = typer.Typer(help="Retrieval helpers for Codex workers.")
worker_app = typer.Typer(help="Worker runtime diagnostics.")
manifest_app = typer.Typer(help="Manifest schema validation and pipeline commands.")
container_app = typer.Typer(help="Run work through MoonMind's Docker backend.")
app.add_typer(rag_app, name="rag")
app.add_typer(worker_app, name="worker")
app.add_typer(manifest_app, name="manifest")
app.add_typer(container_app, name="container")


@container_app.command(
    "python-tests",
    help=(
        "Run Python unit tests in the active managed workspace through a durable "
        "container job."
    ),
)
def container_python_tests(
    targets: Optional[List[str]] = typer.Argument(
        None, help="Optional pytest paths or node ids; defaults to tests/unit."
    ),
    timeout_seconds: int = typer.Option(3600, "--timeout-seconds", min=1, max=86400),
) -> None:
    try:
        result = run_python_tests(targets or [], timeout_seconds=timeout_seconds)
    except ContainerJobCliError as exc:
        typer.secho(f"Error: {exc}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1) from exc
    for line in result.log_tail:
        typer.echo(line)
    if result.log_error:
        typer.secho(
            f"Warning: terminal logs could not be read: {result.log_error}",
            fg=typer.colors.YELLOW,
            err=True,
        )
    typer.echo(
        f"container job {result.job_id}: {result.state} "
        f"(exitCode={result.exit_code}, logsRef={result.logs_ref}, "
        f"artifactsRef={result.artifacts_ref})"
    )
    if result.state != "succeeded" or result.exit_code not in {None, 0}:
        raise typer.Exit(code=1)


@rag_app.command(
    "search", help="Embed a query, query Qdrant, and print a context block."
)
def rag_search(
    query: str = typer.Option(..., "--query", help="Query text to embed and search."),
    filter_args: List[str] = typer.Option(
        [],
        "--filter",
        help="Additional payload filters in key=value form (repeatable).",
    ),
    budget_args: List[str] = typer.Option(
        [],
        "--budget",
        help="Budget ceilings in key=value form (repeatable).",
    ),
    collection_args: List[str] = typer.Option(
        [],
        "--collection",
        help="Qdrant collection to include in federated retrieval (repeatable).",
    ),
    top_k: Optional[int] = typer.Option(
        None, "--top-k", help="Override similarity top-k."
    ),
    overlay: str = typer.Option(
        "include",
        "--overlay",
        case_sensitive=False,
        help="Overlay policy: include or skip run-scoped overlays.",
    ),
    transport: Optional[str] = typer.Option(
        None,
        "--transport",
        case_sensitive=False,
        help="Force direct or gateway transport (default auto).",
    ),
    output_file: Optional[Path] = typer.Option(
        None,
        "--output-file",
        help="Optional path to write structured context pack JSON.",
    ),
    json_output: bool = typer.Option(
        False,
        "--json",
        help="Print full ContextPack JSON to stdout instead of markdown context text.",
    ),
    planning_ref: Optional[str] = typer.Option(
        None,
        "--planning-ref",
        help="Optional Beads work-item id for Planning Memory prefetch.",
    ),
) -> None:
    try:
        pack = rag_cli.run_search(
            query=query,
            filter_args=filter_args,
            budget_args=budget_args,
            collection_args=collection_args,
            top_k=top_k,
            overlay_policy=overlay.lower(),
            transport=transport.lower() if transport else None,
            output_file=output_file,
            planning_ref=planning_ref,
        )
    except rag_cli.CliError as exc:
        typer.secho(f"Error: {exc}", fg=typer.colors.RED)
        raise typer.Exit(code=1) from exc
    typer.echo(pack.to_json() if json_output else pack.context_text)

@rag_app.command(
    "overlay-upsert", help="Embed local files into a run-scoped overlay collection."
)
def overlay_upsert(
    paths: List[Path] = typer.Argument(..., exists=True, help="Files to embed."),
    run_id: Optional[str] = typer.Option(None, "--run-id", help="Override run ID."),
) -> None:
    try:
        count = rag_cli.run_overlay_upsert(
            paths=[str(path) for path in paths],
            run_id=run_id,
        )
    except rag_cli.CliError as exc:
        typer.secho(f"Error: {exc}", fg=typer.colors.RED)
        raise typer.Exit(code=1) from exc
    typer.secho(f"Overlay upserted chunks: {count}", fg=typer.colors.GREEN)

@rag_app.command("overlay-clean", help="Delete run-scoped overlay vectors.")
def overlay_clean(
    run_id: Optional[str] = typer.Option(None, "--run-id", help="Run ID to clean."),
) -> None:
    try:
        rag_cli.run_overlay_clean(run_id=run_id)
    except rag_cli.CliError as exc:
        typer.secho(f"Error: {exc}", fg=typer.colors.RED)
        raise typer.Exit(code=1) from exc
    typer.secho("Overlay collection removed.", fg=typer.colors.GREEN)

@worker_app.command("doctor", help="Verify worker prerequisites for RAG.")
def worker_doctor() -> None:
    settings = RagRuntimeSettings.from_env()
    try:
        ensure_rag_ready(settings)
    except GuardrailError as exc:
        typer.secho(f"RAG guardrail failed: {exc}", fg=typer.colors.RED)
        raise typer.Exit(code=1) from exc
    typer.secho("Worker retrieval prerequisites satisfied.", fg=typer.colors.GREEN)

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

@manifest_app.command("plan", help="Dry-run: estimate scope without writing to vector store.")
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
