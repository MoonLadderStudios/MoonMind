/**
 * Runtime-provider target catalog for the create/authoring surfaces.
 *
 * Source issue: MoonLadderStudios/MoonMind#3833.
 *
 * The server owns the versioned rollout policy and publishes the resolved
 * target catalog in `dashboardConfig.system.runtimeTargetCatalog`. This module
 * is the only place the dashboard derives runtime and Harness labels, Backend
 * choices, and the offline default. No component reconstructs a runtime
 * default or a display-name map of its own.
 */

/** Documented offline fallback for `system.runtimeTargetCatalog.defaultRuntimeId`. */
export const DEFAULT_RUNTIME_ID_FALLBACK = 'omnigent';

export type RuntimeTargetPathClass =
  | 'generic_omnigent'
  | 'legacy_profile_bound_omnigent'
  | 'direct_compatibility';

export type RuntimeTargetRolloutState =
  | 'disabled'
  | 'explicit_only'
  | 'canary'
  | 'preferred'
  | 'new_work_default'
  | 'direct_compatibility_only'
  | 'retired_for_new_work';

export interface RuntimeTarget {
  targetId: string;
  label: string;
  runtimeId: string;
  harnessId?: string | null;
  executionRealizerRef?: string | null;
  pathClass: RuntimeTargetPathClass;
  rolloutState: RuntimeTargetRolloutState;
  rolloutGeneration: number;
  policyVersion: string;
  policyGeneration: number;
  defaultEligible: boolean;
  explicitSelectionAllowed: boolean;
  compatibilityPath: boolean;
  description?: string;
}

export interface RuntimeTargetCatalog {
  policyVersion?: string;
  policyGeneration?: number;
  defaultRuntimeId?: string;
  targets?: RuntimeTarget[];
}

/**
 * Human-friendly display names for raw runtime ids. These are *product* names
 * for a canonical runtime id, never a top-level runtime identity of their own:
 * the submitted identity for every Omnigent-backed target stays `omnigent`.
 */
const RUNTIME_ID_LABELS: Record<string, string> = {
  claude_code: 'Claude Code',
  claude: 'Claude Code',
  codex_cli: 'Codex CLI',
  codex: 'Codex',
  openclaw: 'OpenClaw',
  universal: 'Universal',
  omnigent: 'Omnigent',
  opencode: 'OpenCode',
};

function titleCaseRuntimeId(runtimeId: string): string {
  const formatted = runtimeId
    .split(/[_\s-]+/)
    .filter(Boolean)
    .map((word) =>
      word.toLowerCase() === 'cli'
        ? 'CLI'
        : word.charAt(0).toUpperCase() + word.slice(1),
    )
    .join(' ');
  return formatted || runtimeId;
}

export function catalogTargets(
  catalog: RuntimeTargetCatalog | null | undefined,
): RuntimeTarget[] {
  const targets = catalog?.targets;
  return Array.isArray(targets) ? targets : [];
}

export function targetsForRuntime(
  catalog: RuntimeTargetCatalog | null | undefined,
  runtimeId: string,
): RuntimeTarget[] {
  return catalogTargets(catalog).filter(
    (target) => target.runtimeId === runtimeId,
  );
}

/** Runtime labels describe families; migration rows never rename Omnigent. */
export function formatRuntimeLabel(
  runtimeId: string,
  catalog?: RuntimeTargetCatalog | null,
): string {
  const direct = targetsForRuntime(catalog, runtimeId).find(
    (target) => target.pathClass === 'direct_compatibility',
  );
  return (
    direct?.label || RUNTIME_ID_LABELS[runtimeId] || titleCaseRuntimeId(runtimeId)
  );
}

/**
 * Harness labels name the agent implementation behind a Provider Profile's
 * canonical runtime id (Codex CLI, Claude Code, OpenCode). Unlike
 * `formatRuntimeLabel`, a direct compatibility target never renames the
 * Harness, because the same Harness may also run on Omnigent.
 */
export function formatHarnessLabel(runtimeId: string): string {
  return RUNTIME_ID_LABELS[runtimeId] || titleCaseRuntimeId(runtimeId);
}

export interface BackendOption {
  runtimeId: string;
  label: string;
}

/**
 * Backend choices for one Harness. Omnigent is the normal backend whenever the
 * Harness has an Omnigent route; a direct compatibility backend is offered only
 * while the server catalog publishes an explicitly selectable target for the
 * Harness's runtime id. Callers render a Backend selector only when more than
 * one choice exists or the current Backend matches none of them (so it can be
 * repaired); no frontend list keeps Direct alive on its own.
 */
