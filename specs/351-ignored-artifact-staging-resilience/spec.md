# Feature Specification: Ignored Artifact Staging Resilience

## User Story

As an operator, I want workflow publish finalization to tolerate tracked files that live under gitignored artifact directories, so a completed agent run is not failed only because `git add -A` rejects an ignored parent path.

## Requirements

- **FR-001**: When publishing workspace changes, MoonMind MUST stage tracked modifications and deletions even if their parent directory is ignored by the target repository.
- **FR-002**: MoonMind MUST NOT force-add ignored untracked artifacts into the target repository.
- **FR-003**: MoonMind MUST keep runtime scaffolding exclusions, including `CLAUDE.md`, live log spool files, and projected skill directories.
- **FR-004**: A staging failure for genuine Git errors MUST still surface as a publish failure with an actionable error.

## Acceptance Scenarios

- **SCN-001**: Given a tracked `artifacts/jira-orchestrate-pr.json` is modified under a repository that ignores `artifacts/`, when MoonMind publishes the branch, then the tracked file is staged and committed without requiring a `.gitignore` change.
- **SCN-002**: Given an untracked ignored artifact file exists, when MoonMind publishes the branch, then the ignored untracked file is not force-added.
