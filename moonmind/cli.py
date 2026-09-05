"""Top-level MoonMind CLI exposing worker utilities."""

from __future__ import annotations

from pathlib import Path
from typing import List, Optional

import typer

from moonmind.container_job_cli import (
    ContainerJobCliError,
    ContainerJobResult,
    load_container_job_spec,
    run_container_job,
    run_python_tests,
)
from moonmind.run_cli import (
    RunApiClient,
    RunCliError,
    build_workflow_submit_payload,
    resolve_api_base_url,
    resolve_bearer_token,
    summarize_execution,
    summarize_readiness,
)
from moonmind.manifest import manifest_cli
from moonmind.rag import cli as rag_cli
from moonmind.rag.guardrails import GuardrailError, ensure_rag_ready
from moonmind.rag.settings import RagRuntimeSettings
from moonmind.utils.logging import redact_sensitive_text

app = typer.Typer(help="MoonMind developer utilities.")
rag_app = typer.Typer(help="Retrieval helpers for Codex workers.")
worker_app = typer.Typer(help="Worker runtime diagnostics.")
manifest_app = typer.Typer(help="Manifest schema validation and pipeline commands.")
container_app = typer.Typer(help="Run work through MoonMind's Docker backend.")
app.add_typer(rag_app, name="rag")
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


# ----- public-contract first-run commands (MoonLadderStudios/MoonMind#3926) -----


