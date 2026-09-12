"""A credential-free canary pinned to the candidate worker release."""

from datetime import timedelta

from temporalio import activity, workflow
from temporalio.common import RetryPolicy
from temporalio.exceptions import ApplicationError
from temporalio.workflow import VersioningIntent


@activity.defn(name="release.inspect")
async def inspect_release_activity(expected_digest: str) -> dict[str, str]:
    from moonmind.release_identity import installed_release

    release = installed_release()
    if release is None or release["digest"] != expected_digest:
        raise ApplicationError(
            "Canary worker has no matching immutable release", non_retryable=True
        )
    return {"digest": release["digest"], "status": "verified"}


@workflow.defn(name="MoonMind.ReleaseCanary")
class ReleaseCanaryWorkflow:
    @workflow.run
    async def run(self, expected_digest: str | dict) -> dict[str, str]:
        # String inputs are retained for histories written by the original
        # identity canary. Release promotion qualifies every fleet queue.
        digest = (
            expected_digest
            if isinstance(expected_digest, str)
            else expected_digest["digest"]
        )
        queues = (
            [None]
            if isinstance(expected_digest, str)
            else expected_digest["taskQueues"]
        )
        for queue in queues:
            result = await workflow.execute_activity(
                "release.inspect",
                digest,
                task_queue=queue,
                start_to_close_timeout=timedelta(seconds=30),
                schedule_to_close_timeout=timedelta(seconds=60),
                retry_policy=RetryPolicy(maximum_attempts=1),
                # Deployment-version routing propagates the pinned parent
                # across queues. The older Build-ID compatibility flag rejects
                # cross-queue commands on the Temporal service.
                versioning_intent=(
                    VersioningIntent.COMPATIBLE
                    if isinstance(expected_digest, str)
                    else None
                ),
            )
            if result != {"digest": digest, "status": "verified"}:
                raise ApplicationError(
                    "Release fleet identity differs", non_retryable=True
                )
        return {"digest": digest, "status": "verified"}
