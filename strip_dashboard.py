import re

with open("api_service/api/routers/task_dashboard.py", "r") as f:
    content = f.read()

# Remove imports
content = re.sub(
    r'from moonmind\.workflows\.tasks\.source_mapping import \([\s\S]*?\)\n',
    '',
    content
)
content = re.sub(
    r'    DashboardTaskSourceResponse,\n',
    '',
    content
)
content = re.sub(
    r'    TaskSourceResolutionResponse,\n',
    '',
    content
)

# Remove functions
content = re.sub(
    r'@router\.get\(\n    "/api/tasks/\{task_id\}/resolution",[\s\S]*?(?=\n\n\n@router\.get|\Z)',
    '',
    content
)

content = re.sub(
    r'async def _resolve_dashboard_task_source\([\s\S]*?(?=\n\n\n@router|\Z)',
    '',
    content
)

with open("api_service/api/routers/task_dashboard.py", "w") as f:
    f.write(content)
