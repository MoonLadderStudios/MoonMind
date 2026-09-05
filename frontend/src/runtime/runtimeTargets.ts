/**
 * Runtime-provider target catalog for the create/authoring surfaces.
 *
 * Source issue: MoonLadderStudios/MoonMind#3833.
 *
 * The server owns the versioned rollout policy and publishes the resolved
 * target catalog in `dashboardConfig.system.runtimeTargetCatalog`. This module
 * is the only place the dashboard derives runtime labels, recommended-vs-
 * compatibility grouping, and the offline default. No component reconstructs a
 * runtime default or a display-name map of its own.
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
  codex_cloud: 'Codex Cloud',
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

export interface RuntimeOption {
  runtimeId: string;
  label: string;
  compatibilityPath: boolean;
  available: boolean;
}

export interface RuntimeOptionGroups {
  recommended: RuntimeOption[];
  compatibility: RuntimeOption[];
  unavailable: RuntimeOption[];
}

/** Group runtime families by direct compatibility and policy availability. */
export function runtimeOptionGroups(
  runtimeIds: readonly string[],
  catalog?: RuntimeTargetCatalog | null,
): RuntimeOptionGroups {
  const groups: RuntimeOptionGroups = {
    recommended: [],
    compatibility: [],
    unavailable: [],
  };
  runtimeIds.forEach((runtimeId) => {
    const registered = targetsForRuntime(catalog, runtimeId);
    const option: RuntimeOption = {
      runtimeId,
      label: formatRuntimeLabel(runtimeId, catalog),
      compatibilityPath: registered.some(
        (row) => row.pathClass === 'direct_compatibility',
      ),
      available: registered.length === 0 ||
        registered.some((row) => row.explicitSelectionAllowed),
    };
    if (!option.available) {
      groups.unavailable.push(option);
      return;
    }
    if (option.compatibilityPath) {
      groups.compatibility.push(option);
      return;
    }
    groups.recommended.push(option);
  });
  return groups;
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
 * Return the exact reason a runtime id cannot be selected, or `null` when it
 * can. The caller shows this instead of quietly choosing another runtime.
 */
export function runtimeUnavailableReason(
  runtimeId: string,
  catalog?: RuntimeTargetCatalog | null,
): string | null {
  const registered = targetsForRuntime(catalog, runtimeId);
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
  )} (${detail}). Review runtime availability and the Profile configuration in Settings.`;
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
