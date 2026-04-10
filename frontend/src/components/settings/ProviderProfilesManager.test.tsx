import { describe, it, expect } from 'vitest';
import type { ProviderProfile } from './ProviderProfilesManager';
import {
  defaultFormState,
  toFormState,
  parseCommandBehavior,
  parseTags,
  parsePriority,
  parseClearEnvKeys,
} from './ProviderProfilesManager';

describe('defaultFormState', () => {
  it('includes advanced fields with correct defaults', () => {
    const state = defaultFormState();

    expect(state.commandBehavior).toBe('{}');
    expect(state.tagsText).toBe('');
    expect(state.priority).toBe('');
    expect(state.clearEnvKeysText).toBe('');
    expect(state.accountLabel).toBe('');
    expect(state.isDefault).toBe(false);
  });

  it('includes all legacy fields', () => {
    const state = defaultFormState();

    expect(state.profileId).toBe('');
    expect(state.runtimeId).toBe('');
    expect(state.providerId).toBe('');
    expect(state.credentialSource).toBe('secret_ref');
    expect(state.rateLimitPolicy).toBe('backoff');
    expect(state.enabled).toBe(true);
    expect(state.isDefault).toBe(false);
  });
});

describe('toFormState', () => {
  const minimalProfile: ProviderProfile = {
    profile_id: 'test-profile',
    runtime_id: 'codex_cli',
    provider_id: 'openai',
    credential_source: 'secret_ref',
    runtime_materialization_mode: 'api_key_env',
    secret_refs: {},
    max_parallel_runs: 1,
    cooldown_after_429_seconds: 300,
    rate_limit_policy: 'backoff',
    enabled: true,
    is_default: false,
  };

  const fullProfile: ProviderProfile = {
    ...minimalProfile,
    provider_label: 'OpenAI Prod',
    default_model: 'gpt-4o',
    volume_ref: 'openai-config',
    volume_mount_path: '/root/.openai',
    secret_refs: { OPENAI_API_KEY: 'db://OPENAI_API_KEY' },
    command_behavior: { suppress_default_model_flag: true },
    tags: ['openrouter', 'qwen', 'codex'],
    priority: 200,
    clear_env_keys: ['OPENAI_API_KEY', 'OPENAI_BASE_URL'],
    account_label: 'team-prod',
    is_default: true,
  };

  it('maps a minimal profile with null advanced fields', () => {
    const state = toFormState(minimalProfile);

    expect(state.commandBehavior).toBe('{}');
    expect(state.tagsText).toBe('');
    expect(state.priority).toBe('');
    expect(state.clearEnvKeysText).toBe('');
    expect(state.accountLabel).toBe('');
    expect(state.isDefault).toBe(false);
  });

  it('maps a full profile with advanced fields', () => {
    const state = toFormState(fullProfile);

    expect(state.commandBehavior).toBe(
      JSON.stringify({ suppress_default_model_flag: true }, null, 2),
    );
    expect(state.tagsText).toBe('openrouter, qwen, codex');
    expect(state.priority).toBe('200');
    expect(state.clearEnvKeysText).toBe('OPENAI_API_KEY\nOPENAI_BASE_URL');
    expect(state.accountLabel).toBe('team-prod');
    expect(state.isDefault).toBe(true);
  });

  it('maps legacy fields correctly', () => {
    const state = toFormState(fullProfile);

    expect(state.profileId).toBe('test-profile');
    expect(state.runtimeId).toBe('codex_cli');
    expect(state.providerId).toBe('openai');
    expect(state.providerLabel).toBe('OpenAI Prod');
    expect(state.defaultModel).toBe('gpt-4o');
    expect(state.secretRefsText).toBe(
      JSON.stringify({ OPENAI_API_KEY: 'db://OPENAI_API_KEY' }, null, 2),
    );
    expect(state.maxParallelRuns).toBe('1');
    expect(state.cooldownAfter429Seconds).toBe('300');
    expect(state.rateLimitPolicy).toBe('backoff');
    expect(state.enabled).toBe(true);
    expect(state.isDefault).toBe(true);
  });

  it('handles null/undefined optional string fields', () => {
    const profileWithNulls: ProviderProfile = {
      ...minimalProfile,
      provider_label: null,
      default_model: null,
      volume_ref: null,
      volume_mount_path: null,
    };

    const state = toFormState(profileWithNulls);

    expect(state.providerLabel).toBe('');
    expect(state.defaultModel).toBe('');
    expect(state.volumeRef).toBe('');
    expect(state.volumeMountPath).toBe('');
  });
});

describe('parseCommandBehavior', () => {
  it('returns null for empty or blank input', () => {
    expect(parseCommandBehavior('')).toBe(null);
    expect(parseCommandBehavior('   ')).toBe(null);
  });

  it('returns null for empty object literal', () => {
    expect(parseCommandBehavior('{}')).toBe(null);
    expect(parseCommandBehavior('  {}  ')).toBe(null);
  });

  it('parses a valid object', () => {
    const result = parseCommandBehavior('{"suppress_default_model_flag": true}');
    expect(result).toEqual({ suppress_default_model_flag: true });
  });

  it('throws on invalid JSON', () => {
    expect(() => parseCommandBehavior('{bad json')).toThrow('Command behavior must be valid JSON.');
  });

  it('throws on non-object values (array)', () => {
    expect(() => parseCommandBehavior('[1, 2, 3]')).toThrow('Command behavior must be a JSON object.');
  });

  it('throws on non-object values (string)', () => {
    expect(() => parseCommandBehavior('"just a string"')).toThrow('Command behavior must be a JSON object.');
  });

  it('throws on non-object values (null)', () => {
    expect(() => parseCommandBehavior('null')).toThrow('Command behavior must be a JSON object.');
  });
});

describe('parseTags', () => {
  it('returns null for empty input', () => {
    expect(parseTags('')).toBe(null);
    expect(parseTags('   ')).toBe(null);
  });

  it('splits comma-separated values', () => {
    expect(parseTags('openrouter, qwen, codex')).toEqual(['openrouter', 'qwen', 'codex']);
  });

  it('filters blank entries', () => {
    expect(parseTags('openrouter, , codex')).toEqual(['openrouter', 'codex']);
  });
});

describe('parsePriority', () => {
  it('returns null for empty input', () => {
    expect(parsePriority('')).toBe(null);
    expect(parsePriority('   ')).toBe(null);
  });

  it('parses valid numbers', () => {
    expect(parsePriority('100')).toBe(100);
    expect(parsePriority('0')).toBe(0);
    expect(parsePriority('-5')).toBe(-5);
  });

  it('throws on invalid input', () => {
    expect(() => parsePriority('abc')).toThrow('Priority must be a valid number.');
    expect(() => parsePriority('NaN')).toThrow('Priority must be a valid number.');
  });

  it('throws on Infinity', () => {
    expect(() => parsePriority('Infinity')).toThrow('Priority must be a valid number.');
  });
});

describe('parseClearEnvKeys', () => {
  it('returns null for empty input', () => {
    expect(parseClearEnvKeys('')).toBe(null);
    expect(parseClearEnvKeys('   ')).toBe(null);
  });

  it('splits newline-separated values', () => {
    expect(parseClearEnvKeys('OPENAI_API_KEY\nOPENAI_BASE_URL')).toEqual(['OPENAI_API_KEY', 'OPENAI_BASE_URL']);
  });

  it('filters blank lines', () => {
    expect(parseClearEnvKeys('OPENAI_API_KEY\n\nOPENAI_BASE_URL')).toEqual(['OPENAI_API_KEY', 'OPENAI_BASE_URL']);
  });
});
