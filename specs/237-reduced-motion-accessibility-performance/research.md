# Research: Reduced Motion, Accessibility, and Performance Guardrails

## FR-001 / DESIGN-REQ-013 Auto Reduced Motion

Decision: Keep `reducedMotion="auto"` as a media-query driven mode and strengthen it so reduced-motion preferences stop orbital animation, retain only a static primary illuminated segment, and disable optional glow/companion layers first.
Evidence: `frontend/src/styles/mission-control.css` already has a `prefers-reduced-motion: reduce` block for `data-reduced-motion="auto"` that stops animation and applies a fixed rotation.
Rationale: This matches the source requirement without adding runtime media-query JavaScript or new dependencies.
Alternatives considered: Add React `matchMedia` state; rejected because CSS media queries are simpler, deterministic in stylesheet contract tests, and avoid hydration/state complexity.
Test implications: CSS contract unit tests.

## FR-002 / DESIGN-REQ-013 Minimal Reduced Motion

Decision: Treat minimal mode as stricter than auto: no moving beam, glow, or companion layers; active state is the root static border ring with a brighter border token.
Evidence: Current minimal CSS only stops animation and rotates layers, which leaves static beam/glow layers visible.
Rationale: The Jira brief explicitly says minimal has no movement and a slightly brighter static border ring only.
Alternatives considered: Keep a static illuminated wedge for minimal; rejected because that behavior belongs to auto reduced motion, not minimal.
Test implications: CSS contract tests for hidden layers and border token override.

## FR-004 / DESIGN-REQ-014 Accessible Execution Label

Decision: Add a component-level `statusLabel` prop that defaults to `Executing`, renders as visually hidden text only while active, and can be customized or suppressed with `null`.
Evidence: Existing component children can contain visible text, but the reusable effect does not itself guarantee a non-visual execution cue.
Rationale: A default hidden label prevents the visual beam from being the only execution-state signal while preserving caller flexibility.
Alternatives considered: Require every caller to provide visible text; rejected because the component contract should carry a safe accessibility fallback.
Test implications: Component render tests.

## FR-006 / DESIGN-REQ-014 Dense List and Warning Treatment

Decision: Preserve low opacity defaults and add CSS guardrail assertions that the border-beam contract does not introduce red/orange warning pulse terminology or high-luminance warning effects.
Evidence: Current token values use neutral white/silver, accent, and success tokens with opacity variables rather than red/orange pulse names.
Rationale: This story is a contract guardrail, not a new visual theme story.
Alternatives considered: Add a new dense-list prop; rejected because the brief asks for guardrails and modest defaults, not another tuning mode.
Test implications: CSS contract tests.

## FR-007 / DESIGN-REQ-015 Performance Animation Path

Decision: Keep the orbit animation transform-based and verify the keyframes animate `transform: rotate(1turn)` while layer rules avoid layout-triggering animated properties.
Evidence: Existing keyframes use `transform: rotate(1turn)` and layer animation uses `linear infinite`.
Rationale: Transform-based animation is the lowest-risk path for this decorative effect.
Alternatives considered: Animate conic angle variables directly; rejected for this story because the existing transform path already satisfies the source guidance.
Test implications: CSS contract tests.

## FR-008 / DESIGN-REQ-015 Glow Degradation

Decision: Disable glow and dual-phase companion layers in auto reduced-motion mode before disabling the primary static layer; keep normal glow modest outside degraded modes.
Evidence: Existing glow opacity is tokenized and blur is 5px, but auto reduced-motion currently leaves glow visible.
Rationale: The source guidance identifies glow as the first optional layer to remove for lower-power degradation.
Alternatives considered: Disable the primary beam first; rejected because the active cue must remain meaningful.
Test implications: CSS contract tests.

## FR-009 / FR-010 / DESIGN-REQ-016 Non-Goals and Border-Only Preservation

Decision: Reuse existing border-ring mask/content separation and extend non-goal assertions for MM-468 reduced-motion guardrails.
Evidence: Existing tests verify masks, `mask-composite: exclude`, content wrapper separation, and absence of shimmer/spinner/completion wording.
Rationale: The story should strengthen guardrails without changing established geometry.
Alternatives considered: Add screenshot tests; rejected because existing PostCSS/component tests provide deterministic contract evidence.
Test implications: Component-level integration and CSS contract tests.

## FR-011 Traceability

Decision: Add MM-468 and DESIGN-REQ-013, DESIGN-REQ-014, and DESIGN-REQ-015 to the exported traceability object and verification artifact.
Evidence: Current traceability includes MM-465, MM-466, MM-467 and design IDs through DESIGN-REQ-012 plus DESIGN-REQ-016.
Rationale: The Jira key and coverage IDs must remain visible in downstream artifacts and final evidence.
Alternatives considered: Trace only in specs; rejected because the runtime component already exports traceability.
Test implications: Unit test.
