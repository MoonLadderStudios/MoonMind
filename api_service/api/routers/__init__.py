"""Router package exports."""

from api_service.api.routers.executions import router as executions_router
from api_service.api.routers.recurring_tasks import router as recurring_tasks_router
from api_service.api.routers.task_compatibility import (
    router as task_compatibility_router,
)

__all__ = [
    "executions_router",
    "recurring_tasks_router",
    "task_compatibility_router",
]
