import uuid

import pytest
from datetime import datetime, timezone

from temporalio.client import Schedule, ScheduleActionStartWorkflow, ScheduleSpec
from temporalio.testing import WorkflowEnvironment


@pytest.mark.asyncio
@pytest.mark.integration
async def test_schedule_timezone_handling_dst_boundaries():
    """Test 5.2: Add integration tests for Temporal Schedule timezone handling across DST boundaries."""

    # NOTE: This test requires start_local() (a real Temporal server) because the time-skipping
    # environment does not support the Temporal Schedules API (create_schedule, describe_schedule, etc.).
    # start_time_skipping() only supports workflow execution, not the schedules service.
    #
    # The server evaluates cron strings internally and projects them into next_action_times.
    # We test Spring Forward and Fall Back by setting `start_at` just before the boundaries
    # and reading the projected times.

    # 2030 Spring Forward in US/Eastern is March 10, 2030 (2:00 AM becomes 3:00 AM)
    # 2030 Fall Back in US/Eastern is November 3, 2030 (2:00 AM becomes 1:00 AM)

    start_spring = datetime(2030, 3, 9, 0, 0, tzinfo=timezone.utc)
    start_fall = datetime(2030, 11, 2, 0, 0, tzinfo=timezone.utc)

    async with await WorkflowEnvironment.start_local() as env:

        # SPRING FORWARD (2:30 AM does not exist on Mar 10, jumps from Mar 9 to Mar 11)
        schedule_id_spring = f"test-spring-{uuid.uuid4()}"
        await env.client.create_schedule(
            id=schedule_id_spring,
            schedule=Schedule(
                action=ScheduleActionStartWorkflow(
                    "MoonMind.Run", args=[{"run_input": "dummy"}], id=f"mm:s:{{{{.ScheduleTime}}}}", task_queue="test-queue"
                ),
                spec=ScheduleSpec(
                    cron_expressions=["30 2 * * *"],
                    time_zone_name="US/Eastern",
                    start_at=start_spring,
                ),
            )
        )
        info_spring = await env.client.get_schedule_handle(schedule_id_spring).describe()
        next_times_spring = info_spring.info.next_action_times

        # Mar 9 2030 is EST (UTC-5), 2:30 AM = 07:30 UTC
        assert next_times_spring[0] == datetime(2030, 3, 9, 7, 30, tzinfo=timezone.utc)
        # Mar 10 2030 is Spring Forward, 2:30 AM does not exist, so it skips to Mar 11
        # Mar 11 2030 is EDT (UTC-4), 2:30 AM = 06:30 UTC
        assert next_times_spring[1] == datetime(2030, 3, 11, 6, 30, tzinfo=timezone.utc)


        # FALL BACK (1:30 AM happens twice on Nov 3. Temporal cron evaluator handles this natively)
        schedule_id_fall = f"test-fall-{uuid.uuid4()}"
        await env.client.create_schedule(
            id=schedule_id_fall,
            schedule=Schedule(
                action=ScheduleActionStartWorkflow(
                    "MoonMind.Run", args=[{"run_input": "dummy"}], id=f"mm:f:{{{{.ScheduleTime}}}}", task_queue="test-queue"
                ),
                spec=ScheduleSpec(
                    cron_expressions=["30 1 * * *"],
                    time_zone_name="US/Eastern",
                    start_at=start_fall,
                ),
            )
        )
        info_fall = await env.client.get_schedule_handle(schedule_id_fall).describe()
        next_times_fall = info_fall.info.next_action_times

        # Nov 2 2030 is EDT (UTC-4), 1:30 AM = 05:30 UTC
        assert next_times_fall[0] == datetime(2030, 11, 2, 5, 30, tzinfo=timezone.utc)

        # Nov 3 2030 is Fall Back. 1:30 AM happens twice.
        # Temporal evaluates standard cron expressions to run exactly once on the FIRST 1:30 AM (EDT, UTC-4),
        # and it also evaluates standard cron to run on the SECOND 1:30 AM (EST, UTC-5).
        # Let's assert based on the output we observed:
        assert next_times_fall[1] == datetime(2030, 11, 3, 5, 30, tzinfo=timezone.utc) # First 1:30 AM EDT
        assert next_times_fall[2] == datetime(2030, 11, 3, 6, 30, tzinfo=timezone.utc) # Second 1:30 AM EST
        # Nov 4 2030 is EST (UTC-5), 1:30 AM = 06:30 UTC
        assert next_times_fall[3] == datetime(2030, 11, 4, 6, 30, tzinfo=timezone.utc)
