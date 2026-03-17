# Research: Skills Workflow Alignment Refresh

## Decision 1: Adopt current runtime stage names as the canonical contract

- **Decision**: Replace legacy stage names (`specify`, `plan`, `tasks`, `analyze`, `implement`) with runtime stage task names (`discover_next_phase`, `submit_codex_job`, `apply_and_publish`) across `015` artifacts.
- **Rationale**: Current workers, task payloads, and stage routing all execute on the three runtime task stages.
- **Alternatives considered**:
  - Keep legacy stage terms in `015`: rejected because it creates contract drift with production behavior.

## Decision 2: Surface adapter metadata in workflow automation phase responses

- **Decision**: Include `adapterId` in normalized phase metadata and expose it as `adapter_id` in API responses.
- **Rationale**: Stage-level observability needs both selected skill and selected adapter to diagnose routing issues quickly.
- **Alternatives considered**:
  - Keep adapter data only in raw metadata payloads: rejected because consumers would need custom payload parsing and lose typed API support.

## Decision 3: Preserve compatibility defaults for legacy Agentkit metadata

- **Decision**: For legacy Agentkit phase records missing skill fields, derive defaults (`selectedSkill=agentkit`, `adapterId=agentkit`, `executionPath=skill`).
- **Rationale**: Historical records should remain interpretable without requiring data backfills.
- **Alternatives considered**:
  - Return null for missing fields: rejected because it weakens operator diagnostics on older runs.

## Decision 4: Align shared-skills workspace contracts with run materialization

- **Decision**: Document `.agents/skills` and `.gemini/skills` as links to a single run-scoped `skills_active` directory.
- **Rationale**: Current workers materialize one shared active skill set and expose it to both adapters through workspace links.
- **Alternatives considered**:
  - Keep per-adapter independent skill mirrors as the primary contract: rejected because it no longer matches runtime behavior.

## Decision 5: Keep Agentkit verification conditional on selected/configured stage skills

- **Decision**: Verify Agentkit CLI only for stages that actually resolve to a Agentkit-backed adapter when skills mode is enabled.
- **Rationale**: Startup and per-stage checks should align with selected stage-skill strategy, not assume unconditional Agentkit usage.
- **Alternatives considered**:
  - Keep unconditional Agentkit checks for all runs: rejected because it blocks non-Agentkit stage strategies and misrepresents runtime policy.

## Decision 6: Keep runtime-vs-docs behavior aligned with orchestration mode

- **Decision**: Treat this feature as runtime implementation mode and require production code surfaces plus unit validation via `./tools/test_unit.sh`.
- **Rationale**: The feature input explicitly requires runtime deliverables, so docs-only completion is not acceptable for this scope.
- **Alternatives considered**:
  - Execute as docs-only refresh mode: rejected because it would violate FR-007/FR-008 and leave runtime expectations unverified.
