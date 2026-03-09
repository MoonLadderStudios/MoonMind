import asyncio
from pathlib import Path
from uuid import uuid4
from tests.integration.workflows.agent_queue.test_service_update import queue_db, _create_task_job
from moonmind.workflows.agent_queue.repositories import AgentQueueRepository
from moonmind.workflows.agent_queue.service import AgentQueueService

async def main():
    async with queue_db(Path("/tmp")) as session_maker:
        async with session_maker() as session:
            repo = AgentQueueRepository(session)
            service = AgentQueueService(repo)
            owner_id = uuid4()
            job = await _create_task_job(service, owner_id=owner_id)
            print("Job ID:", job.id, "Status:", job.status)
            res = await service.claim_job(
                worker_id="worker-1",
                lease_seconds=30,
                worker_capabilities=["codex", "git", "gh", "task"],
            )
            print("Claim result:", res.job.id if res.job else "None")
            job_after = await service.get_job(job.id)
            print("Job after claim:", job_after.status)

if __name__ == "__main__":
    asyncio.run(main())