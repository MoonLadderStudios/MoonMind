# MoonMind Development Guidelines

Auto-generated from all feature plans. Last updated: 2025-11-23

## Active Technologies
- Python 3.11 + Celery (Work Queue), Docker (Containerization), Codex CLI (Tooling) (007-scalable-codex-worker)
- Docker Volume (`codex_auth_volume`), Redis (Message Broker) (007-scalable-codex-worker)
- Python 3.11+ (matches existing `api_service` and workers) (008-gemini-cli-worker)
- Docker named volume (`gemini_auth_volume`) for auth persistence (008-gemini-cli-worker)

- Node.js 20+ (for CLI runtime), Python 3.11 (existing service) + `@google/gemini-cli` (npm package) (006-add-gemini-cli)

## Project Structure

```text
src/
tests/
```

## Commands

- npm install -g @google/gemini-cli
- gemini --version

## Code Style

Node.js 20+ (for CLI runtime), Python 3.11 (existing service): Follow standard conventions

## Recent Changes
- 008-gemini-cli-worker: Added Python 3.11+ (matches existing `api_service` and workers)
- 007-scalable-codex-worker: Added Python 3.11 + Celery (Work Queue), Docker (Containerization), Codex CLI (Tooling)

- 006-add-gemini-cli: Added Node.js 20+ (for CLI runtime), Python 3.11 (existing service) + `@google/gemini-cli` (npm package)

<!-- MANUAL ADDITIONS START -->
<!-- MANUAL ADDITIONS END -->
