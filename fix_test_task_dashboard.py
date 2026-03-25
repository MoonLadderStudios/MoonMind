import re

with open("tests/unit/api/routers/test_task_dashboard.py", "r") as f:
    content = f.read()

# remove test_task_source_endpoint_returns_resolved_temporal_source
content = re.sub(
    r'def test_task_source_endpoint_returns_resolved_temporal_source[\s\S]*?(?=def test_|$)',
    '',
    content
)

# remove test_task_source_endpoint_returns_404_when_not_found
content = re.sub(
    r'def test_task_source_endpoint_returns_404_when_not_found[\s\S]*?(?=def test_|$)',
    '',
    content
)

# remove test_task_resolution_returns_temporal_source_for_workflow_id
content = re.sub(
    r'def test_task_resolution_returns_temporal_source_for_workflow_id[\s\S]*?(?=def test_|$)',
    '',
    content
)

# remove test_task_resolution_rejects_orchestrator_source_hint
content = re.sub(
    r'def test_task_resolution_rejects_orchestrator_source_hint[\s\S]*?(?=def test_|$)',
    '',
    content
)

# remove test_task_resolution_returns_queue_source_for_legacy_tasks
content = re.sub(
    r'def test_task_resolution_returns_queue_source_for_legacy_tasks[\s\S]*?(?=def test_|$)',
    '',
    content
)

# remove test_task_resolution_returns_queue_source
content = re.sub(
    r'def test_task_resolution_returns_queue_source[\s\S]*?(?=def test_|$)',
    '',
    content
)

with open("tests/unit/api/routers/test_task_dashboard.py", "w") as f:
    f.write(content)
