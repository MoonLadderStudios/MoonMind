# Quickstart: Using the Gemini CLI

**Feature**: Add Gemini CLI to Docker Environment

## Prerequisites

- Docker container (Orchestrator or Celery Worker) must be running.
- `GOOGLE_API_KEY` must be set in the container environment.

## Usage

### Check Installation

```bash
gemini --version
```

### Basic Prompting

To send a prompt to Gemini:

```bash
gemini "Explain the concept of recursion in one sentence."
```

### Piped Input

You can pipe text into the CLI:

```bash
echo "Refactor this code..." | gemini
```

### In Orchestrator

The orchestrator can use `subprocess` to invoke the CLI:

```python
import subprocess

result = subprocess.run(
    ["gemini", "Generate a summary for this text"],
    capture_output=True,
    text=True
)
print(result.stdout)
```
