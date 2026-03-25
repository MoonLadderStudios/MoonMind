import asyncio
import logging

from sqlalchemy import select

from api_service.core.sync import create_async_session_maker
from api_service.db.models import RecurringTaskDefinition
from moonmind.config.settings import AppSettings
from moonmind.workflows.temporal.client import TemporalClientAdapter
from moonmind.workflows.temporal.schedule_errors import (
    ScheduleAdapterError,
    ScheduleAlreadyExistsError,
    ScheduleOperationError,
)

logger = logging.getLogger(__name__)


def _workflow_type_for_target(target: dict) -> str:
    kind = str(target.get("kind") or "")
    if kind in {"queue_task", "queue_task_template"}:
        return "MoonMind.Run"
    if kind == "manifest_run":
        return "MoonMind.ManifestIngest"
    return "MoonMind.Run"


async def migrate_definitions() -> None:
    logger.info("Starting migration to Temporal Schedules...")
    settings = AppSettings()
    async_session = create_async_session_maker(settings.database.POSTGRES_URL)
    adapter = TemporalClientAdapter.from_settings(settings)

    async with async_session() as session:
        stmt = select(RecurringTaskDefinition).where(
            RecurringTaskDefinition.enabled == True,
            RecurringTaskDefinition.temporal_schedule_id.is_(None),
        )
        result = await session.execute(stmt)
        definitions = result.scalars().all()

        for dfn in definitions:
            logger.info("Migrating definition %s (%s)...", dfn.id, dfn.name)
            policy_payload = dfn.policy if isinstance(dfn.policy, dict) else {}
            overlap_raw = policy_payload.get("overlap") or {}
            overlap_mode = str(
                (overlap_raw.get("mode") if isinstance(overlap_raw, dict) else None)
                or "skip"
            ).strip().lower()
            catchup_raw = policy_payload.get("catchup") or {}
            catchup_mode = str(
                (catchup_raw.get("mode") if isinstance(catchup_raw, dict) else None)
                or "last"
            ).strip().lower()
            try:
                jitter_seconds = int(policy_payload.get("jitterSeconds", 0))
            except (TypeError, ValueError):
                jitter_seconds = 0
            jitter_seconds = max(0, jitter_seconds)

            target = dfn.target if isinstance(dfn.target, dict) else {}
            workflow_type = _workflow_type_for_target(target)

            workflow_input = {
                "title": dfn.name,
                "ownerUserId": str(dfn.owner_user_id) if dfn.owner_user_id else None,
                "system": {
                    "recurrence": {
                        "definitionId": str(dfn.id),
                    }
                },
                "recurringTarget": target,
            }

            try:
                await adapter.create_schedule(
                    definition_id=dfn.id,
                    cron_expression=dfn.cron,
                    timezone=dfn.timezone,
                    overlap_mode=overlap_mode,
                    catchup_mode=catchup_mode,
                    jitter_seconds=jitter_seconds,
                    enabled=dfn.enabled,
                    note=dfn.name,
                    workflow_type=workflow_type,
                    workflow_input=workflow_input,
                    memo={"definitionId": str(dfn.id)},
                )
                dfn.temporal_schedule_id = f"mm-schedule:{dfn.id}"
                session.add(dfn)
                await session.commit()
                logger.info("Successfully migrated %s", dfn.id)
            except ScheduleAlreadyExistsError:
                logger.warning(
                    "Schedule already exists for %s; linking temporal_schedule_id",
                    dfn.id,
                )
                dfn.temporal_schedule_id = f"mm-schedule:{dfn.id}"
                session.add(dfn)
                await session.commit()
            except ScheduleOperationError as exc:
                logger.error(
                    "Temporal schedule operation failed for %s: %s", dfn.id, exc
                )
                await session.rollback()
            except ScheduleAdapterError as exc:
                logger.error("Schedule adapter error for %s: %s", dfn.id, exc)
                await session.rollback()
            except Exception:
                logger.exception("Unexpected error migrating %s", dfn.id)
                await session.rollback()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(migrate_definitions())
