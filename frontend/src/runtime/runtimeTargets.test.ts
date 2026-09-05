import { describe, expect, it } from 'vitest';

import {
  DEFAULT_RUNTIME_ID_FALLBACK,
  formatRolloutStateLabel,
  formatRuntimeLabel,
  preferredTargetForRuntime,
  resolveDefaultRuntimeId,
  runtimeOptionGroups,
  runtimeUnavailableReason,
} from './runtimeTargets';
import type { RuntimeTarget, RuntimeTargetCatalog } from './runtimeTargets';

function target(overrides: Partial<RuntimeTarget>): RuntimeTarget {
  return {
    targetId: 'codex.generic-omnigent',
    label: 'Codex via generic Omnigent',
    runtimeId: 'omnigent',
    harnessId: 'codex-native',
    executionRealizerRef: 'generic-omnigent-host@1',
    pathClass: 'generic_omnigent',
    rolloutState: 'new_work_default',
    rolloutGeneration: 1,
    policyVersion: 'moonmind.omnigent-runtime-provider-rollout/v1',
    policyGeneration: 1,
    defaultEligible: true,
    explicitSelectionAllowed: true,
    compatibilityPath: false,
    ...overrides,
  };
}

const promotedCatalog: RuntimeTargetCatalog = {
  policyVersion: 'moonmind.omnigent-runtime-provider-rollout/v1',
  policyGeneration: 1,
  defaultRuntimeId: 'omnigent',
  targets: [
    target({}),
    target({
      targetId: 'codex.legacy-profile-bound-omnigent',
      label: 'Codex via legacy profile-bound Omnigent',
      executionRealizerRef: 'codex-profile-bound@1',
      pathClass: 'legacy_profile_bound_omnigent',
      rolloutState: 'retired_for_new_work',
      defaultEligible: false,
      explicitSelectionAllowed: false,
      compatibilityPath: true,
    }),
    target({
      targetId: 'codex.direct',
      label: 'Direct Codex compatibility',
      runtimeId: 'codex_cli',
      harnessId: null,
      executionRealizerRef: null,
      pathClass: 'direct_compatibility',
      rolloutState: 'direct_compatibility_only',
      defaultEligible: false,
      compatibilityPath: true,
    }),
    target({
      targetId: 'claude.generic-omnigent',
      label: 'Claude Code via generic Omnigent',
      harnessId: 'claude-native',
      rolloutState: 'disabled',
      defaultEligible: false,
      explicitSelectionAllowed: false,
    }),
    target({
      targetId: 'claude.direct',
      label: 'Direct Claude compatibility',
      runtimeId: 'claude_code',
      harnessId: null,
      executionRealizerRef: null,
      pathClass: 'direct_compatibility',
      rolloutState: 'direct_compatibility_only',
      defaultEligible: false,
      compatibilityPath: true,
    }),
  ],
};

describe('resolveDefaultRuntimeId', () => {
  it('prefers the server-resolved default over the catalog default', () => {
    // `build_runtime_config` already applied the rollout policy plus any
    // per-request override, so its answer is authoritative.
    expect(
      resolveDefaultRuntimeId(['codex_cli', 'claude_code'], promotedCatalog),
    ).toBe('codex_cli');
  });

  it('falls back to the catalog default, then to the documented constant', () => {
    expect(resolveDefaultRuntimeId([undefined, ''], promotedCatalog)).toBe(
      'omnigent',
    );
    expect(resolveDefaultRuntimeId(['', null], undefined)).toBe(
      DEFAULT_RUNTIME_ID_FALLBACK,
    );
  });
});

describe('preferredTargetForRuntime', () => {
  it('selects the promoted generic Omnigent target over compatibility rows', () => {
    const selected = preferredTargetForRuntime(promotedCatalog, 'omnigent');
    expect(selected?.targetId).toBe('codex.generic-omnigent');
    expect(selected?.compatibilityPath).toBe(false);
  });

  it('never offers a target the rollout policy disabled', () => {
    const disabledOnly: RuntimeTargetCatalog = {
      targets: [
        target({
          targetId: 'claude.generic-omnigent',
          runtimeId: 'omnigent',
          rolloutState: 'disabled',
          defaultEligible: false,
          explicitSelectionAllowed: false,
        }),
      ],
    };
    expect(preferredTargetForRuntime(disabledOnly, 'omnigent')).toBeNull();
  });
});

