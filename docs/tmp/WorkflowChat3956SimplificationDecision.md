# Workflow Chat Upstream-Native Simplification Decision (MoonLadderStudios/MoonMind#3956)

Execution note for PR #4085. Canonical target state lives in `docs/UI/WorkflowChatPanel.md`;
this file preserves the issue-specific implementation history moved out of that
canonical page per review feedback.

## Correction to the parent epic

The parent epic's duplication claim is corrected: `WorkflowChatNative` never
reimplemented Omnigent's chat UI. The single ordinary interactive composer is
the provider-maintained application mounted by `WorkflowNativeChatRoute`
(Chat tab) through the server-issued, binding-scoped `chatUrl`; the Debug tab
fallback (`WorkflowChatNative`) is a read-only diagnostic shell plus terminal
workflow actions, not a second composer. No second composer was found on any
reachable supported path, so none was removed; instead the duplicate
fetch/iframe/readiness lifecycle in the fallback shell was deleted and the
canonical behavior stays in `features/workflow-native-chat/` (`chatBindingModel`,
`NativeChatFrame`, `NativeChatUnavailableState`, `useWorkflowChatBinding`).

## Transform inventory

`native_ui.py` and `native_ui_compat.py` transforms were inventoried against the
pinned `omnigent.server.v1` contract and all retained: bootstrap injection,
scoped asset-URL rewriting, document/asset classification, version gating,
security headers, and the route/transport allowlist remain load-bearing for the
supported bundle. Removal criteria for any future deletion: the supported
bundle demonstrably no longer needs the transform, the pinned contract fixture
is updated in the same change, and no production caller remains. The
method/route allowlist, ownership checks, expected-state checks, idempotency,
secret scanning, header filtering, and bounds at the trusted boundary are never
simplification targets.

## Flag clarification

`embedded=1` is the presentation-only flag on the MoonMind-scoped `chatUrl`
(WorkflowChatPanel §4) and is unrelated to the experimental embedded-host
transport in #3955.
