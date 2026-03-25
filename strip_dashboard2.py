import re

with open("api_service/api/routers/task_dashboard.py", "r") as f:
    content = f.read()

# Remove the classes/imports
content = re.sub(
    r'from moonmind\.schemas\.task_dashboard_models import \([\s\S]*?\)\n',
    'from moonmind.schemas.task_dashboard_models import (\n    CreateSkillRequest,\n    TaskDashboardSkillContext,\n)\n',
    content
)

# Remove _build_task_source_response up to _render_dashboard
content = re.sub(
    r'def _build_task_source_response\([\s\S]*?(?=def _render_dashboard)',
    '',
    content
)

# Remove the endpoints at the bottom
content = re.sub(
    r'@router\.get\(\n    "/api/tasks/\{task_id\}/resolution",[\s\S]*?(?=__all__ = \[)',
    '',
    content
)

with open("api_service/api/routers/task_dashboard.py", "w") as f:
    f.write(content)
