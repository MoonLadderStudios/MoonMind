# Workflow Status Color Semantics

Status: Active  
Owners: MoonMind Engineering  
Last updated: 2026-06-28

Canonical for: execution status pill color semantics, Workflows List status color grouping, workflow-detail status color grouping, and shared dashboard execution-status color rationale.

Related:

- `docs/UI/DashboardDesignSystem.md` — shared dashboard color tokens and overall visual language.
- `docs/UI/WorkflowsListPage.md` — Workflows List page behavior and status filtering.
- `docs/Temporal/VisibilityAndUiQueryModel.md` — canonical `mm_state` values and dashboard compatibility grouping.
- `docs/Workflows/NoCommitStatus.md` — `no_commit` lifecycle and publish-outcome semantics.

---

## 1. Purpose

Workflow status colors are operational signals. They should help operators scan the dashboard by answer category:

1. Did the workflow succeed?
2. Did it fail?
3. Is it actively consuming execution capacity?
4. Is it waiting, and what kind of wait is it?
5. Did it finish without creating a repository commit?

The color system must preserve the exact existing hues for `completed`, `failed`, and `executing`. New distinctions should use hue differences, not light/dark variants of the same hue.

---

## 2. Required existing-color invariants

These states keep their existing dashboard token values. Do not change their hue as part of status-color cleanup.

| Status | Existing token | Light hex | Dark hex | Rule |
| --- | --- | --- | --- | --- |
| `completed` | `--mm-ok` | `#22C55E` | `#4ADE80` | Terminal success stays green. |
| `failed` | `--mm-danger` | `#F43F5E` | `#FB7185` | Terminal failure stays red. |
| `executing` | `--mm-accent-2` | `#22D3EE` | `#7DF9FF` | Active execution stays the existing live/executing color. |

`executing` is the only state in this palette that may continue using the existing cyan-like live accent. Do not introduce additional amber or cyan status assignments for the other states in this model.

---

## 3. Provider-profile slot color rule

Any status that **actively consumes a provider-profile slot** must use the same color as `executing`.

Current interpretation:

| Status | Consumes provider-profile slot? | Color consequence |
| --- | --- | --- |
| `executing` | Yes | Use existing executing color. |
| `scheduled` | No | Use scheduled/waiting color. |
| `awaiting_slot` | No; blocked before slot assignment | Use scheduled/waiting color. |
| `waiting_on_dependencies` | No | Use dependency/external wait color. |
| `awaiting_external` | No by default; waiting outside immediate execution | Use dependency/external wait color. |
| `initializing` | No | Use pre-execution setup color. |
| `planning` | No by current lifecycle semantics | Use pre-execution setup color. |
| `finalizing` | No by normal provider-slot lifecycle; cleanup/final record work after execution | Use finalization color. |
| `no_commit` | No; terminal completed-without-commit outcome | Use no-commit color. |

Rationale: `awaiting_slot` represents a workflow that is waiting for capacity, not one that already owns it. `planning` and `finalizing` are not treated as provider-slot-consuming states unless a future runtime lifecycle explicitly moves slot ownership into those phases. If that future behavior changes, the state should move to the executing color or expose a separate slot-held indicator rather than relying on copy alone.

---

## 4. Status color map

| Status | Display label | Color role | Light hex | Dark hex | Rationale |
| --- | --- | --- | --- | --- | --- |
| `completed` | Completed | Success green | `#22C55E` | `#4ADE80` | Successful terminal outcome. |
| `failed` | Failed | Failure red | `#F43F5E` | `#FB7185` | Terminal error outcome. |
| `executing` | Executing | Live/executing | `#22D3EE` | `#7DF9FF` | Active execution and provider-slot consumption. |
| `canceled` | Canceled | Canceled orange | `#F97316` | `#F97316` | Intentional stop; attention-worthy but not failure. |
| `scheduled` | Scheduled | Queue purple | `#8248F6` | `#8248F6` | Future or deferred work that has not begun. |
| `awaiting_slot` | Awaiting slot | Queue purple | `#8248F6` | `#8248F6` | Same family as scheduled: waiting before execution capacity is acquired. |
| `waiting_on_dependencies` | Awaiting dep | Wait magenta | `#EC4899` | `#EC4899` | Blocked on prerequisite workflow state. |
| `awaiting_external` | Awaiting external | Wait magenta | `#EC4899` | `#EC4899` | Same wait family as dependency blocking: outside immediate MoonMind execution. |
| `initializing` | Initializing | Setup indigo | `#6366F1` | `#6366F1` | Startup/preparation before active execution. |
| `planning` | Planning | Setup indigo | `#6366F1` | `#6366F1` | Active planning/preparation, not provider-slot consumption. |
| `finalizing` | Finalizing | Finalization slate | `#64748B` | `#64748B` | Wrap-up, recording, publish summary, and terminalization work. |
| `no_commit` | No commit | No-commit teal | `#159376` | `#1A997B` | Successful or side-effectful completion with no repository commit/publish artifact. |

---

## 5. Grouping rules

### 5.1 Terminal outcomes

Terminal outcomes should remain visually stable and easy to scan:

- `completed` is green.
- `failed` is red.
- `canceled` is orange.
- `no_commit` is teal and success-adjacent, not green.

`no_commit` must not be colored green because that would hide the fact that a publish-mode workflow did not produce a commit or PR. It must not be colored red, orange, or magenta because the outcome is not a failure or blocker when the workflow correctly determined no commit was required.

### 5.2 Active execution

`executing` is the live/executing hue. Other states should only use the executing hue when they actively consume the same scarce runtime/provider resource. Do not use the executing hue simply because a workflow is non-terminal.

### 5.3 Pre-execution and wait states

`scheduled` and `awaiting_slot` intentionally share purple because both represent pre-execution waiting. The difference is expressed by label and waiting reason, not by color.

`waiting_on_dependencies` and `awaiting_external` intentionally share magenta because both mean progress is blocked outside the current execution loop. The difference is expressed by label, `waitingReason`, and `attentionRequired`.

`initializing` and `planning` intentionally share indigo because both are setup/planning states before active execution.

`finalizing` uses slate because it is neither active execution nor terminal success/failure. It should feel calm and transitional.

---

## 6. Implementation guidance

1. Keep `completed`, `failed`, and `executing` wired to their existing token values.
2. Add explicit classes or token aliases for `status-canceled`, `status-scheduled`, `status-awaiting-slot`, `status-awaiting-dependencies`, `status-awaiting-external`, `status-initializing`, `status-planning`, `status-finalizing`, and `status-no-commit` rather than forcing all states through broad `running`, `queued`, or `waiting` classes.
3. Preserve shimmer/motion only for states where motion communicates actual active work. Do not apply executing shimmer to `scheduled`, `awaiting_slot`, `waiting_on_dependencies`, `awaiting_external`, `finalizing`, or `no_commit`.
4. If the implementation keeps compatibility classes such as `status-running` or `status-queued`, those classes are grouping helpers only. Exact `mm_state` should win for final pill color.
5. Status filters should show the same pill colors used by table rows and detail headers.
6. Do not introduce amber or cyan for new status assignments. The existing executing color is grandfathered because it is the current `executing` hue.
