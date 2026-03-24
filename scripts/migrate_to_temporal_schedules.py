import asyncio
import os
import sys
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from api_service.core.sync import create_async_session_maker
from api_service.db.models import RecurringTaskDefinition
from moonmind.workflows.temporal.client import TemporalClientAdapter
from moonmind.config.settings import AppSettings

async def migrate_definitions():
    print("Starting migration to Temporal Schedules...")
    settings = AppSettings()
    async_session = create_async_session_maker(settings.database.POSTGRES_URL)
    adapter = TemporalClientAdapter.from_settings(settings)
    
    async with async_session() as session:
        # Get all enabled definitions
        stmt = select(RecurringTaskDefinition).where(RecurringTaskDefinition.enabled == True, RecurringTaskDefinition.temporal_schedule_id.is_(None))
        result = await session.execute(stmt)
        definitions = result.scalars().all()
        
        for dfn in definitions:
            print(f"Migrating definition {dfn.id} ({dfn.name})...")
            policy_payload = dfn.policy or {}
            
            # Helper logic to figure out workflow type and input
            target_kind = (dfn.target or {}).get("kind", "")
            workflow_type = "MoonMind.Run"
            if target_kind == "manifest":
                workflow_type = "MoonMind.ManifestIngest"
                
            workflow_input = {
                "title": dfn.name,
                "ownerUserId": str(dfn.owner_user_id) if dfn.owner_user_id else None,
                "system": {
                    "recurrence": {
                        "definitionId": str(dfn.id)
                    }
                },
                "recurringTarget": dfn.target or {},
            }
            
            try:
                # Assuming adapter methods match what we did in create_definition
                await adapter.create_schedule(
                    definition_id=dfn.id,
                    cron_expression=dfn.cron,
                    timezone=dfn.timezone,
                    overlap_mode=policy_payload.get("overlap", {}).get("mode") if policy_payload else None,
                    catchup_mode=policy_payload.get("catchup", {}).get("mode") if policy_payload else None,
                    jitter_seconds=policy_payload.get("jitter_seconds", 0) if policy_payload else None,
                    enabled=dfn.enabled,
                    note=dfn.name,
                    workflow_type=workflow_type,
                    workflow_input=workflow_input,
                    memo={"definitionId": str(dfn.id)},
                )
                dfn.temporal_schedule_id = f"mm-schedule:{dfn.id}"
                session.add(dfn)
                await session.commit()
                print(f"Successfully migrated {dfn.id}")
            except Exception as e:
                print(f"Failed to migrate {dfn.id}: {e}")
                await session.rollback()

if __name__ == "__main__":
    asyncio.run(migrate_definitions())
