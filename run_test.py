import asyncio
from moonmind.workflows.skills.skill_plan_contracts import parse_plan_definition

plan_dict = {
    "plan_version": "1.0",
    "metadata": {
        "title": "Multi-step Test", 
        "created_at": "2024-01-01T00:00:00Z", 
        "registry_snapshot": {"digest": "reg:sha256:123", "artifact_ref": "art:sha256:456"}
    },
    "policy": {"failure_mode": "FAIL_FAST", "max_concurrency": 1},
    "nodes": [
        {"id": "step1", "tool": {"type": "skill", "name": "t"}, "inputs": {"instructions": "Step 1"}},
        {"id": "step2", "tool": {"type": "skill", "name": "t"}, "inputs": {"instructions": "Step 2"}},
        {"id": "step3", "tool": {"type": "skill", "name": "t"}, "inputs": {"instructions": "Step 3"}},
    ],
    "edges": [
        {"from": "step1", "to": "step2"},
        {"from": "step2", "to": "step3"}
    ]
}
try:
    parse_plan_definition(plan_dict)
    print("Success")
except Exception as e:
    import traceback
    traceback.print_exc()
