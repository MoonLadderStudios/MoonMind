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