export function backendOptionsForHarness(
  harnessRuntimeId: string,
  catalog: RuntimeTargetCatalog | null | undefined,
  { omnigentRoute }: { omnigentRoute: boolean },
): BackendOption[] {
  const options: BackendOption[] = [];
  if (omnigentRoute) {
    options.push({ runtimeId: 'omnigent', label: formatRuntimeLabel('omnigent', catalog) });
  }
  const direct = targetsForRuntime(catalog, harnessRuntimeId).find(
    (target) =>
      target.pathClass === 'direct_compatibility' &&
      target.explicitSelectionAllowed,
  );
  if (harnessRuntimeId && direct) {
    options.push({ runtimeId: harnessRuntimeId, label: direct.label });
  }
  return options;
}

const ROLLOUT_STATE_LABELS: Record<RuntimeTargetRolloutState, string> = {
  disabled: 'Unavailable',
  explicit_only: 'Explicit selection only',
  canary: 'Canary',
  preferred: 'Preferred',
  new_work_default: 'Default for new work',
  direct_compatibility_only: 'Compatibility path',
  retired_for_new_work: 'Retired for new work',
};

export function formatRolloutStateLabel(
  state: RuntimeTargetRolloutState | null | undefined,
): string {
  if (!state) {
    return '';
  }
  return ROLLOUT_STATE_LABELS[state] || state;
}

/**
 * Omnigent targets the catalog publishes for one Harness (`codex-native`).
 * Empty when no Harness is known or the catalog does not list it, so server
 * admission stays the authority for that combination.
 */
function omnigentTargetsForHarness(
  catalog: RuntimeTargetCatalog | null | undefined,
  harnessId: string | null | undefined,
): RuntimeTarget[] {
  if (!harnessId) return [];
  return targetsForRuntime(catalog, 'omnigent').filter(
    (target) => target.harnessId === harnessId,
  );
}

/** Whether new work may select Omnigent for this Harness. */
export function omnigentAllowedForHarness(
  catalog: RuntimeTargetCatalog | null | undefined,
  harnessId: string | null | undefined,
): boolean {
  const targets = omnigentTargetsForHarness(catalog, harnessId);
  return targets.length === 0 || targets.some((target) => target.explicitSelectionAllowed);
}

/**
 * Return the exact reason a runtime id cannot be selected, or `null` when it
 * can. The caller shows this instead of quietly choosing another runtime.
 * For Omnigent, `harnessId` narrows the check to that Harness's own targets,
 * because one Harness can be allowed through Omnigent while another is not.
 */
export function runtimeUnavailableReason(
  runtimeId: string,
  catalog?: RuntimeTargetCatalog | null,
  harnessId?: string | null,
): string | null {
  if (!Object.prototype.hasOwnProperty.call(RUNTIME_ID_LABELS, runtimeId)) {
    return `Unsupported Harness ${runtimeId}. Explicitly choose a supported replacement; your draft content is preserved.`;
  }
  const harnessTargets =
    runtimeId === 'omnigent' ? omnigentTargetsForHarness(catalog, harnessId) : [];
  const registered = harnessTargets.length > 0
    ? harnessTargets
    : targetsForRuntime(catalog, runtimeId);
  if (registered.length === 0) {
    return null;
  }
  if (registered.some((target) => target.explicitSelectionAllowed)) {
    return null;
  }
  const states = Array.from(
    new Set(registered.map((target) => target.rolloutState)),
  );
  const detail = states.map(formatRolloutStateLabel).join(', ');
  return `No qualified target is available for ${formatRuntimeLabel(
    runtimeId,
    catalog,
  )} (${detail}). Review Harness availability and the Profile configuration in Settings.`;
}

/**
 * Resolve the server-owned default runtime id for new work.
 *
 * `configuredCandidates` comes from `build_runtime_config`, which already
 * resolved the rollout-policy default *and* any per-request user/workspace
 * override through the shared selection boundary — so it wins. The catalog's
 * `defaultRuntimeId` is the client-side fallback when a payload predates that
 * field, and `DEFAULT_RUNTIME_ID_FALLBACK` is the last documented resort.
 */
export function resolveDefaultRuntimeId(
  configuredCandidates: ReadonlyArray<string | null | undefined>,
  catalog?: RuntimeTargetCatalog | null,
): string {
  const candidates = [...configuredCandidates, catalog?.defaultRuntimeId];
  const resolved = candidates.find(
    (value) => typeof value === 'string' && value.trim().length > 0,
  );
  return String(resolved || DEFAULT_RUNTIME_ID_FALLBACK);
}