@app.command(
    "run",
    help=(
        "Submit a bounded workflow through the same POST /api/executions contract "
        "as the dashboard. Omitted --provider-profile resolves to the server "
        "default (credentialless route); failures are actionable and never "
        "switch credentials silently."
    ),
)
def first_run(
    prompt: str = typer.Option(
        ...,
        "--prompt",
        "--instructions",
        help="Bounded task instructions for the first-run workflow.",
    ),
    title: Optional[str] = typer.Option(
        None, "--title", help="Optional workflow title (server derives one if omitted)."
    ),
    provider_profile: str = typer.Option(
        "auto",
        "--provider-profile",
        help="Provider profile ref, or 'auto'/'default' for the server default.",
    ),
    request_id: Optional[str] = typer.Option(
        None,
        "--request-id",
        help="Stable caller request id for idempotent retries.",
    ),
    base_url: Optional[str] = typer.Option(
        None, "--base-url", help="API base URL (default MOONMIND_URL or localhost:7000)."
    ),
    token: Optional[str] = typer.Option(
        None, "--token", help="API bearer token (default MOONMIND_API_TOKEN)."
    ),
    timeout_seconds: float = typer.Option(30.0, "--timeout", min=1.0, max=300.0),
    wait: bool = typer.Option(
        False,
        "--wait",
        help="Poll UI-equivalent status until a terminal state.",
    ),
    wait_timeout_seconds: float = typer.Option(
        600.0, "--wait-timeout", min=1.0, max=86400.0
    ),
    json_output: bool = typer.Option(
        False, "--json", help="Print the raw execution JSON document."
    ),
) -> None:
    import json as _json

    from moonmind.run_cli import wait_for_terminal

    try:
        payload, effective_key = build_workflow_submit_payload(
            instructions=prompt,
            title=title,
            provider_profile_ref=provider_profile,
            idempotency_key=request_id,
        )
        client = RunApiClient(
            base_url=resolve_api_base_url(base_url),
            bearer_token=resolve_bearer_token(token),
            timeout_seconds=timeout_seconds,
        )
        try:
            execution = client.submit_workflow(payload)
            if wait:
                workflow_id = str(
                    execution.get("workflowId")
                    or execution.get("workflow_id")
                    or execution.get("id")
                    or ""
                )
                if workflow_id:
                    execution = wait_for_terminal(
                        client,
                        workflow_id,
                        timeout_seconds=wait_timeout_seconds,
                    )
        finally:
            client.close()
    except RunCliError as exc:
        typer.secho(f"Error: {exc}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1) from exc
    if json_output:
        typer.echo(_json.dumps(execution, indent=2))
    else:
        typer.echo(summarize_execution(execution))
        typer.echo(
            "Inspect evidence with: "
            f"moonmind logs {execution.get('workflowId', execution.get('workflow_id', execution.get('id', '')))} "
            f"(idempotencyKey={effective_key})"
        )


@app.command(
    "status",
    help=(
        "Show workflow status through the same GET /api/executions/{id} contract "
        "as the dashboard."
    ),
)
def first_run_status(
    workflow_id: str = typer.Argument(..., help="Workflow id to describe."),
    base_url: Optional[str] = typer.Option(
        None, "--base-url", help="API base URL (default MOONMIND_URL or localhost:7000)."
    ),
    token: Optional[str] = typer.Option(
        None, "--token", help="API bearer token (default MOONMIND_API_TOKEN)."
    ),
    timeout_seconds: float = typer.Option(30.0, "--timeout", min=1.0, max=300.0),
    json_output: bool = typer.Option(
        False, "--json", help="Print the raw execution JSON document."
    ),
) -> None:
    import json as _json

    try:
        client = RunApiClient(
            base_url=resolve_api_base_url(base_url),
            bearer_token=resolve_bearer_token(token),
            timeout_seconds=timeout_seconds,
        )
        try:
            execution = client.describe_execution(workflow_id)
        finally:
            client.close()
    except RunCliError as exc:
        typer.secho(f"Error: {exc}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1) from exc
    if json_output:
        typer.echo(_json.dumps(execution, indent=2))
    else:
        typer.echo(summarize_execution(execution))


@app.command(
    "logs",
    help=(
        "List captured terminal evidence through the same "
        "GET /api/executions/{id}/captured-evidence contract as the dashboard."
    ),
)
def first_run_logs(
    workflow_id: str = typer.Argument(..., help="Workflow id to inspect."),
    base_url: Optional[str] = typer.Option(
        None, "--base-url", help="API base URL (default MOONMIND_URL or localhost:7000)."
    ),
    token: Optional[str] = typer.Option(
        None, "--token", help="API bearer token (default MOONMIND_API_TOKEN)."
    ),
    timeout_seconds: float = typer.Option(30.0, "--timeout", min=1.0, max=300.0),
    json_output: bool = typer.Option(
        False, "--json", help="Print the raw captured-evidence JSON document."
    ),
) -> None:
    import json as _json
    from urllib.parse import quote as _quote

    try:
        client = RunApiClient(
            base_url=resolve_api_base_url(base_url),
            bearer_token=resolve_bearer_token(token),
            timeout_seconds=timeout_seconds,
        )
        try:
            evidence = client.get_captured_evidence(workflow_id)
        finally:
            client.close()
    except RunCliError as exc:
        typer.secho(f"Error: {exc}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1) from exc
    if json_output:
        typer.echo(_json.dumps(evidence, indent=2))
        return
    items = evidence.get("items") if isinstance(evidence, dict) else None
    available = evidence.get("available") if isinstance(evidence, dict) else None
    typer.echo(
        f"captured evidence for workflow {workflow_id}: "
        f"available={bool(available)} items={len(items) if isinstance(items, list) else 0}"
    )
    if isinstance(items, list):
        for item in items:
            if not isinstance(item, dict):
                continue
            ref = str(item.get("ref", item.get("artifactRef", "")) or "")
            label = str(item.get("label", "") or "")
            download = (
                f"/api/executions/{_quote(workflow_id, safe='')}"
                f"/captured-evidence/download?ref={_quote(ref, safe='')}"
                if ref
                else ""
            )
            detail = f" {label}" if label else ""
            typer.echo(f"- {ref}{detail} ({download})" if download else f"- {ref}{detail}")
    summary = evidence.get("summary") if isinstance(evidence, dict) else None
    if isinstance(summary, str) and summary.strip():
        typer.echo(summary.strip())


@app.command(
    "readiness",
    help=(
        "Show Omnigent bootstrap readiness through the same "
        "GET /api/omnigent/bootstrap/readiness contract as the dashboard."
    ),
)
def first_run_readiness(
    base_url: Optional[str] = typer.Option(
        None, "--base-url", help="API base URL (default MOONMIND_URL or localhost:7000)."
    ),
    token: Optional[str] = typer.Option(
        None, "--token", help="API bearer token (default MOONMIND_API_TOKEN)."
    ),
    timeout_seconds: float = typer.Option(30.0, "--timeout", min=1.0, max=300.0),
    json_output: bool = typer.Option(
        False, "--json", help="Print the raw readiness JSON document."
    ),
) -> None:
    import json as _json

    try:
        client = RunApiClient(
            base_url=resolve_api_base_url(base_url),
            bearer_token=resolve_bearer_token(token),
            timeout_seconds=timeout_seconds,
        )
        try:
            readiness = client.get_readiness()
        finally:
            client.close()
    except RunCliError as exc:
        typer.secho(f"Error: {exc}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1) from exc
    if json_output:
        typer.echo(_json.dumps(readiness, indent=2))
        return
    typer.echo(summarize_readiness(readiness))
    if str(readiness.get("readiness", "")) != "ready":
        typer.secho(
            "Not ready: resolve the blocking readiness entry before submitting. "
            "The CLI does not switch credentials, runtimes, or models silently.",
            fg=typer.colors.YELLOW,
            err=True,
        )


if __name__ == "__main__":  # pragma: no cover
    main()
