# Feature Specification: Reduced Motion, Accessibility, and Performance Guardrails

**Feature Branch**: `237-reduced-motion-accessibility-performance`  
**Created**: 2026-04-22  
**Status**: Draft  
**Input**:

```text
Use the Jira preset brief for MM-468 as the canonical Moon Spec orchestration input.

Additional constraints:

Selected mode: runtime.
Default to runtime mode and only use docs mode when explicitly requested.
If the brief points at an implementation document, treat it as runtime source requirements.
Source design path (optional): .

Classify the input as a single-story feature request, broad technical or declarative design, or existing feature directory.
Inspect existing Moon Spec artifacts and resume from the first incomplete stage instead of regenerating valid later-stage artifacts.
```

**Canonical Jira Brief**: `spec.md` (Input)

## Original Jira Preset Brief

```text
Jira issue: MM-468 from MM project
Summary: Support reduced motion, accessibility, and performance guardrails
Issue type: Story
Current Jira status: In Progress
Jira project key: MM

Use this Jira preset brief as the canonical MoonSpec orchestration input. Preserve the Jira issue key MM-468 in spec artifacts, implementation notes, verification output, commit text, and pull request metadata.

MM-468: Support reduced motion, accessibility, and performance guardrails

Short Name
reduced-motion-accessibility-performance

Source Reference
- Source document: `docs/UI/EffectBorderBeam.md`
- Source title: Masked Conic Border Beam - Declarative Design
- Source sections: Reduced motion behavior, Accessibility and UX constraints, Performance guidance, Acceptance criteria, Non-goals
- Coverage IDs: DESIGN-REQ-013, DESIGN-REQ-014, DESIGN-REQ-015, DESIGN-REQ-016

User Story
As an operator and end user, I need the border beam to respect reduced-motion preferences, remain accessible as a secondary status cue, and avoid expensive animation behavior, so execution state is visible without harming usability or dense-list performance.

Acceptance Criteria
- With reducedMotion auto and user preference enabled, orbital rotation stops and a static illuminated segment or very gentle 2.4-3.2s opacity pulse remains.
- With reducedMotion minimal, there is no movement and the active state is represented by a slightly brighter static border ring only.
- The effect is never the only indicator of execution state and is paired with an accessible label such as Executing.
- The visual treatment avoids strong red/orange pulses that imply warning and keeps average luminance low for dense lists.
- Animation uses transform/rotation or an equivalent composited angle where possible and avoids layout-triggering animation.
- Glow remains modest and is the first optional layer disabled for lower-power degradation.

Requirements
- Respect reducedMotion modes without replacing the beam with rapid pulsing.
- Stop orbital rotation when reducedMotion is auto and the user prefers reduced motion.
- Preserve a meaningful static active state through a static illuminated segment, very gentle opacity pulse, or slightly brighter static border ring depending on reducedMotion mode.
- Keep the effect distinguishable at small sizes without becoming noisy.
- Ensure the effect is decorative status communication only and is paired with a non-visual execution indicator.
- Avoid strong red or orange pulses that imply warning.
- Keep average luminance low enough for dense-list usage.
- Prefer composited transform or angle animation paths where possible.
- Avoid layout-triggering animation.
- Keep glow modest and degrade lower-power behavior by disabling glow before removing the primary active-state cue.
- Keep all non-goals enforced under accessibility and performance modes.

Relevant Implementation Notes
- Use reducedMotion auto to honor user reduced-motion preferences.
- In reducedMotion auto with reduced-motion preference enabled, stop orbit animation and keep a static illuminated segment on one edge or corner, with only an optional gentle 2.4-3.2s opacity pulse.
- In reducedMotion minimal, disable movement entirely and represent active state with a slightly brighter static border ring.
- Do not use rapid pulsing as the reduced-motion replacement.
- Pair the visual effect with accessible text or status semantics such as Executing so the beam is never the only indicator of execution state.
- Preserve content readability and keep the animated beam constrained to the border ring.
- Avoid warning-like red or orange motion treatments for execution state.
- Keep dense-list behavior calm by maintaining low average luminance and modest glow.
- Prefer a single rotating pseudo-element or composited layer for active animation.
- Animate transform/rotation or an equivalent composited angle where possible.
- Avoid animating properties that trigger layout.
- Disable optional glow first when degrading for lower-power devices.
- Preserve MM-468 and coverage IDs DESIGN-REQ-013, DESIGN-REQ-014, DESIGN-REQ-015, and DESIGN-REQ-016 in downstream MoonSpec artifacts and final implementation evidence.

Non-Goals
- No rapid pulsing as a substitute for reduced-motion behavior.
- No full-card shimmer.
- No filling background gradient.
- No spinner icon replacement.
- No completion pulse or success burst.
- No content-area masking beyond the border ring.
- No effect state that makes the beam the only indicator of execution.
- No expensive glow behavior retained when lower-power degradation is needed.

Validation
- Verify reducedMotion auto stops orbital rotation when the user prefers reduced motion.
- Verify reducedMotion auto keeps a static illuminated segment or only a very gentle 2.4-3.2s opacity pulse.
- Verify reducedMotion minimal disables movement and uses a slightly brighter static border ring.
- Verify the execution state has an accessible non-visual indicator such as Executing.
- Verify the visual treatment avoids strong red and orange warning-like pulses.
- Verify dense-list presentation keeps average luminance low and remains distinguishable without becoming noisy.
- Verify animation uses transform/rotation or an equivalent composited angle where possible.
- Verify implementation avoids layout-triggering animation.
- Verify glow remains modest and is the first optional layer disabled during lower-power degradation.
- Verify accessibility and performance modes still enforce the border-only mask and other source non-goals.

Needs Clarification
- None
```

