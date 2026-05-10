# Managed Runtime Isolation Contract

## Purpose

Define the runtime boundary that prevents MoonMind-launched managed agent sessions from reaching external services or publish surfaces outside the resolved MoonMind skill contract.

## Launch Request Inputs

Required fields:
- `runtime`: managed runtime adapter identifier.
- `workflow_id` and `run_id`: execution identity.
- `resolvedSkillsetRef`: immutable resolved skillset reference.
- `selectedSkill`: selected skill when applicable.
- `surfaceContractRef` or inline compact surface contract metadata.
- `serviceIdentityRef`: non-secret MoonMind-managed service identity reference.

Forbidden fields for managed agent sessions:
- Raw tokens, cookies, authorization headers, private keys, or tokenized repository URLs.
- Operator-account OAuth credentials or account-level connector grants.
- Undeclared MCP server identifiers, connector identifiers, or external destination rules.
- In-agent publish credentials or writable publish remotes for branch/PR mutation.

## Validation Behavior

1. Resolve and verify the selected active skill snapshot before runtime startup.
2. Load the closed surface contract from the resolved skill metadata.
3. Compare requested tools, MCPs, connectors, egress rules, and publish authority with the contract.
4. Reject startup when any requested surface is undeclared or when operator identity is present.
5. Materialize only declared surfaces into runtime-specific launch options.
6. Emit a sanitized `IsolationDiagnostic` for every rejection.

## Runtime Denial Behavior

For in-session attempts discovered after launch:
- Non-contract egress attempts fail closed at the mediation boundary.
- Direct publish attempts have no usable credentials or writable remote authority.
- Denials produce sanitized diagnostics without exposing secrets.
- Denials must not mutate external Jira, GitHub, or other provider state.

## Compatibility and Cutover

MoonMind is pre-release, so internal compatibility aliases should not be introduced. If existing internal payloads cannot carry the required contract fields safely, cut over the managed runtime launch path in one cohesive change and update callers/tests/docs in the same change. Temporal-facing payload shape changes must preserve worker-bound invocation compatibility for in-flight runs or document an explicit cutover.
