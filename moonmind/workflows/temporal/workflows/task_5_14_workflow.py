from datetime import timedelta

from temporalio import workflow

@workflow.defn
class Task514Workflow:
    """Workflow implementation for task 5.14"""

    @workflow.run
    async def run(self, input_str: str) -> str:
        return await workflow.execute_activity(
            "task_5_14_activity",
            input_str,
            start_to_close_timeout=timedelta(minutes=5),
            schedule_to_close_timeout=timedelta(minutes=5),
        )