## Classification

- Input type: Single-story feature request.
- Breakdown decision: `moonspec-breakdown` was not run because the Jira preset brief defines one independently testable UI guardrail story.
- Selected mode: Runtime.
- Source design: `docs/UI/EffectBorderBeam.md` is treated as runtime source requirements because the brief describes product behavior, not documentation-only work.
- Resume decision: No existing Moon Spec artifacts for MM-468 were found under `specs/`; specification is the first incomplete stage.

## User Story - Guard Border Beam Motion and Accessibility

**Summary**: As an operator and end user, I want MaskedConicBorderBeam to respect reduced-motion preferences, expose an accessible execution cue, and avoid expensive animation behavior so execution state remains visible without harming usability or dense-list performance.

**Goal**: Product surfaces can use the border-beam effect as a calm secondary execution cue that becomes static under reduced-motion preferences, remains accessible beyond visual motion, and avoids high-cost animation or glow behavior.

**Independent Test**: Render MaskedConicBorderBeam with auto and minimal reduced-motion modes, inspect accessible output and CSS rules, and verify reduced-motion, low-power, non-goal, and performance guardrails preserve a meaningful active state without orbital motion, rapid pulse, content masking, or expensive glow.

**Acceptance Scenarios**:

1. **Given** MaskedConicBorderBeam uses reducedMotion auto and the user prefers reduced motion, **When** the CSS media query applies, **Then** orbital animation stops, one static illuminated border segment remains, and optional glow/companion layers are disabled first.
2. **Given** MaskedConicBorderBeam uses reducedMotion minimal, **When** it renders active, **Then** there is no motion and the active state is represented by a slightly brighter static border ring only.
3. **Given** MaskedConicBorderBeam is active, **When** assistive technology reads the surface, **Then** a non-visual execution label such as Executing is available and decorative beam layers remain hidden from the accessibility tree.
4. **Given** the border-beam CSS is inspected, **When** reduced-motion and performance guardrails are evaluated, **Then** the implementation avoids rapid pulse, warning-like red/orange treatments, layout-triggering animation properties, and expensive glow retention during degraded modes.
5. **Given** the effect is rendered in dense UI contexts, **When** theme/intensity and reduced-motion rules are applied, **Then** the average luminance remains modest and the effect stays distinguishable without becoming noisy.

### Edge Cases

- `reducedMotion="off"` intentionally preserves normal orbit behavior.
- `reducedMotion="auto"` reacts only when the user preference media query matches reduced motion.
- `reducedMotion="minimal"` hides moving beam/glow/companion layers instead of keeping a static glint.
- `glow="medium"` must still degrade by disabling glow before removing the primary active cue under reduced-motion auto.
- Callers may provide a custom accessible status label when the default `Executing` label is not appropriate.
- Inactive surfaces do not expose an active execution label.

## Assumptions

- MM-465, MM-466, and MM-467 established the reusable MaskedConicBorderBeam component, border-only geometry, and tuning controls; this story tightens guardrails on that existing runtime surface.
- A hidden default status label is acceptable as the component-level accessibility fallback, while visible product surfaces may still render their own execution text.
- Lower-power degradation for this focused component is represented by reduced-motion/performance CSS paths that disable optional glow before removing the primary active-state cue.

