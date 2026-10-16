import { describe, expect, it } from 'vitest';

import {
  DEFAULT_RUNTIME_ID_FALLBACK,
  backendOptionsForHarness,
  formatHarnessLabel,
  formatRolloutStateLabel,
  formatRuntimeLabel,
  omnigentAllowedForHarness,
  resolveDefaultRuntimeId,
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

describe('backendOptionsForHarness', () => {
  it('keeps the supported legacy Jules identity available for restored drafts', () => {
    expect(backendOptionsForHarness('jules_api', undefined, { omnigentRoute: false })).toEqual([]);
    expect(runtimeUnavailableReason('jules_api')).toBeNull();
    expect(formatRuntimeLabel('jules_api')).toBe('Jules');
    expect(formatHarnessLabel('jules_api')).toBe('Jules');
  });

  it('offers a direct Backend only beside Omnigent for its own Harness', () => {
    expect(
      backendOptionsForHarness('codex_cli', promotedCatalog, { omnigentRoute: true }),
    ).toEqual([
      { runtimeId: 'omnigent', label: 'Omnigent' },
      { runtimeId: 'codex_cli', label: 'Direct Codex compatibility' },
    ]);
    expect(
      backendOptionsForHarness('opencode', promotedCatalog, { omnigentRoute: true }),
    ).toEqual([{ runtimeId: 'omnigent', label: 'Omnigent' }]);
  });

  it('omits a direct Backend the catalog does not publish as selectable', () => {
    const rolledBack: RuntimeTargetCatalog = {
      targets: (promotedCatalog.targets || []).map((row) =>
        row.pathClass === 'direct_compatibility'
          ? { ...row, explicitSelectionAllowed: false }
          : row,
      ),
    };
    expect(
      backendOptionsForHarness('codex_cli', rolledBack, { omnigentRoute: true }),
    ).toEqual([{ runtimeId: 'omnigent', label: 'Omnigent' }]);
    expect(
      backendOptionsForHarness('codex_cli', undefined, { omnigentRoute: true }),
    ).toEqual([{ runtimeId: 'omnigent', label: 'Omnigent' }]);
  });
});

describe('formatHarnessLabel', () => {
  it('names the Harness without a direct target relabeling it', () => {
    expect(formatHarnessLabel('codex_cli')).toBe('Codex CLI');
    expect(formatHarnessLabel('claude_code')).toBe('Claude Code');
    expect(formatHarnessLabel('opencode')).toBe('OpenCode');
    expect(formatHarnessLabel('future_harness')).toBe('Future Harness');
  });
});

describe('formatRuntimeLabel', () => {
  it('uses the explicit target label when one target owns the runtime', () => {
    expect(formatRuntimeLabel('codex_cli', promotedCatalog)).toBe(
      'Direct Codex compatibility',
    );
  });

  it('keeps the runtime family label when several targets share a runtime', () => {
    // Migration state cannot rename an entire runtime family.
    expect(formatRuntimeLabel('omnigent', promotedCatalog)).toBe(
      'Omnigent',
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
    expect(reason).toContain('Review Harness availability and the Profile configuration in Settings.');
  });

  it('returns null while a selectable target exists', () => {
    expect(runtimeUnavailableReason('omnigent', promotedCatalog)).toBeNull();
    expect(runtimeUnavailableReason('omnigent', undefined)).toBeNull();
  });

  it("judges Omnigent by the selected Harness's own target", () => {
    // Codex via Omnigent is allowed, Claude via Omnigent is disabled.
    expect(runtimeUnavailableReason('omnigent', promotedCatalog, 'codex-native')).toBeNull();
    expect(runtimeUnavailableReason('omnigent', promotedCatalog, 'claude-native')).toContain(
      'No qualified target is available',
    );
    // A Harness the catalog does not list is left to server admission.
    expect(runtimeUnavailableReason('omnigent', promotedCatalog, 'opencode-native')).toBeNull();
    expect(omnigentAllowedForHarness(promotedCatalog, 'codex-native')).toBe(true);
    expect(omnigentAllowedForHarness(promotedCatalog, 'claude-native')).toBe(false);
    expect(omnigentAllowedForHarness(promotedCatalog, undefined)).toBe(true);
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

 it('does not label Omnigent as compatibility when legacy Codex sorts first', () => {
   const catalog = { targets: [
     target({ targetId: 'codex.legacy-profile-bound-omnigent', pathClass: 'legacy_profile_bound_omnigent', compatibilityPath: true }),
     target({ targetId: 'opencode.generic-omnigent', harnessId: 'opencode-native' }),
   ] };
   for (const targets of [catalog.targets, [...catalog.targets].reverse()]) {
     expect(backendOptionsForHarness('codex_cli', { targets }, { omnigentRoute: true })).toEqual([
       { runtimeId: 'omnigent', label: 'Omnigent' },
     ]);
   }
 });


describe('removed and unknown authoring targets (#4644)', () => {
  it.each([undefined, promotedCatalog, { targets: [target({ runtimeId: 'codex_cloud' })] }])('never offers Cloud from an old boot catalog or fallback', (catalog) => {
    expect(backendOptionsForHarness('codex_cloud', catalog, { omnigentRoute: false })).toEqual([]);
    expect(runtimeUnavailableReason('codex_cloud', catalog)).toMatch(/^Unsupported Harness codex_cloud\. Explicitly choose a supported replacement/);
  });
});
