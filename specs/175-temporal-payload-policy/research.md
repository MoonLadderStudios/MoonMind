# Research: Temporal Payload Policy

## Decision 1: Validate Escape Hatches At Schema Boundaries

- **Decision**: Enforce compact JSON mapping rules in Pydantic models for metadata/provider-summary fields.
- **Rationale**: These models are the closest shared boundary before payloads enter Temporal histories.
- **Rejected**: Scanning workflow history after execution. That would detect bloat too late and would not prevent accidental encoder behavior.

## Decision 2: Preserve Existing Artifact Ref Fields

- **Decision**: Keep summary, checkpoint, diagnostics, output, and provider payload refs as compact strings or typed artifact ref models.
- **Rationale**: Existing models already carry refs; this story closes the gap by blocking large bodies in escape hatches.
- **Rejected**: Redesigning the artifact storage contract. That is explicitly out of scope.

## Decision 3: Keep Compatibility Names Stable

- **Decision**: Do not rename Temporal activity/workflow/message types or public aliases.
- **Rationale**: The source design and constitution require compatibility-sensitive Temporal contracts to preserve public names unless a cutover plan exists.

## Decision 4: Separate Unit And Integration Verification

- **Decision**: Use focused schema tests and the full unit wrapper as required evidence for this schema-boundary story; reserve `./tools/test_integration.sh` for any implementation that changes workflow/activity invocation wiring.
- **Rationale**: The planned change validates payload shape at Pydantic model boundaries and does not introduce new services, persistence, API routes, or compose-backed runtime behavior. Hermetic integration remains the required escalation path if the implementation crosses into Temporal worker binding or activity invocation code.
- **Rejected**: Adding a new integration fixture for pure schema validation. That would add runtime cost without exercising a broader system boundary than the unit/schema tests already cover.
