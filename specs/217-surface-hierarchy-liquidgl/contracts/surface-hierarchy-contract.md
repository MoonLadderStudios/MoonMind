# Contract: Mission Control Surface Hierarchy

## Stable Selectors

The shared stylesheet must expose these reusable selectors:

- `.panel--data` and `.data-table-slab` for matte data slabs
- `.panel--satin` for satin form/editing surfaces
- `.panel--controls`, `.panel--floating`, and `.panel--utility` for glass control surfaces
- `.surface--liquidgl-hero` and `.queue-floating-bar--liquid-glass` for explicit liquidGL hero targets
- `.surface--accent-live` for active/live accent surfaces
- `.surface--nested-dense` for quieter nested dense panels/cards

## Fallback Contract

- Glass control surfaces use shared glass tokens, 1px borders, elevation tokens, and backdrop blur/saturation when supported.
- Unsupported backdrop filtering falls back to near-opaque token-based fills.
- liquidGL target selectors remain fully laid out and styled before initialization.
- Default `.panel` and `.card` selectors are not liquidGL targets.

## Non-Goals

- No backend API changes.
- No task submission payload changes.
- No automatic liquidGL application to all panels/cards.
- No route-specific redesign outside the shared surface contract.
