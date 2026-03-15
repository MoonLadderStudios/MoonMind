import os
import re

directories = ['moonmind', 'api_service', 'tests', 'services', 'tools']
files_to_check = ['config.toml', 'docker-compose.yaml', 'docker-compose.job.yaml', 'docker-compose.test.yaml', 'AGENTS.md', '.env-template', '.env.vllm-template']

extensions = ('.py', '.yaml', '.yml', '.sh', '.toml', '.md', '.jsonl', '.js')

replacements = [
    (r'\bspec_workflow\b', 'workflow'),
    (r'\bSpecWorkflow\b', 'Workflow'),
    (r'\bspec_workflows\b', 'workflows'),
    (r'\bSPEC_WORKFLOW_', 'WORKFLOW_'),
    (r'\bspec_automation\b', 'automation'),
    (r'\bspec-automation\b', 'workflows'),
    (r'\bSpeckit\b', 'Workflow'),
    (r'\bSPEC_AUTOMATION_', 'WORKFLOW_'),
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
        # skip alembic migrations, as changing historical migration files can break downgrade or hashes
        if 'migrations/versions' in root.replace('\\', '/'):
            continue
        for file in files:
            if file.endswith(extensions):
                process_file(os.path.join(root, file))

for file in files_to_check:
    if os.path.exists(file):
        process_file(file)
