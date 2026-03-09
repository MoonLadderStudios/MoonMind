import asyncio
from pathlib import Path


from tests.integration.workflows.agent_queue.test_service_update import (
    test_update_queued_job_rejects_non_queued_status,
)


async def main():
    try:
        await test_update_queued_job_rejects_non_queued_status(Path("/tmp"))
    except Exception as e:
        print(f"RAISED: {type(e)}")
        import traceback

        traceback.print_exc()
    else:
        print("DID NOT RAISE")


if __name__ == "__main__":
    asyncio.run(main())
