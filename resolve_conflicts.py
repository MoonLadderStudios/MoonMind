import os
import re

def resolve_file(filepath):
    with open(filepath, 'r') as f:
        content = f.read()

    # Regex to match conflict markers and keep the HEAD part
    # We use re.sub with a custom replacer to handle this safely
    def replacer(match):
        return match.group(1)

    pattern = r'<<<<<<< HEAD\n(.*?)\n=======\n.*?\n>>>>>>> origin/main\n'
    
    resolved_content = re.sub(
        pattern,
        replacer,
        content,
        flags=re.DOTALL
    )

    with open(filepath, 'w') as f:
        f.write(resolved_content)

files_to_resolve = [
    'api_service/api/routers/executions.py',
    'step-11-report.md',
    'step-12-report.md',
    'step-13-report.md',
    'step-16-report.md',
    'step-3-report.md'
]

for filepath in files_to_resolve:
    if os.path.exists(filepath):
        resolve_file(filepath)
        print(f"Resolved {filepath}")
