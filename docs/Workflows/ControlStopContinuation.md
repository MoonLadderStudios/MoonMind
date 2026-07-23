# Control-stop remediation continuation

Control-stop continuation is a deployment-gated recovery path for a terminal
`workflow_gate` whose authoritative verifier returned
`ADDITIONAL_WORK_NEEDED`. It does not change the source workflow outcome or
fabricate a failed Step Execution.

The deployment generation and rollout policy are frozen before destination
creation. `shadow` mode admits no continuation. `canary` mode admits only owners
and Provider Profiles in the frozen allowlists. `enabled` mode still requires a
promoted generation, supported `external/omnigent` + `codex-native` +
`omnigent-host-codex` lane, complete source evidence, and an explicit bounded
budget grant.

Rollback is performed by promoting a new generation whose frozen policy is
`shadow`, or by marking the current generation unpromoted. This prevents new
destinations before mutation. Existing destination workflows retain their
frozen contract and remain readable; rollback must not rewrite their history or
the immutable source outcome.

Operators should monitor admission rejection reasons, restore failures,
duplicate requests reconciled to an existing destination, remediation
convergence and renewed control stops, and side-effect blocks. Alert payloads,
workflow projections, artifacts, and UI responses must contain Provider Profile
identifiers and evidence references only, never OAuth credentials.
