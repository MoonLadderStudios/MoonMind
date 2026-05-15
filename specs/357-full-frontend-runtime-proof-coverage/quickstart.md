# Quickstart: Full Frontend Runtime Proof Coverage

## Prerequisites

- Run these steps in the THOR Tactics Unreal workspace, not this MoonMind repository.
- Confirm the workspace contains the THOR `.uproject`, `Source/ThorTactics`, and the TacticsEditor target.
- Use `/Game/Maps/MainMenu` for Tier 3 when present; otherwise record the active frontend entry route used by the project.

## Tier 1: Compile Evidence

1. Run the target project's TacticsEditor compile command.
2. Record:
   - exact command
   - exit code
   - concise output summary
   - key `LogTactics` lines, or a documented reason none are expected during compile

Expected result: Tier 1 passes only when TacticsEditor compiles and the evidence record includes the command and exit code.

## Tier 2: Frontend Automation

1. Run frontend automation that covers:
   - Home startup
   - generated Home navigation
   - Play panel
   - Options panel
   - modal behavior
   - Online Co-op blocking
   - generated selection telemetry
2. Record the exact automation command, exit code, covered flow checklist, and key `LogTactics` lines.

Expected result: Tier 2 passes only when all seven flow areas are covered in the same documented validation run.

## Tier 3: Map or Entry Smoke

1. Launch `/Game/Maps/MainMenu` or the active frontend entry route.
2. Record the exact command, exit code, route, and key `LogTactics` lines proving frontend entry.

Expected result: Tier 3 passes only when the frontend runtime entry succeeds and evidence identifies the route used.

## Fallback Handling

If local Unreal tooling is unavailable:

1. Attempt the target project's Docker fallback validation command.
2. Record the Docker command and exit code.
3. Declare CI-only validation only after the fallback is attempted or explicitly unavailable with a concrete reason.

## PR Evidence Block

Copy the final evidence summary from `contracts/runtime-proof-evidence-contract.md` into the PR description and update this quickstart with the exact commands and results from the implementation run.
