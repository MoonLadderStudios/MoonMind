"""CLI entrypoint for the standalone MoonMind Codex worker daemon."""

from __future__ import annotations

import argparse
import asyncio
import subprocess

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


def run_preflight() -> None:
    """Validate Codex CLI availability and authentication before daemon start."""

    try:
        codex_path = verify_cli_is_executable("codex")
    except CliVerificationError as exc:
        raise RuntimeError(str(exc)) from exc

    result = subprocess.run(
        [codex_path, "login", "status"],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        message = (
            result.stderr.strip()
            or result.stdout.strip()
            or "unknown codex login status error"
        )
        raise RuntimeError(f"codex login status failed: {message}")


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
