from datetime import timedelta
from temporalio import workflow

with workflow.unsafe.imports_passed_through():
    from moonmind.workflows.temporal.activities.task_5_14 import task_5_14_activity

@workflow.defn(sandboxed=False)
class Task514Workflow:
    """Workflow implementation for task 5.14"""
    @workflow.run
    async def run(self, input_str: str) -> str:
        return await workflow.execute_activity(
            task_5_14_activity,
            input_str,
            start_to_close_timeout=timedelta(minutes=5),
        )
