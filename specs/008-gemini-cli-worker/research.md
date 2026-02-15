# Phase 0: Research Findings

**Feature**: Gemini CLI Worker
**Branch**: `008-gemini-cli-worker`
**Date**: 2025-11-30

## Authentication & Configuration

### Decision: Use `GEMINI_API_KEY` and Named Volume
We will use the `GEMINI_API_KEY` environment variable for primary authentication, which supports non-interactive execution. We will also mount a Docker volume (`gemini_auth_volume`) to `~/.gemini` (or the CLI's config directory) to support:
1.  Persistent configuration (e.g., default model settings).
2.  Future OAuth token caching (if switching to User/OAuth auth).
3.  Consistency with the Codex worker architecture.

### Rationale
- **Non-interactive**: `GEMINI_API_KEY` avoids login prompts.
- **Consistency**: Matches the `codex_auth_volume` pattern.
- **Flexibility**: The volume allows switching to OAuth (via `gcloud` or similar) without changing the container architecture.

### Alternatives Considered
- **Env Var Only**: Simpler, but doesn't support future OAuth flows or complex config files without rebuilding containers.
- **File Mounts**: Mounting host directories is brittle across environments; named volumes are preferred in Docker.

## CLI Package

### Decision: `@google/gemini-cli` from Public NPM
We will install `@google/gemini-cli` (or the canonical package name confirmed during implementation) from the public registry.

### Rationale
- **Spec Requirement**: Explicitly requested to use public sources.
- **Availability**: The package is available publicly, removing the need for private registry auth tokens in the build process.

## Task Routing

### Decision: `gemini` Queue
All Gemini-related tasks will be routed to a dedicated `gemini` queue.

### Rationale
- **Isolation**: Prevents blocking main worker threads.
- **Scaling**: Allows scaling Gemini workers independently of general tasks.
