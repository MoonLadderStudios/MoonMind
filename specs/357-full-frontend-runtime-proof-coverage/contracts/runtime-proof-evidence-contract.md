# Runtime Proof Evidence Contract

## Purpose

The THOR-406 runtime proof workflow must emit deterministic reviewer-facing evidence for compile, automation, and runtime smoke validation.

## Evidence Summary Shape

Each validation run must produce a PR-ready summary with this information:

```text
Frontend Runtime Proof Coverage

Tier 1 - TacticsEditor Compile
- Command: <exact command>
- Exit code: <integer>
- Status: passed | failed | blocked | ci_only
- Key LogTactics lines:
  - <line or documented absence>

Tier 2 - Frontend Automation
- Command: <exact command>
- Exit code: <integer>
- Status: passed | failed | blocked | ci_only
- Covered flows:
  - Home startup: covered | missing
  - Generated Home navigation: covered | missing
  - Play panel: covered | missing
  - Options panel: covered | missing
  - Modal behavior: covered | missing
  - Online Co-op blocking: covered | missing
  - Generated selection telemetry: covered | missing
- Key LogTactics lines:
  - <line or documented absence>

Tier 3 - Entry Smoke
- Route: /Game/Maps/MainMenu | <active frontend entry route>
- Command: <exact command>
- Exit code: <integer>
- Status: passed | failed | blocked | ci_only
- Key LogTactics lines:
  - <line or documented absence>

Fallback
- Local tooling unavailable: yes | no
- Docker fallback attempted: yes | no
- CI-only reason: <reason when applicable>
```

## Required Behavior

- A tier cannot be marked `passed` without an exact command and exit code.
- Tier 2 cannot be marked `passed` unless every required flow is marked `covered`.
- A run cannot be marked `ci_only` unless Docker fallback was attempted or explicitly unavailable with a recorded reason.
- The summary must be suitable for direct inclusion in a PR description without full raw logs.
