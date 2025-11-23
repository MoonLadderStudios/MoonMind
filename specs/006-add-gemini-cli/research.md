# Research: Gemini CLI Integration

**Feature**: Add Gemini CLI to Docker Environment
**Date**: 2025-11-23

## Decisions

### 1. Tool Selection: `@google/gemini-cli`
**Decision**: Install the official `@google/gemini-cli` package via npm.
**Rationale**: 
- It is the official CLI tool provided by Google (maintained by `google-wombot`).
- It integrates well with the existing Node.js tooling layer in the `api_service` Dockerfile (which already installs `codex-cli` via npm).
- It provides the required natural language processing capabilities out of the box.
**Alternatives Considered**:
- **Python SDK (`google-generativeai`)**: Would require writing and maintaining a custom wrapper script to expose CLI functionality.
- **Unofficial CLIs (e.g., `gemini-chat-cli`)**: Risk of abandonment or lack of feature parity with official API updates.
- **`gcloud` CLI**: Significantly larger footprint and broader scope than needed for just LLM interaction.

### 2. Installation Method: Multi-Stage Docker Build
**Decision**: Use the existing `tooling-builder` stage in `api_service/Dockerfile`.
**Rationale**:
- Matches the established pattern for `codex-cli` and `spec-kit`.
- Keeps the final runtime image smaller by copying only necessary artifacts.
- Allows for fallback "stub" binaries if the npm registry is unreachable during build, ensuring CI resilience.

### 3. Environment Configuration
**Decision**: Pass `GOOGLE_API_KEY` (or `GEMINI_API_KEY`) through to the container.
**Rationale**: Standard authentication method for Google AI tools.

## Outstanding Questions

- **Command Name**: Verify if the binary is exposed as `gemini` or `gemini-cli`. (Assumed `gemini` based on naming conventions, will verify during implementation).
