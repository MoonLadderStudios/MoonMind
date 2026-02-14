"""CLI entrypoint for the standalone MoonMind Codex worker daemon."""

from __future__ import annotations

import argparse
import asyncio
import os
import subprocess
from typing import Mapping, Sequence

from moonmind.agents.codex_worker.handlers import CodexExecHandler
from moonmind.agents.codex_worker.utils import (
    CliVerificationError,
    verify_cli_is_executable,
)
from moonmind.agents.codex_worker.worker import (
    CodexWorker,
    CodexWorkerConfig,
    QueueApiClient,
)


def _redact_value(text: str, secrets: Sequence[str]) -> str:
    redacted = text
    for secret in secrets:
        if secret:
            redacted = redacted.replace(secret, "[REDACTED]")
    return redacted


def _run_checked_command(
    command: list[str],
    *,
    input_text: str | None = None,
    redaction_values: Sequence[str] = (),
) -> None:
    result = subprocess.run(
        command,
        input=input_text,
        capture_output=True,
        text=True,
    )
    if result.returncode == 0:
        return

    message = (
        result.stderr.strip()
        or result.stdout.strip()
        or f"command failed: {' '.join(command)}"
    )
    raise RuntimeError(_redact_value(message, redaction_values))


def run_preflight(env: Mapping[str, str] | None = None) -> None:
    """Validate CLI dependencies and auth state before daemon start."""

    source = env if env is not None else os.environ

    try:
        codex_path = verify_cli_is_executable("codex")
    except CliVerificationError as exc:
        raise RuntimeError(str(exc)) from exc

    github_token = str(source.get("GITHUB_TOKEN", "")).strip()
    redaction_values = (github_token,) if github_token else ()

    _run_checked_command(
        [codex_path, "login", "status"],
        redaction_values=redaction_values,
    )

    if not github_token:
        return

    try:
        gh_path = verify_cli_is_executable("gh")
    except CliVerificationError as exc:
        raise RuntimeError(str(exc)) from exc

    _run_checked_command(
        [
            gh_path,
            "auth",
            "login",
            "--hostname",
            "github.com",
            "--with-token",
        ],
        input_text=github_token,
        redaction_values=redaction_values,
    )
    _run_checked_command(
        [gh_path, "auth", "setup-git"],
        redaction_values=redaction_values,
    )
    _run_checked_command(
        [gh_path, "auth", "status", "--hostname", "github.com"],
        redaction_values=redaction_values,
    )


def build_parser() -> argparse.ArgumentParser:
    """Create CLI parser for worker runtime options."""

    parser = argparse.ArgumentParser(prog="moonmind-codex-worker")
    parser.add_argument(
        "--once",
        action="store_true",
        help="Process at most one claim cycle and exit.",
    )
    return parser


async def _run(args: argparse.Namespace) -> None:
    config = CodexWorkerConfig.from_env()
    config.workdir.mkdir(parents=True, exist_ok=True)

    run_preflight()

    queue_client = QueueApiClient(
        base_url=config.moonmind_url,
        worker_token=config.worker_token,
    )
    handler = CodexExecHandler(workdir_root=config.workdir)
    worker = CodexWorker(
        config=config,
        queue_client=queue_client,
        codex_exec_handler=handler,
    )

    try:
        if args.once:
            await worker.run_once()
        else:
            await worker.run_forever()
    finally:
        await queue_client.aclose()


def main(argv: list[str] | None = None) -> int:
    """Entry point for `moonmind-codex-worker`."""

    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        asyncio.run(_run(args))
    except Exception as exc:
        parser.exit(status=1, message=f"moonmind-codex-worker failed: {exc}\n")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
