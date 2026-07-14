"""Temporal Activity boundary for the deployment-selected container backend.

MoonLadderStudios/MoonMind#3254.  Workflow history carries only the resolved,
backend-neutral launch plan; endpoint configuration remains in the adapter.
"""

from __future__ import annotations

from temporalio import activity

from moonmind.schemas.container_job_models import ResolvedContainerLaunchPlan
from moonmind.workloads.docker_backend import DockerBackendAdapter


class ContainerJobActivities:
    def __init__(self, backend: DockerBackendAdapter) -> None:
        self._backend = backend

    @activity.defn(name="container_job.inspect_image")
    async def inspect_image(self, image: str) -> dict[str, object]:
        observation = await self._backend.inspect_image(image)
        return observation.model_dump(mode="json", by_alias=True, exclude_none=True)

    @activity.defn(name="container_job.execute")
    async def execute(self, payload: dict[str, object]) -> dict[str, object]:
        plan = ResolvedContainerLaunchPlan.model_validate(payload)
        result = await self._backend.run(plan)
        return {
            "containerId": result.container_id,
            "exitCode": result.exit_code,
            "stdout": result.stdout,
            "stderr": result.stderr,
            "reattached": result.reattached,
        }

    @activity.defn(name="container_job.stop")
    async def stop(self, payload: dict[str, object]) -> None:
        await self._backend.stop(str(payload["containerId"]), int(payload.get("graceSeconds", 0)))

    @activity.defn(name="container_job.remove")
    async def remove(self, container_id: str) -> None:
        await self._backend.remove(container_id)

    @property
    def handlers(self) -> tuple[object, ...]:
        return (self.inspect_image, self.execute, self.stop, self.remove)

