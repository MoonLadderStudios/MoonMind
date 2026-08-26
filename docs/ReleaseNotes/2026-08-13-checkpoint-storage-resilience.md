# Checkpoint storage growth is bounded by default

Managed-runtime workspace retention now defaults to 10 days. The ownership,
terminal-state, active-turn, live-container, path-containment, grace-window, and
bounded-deletion gates remain unchanged; deployments that need a different
window can continue to set
`MOONMIND_MANAGED_RUNTIME_WORKSPACE_RETENTION_DAYS` explicitly.

New checkpoint artifacts keep independent logical identity and retention while
sharing immutable object-store bytes by digest. A workflow also reuses captured
workspace evidence across adjacent checkpoint boundaries until execution can
mutate the workspace. Existing checkpoint objects retain their current lifecycle
and expire normally; no migration deletes historical evidence. Once shared
storage keys exist, the schema downgrade refuses before changing the schema
rather than restoring an invalid uniqueness constraint.

The deployment-approved `tactics-unreal` image source now defaults to
`pullPolicy=if-missing`. The first admitted Unreal job pulls a cold image through
the trusted, coalesced Docker backend. Operators may explicitly set `never` when
their registry policy requires prewarming.
