# Research: Tailwind Style System Phase 2

## Decision 1: Token format (RGB triplets via CSS variables)
- **Decision**: Represent all MoonMind palette tokens as space-separated RGB triplets (e.g., `--mm-accent: 139 92 246`) to support Tailwind opacity helpers and calc-based fallbacks.
- **Rationale**: Matches the guidance in `docs/TailwindStyleSystem.md`, keeps Tailwind-compatible while also supporting vanilla CSS `rgb(var(--mm-accent) / <alpha>)` syntax.
- **Alternatives Considered**:
  - Hex codes per token: easier to read but breaks Tailwind opacity shorthands.
  - HSL tokens: smoother gradient tweaking but adds conversion overhead in current CSS.

## Decision 2: Gradient layering strategy
- **Decision**: Use three radial gradients (left, right, bottom) layering violet, cyan, and pink accent washes on top of `--mm-bg`.
- **Rationale**: Minimizes paint cost while visually matching the “liquid glass” reference; gradients only cover corners rather than the full viewport, reducing GPU work.
- **Alternatives Considered**:
  - Full-screen linear gradient overlays: looked flatter and risked muddying cards.
  - Canvas/WebGL shader: unnecessary complexity and incompatible with static CSS build.

## Decision 3: Status color mapping
- **Decision**: Map normalized statuses to `--mm-warn`, `--mm-accent-2`, `--mm-accent`, `--mm-ok`, and `--mm-danger` tokens with translucent backgrounds.
- **Rationale**: Maintains quick visual parsing (amber queued, cyan running, violet awaiting action, green success, rose failure) while keeping tokens centralized.
- **Alternatives Considered**:
  - Use Tailwind utility classes inline: would require refactoring `dashboard.js` to emit Tailwind class strings and risks purge issues.
  - Keep previous blue/green palette: breaks MoonMind branding goals.

## Decision 4: Token storage location
- **Decision**: Continue editing `api_service/static/task_dashboard/dashboard.tailwind.css` as the source of truth; compile to `dashboard.css` via npm scripts.
- **Rationale**: Aligns with Phase 1 scaffolding and ensures `dashboard.css` remains generated, preventing manual drift.
- **Alternatives Considered**:
  - Directly editing `dashboard.css`: faster once but error-prone; impossible to adopt Tailwind utilities later.
  - Moving tokens to a separate `tokens.css`: adds another asset and build step without value yet.

## Decision 5: Validation approach
- **Decision**: Rely on `./tools/test_unit.sh` for regression safety plus a structured visual QA checklist (screenshots in light mode) before merging.
- **Rationale**: No new backend logic is introduced, so existing FastAPI route tests suffice; manual visual QA confirms new palette.
- **Alternatives Considered**:
  - Automated visual regression tests: desirable long-term but not part of current scope.
  - Skipping tests entirely: conflicts with runtime scope guard and MoonMind practices.
