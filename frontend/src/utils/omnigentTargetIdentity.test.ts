import { describe, expect, it } from 'vitest';

import {
  canonicalRuntimeIdFor,
  compatibilitySuffixForRuntime,
  isCompatibilityPath,
  targetIdentityLabel,
} from './omnigentTargetIdentity';

describe('targetIdentityLabel', () => {
  it('labels every exact harness/realizer pair without new runtime ids', () => {
    expect(targetIdentityLabel('codex-native', 'generic-omnigent-host@1')).toBe(
      'Codex via generic Omnigent',
    );
    expect(targetIdentityLabel('codex-native', 'codex-profile-bound@1')).toBe(
      'Codex via legacy profile-bound Omnigent',
    );
    expect(targetIdentityLabel('codex-native', 'direct')).toBe('Direct Codex compatibility');
    expect(targetIdentityLabel('claude-native', 'generic-omnigent-host@1')).toBe(
      'Claude Code via generic Omnigent',
    );
    expect(targetIdentityLabel('claude-native', 'direct')).toBe('Direct Claude compatibility');
    expect(targetIdentityLabel('opencode-native', 'generic-omnigent-host@1')).toBe(
      'OpenCode via generic Omnigent',
    );
  });

  it('rejects unknown identities instead of inventing labels', () => {
    expect(() => targetIdentityLabel('codex-native', 'other@9')).toThrow();
  });
});

describe('isCompatibilityPath', () => {
  it('marks direct and legacy paths compatibility-only', () => {
    expect(isCompatibilityPath('codex-native', 'direct')).toBe(true);
    expect(isCompatibilityPath('codex-native', 'codex-profile-bound@1')).toBe(true);
    expect(isCompatibilityPath('codex-native', 'generic-omnigent-host@1')).toBe(false);
    expect(isCompatibilityPath('opencode-native', 'generic-omnigent-host@1')).toBe(false);
  });
});

describe('canonicalRuntimeIdFor', () => {
  it('keeps Omnigent-backed targets on external/omnigent', () => {
    expect(canonicalRuntimeIdFor('codex-native', 'generic-omnigent-host@1')).toBe(
      'external/omnigent',
    );
    expect(canonicalRuntimeIdFor('codex-native', 'direct')).toBe('codex_cli');
    expect(canonicalRuntimeIdFor('claude-native', 'direct')).toBe('claude_code');
  });
});

describe('compatibilitySuffixForRuntime', () => {
  const promotedCodex = [
    {
      harnessImplementation: 'codex-native@sha256:abc',
      executionRealizer: 'generic-omnigent-host@1',
      rolloutState: 'new_work_default',
    },
  ];

  it('suffixes direct options once the generic target is promoted', () => {
    expect(compatibilitySuffixForRuntime('codex_cli', promotedCodex)).toBe(' (compatibility)');
    expect(compatibilitySuffixForRuntime('claude_code', promotedCodex)).toBe('');
  });

  it('renders no suffix before operator evidence loads', () => {
    expect(compatibilitySuffixForRuntime('codex_cli', undefined)).toBe('');
    expect(compatibilitySuffixForRuntime('codex_cli', [])).toBe('');
    expect(
      compatibilitySuffixForRuntime('codex_cli', [
        {
          harnessImplementation: 'codex-native@sha256:abc',
          executionRealizer: 'generic-omnigent-host@1',
          rolloutState: 'explicit_only',
        },
      ]),
    ).toBe('');
  });
});
