# Feature Specification: Executing Text Brightening Sweep

**Feature Branch**: `259-executing-text-brightening`  
**Created**: 2026-04-25  
**Status**: Draft  
**Input**:

```text
Add this text brightening sweep to the documentation and codebase:

Treat the existing EXECUTING shimmer as the physical sweep layer, then add a second text-brightening layer that runs on the same timing. Codex does this in a terminal by rendering the status phrase as one styled span per character. For each character, it computes distance from a moving sweep center, uses a cosine falloff to get intensity, then blends the foreground/background colors and emits a styled character span. It also pads the sweep before and after the word so the highlight enters and exits smoothly.

In MoonMind, the browser equivalent should usually not be "rerender React every 32 ms." In the browser, CSS animation is the better default: it is declarative, cheaper for many table rows, and can be synchronized with your existing shimmer duration variable.

Recommended implementation: split only EXECUTING into glyph spans. CSS alone cannot target "the third letter of this text node," so true per-letter brightening requires wrapping each visible grapheme in a span. Because EXECUTING is short, the DOM cost is tiny. Replace the two status spans in frontend/src/entrypoints/tasks-list.tsx with a small ExecutionStatusPill component. Use Intl.Segmenter when available, aria-label on the parent, aria-hidden on the glyph span, CSS-derived animation delays per glyph, the existing --mm-executing-sweep-cycle-duration: 1650ms token, and a reduced-motion override. If the visible beam direction changes, update the CSS --mm-executing-letter-sweep-direction token with the sweep geometry.

The cleanest patch: implement the glyph-span component and keep the existing broad shimmer. Existing status detection remains centralized in executionStatusPillProps(); the existing shimmer remains the background/beam layer; the new glyph spans become the foreground Codex-like letter-brightening layer; the animation stays CSS-driven and phase-locked through --mm-executing-sweep-cycle-duration; accessibility remains clean because the visible glyph spans are hidden from screen readers.
```

## Classification

- Input type: Single-story feature request.
- Breakdown decision: `moonspec-breakdown` was not run because the request selects one independently testable Mission Control UI behavior.
- Selected mode: Runtime.
- Source design: The inline task text is treated as the source requirements for product behavior and validation.

## User Story - Executing Letter Brightening

**Summary**: As a Mission Control user, I want executing task-list status pills to brighten letters in sync with the existing shimmer sweep so active work feels visibly alive without extra polling or layout changes.

**Goal**: The task-list executing pill keeps the existing physical shimmer sweep and adds a foreground, CSS-driven per-letter brightening wave that remains accessible, efficient, and isolated to the executing state.

**Independent Test**: Render task-list rows in executing and non-executing states, then verify only executing pills use per-glyph visual spans with staggered CSS delays, preserve the shared status-pill metadata and accessible label, keep non-executing pills as plain text, and rely on the shared shimmer duration with reduced-motion suppression.

**Acceptance Scenarios**:

1. **Given** a task-list table row or card row is executing, **When** the status pill renders, **Then** the visible label is represented by one hidden visual glyph span containing one styled span per grapheme.
2. **Given** an executing task-list status pill is rendered, **When** the letter wave is active, **Then** each glyph receives a deterministic CSS delay so the brightening wave enters and exits smoothly with edge padding.
3. **Given** the executing status pill renders its glyph layer, **When** assistive technology reads the pill, **Then** it receives the complete status phrase from the parent label rather than individual letters.
4. **Given** a task-list status is not executing, **When** the pill renders, **Then** it keeps the existing plain status text and does not receive glyph-wave markup or executing shimmer metadata.
5. **Given** reduced-motion preference is active, **When** executing pills are visible, **Then** the physical sweep and glyph brightening animations are disabled while the active executing treatment remains recognizable.

### Edge Cases

- The visible status may be blank, null, or whitespace and must fall back to an em dash label.
- The visible status may include spaces or extended graphemes in the future and must not split surrogate pairs or grapheme clusters when platform support exists.
- The visible physical shimmer travels left-to-right/top-left-to-bottom-right, so glyph phase order must match that direction.
- Multiple executing rows may be visible at once; the effect must remain CSS-driven instead of requiring a JavaScript animation loop.