## Source Design Requirements

| ID | Source | Requirement | Scope | Mapped Requirements |
| --- | --- | --- | --- | --- |
| DESIGN-REQ-013 | `docs/UI/EffectBorderBeam.md` Reduced motion behavior | Auto reduced motion stops orbital rotation and preserves a static illuminated segment or very gentle 2.4-3.2s opacity pulse; minimal mode uses no movement and a slightly brighter static border ring only. | In scope | FR-001, FR-002, FR-003 |
| DESIGN-REQ-014 | `docs/UI/EffectBorderBeam.md` Accessibility and UX constraints | The effect is never the only execution-state indicator, pairs with a label such as Executing, avoids warning-like red/orange pulses, remains calm in dense lists, and stays distinguishable at small sizes. | In scope | FR-004, FR-005, FR-006 |
| DESIGN-REQ-015 | `docs/UI/EffectBorderBeam.md` Performance guidance | The effect prefers a single rotating pseudo-element or composited transform/angle animation, avoids layout-triggering animation, keeps blur/glow modest, and disables glow first for degraded modes. | In scope | FR-007, FR-008 |
| DESIGN-REQ-016 | `docs/UI/EffectBorderBeam.md` Acceptance criteria and Non-goals | Reduced-motion mode yields a static meaningful active state while preserving border-only rendering and excluding full-card shimmer, background fill, spinner replacement, completion pulse, success burst, and content-area masking. | In scope | FR-009, FR-010 |

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: With `reducedMotion="auto"` and a reduced-motion user preference, the system MUST stop orbital rotation while keeping a static illuminated border segment or only a very gentle 2.4-3.2s opacity pulse.
- **FR-002**: With `reducedMotion="minimal"`, the system MUST disable movement and represent active state with a slightly brighter static border ring only.
- **FR-003**: Reduced-motion behavior MUST NOT replace the beam with rapid pulsing.
- **FR-004**: Active MaskedConicBorderBeam output MUST include an accessible non-visual execution label by default and allow callers to customize that label.
- **FR-005**: Decorative beam, glow, and companion layers MUST remain hidden from the accessibility tree.
- **FR-006**: The visual treatment MUST avoid strong red or orange warning-like pulse treatments and keep dense-list luminance modest.
- **FR-007**: Normal active animation MUST use transform/rotation or an equivalent composited angle path and MUST avoid layout-triggering animation properties.
- **FR-008**: Glow MUST remain modest and be the first optional layer disabled under reduced-motion/performance degradation before the primary active cue is removed.
- **FR-009**: Accessibility and performance modes MUST preserve border-only rendering and content readability.
- **FR-010**: Tests or contract evidence MUST continue to reject full-card shimmer, background fill, spinner replacement, completion pulse, success burst, and content-area masking.
- **FR-011**: Moon Spec artifacts, verification evidence, commit text, and pull request metadata for this work MUST preserve Jira issue key MM-468.

### Key Entities

- **MaskedConicBorderBeam Surface**: A reusable visual wrapper whose active border decoration must remain accessible, calm, and performance-safe.
- **Reduced Motion Mode**: `auto`, `off`, or `minimal`; controls whether orbiting animation is allowed, preference-driven, or fully static.
- **Execution Status Label**: The non-visual label that prevents the border beam from being the only execution-state indicator.
- **Degraded Visual Mode**: A lower-motion/lower-power presentation that disables optional glow before removing the primary active-state cue.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: Component tests verify active output includes a default `Executing` status label, supports a custom label, omits the label when inactive, and keeps decorative layers `aria-hidden`.
- **SC-002**: CSS contract tests verify `reducedMotion="auto"` under `prefers-reduced-motion: reduce` stops orbital animation, keeps a static primary segment, and disables glow/companion layers first.
- **SC-003**: CSS contract tests verify `reducedMotion="minimal"` disables motion and hides beam/glow/companion layers while brightening the static border ring.
- **SC-004**: CSS contract tests verify normal orbit animation uses transform-based keyframes with linear infinite animation and no layout-triggering animated properties.
- **SC-005**: CSS contract tests verify warning-like red/orange pulses, rapid pulse replacements, full-card shimmer, spinner replacement, completion pulse, success burst, and content-area masking are absent from the border-beam contract.
- **SC-006**: Component and CSS evidence verifies border-only rendering and content readability remain intact in reduced-motion and degraded modes.
- **SC-007**: MM-468 appears in the spec, plan, tasks, verification evidence, traceability export, and final implementation summary.
