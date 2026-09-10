# Validated pr-resolver verdict retried as a provider failure

The terminal-evidence fixture is the recorded resolver result from artifact
`art_01M264P832WTY8ZFRVH9AX88E3`, September 10, 2026 (issues #4221 and #4222).
The provider result is a minimized completed-session input; it deliberately
contains no terminal-contract decision or retry metadata.

The required `reliability-journey-checkpoint-resume` CI job executes
`test_recorded_pr_resolver_verdict_crosses_terminal_authority_without_retry`.
It writes the recorded evidence to an isolated workspace, invokes AgentRun's
production terminal-evidence handoff and Activity handler, round-trips the
serialized result, and evaluates the parent's mapped result. Only the Temporal
SDK transport and patch selection are replaced; terminal validation, activity
routing, projection and retry decisions remain production code. The legacy
patch case retains its old retry decision for in-flight histories.

This is a deterministic boundary replay, not a serialized Temporal event history
or external-provider qualification.
