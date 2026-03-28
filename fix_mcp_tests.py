import re
from pathlib import Path

def process_file(file_path):
    path = Path(file_path)
    content = path.read_text()

    # Replace models.AgentJobStatus.QUEUED with "queued"
    content = re.sub(r'models\.AgentJobStatus\.QUEUED(\.value)?', '"queued"', content)

    # Replace models.AgentJobStatus.RUNNING with "running"
    content = re.sub(r'models\.AgentJobStatus\.RUNNING', '"running"', content)

    # Replace models.AgentJobStatus.CANCELLED with "cancelled"
    content = re.sub(r'models\.AgentJobStatus\.CANCELLED', '"cancelled"', content)

    # Replace the type annotation
    content = re.sub(r'status: models\.AgentJobStatus =', 'status: str =', content)

    # Remove the import we just added
    content = re.sub(r'import api_service\.db\.models as models\n', '', content)

    path.write_text(content)

process_file("tests/unit/mcp/test_tool_registry.py")
process_file("tests/unit/api/routers/test_mcp_tools.py")
