import re

with open("moonmind/schemas/__init__.py", "r") as f:
    content = f.read()

content = re.sub(
    r'from \.task_compatibility_models import \([\s\S]*?\)\n',
    '',
    content
)

for item in ["TaskActionAvailability", "TaskDebugContext", "TaskCompatibilityRow", "TaskCompatibilityDetail", "TaskCompatibilityListResponse"]:
    content = re.sub(rf'    "{item}",\n', '', content)

with open("moonmind/schemas/__init__.py", "w") as f:
    f.write(content)