## Assumptions

- This story applies the glyph-span component to the task-list table and card status pills requested by the source input.
- Existing detail and proposal status-pill surfaces can keep the prior shared status-pill plumbing unless a later story expands the glyph effect there.
- The current oversized sweep background uses inverse background-position values, but the visible beam travels left-to-right/top-left-to-bottom-right.

## Source Design Requirements

| ID | Source | Requirement | Scope | Mapped Requirements |
| --- | --- | --- | --- | --- |
| DESIGN-REQ-001 | Input Core idea | Existing executing shimmer remains the physical sweep layer while a foreground text-brightening layer is added. | In scope | FR-001, FR-002 |
| DESIGN-REQ-002 | Input browser guidance | The browser implementation must use CSS animation rather than rerendering React on a timer. | In scope | FR-003 |
| DESIGN-REQ-003 | Input recommended implementation | Executing labels must be split into per-grapheme spans so letters brighten independently. | In scope | FR-004, FR-005 |
| DESIGN-REQ-004 | Input timing guidance | The letter brightening must use the same 1650ms shimmer duration token as the physical sweep. | In scope | FR-006 |
| DESIGN-REQ-005 | Input accessibility notes | Split glyphs must be hidden from assistive technology while the parent exposes the complete label. | In scope | FR-007 |
| DESIGN-REQ-006 | Input reduced-motion notes | Reduced motion must disable non-essential sweep and letter animations. | In scope | FR-008 |
| DESIGN-REQ-007 | Input cleanest patch | Existing status detection remains centralized through `executionStatusPillProps()`. | In scope | FR-009 |
| DESIGN-REQ-008 | Input task-list replacement | The two task-list status spans are replaced with the component without changing data plumbing. | In scope | FR-010 |

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The system MUST keep the existing executing status-pill physical shimmer sweep for task-list executing pills.
- **FR-002**: The system MUST add a foreground text-brightening layer for task-list executing status pills.
- **FR-003**: The text-brightening layer MUST be CSS-driven and MUST NOT use a JavaScript animation loop or periodic React rerender.
- **FR-004**: The executing text-brightening layer MUST render one visual glyph span per visible grapheme.
- **FR-005**: The glyph splitting MUST use platform grapheme segmentation when available and fall back safely when it is not.
- **FR-006**: The glyph brightening animation MUST use `--mm-executing-sweep-cycle-duration` with a 1650ms fallback and per-glyph delays with edge padding.
- **FR-007**: The executing glyph layer MUST be hidden from assistive technology while the parent status pill exposes the full readable label.
- **FR-008**: Reduced-motion styling MUST disable glyph brightening animation, text shadow, and filter effects.
- **FR-009**: Executing status detection MUST continue to use `executionStatusPillProps()` as the central selector contract.
- **FR-010**: The task-list table and card status pills MUST use the new status pill component without changing status source precedence.
- **FR-011**: Non-executing task-list status pills MUST remain plain text without glyph-wave markup or executing shimmer metadata.

### Key Entities

- **Execution Status Pill**: The reusable visual presentation for a task status label.
- **Glyph Wave**: The foreground layer of per-grapheme spans that brightens letters in sequence.
- **Physical Sweep**: The existing executing shimmer background treatment on the status pill host.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: Tests verify exactly the task-list executing table and card pills receive glyph-wave markup while non-executing pills do not.
- **SC-002**: Tests verify executing glyphs expose per-glyph index/count values used by CSS delay calculation and preserve full visible text.
- **SC-003**: Tests verify the CSS includes glyph brightening keyframes, shared-duration animation, and reduced-motion suppression.
- **SC-004**: Typecheck and lint pass for the new component and task-list integration.
- **SC-005**: The final verification report maps all in-scope `DESIGN-REQ-*` items to implementation and test evidence.
