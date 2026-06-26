import os
import re

directories = ['moonmind', 'api_service', 'tests', 'services', 'tools']
extensions = ('.py', '.yaml', '.yml', '.sh', '.toml', '.md', '.jsonl', '.js')

replacements = [
    (r'\bspec_workflow_runs\b', 'workflow_runs'),
    (r'\bspec_workflow_task_states\b', 'workflow_task_states'),
    (r'ix_workflow_runs_', 'ix_workflow_runs_'),
    (r'ix_workflow_task_states_', 'ix_workflow_task_states_'),
    (r'ck_workflow_task_state_', 'ck_workflow_task_state_'),
    (r'uq_workflow_task_state_', 'uq_workflow_task_state_'),
]

def process_file(filepath):
    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            content = f.read()
    except UnicodeDecodeError:
        return

    original_content = content
    for pattern, repl in replacements:
        content = re.sub(pattern, repl, content)

    if content != original_content:
        with open(filepath, 'w', encoding='utf-8') as f:
            f.write(content)
        print(f"Updated {filepath}")

for root_dir in directories:
    for root, dirs, files in os.walk(root_dir):
        if 'migrations/versions' in root.replace('\\', '/'):
            continue
        for file in files:
            if file.endswith(extensions):
                process_file(os.path.join(root, file))
