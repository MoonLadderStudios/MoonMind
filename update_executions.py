with open("api_service/api/routers/executions.py", "r") as f:
    content = f.read()

import re

search = """    params = dict(getattr(record, "parameters", None) or {})
    target_runtime, param_model, param_effort = [
        str(params.get(key) or "").strip() or None
        for key in ["targetRuntime", "model", "effort"]
    ]
    task_params = params.get("task") if isinstance(params.get("task"), dict) else {}
    tool_params = task_params.get("tool") if isinstance(task_params.get("tool"), dict) else {}
    skill_params = task_params.get("skill") if isinstance(task_params.get("skill"), dict) else {}
    target_skill = (
        str(tool_params.get("name") or skill_params.get("name") or "").strip() or None
    )"""

replace = """    params = dict(getattr(record, "parameters", None) or {})
    target_runtime, param_model, param_effort = [
        str(params.get(key) or "").strip() or None
        for key in ["targetRuntime", "model", "effort"]
    ]
    if not target_runtime:
        target_runtime = _coerce_temporal_scalar(
            search_attributes.get("mm_target_runtime")
            or search_attributes.get("mm_runtime")
            or search_attributes.get("runtime")
        )

    task_params = params.get("task") if isinstance(params.get("task"), dict) else {}
    tool_params = task_params.get("tool") if isinstance(task_params.get("tool"), dict) else {}
    skill_params = task_params.get("skill") if isinstance(task_params.get("skill"), dict) else {}
    target_skill = (
        str(tool_params.get("name") or skill_params.get("name") or "").strip() or None
    )
    if not target_skill:
        target_skill = _coerce_temporal_scalar(
            search_attributes.get("mm_target_skill")
            or search_attributes.get("mm_skill_id")
            or search_attributes.get("mm_skill")
            or search_attributes.get("skillId")
            or search_attributes.get("skill")
        )"""

content = content.replace(search, replace)

with open("api_service/api/routers/executions.py", "w") as f:
    f.write(content)