describe('runtimeOptionGroups', () => {
  it('labels direct paths as compatibility options rather than equal defaults', () => {
    const groups = runtimeOptionGroups(
      ['omnigent', 'codex_cli', 'claude_code'],
      promotedCatalog,
    );
    expect(groups.recommended.map((option) => option.runtimeId)).toEqual([
      'omnigent',
    ]);
    expect(groups.compatibility.map((option) => option.runtimeId)).toEqual([
      'codex_cli',
      'claude_code',
    ]);
    expect(groups.compatibility.every((option) => option.compatibilityPath)).toBe(
      true,
    );
    expect(groups.unavailable).toEqual([]);
  });

  it('moves a runtime with no selectable target into unavailable', () => {
    const rolledBack: RuntimeTargetCatalog = {
      targets: [
        target({
          rolloutState: 'disabled',
          defaultEligible: false,
          explicitSelectionAllowed: false,
        }),
      ],
    };
    const groups = runtimeOptionGroups(['omnigent', 'codex_cli'], rolledBack);
    expect(groups.unavailable.map((option) => option.runtimeId)).toEqual([
      'omnigent',
    ]);
    // An unregistered runtime id stays authorable; promotion is policy-owned.
    expect(groups.recommended.map((option) => option.runtimeId)).toEqual([
      'codex_cli',
    ]);
  });

  it('keeps every runtime available when no catalog is published', () => {
    const groups = runtimeOptionGroups(['omnigent', 'codex_cli'], undefined);
    expect(groups.recommended.map((option) => option.runtimeId)).toEqual([
      'omnigent',
      'codex_cli',
    ]);
    expect(groups.unavailable).toEqual([]);
  });
});

describe('formatRuntimeLabel', () => {
  it('uses the explicit target label when one target owns the runtime', () => {
    expect(formatRuntimeLabel('codex_cli', promotedCatalog)).toBe(
      'Direct Codex compatibility',
    );
  });

  it('uses the promoted target label when several targets share a runtime', () => {
    // `omnigent` owns the promoted generic Codex row plus a retired legacy row
    // and a disabled Claude row, so the label is the truthful selected path.
    expect(formatRuntimeLabel('omnigent', promotedCatalog)).toBe(
      'Codex via generic Omnigent',
    );
  });

  it('falls back to the runtime product name when nothing is promoted', () => {
    expect(
      formatRuntimeLabel('omnigent', {
        targets: [
          target({
            rolloutState: 'explicit_only',
            defaultEligible: false,
          }),
          target({
            targetId: 'claude.generic-omnigent',
            label: 'Claude Code via generic Omnigent',
            rolloutState: 'explicit_only',
            defaultEligible: false,
          }),
        ],
      }),
    ).toBe('Omnigent');
  });

  it('falls back to the runtime product name for raw ids', () => {
    expect(formatRuntimeLabel('claude_code', undefined)).toBe('Claude Code');
    expect(formatRuntimeLabel('future_runtime', undefined)).toBe(
      'Future Runtime',
    );
  });
});

describe('runtimeUnavailableReason', () => {
  it('explains the exact reason and offers explicit alternatives', () => {
    const rolledBack: RuntimeTargetCatalog = {
      targets: [
        target({
          rolloutState: 'disabled',
          defaultEligible: false,
          explicitSelectionAllowed: false,
        }),
      ],
    };
    const reason = runtimeUnavailableReason('omnigent', rolledBack);
    expect(reason).toContain('No qualified target is available');
    expect(reason).toContain('Unavailable');
    expect(reason).toContain('Choose an explicitly available target instead.');
  });

  it('returns null while a selectable target exists', () => {
    expect(runtimeUnavailableReason('omnigent', promotedCatalog)).toBeNull();
    expect(runtimeUnavailableReason('omnigent', undefined)).toBeNull();
  });
});

describe('formatRolloutStateLabel', () => {
  it('maps every rollout state to an operator-readable label', () => {
    expect(formatRolloutStateLabel('new_work_default')).toBe(
      'Default for new work',
    );
    expect(formatRolloutStateLabel('direct_compatibility_only')).toBe(
      'Compatibility path',
    );
    expect(formatRolloutStateLabel(null)).toBe('');
  });
});
