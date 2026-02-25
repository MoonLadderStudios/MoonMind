"""Recurring schedule daemon that converts definitions into queue jobs."""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
from dataclasses import dataclass

from api_service.db.base import get_async_session_context
from api_service.services.recurring_tasks_service import RecurringTasksService
from moonmind.config.settings import settings

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class SchedulerConfig:
    """Runtime options for recurring schedule polling."""

    poll_interval_ms: int
    batch_size: int
    max_backfill: int

    @classmethod
    def from_settings(cls) -> "SchedulerConfig":
        return cls(
            poll_interval_ms=max(250, int(settings.spec_workflow.scheduler_poll_interval_ms)),
            batch_size=max(1, int(settings.spec_workflow.scheduler_batch_size)),
            max_backfill=max(1, int(settings.spec_workflow.scheduler_max_backfill)),
        )


class RecurringTaskScheduler:
    """Daemon wrapper that executes scheduler ticks against the DB."""

    def __init__(self, *, config: SchedulerConfig) -> None:
        self._config = config

    async def run_tick(self) -> tuple[int, int]:
        async with get_async_session_context() as session:
            service = RecurringTasksService(session)
            result = await service.run_scheduler_tick(
                batch_size=self._config.batch_size,
                max_backfill=self._config.max_backfill,
            )
            return result.scheduled_runs, result.dispatched_runs

    async def run_forever(self) -> None:
        sleep_seconds = self._config.poll_interval_ms / 1000.0
        while True:
            try:
                scheduled_runs, dispatched_runs = await self.run_tick()
                if scheduled_runs or dispatched_runs:
                    logger.info(
                        "Recurring scheduler tick complete: scheduled=%s dispatched=%s",
                        scheduled_runs,
                        dispatched_runs,
                    )
            except Exception:
                logger.exception("Recurring scheduler tick failed")
            await asyncio.sleep(sleep_seconds)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="moonmind-scheduler")
    parser.add_argument(
        "--once",
        action="store_true",
        help="Run one scheduler tick then exit.",
    )
    parser.add_argument(
        "--poll-interval-ms",
        type=int,
        default=None,
        help="Override MOONMIND_SCHEDULER_POLL_INTERVAL_MS.",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=None,
        help="Override MOONMIND_SCHEDULER_BATCH_SIZE.",
    )
    parser.add_argument(
        "--max-backfill",
        type=int,
        default=None,
        help="Override MOONMIND_SCHEDULER_MAX_BACKFILL.",
    )
    return parser


def _resolve_config(args: argparse.Namespace) -> SchedulerConfig:
    base = SchedulerConfig.from_settings()
    poll_interval_ms = (
        max(250, int(args.poll_interval_ms))
        if args.poll_interval_ms is not None
        else base.poll_interval_ms
    )
    batch_size = (
        max(1, int(args.batch_size))
        if args.batch_size is not None
        else base.batch_size
    )
    max_backfill = (
        max(1, int(args.max_backfill))
        if args.max_backfill is not None
        else base.max_backfill
    )
    return SchedulerConfig(
        poll_interval_ms=poll_interval_ms,
        batch_size=batch_size,
        max_backfill=max_backfill,
    )


async def _run(args: argparse.Namespace) -> int:
    config = _resolve_config(args)
    scheduler = RecurringTaskScheduler(config=config)
    if args.once:
        scheduled_runs, dispatched_runs = await scheduler.run_tick()
        logger.info(
            "Recurring scheduler one-shot tick complete: scheduled=%s dispatched=%s",
            scheduled_runs,
            dispatched_runs,
        )
        return 0
    await scheduler.run_forever()
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO").upper())
    try:
        return asyncio.run(_run(args))
    except Exception as exc:
        parser.exit(status=1, message=f"moonmind-scheduler failed: {exc}\n")
    return 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
