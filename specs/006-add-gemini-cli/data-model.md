# Data Model: Gemini CLI Configuration

**Feature**: Add Gemini CLI to Docker Environment

## Configuration

The Gemini CLI relies on environment variables for authentication and configuration.

### Environment Variables

| Variable | Description | Required |
|----------|-------------|----------|
| `GOOGLE_API_KEY` | API Key for accessing Google Gemini models. | Yes |
| `GEMINI_API_KEY` | Alternate API Key (tool dependent). | Optional |

### CLI State

The CLI is stateless for single-turn interactions but may maintain a local configuration file or history if run in interactive mode. In the context of the Orchestrator/Worker, it will primarily be used statelessly.

## File System

- **Location**: `/usr/local/bin/gemini` (target path in container)
- **Global Install**: `/usr/local/lib/node_modules/@google/gemini-cli`
