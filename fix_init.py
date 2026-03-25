with open("api_service/api/routers/__init__.py", "r") as f:
    lines = f.readlines()
with open("api_service/api/routers/__init__.py", "w") as f:
    for line in lines:
        if "task_compatibility" not in line:
            f.write(line)
