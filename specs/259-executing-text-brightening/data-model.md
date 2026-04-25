# Data Model: Executing Text Brightening Sweep

## Execution Status Pill

- **status input**: nullable task status/state string from the existing task-list row precedence.
- **visible label**: trimmed status phrase with whitespace collapsed, falling back to an em dash when empty.
- **state metadata**: class and data attributes returned by `executionStatusPillProps()`.
- **accessibility label**: complete visible label exposed on executing pill parents.

Validation rules:

- Non-executing status inputs render as plain text inside the status pill.
- Executing status inputs render the complete accessible label once on the parent and visual glyphs under an `aria-hidden` wrapper.
- Existing status source precedence remains `row.rawState || row.state || row.status`.

## Glyph Wave

- **glyphs**: visible label split into graphemes.
- **phase index**: glyph order adjusted to match the current right-to-left sweep direction.
- **delay**: millisecond CSS custom property derived from `(phaseIndex + edgePadding) / (glyphCount + edgePadding * 2) * 1650`.

State transitions:

- Non-executing to executing: plain label is replaced with visual glyph spans and executing shimmer metadata.
- Executing to non-executing: glyph spans and executing shimmer metadata are removed.
- Reduced motion: host and glyph animations are disabled by CSS while the executing active treatment remains present.
