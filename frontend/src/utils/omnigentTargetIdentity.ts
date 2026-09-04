/**
 * Truthful Omnigent target identity for authoring surfaces (#3833).
 *
 * Mirrors `moonmind/omnigent/runtime_provider_rollout.py`: friendly labels
 * never create new top-level runtime ids, and direct/legacy paths render as
 * compatibility options once the qualified generic Omnigent target is
 * promoted -- never as equal defaults.
 */

export const OMNIGENT_CANONICAL_RUNTIME_ID = 'external/omnigent';

export const TARGET_IDENTITY_LABELS: Record<string, string> = {
  'codex-native|generic-omnigent-host@1': 'Codex via generic Omnigent',
  'codex-native|codex-profile-bound@1': 'Codex via legacy profile-bound Omnigent',
  'codex-native|direct': 'Direct Codex compatibility',
  'claude-native|generic-omnigent-host@1': 'Claude Code via generic Omnigent',
  'claude-native|direct': 'Direct Claude compatibility',
  'opencode-native|generic-omnigent-host@1': 'OpenCode via generic Omnigent',
};

const DIRECT_COMPAT_RUNTIME: Record<string, string> = {
  'codex-native': 'codex_cli',
  'claude-native': 'claude_code',
};

const DIRECT_RUNTIME_HARNESS: Record<string, string> = {
  codex_cli: 'codex-native',
  claude_code: 'claude-native',
};

export function targetIdentityLabel(harnessId: string, realizerRef: string): string {
  const label = TARGET_IDENTITY_LABELS[`${harnessId}|${realizerRef}`];
  if (!label) throw new Error(`unknown target identity: ${harnessId} via ${realizerRef}`);
  return label;
}

export function isCompatibilityPath(harnessId: string, realizerRef: string): boolean {
  return realizerRef === 'direct' || realizerRef === 'codex-profile-bound@1';
}

export function canonicalRuntimeIdFor(harnessId: string, realizerRef: string): string {
  if (realizerRef === 'direct') {
    const direct = DIRECT_COMPAT_RUNTIME[harnessId];
    if (!direct) throw new Error(`no direct compatibility runtime for ${harnessId}`);
    return direct;
  }
  return OMNIGENT_CANONICAL_RUNTIME_ID;
}

export interface RolloutCombinationView {
  harnessImplementation?: string;
  executionRealizer?: string;
  rolloutState?: string;
}

function harnessFamily(harnessImplementation: string | undefined): string | null {
  const lowered = (harnessImplementation || '').trim().toLowerCase();
  for (const family of ['codex-native', 'claude-native', 'opencode-native']) {
    if (lowered === family || lowered.startsWith(`${family}@`) || lowered.startsWith(`${family}:`)) {
      return family;
    }
  }
  return null;
}

/**
 * Data-driven compatibility suffix for a direct runtime option.
 *
 * Returns ' (compatibility)' when the rollout-status view shows a promoted
 * (`preferred` or `new_work_default`) generic Omnigent row for the same
 * harness family; otherwise ''. Unknown/unavailable status renders no
 * suffix so options never mislabel before operator evidence loads.
 */
export function compatibilitySuffixForRuntime(
  runtimeId: string,
  combinations: RolloutCombinationView[] | undefined | null,
): string {
  const harness = DIRECT_RUNTIME_HARNESS[runtimeId];
  if (!harness || !Array.isArray(combinations)) return '';
  const promoted = combinations.some(
    (entry) =>
      harnessFamily(entry.harnessImplementation) === harness &&
      entry.executionRealizer === 'generic-omnigent-host@1' &&
      (entry.rolloutState === 'preferred' || entry.rolloutState === 'new_work_default'),
  );
  return promoted ? ' (compatibility)' : '';
}
