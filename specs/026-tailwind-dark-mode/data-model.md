# Data Model: Tailwind Style System Phase 3 Dark Mode

## ThemePreference
- **Purpose**: Represents the operator-selected theme override state used by the dashboard runtime.
- **Type**: Client-side preference value.
- **Allowed Values**:
  - `light`
  - `dark`
  - `unset` (implicit when storage key is missing)
- **Validation Rules**:
  - Unknown stored values are treated as `unset`.
  - Explicit values (`light`/`dark`) must override system preference.
- **Transitions**:
  - `unset -> light|dark` when user toggles from default-follow state.
  - `light <-> dark` when user toggles.
  - `light|dark -> unset` only if preference is explicitly cleared.

## SystemThemeSignal
- **Purpose**: Captures runtime operating-system color-scheme preference used when `ThemePreference` is `unset`.
- **Type**: Browser media-query signal.
- **Allowed Values**:
  - `light`
  - `dark`
- **Validation Rules**:
  - Runtime change events are applied only while user preference remains unset.

## ResolvedThemeState
- **Purpose**: The effective dashboard theme applied to the document.
- **Fields**:
  - `mode`: `light` | `dark`
  - `source`: `user` | `system`
- **Resolution Rule**:
  - If `ThemePreference` is explicit, `mode` derives from it and `source=user`.
  - Otherwise, `mode` derives from `SystemThemeSignal` and `source=system`.

## ThemeTokenSet
- **Purpose**: Semantic visual tokens consumed by all dashboard surfaces.
- **Fields**:
  - Base surfaces: `--mm-bg`, `--mm-panel`, `--mm-ink`, `--mm-muted`, `--mm-border`
  - Brand accents: `--mm-accent`, `--mm-accent-2`, `--mm-accent-warm`
  - Status accents: `--mm-ok`, `--mm-warn`, `--mm-danger`
  - Depth: `--mm-shadow`
- **Relationships**:
  - `:root` token set powers light mode.
  - `.dark` token set overrides the same fields for dark mode.
  - `ResolvedThemeState.mode` determines whether `.dark` overrides are active.

## ThemeControl
- **Purpose**: Dashboard shell control that allows operators to change theme mode.
- **Fields**:
  - `control_id`: semantic selector (e.g., `.theme-toggle`)
  - `action`: toggle light/dark preference
  - `label`: user-visible/accessible theme intent text
- **Validation Rules**:
  - Must be available on every dashboard route shell.
  - Must update both active theme class and persisted preference.

## ReadabilitySurface
- **Purpose**: High-priority UI regions requiring explicit dark-mode readability validation.
- **Entities**:
  - `TableSurface` (`table`, `th`, `td`, row states)
  - `FormSurface` (`input`, `select`, `textarea`, focus states)
  - `LiveOutputSurface` (`.queue-live-output`)
- **Validation Rules**:
  - Foreground/background contrast must remain readable for routine operations.
  - Focus and interaction cues must stay visible in dark mode.

## ThemeBootstrap
- **Purpose**: Pre-render initialization behavior that applies theme before paint.
- **Fields**:
  - `execution_phase`: HTML head, before deferred dashboard runtime
  - `inputs`: persisted preference + system preference
  - `output`: initial document theme class and optional data attribute
- **Validation Rules**:
  - Must run before first visual frame using dashboard stylesheet.
  - Must produce the same resolved mode as runtime initialization logic.
