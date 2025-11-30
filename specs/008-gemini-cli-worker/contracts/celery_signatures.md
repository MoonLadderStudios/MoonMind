# Celery Task Signatures: Gemini Worker

**Feature**: Gemini CLI Worker
**Branch**: `008-gemini-cli-worker`

## Task: `gemini_generate`

Invokes the Gemini CLI to generate content.

- **Queue**: `gemini`
- **Name**: `gemini_worker.tasks.generate` (or similar)

### Input Arguments

| Argument | Type | Required | Description |
|----------|------|----------|-------------|
| `prompt` | str | Yes | The text prompt to send to Gemini. |
| `model` | str | No | Specific model to use (default: configured default). |
| `output_file` | str | No | Path to save output (if not returning raw). |

### Return Value

| Type | Description |
|------|-------------|
| `dict` | Result object containing `content` (generated text) or `error`. |

## Task: `gemini_process_response`

Processes the raw output from a Gemini generation task.

- **Queue**: `gemini`
- **Name**: `gemini_worker.tasks.process_response`

### Input Arguments

| Argument | Type | Required | Description |
|----------|------|----------|-------------|
| `generation_result` | dict | Yes | Output from `gemini_generate`. |

### Return Value

| Type | Description |
|------|-------------|
| `dict` | Structured data parsed from the response. |
