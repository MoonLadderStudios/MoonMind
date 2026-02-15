# Data Model: Gemini CLI Configuration

**Feature**: Add Gemini CLI to Docker Environment

## Configuration

The Gemini CLI relies on environment variables for authentication and configuration.

### Environment Variables

| Variable | Description | Required |
|----------|-------------|----------|
| `GOOGLE_API_KEY` or `GEMINI_API_KEY` | API Key for accessing Google Gemini models. Only one is required. If both are set, `GEMINI_API_KEY` may take precedence depending on the tool. | Yes (one of them) |

> Store API keys in your local `.env` file or secrets manager rather than committing them to version control. Rotate the keys periodically and prefer scoped keys when available.

### CLI State

The CLI is stateless for single-turn interactions but may maintain a local configuration file or history if run in interactive mode. In the context of the Orchestrator/Worker, it will primarily be used statelessly.

## File System

- **Location**: `/usr/local/bin/gemini` (target path in container)
- **Global Install**: `/usr/local/lib/node_modules/@google/gemini-cli`
