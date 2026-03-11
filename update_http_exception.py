filepath = "api_service/api/routers/agent_queue.py"
with open(filepath, "r") as f:
    lines = f.readlines()

for i, line in enumerate(lines):
    if (
        'detail["message"] = message' in line
        and "return HTTPException(status_code=status_code, detail=detail)"
        in lines[i + 1]
    ):
        lines.insert(i, '        detail["code"] = code\n')
        break

with open(filepath, "w") as f:
    f.writelines(lines)
