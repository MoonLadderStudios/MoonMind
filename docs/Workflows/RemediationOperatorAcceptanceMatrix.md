# Remediation Operator Acceptance Matrix

**Status:** Required release gate  
**Document Class:** Operations / Acceptance Contract  
**Issue:** MoonLadderStudios/MoonMind#3512

Automatic or scheduled remediation is fail-closed until one production-shaped run
records a passing evidence artifact for every row below. A unit-test result alone is
not sufficient to open the gate. The gate artifact must identify the immutable action
and approval policy versions, environment, build revision, operator, timestamp, and
the artifact refs produced by each scenario.

| Scenario | Required terminal evidence |
| --- | --- |
| Diagnosis only | Bounded context and summary; no mutation or unrestricted authority |
| Evidence-gated resume | Request, result, fresh before/after evidence, and `verified_resolved` |
| Corrected-instruction Checkpoint Branch | Source checkpoint, fresh branch turn, cumulative head, comparison and promotion state |
| Denied and approval-gated actions | Expiring request, policy snapshot, actor-attributed decision, and no denied side effect |
| Stale target, approval, or lock | Rejected binding with changed run/checkpoint/host/session/credential/policy evidence |
| Interrupt, cancel, and cleanup | Action-linked result, verification, lock/lease release, and janitor outcome |
| Unsuccessful repair and escalation | `verified_no_change`, `still_failed`, or `regressed`, cooldown, bounded escalation |
| Cumulative multi-attempt repair | Changing evidence signature and workspace head across attempts |
| Prevention change | Reviewable branch/PR plus separate prevention verification; target outcome unchanged |
| Missing historical evidence | `evidence_unavailable` and bounded degraded-mode completion |
| Cancellation and worker restart | Replay-safe outcome during diagnosis, action, and verification phases |

Every row must also prove that the remediation runtime received no raw host shell,
Docker daemon, SQL, storage-key, secret-read, or redaction-bypass authority. Audit
evidence must include target/run/step or checkpoint identity, action risk and policy
decision, idempotency key, actor, approval, lock, before/after refs, verification
outcome, cleanup, and remaining operator work.

The admission service rejects non-manual `admin_auto` remediation while this gate is
closed. A future gate-opening change must add a versioned, content-addressed matrix
result contract and verify it before submission; documentation or a configuration
boolean is not sufficient evidence.
