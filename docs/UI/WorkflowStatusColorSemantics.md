# Workflow Status Color Semantics
Status: Active  
Owners: MoonMind Engineering  
Last updated: 2026-06-29

Canonical for: execution status pill color semantics, Workflows List status color grouping, workflow-detail status color grouping, status-aware shimmer hue, and shared dashboard execution-status color rationale.

Related:

- `docs/UI/DashboardDesignSystem.md` — shared dashboard color tokens and overall visual language.
- `docs/UI/EffectShimmerSweep.md` — shared status-pill shimmer sweep behavior, status-derived shimmer hue, and sweep motion profile.
- `docs/UI/WorkflowsListPage.md` — Workflows List page behavior and status filtering.
- `docs/Temporal/VisibilityAndUiQueryModel.md` — canonical `mm_state` values and dashboard compatibility grouping.
- `docs/Workflows/NoCommitStatus.md` — `no_commit` lifecycle and publish-outcome semantics.

---

## 1. Purpose

Workflow status colors are operational signals. They should help operators scan the dashboard by answer category:

1. Did the workflow succeed?
2. Did it fail?
3. Is it actively consuming execution capacity?
4. How close is the workflow to active execution?
5. Is it waiting in a state that may stay blocked until another workflow, provider, human, or external system changes state?
6. Did it finish without creating a repository commit?

The color system must preserve the exact existing hues for `completed`, `failed`, and `executing`. New distinctions should use hue differences, not light/dark variants of the same hue.

For non-terminal states, hue communicates **conceptual distance from active execution**:

```text
executing
  -> initializing / planning
  -> scheduled / awaiting_slot
  -> waiting_on_dependencies / awaiting_external
```

The closer a state is to active execution, the closer its hue should sit to the existing executing hue. The furthest waiting states should stay away from red-adjacent colors so they do not read as failure.

When a status pill has shimmer motion, that shimmer must reinforce the same semantic hue as the pill. Shimmer is not a license to reuse the executing hue for every active-looking status.

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
| `running` | Compatibility/live alias | Use existing executing color. |
| `initializing` | No | Use near-execution blue. |
| `planning` | No by current lifecycle semantics | Use near-execution blue. |
| `scheduled` | No | Use pre-execution waiting indigo. |
| `awaiting_slot` | No; blocked before slot assignment | Use pre-execution waiting indigo. |
| `waiting_on_dependencies` | No | Use furthest-from-execution purple. |
| `awaiting_external` | No by default; waiting outside immediate execution | Use furthest-from-execution purple. |
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
| `running` | Running | Live/executing compatibility | `#22D3EE` | `#7DF9FF` | Compatibility/live alias that shares executing treatment. |
| `canceled` | Canceled | Canceled orange | `#F97316` | `#F97316` | Intentional stop; attention-worthy but not failure. |
| `initializing` | Initializing | Near-execution blue | `#2563EB` | `#2563EB` | Closest non-executing state to execution; startup/preparation before active work. |
| `planning` | Planning | Near-execution blue | `#2563EB` | `#2563EB` | Active planning/preparation and conceptually near execution. |
| `scheduled` | Scheduled | Pre-execution indigo | `#6366F1` | `#6366F1` | Deferred or future work; farther from execution than setup/planning. |
| `awaiting_slot` | Awaiting slot | Pre-execution indigo | `#6366F1` | `#6366F1` | Waiting before execution capacity is acquired; same distance family as scheduled. |
| `waiting_on_dependencies` | Awaiting dep | Blocked purple | `#8248F6` | `#8248F6` | Furthest non-terminal distance from active execution; blocked on prerequisite workflow state. |
| `awaiting_external` | Awaiting external | Blocked purple | `#8248F6` | `#8248F6` | Furthest non-terminal distance from active execution; waiting on an external system, provider, or human. |
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

`no_commit` must not be colored green because that would hide the fact that a publish-mode workflow did not produce a commit or PR. It must not be colored red, orange, or purple because the outcome is not a failure or blocker when the workflow correctly determined no commit was required.

### 5.2 Active execution

`executing` is the live/executing hue. Other states should only use the executing hue when they actively consume the same scarce runtime/provider resource or when they are a compatibility/live alias such as `running`. Do not use the executing hue simply because a workflow is non-terminal or animated.

### 5.3 Distance from execution

`initializing` and `planning` intentionally share blue because they are active pre-execution states and closest to `executing` conceptually. They are near execution, but they do not normally consume provider-profile slots.

`scheduled` and `awaiting_slot` intentionally share indigo because both are pre-execution waiting states. Work has not begun, but the workflow is still expected to progress once time or capacity becomes available.

`waiting_on_dependencies` and `awaiting_external` intentionally share purple because they are the furthest non-terminal states from active execution. They are blocked outside the current execution loop and may remain stuck until a dependency, provider, human, or external system changes state. Purple keeps these states distinct without making them feel like red failure states.

`finalizing` uses slate because it is neither active execution nor terminal success/failure. It should feel calm and transitional.

### 5.4 Motion and shimmer hue

Motion eligibility is separate from color grouping. A status may shimmer because it communicates active transition, but its shimmer must still derive from the exact status pill hue.

Current shimmer-hue expectations:

| Status | Shimmer eligibility | Shimmer hue rule |
| --- | --- | --- |
| `executing` | On | Use live/executing cyan, matching the pill. |
| `running` | On | Use live/executing cyan, matching the compatibility/live pill. |
| `initializing` | On | Use near-execution blue, matching the pill. |
| `planning` | On | Use near-execution blue, matching the pill. |
| `finalizing` | On | Use finalization slate, matching the pill. |
| `scheduled` / `awaiting_slot` | Off | No shimmer. |
| `waiting_on_dependencies` / `awaiting_external` | Off | No shimmer. |
| Terminal statuses | Off | No shimmer. |

The shimmer implementation should use shared CSS variables or `currentColor`-derived tokens so the existing fill, border, and text masks all inherit the same status-aware hue. Do not copy the executing shimmer into separate per-status selector blocks, and do not force `planning`, `initializing`, or `finalizing` through the executing/cyan hue.

The shimmer sweep angle should remain subtle. A small horizontal-bias refinement is acceptable; the effect should not become a scanner beam, loading bar, warning pulse, or decorative rainbow.

---

## 6. Implementation guidance

1. Keep `completed`, `failed`, and `executing` wired to their existing token values.
2. Add or preserve explicit classes or token aliases for `status-canceled`, `status-scheduled`, `status-awaiting-slot`, `status-awaiting-dependencies`, `status-awaiting-external`, `status-initializing`, `status-planning`, `status-finalizing`, and `status-no-commit` rather than forcing all states through broad `running`, `queued`, or `waiting` classes.
3. Preserve shimmer/motion only for states where motion communicates active work or active transition.
4. When shimmer is enabled, derive the shimmer hue from the exact status pill hue. `planning` and `initializing` shimmer blue; `finalizing` shimmers slate; only `executing` and `running` shimmer cyan.
5. Keep shimmer implemented as one shared effect: one selector contract, one moving light field, one keyframe path, and status-derived hue inputs. Avoid duplicated per-status shimmer implementations.
6. If the implementation keeps compatibility classes such as `status-running` or `status-queued`, those classes are grouping helpers only. Exact `mm_state` should win for final pill color.
7. Status filters should show the same pill colors used by table rows and detail headers.
8. Do not introduce amber or cyan for new status assignments. The existing executing color is grandfathered because it is the current `executing` hue.
