# Contract: Execution Status Pill Glyph Wave

## Component Contract

`ExecutionStatusPill` accepts:

- `status: string | null | undefined`

It renders:

- Non-executing status: `<span {...executionStatusPillProps(status)}>{visibleLabel}</span>`
- Executing status: parent status `<span>` with existing executing metadata, `aria-label`, and one child `.status-letter-wave[aria-hidden="true"]`

## CSS Contract

- The executing host continues to use `mm-status-pill-shimmer` and `--mm-executing-sweep-cycle-duration`.
- `.status-letter-wave` is the foreground visual layer above the physical sweep.
- `.status-letter-wave__glyph` uses `animation-name: mm-executing-letter-brighten`, `animation-duration: var(--mm-executing-sweep-cycle-duration, 1650ms)`, and `animation-delay: var(--mm-letter-delay, 0ms)`.
- Reduced motion disables glyph animation, text shadow, and filter.

## Task-List Integration Contract

- Table status cell and card status header both render `ExecutionStatusPill`.
- Status source precedence remains `row.rawState || row.state || row.status`.
- Non-executing states do not receive `.status-letter-wave` or `.status-letter-wave__glyph`.
