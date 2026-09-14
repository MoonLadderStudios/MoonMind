import { QueryClient, QueryClientProvider, useQuery } from '@tanstack/react-query';
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, describe, it, expect, vi } from 'vitest';
import type { ProviderProfile } from './ProviderProfilesManager';
import {
  defaultFormState,
  PROVIDER_PROFILE_QUERY_KEY,
  ProviderProfilesManager,
  toFormState,
  parseCommandBehavior,
  parseTags,
  parsePriority,
  parseClearEnvKeys,
  resolveCreationAction,
  setupContinuationAvailableForCreate,
  captureSubmittedProfileOperation,
  saveResponseIdentityMatches,
  submissionOwnsCurrentForm,
  computeAdvancedPolicySummary,
  collectAdvancedControlDeviations,
  loadPersistedDefaultIntents,
  persistDefaultIntents,
  isLostSaveAcknowledgment,
  AmbiguousSaveAcknowledgmentError,
  resolveAdvancedRecommendedText,
  PENDING_DEFAULT_INTENT_STORAGE_KEY,
  ProviderProfileRequestError,
} from './ProviderProfilesManager';
import { renderWithClient } from '../../utils/test-utils';
import { runtimeDefaultTierDraft } from '../../utils/providerProfileTiers';

afterEach(() => {
  vi.useRealTimers();
  vi.restoreAllMocks();
});

function renderProviderProfilesManager(profiles: ProviderProfile[] = []) {
  const queryClient = new QueryClient({
    defaultOptions: {
      queries: {
        retry: false,
      },
    },
  });
  const onNotice = vi.fn();

  renderWithClient(
    <ProviderProfilesManager
      profiles={profiles}
      secretSlugs={['OPENAI_API_KEY']}
      onNotice={onNotice}
      queryClient={queryClient}
      defaultTaskModelByRuntime={{}}
    />,
  );

  return { onNotice, queryClient };
}

function renderReadOnlyProviderProfilesManager(profiles: ProviderProfile[] = []) {
  const queryClient = new QueryClient({
    defaultOptions: {
      queries: {
        retry: false,
      },
    },
  });
  const onNotice = vi.fn();

  renderWithClient(
    <ProviderProfilesManager
      profiles={profiles}
      secretSlugs={['OPENAI_API_KEY']}
      onNotice={onNotice}
      queryClient={queryClient}
      defaultTaskModelByRuntime={{}}
      canWriteProviderProfiles={false}
    />,
  );

  return { onNotice, queryClient };
}

function renderProviderProfilesManagerWithQuery(profiles: ProviderProfile[] = []) {
  const queryClient = new QueryClient({
    defaultOptions: {
      queries: {
        retry: false,
      },
    },
  });
  queryClient.setQueryData(PROVIDER_PROFILE_QUERY_KEY, profiles);
  const onNotice = vi.fn();

  function ProviderProfilesHarness() {
    const { data = [] } = useQuery<ProviderProfile[]>({
      queryKey: PROVIDER_PROFILE_QUERY_KEY,
      queryFn: async () => queryClient.getQueryData<ProviderProfile[]>(PROVIDER_PROFILE_QUERY_KEY) ?? profiles,
      initialData: profiles,
      staleTime: Infinity,
    });

    return (
      <ProviderProfilesManager
        profiles={data}
        secretSlugs={['OPENAI_API_KEY']}
        onNotice={onNotice}
        queryClient={queryClient}
        defaultTaskModelByRuntime={{}}
      />
    );
  }

  render(
    <QueryClientProvider client={queryClient}>
      <ProviderProfilesHarness />
    </QueryClientProvider>,
  );

  return { onNotice, queryClient };
}

describe('defaultFormState', () => {
  it('includes advanced fields with correct defaults', () => {
    const state = defaultFormState();

    expect(state.commandBehavior).toBe('{}');
    expect(state.tagsText).toBe('');
    expect(state.priority).toBe('');
    expect(state.clearEnvKeysText).toBe('');
    expect(state.accountLabel).toBe('');
    expect(state.defaultEffort).toBe('');
    expect(state.isDefault).toBe(false);
  });

  it('does not guess backend-owned creation policy', () => {
    const state = defaultFormState();

    expect(state.profileId).toBe('');
    expect(state.runtimeId).toBe('');
    expect(state.providerId).toBe('');
    expect(state.authenticationMethod).toBe('');
    expect(state.credentialSource).toBe('');
    expect(state.runtimeMaterializationMode).toBe('');
    expect(state.maxParallelRuns).toBe('');
    expect(state.cooldownAfter429Seconds).toBe('');
    expect(state.rateLimitPolicy).toBe('');
    expect(state.enabled).toBe(false);
    expect(state.isDefault).toBe(false);
  });
});

describe('backend creation presets', () => {
  it('uses the generated preset contract and omits untouched advanced values', async () => {
    const field = (
      value: unknown,
      editable = true,
      source = 'test_policy',
    ) => ({
      value,
      source,
      editable,
      required: false,
      lock_reason: editable ? null : 'Backend controlled.',
    });
    const preset = {
      version: 'provider-profile-create-v1-test',
      supported: true,
      runtime_id: 'codex_cli',
      provider_id: 'openai',
      authentication_method: 'api_key',
      fields: {
        credential_source: field('none', false),
        runtime_materialization_mode: field('api_key_env', false),
        secret_refs: field({}),
        volume_ref: field(null, false),
        volume_mount_path: field(null, false),
        max_parallel_runs: field(1),
        cooldown_after_429_seconds: field(300),
        rate_limit_policy: field('backoff'),
        enabled: field(false, false),
        is_default: field(false),
        command_behavior: field({ auth_strategy: 'api_key_env' }, false),
        user_tags: field([]),
        priority: field(100),
        clear_env_keys: field(['MINIMAX_API_KEY'], false),
      },
      diagnostics: [],
      manual_creation_allowed: false,
      required_manual_fields: [],
    };
    const savedProfile: ProviderProfile = {
      profile_id: 'preset-profile',
      runtime_id: 'codex_cli',
      provider_id: 'openai',
      credential_source: 'none',
      runtime_materialization_mode: 'api_key_env',
      secret_refs: {},
      max_parallel_runs: 1,
      cooldown_after_429_seconds: 300,
      rate_limit_policy: 'backoff',
      enabled: false,
      is_default: false,
    };
    const fetchSpy = vi.spyOn(window, 'fetch').mockImplementation(
      async (input, init) => {
        const url = String(input);
        if (url.startsWith('/api/v1/provider-profiles/capabilities?') || /\/api\/v1\/provider-profiles\/[^/]+\/capabilities/.test(url)) {
          return { ok: true, json: async () => ({
            version: 'tier-cap-v1-test',
            profile_id: null,
            runtime_id: 'codex_cli',
            provider_id: 'openai',
            evidence: { source: 'runtime_draft', credential_generation: null, image_ref: null, observed_at: null, stale: false },
            tier_constraints: { min_count: 1, max_count: null },
            model: { runtime_default: 'gpt-5.5', allow_custom: true, options: [{ value: 'gpt-5.5', label: 'GPT-5.5', description: 'General coding model', status: 'available', recommended: true }, { value: 'gpt-4o', label: 'GPT-4o', description: null, status: 'available', recommended: false }] },
            effort: { supported: true, runtime_default: 'medium', allow_custom: false, application: 'native', options: [{ value: 'low', label: 'Low', description: null, status: 'available', compatible_models: null }, { value: 'medium', label: 'Medium', description: null, status: 'available', compatible_models: null }, { value: 'high', label: 'High', description: null, status: 'available', compatible_models: null }, { value: 'xhigh', label: 'Extra high', description: null, status: 'available', compatible_models: null }] },
            diagnostics: [],
          }) } as Response;
        }
        if (url.startsWith('/api/v1/provider-profiles/creation-capabilities?')) {
          return {
            ok: true,
            json: async () => ({
              version: preset.version,
              runtime_id: preset.runtime_id,
              provider_id: preset.provider_id,
              supported: true,
              authentication_methods: [
                {
                  id: 'api_key',
                  label: 'API key',
                  setup_action: 'api_key',
                  launch_ready_after_setup: true,
                  fields: preset.fields,
                  secret_roles: [],
                  imported_volume: {
                    supported: false,
                    mount_path: null,
                    source: 'test_policy',
                    lock_reason: 'API-key setup does not use a credential volume.',
                  },
                },
              ],
              diagnostics: [],
            }),
          } as Response;
        }
        if (url.startsWith('/api/v1/provider-profiles/creation-preset?')) {
          return {
            ok: true,
            json: async () => preset,
          } as Response;
        }
        if (url === '/api/v1/provider-profiles' && init?.method === 'POST') {
          return {
            ok: true,
            json: async () => savedProfile,
          } as Response;
        }
        throw new Error(`Unexpected fetch: ${url}`);
      },
    );

    renderProviderProfilesManager();
    fireEvent.change(screen.getByLabelText(/Profile ID/), {
      target: { value: 'preset-profile' },
    });
    fireEvent.change(screen.getByLabelText(/Runtime ID/), {
      target: { value: 'codex_cli' },
    });
    fireEvent.change(screen.getByLabelText(/Provider ID/), {
      target: { value: 'openai' },
    });
    fireEvent.click(await screen.findByLabelText('API key'));

    await screen.findByText(/Backend preset provider-profile-create-v1-test loaded/);
    expect(screen.queryByLabelText(/Credential source/)).toBeNull();
    expect(screen.getByLabelText('Runtime default')).toBeTruthy();
    fireEvent.click(screen.getByLabelText(/Show advanced options/));
    expect(screen.getByText('Credential source: none')).toBeTruthy();
    expect(screen.getByText('Materialization mode: api_key_env')).toBeTruthy();
    expect(screen.queryByLabelText('Enabled')).toBeNull();

    // MoonLadderStudios/MoonMind#4002: guided continuation.
    fireEvent.click(screen.getByRole('button', { name: 'Create and connect' }));

    await waitFor(() => {
      expect(
        fetchSpy.mock.calls.some(([, init]) => init?.method === 'POST'),
      ).toBe(true);
    });
    const postCall = fetchSpy.mock.calls.find(([, init]) => init?.method === 'POST');
    const payload = JSON.parse(String((postCall?.[1] as RequestInit).body));
    expect(payload).toMatchObject({
      profile_id: 'preset-profile',
      runtime_id: 'codex_cli',
      provider_id: 'openai',
      authentication_method: 'api_key',
      preset_version: 'provider-profile-create-v1-test',
    });
    for (const omittedField of [
      'credential_source',
      'runtime_materialization_mode',
      'secret_refs',
      'volume_ref',
      'volume_mount_path',
      'max_parallel_runs',
      'cooldown_after_429_seconds',
      'rate_limit_policy',
      'enabled',
      'is_default',
      'command_behavior',
      'tags',
      'priority',
      'clear_env_keys',
    ]) {
      expect(payload).not.toHaveProperty(omittedField);
    }
  });

  it('routes unsupported guided combinations through manual creation', async () => {
    const unsupportedPreset = {
      version: 'provider-profile-create-v1-unsupported',
      supported: false,
      runtime_id: 'codex_cli',
      provider_id: 'openrouter',
      authentication_method: 'api_key',
      fields: {},
      diagnostics: [
        {
          code: 'no_safe_standard_creation_preset',
          severity: 'error',
          message: 'Use the authorized manual profile path.',
          field: null,
          action: 'open_manual_profile',
        },
      ],
      manual_creation_allowed: true,
      required_manual_fields: [
        'credential_source',
        'runtime_materialization_mode',
        'clear_env_keys',
        'command_behavior',
      ],
    };
    const savedProfile: ProviderProfile = {
      profile_id: 'manual-openrouter',
      runtime_id: 'codex_cli',
      provider_id: 'openrouter',
      credential_source: 'secret_ref',
      runtime_materialization_mode: 'api_key_env',
      secret_refs: {},
      max_parallel_runs: 1,
      cooldown_after_429_seconds: 900,
      rate_limit_policy: 'backoff',
      enabled: false,
    };
    const fetchSpy = vi.spyOn(window, 'fetch').mockImplementation(
      async (input, init) => {
        const url = String(input);
        if (url.startsWith('/api/v1/provider-profiles/capabilities?') || /\/api\/v1\/provider-profiles\/[^/]+\/capabilities/.test(url)) {
          return { ok: true, json: async () => ({
            version: 'tier-cap-v1-test',
            profile_id: null,
            runtime_id: 'codex_cli',
            provider_id: 'openai',
            evidence: { source: 'runtime_draft', credential_generation: null, image_ref: null, observed_at: null, stale: false },
            tier_constraints: { min_count: 1, max_count: null },
            model: { runtime_default: 'gpt-5.5', allow_custom: true, options: [{ value: 'gpt-5.5', label: 'GPT-5.5', description: 'General coding model', status: 'available', recommended: true }, { value: 'gpt-4o', label: 'GPT-4o', description: null, status: 'available', recommended: false }] },
            effort: { supported: true, runtime_default: 'medium', allow_custom: false, application: 'native', options: [{ value: 'low', label: 'Low', description: null, status: 'available', compatible_models: null }, { value: 'medium', label: 'Medium', description: null, status: 'available', compatible_models: null }, { value: 'high', label: 'High', description: null, status: 'available', compatible_models: null }, { value: 'xhigh', label: 'Extra high', description: null, status: 'available', compatible_models: null }] },
            diagnostics: [],
          }) } as Response;
        }
        if (url.startsWith('/api/v1/provider-profiles/creation-capabilities?')) {
          return {
            ok: true,
            json: async () => ({
              version: unsupportedPreset.version,
              runtime_id: unsupportedPreset.runtime_id,
              provider_id: unsupportedPreset.provider_id,
              supported: true,
              authentication_methods: [
                {
                  id: 'api_key',
                  label: 'API key',
                  setup_action: 'api_key',
                  launch_ready_after_setup: false,
                  fields: {
                    credential_source: {
                      value: 'secret_ref',
                      source: 'expert_manual',
                      editable: true,
                      lock_reason: 'Expert manual value.',
                    },
                    runtime_materialization_mode: {
                      value: 'api_key_env',
                      source: 'expert_manual',
                      editable: true,
                      lock_reason: 'Expert manual value.',
                    },
                  },
                  secret_roles: [],
                  imported_volume: {
                    supported: false,
                    mount_path: null,
                    source: 'expert_manual',
                    lock_reason: 'Manual profile does not import a volume.',
                  },
                },
              ],
              diagnostics: [],
            }),
          } as Response;
        }
        if (url.startsWith('/api/v1/provider-profiles/creation-preset?')) {
          return {
            ok: true,
            json: async () => unsupportedPreset,
          } as Response;
        }
        if (url === '/api/v1/provider-profiles' && init?.method === 'POST') {
          return {
            ok: true,
            json: async () => savedProfile,
          } as Response;
        }
        throw new Error(`Unexpected fetch: ${url}`);
      },
    );

    renderProviderProfilesManager();
    fireEvent.change(screen.getByLabelText(/Profile ID/), {
      target: { value: 'manual-openrouter' },
    });
    fireEvent.change(screen.getByLabelText(/Runtime ID/), {
      target: { value: 'codex_cli' },
    });
    fireEvent.change(screen.getByLabelText(/Provider ID/), {
      target: { value: 'openrouter' },
    });
    fireEvent.click(await screen.findByLabelText('API key'));

    await screen.findByText('Use the authorized manual profile path.');
    expect((screen.getByLabelText(/Show advanced options/) as HTMLInputElement).checked).toBe(true);
    fireEvent.change(screen.getByLabelText(/Credential source/), {
      target: { value: 'secret_ref' },
    });
    fireEvent.change(screen.getByLabelText(/Materialization mode/), {
      target: { value: 'api_key_env' },
    });
    fireEvent.change(screen.getByLabelText(/Clear env keys/), {
      target: { value: 'OPENAI_API_KEY' },
    });
    fireEvent.change(screen.getByLabelText('Command behavior'), {
      target: { value: '{"auth_strategy":"manual"}' },
    });
    // MoonLadderStudios/MoonMind#4002: explicitly permitted manual creation.
    fireEvent.click(screen.getByRole('button', { name: 'Save disabled profile' }));

    await waitFor(() => {
      expect(fetchSpy.mock.calls.some(([, init]) => init?.method === 'POST')).toBe(true);
    });
    const postCall = fetchSpy.mock.calls.find(([, init]) => init?.method === 'POST');
    const payload = JSON.parse(String((postCall?.[1] as RequestInit).body));
    expect(payload).toMatchObject({
      profile_id: 'manual-openrouter',
      runtime_id: 'codex_cli',
      provider_id: 'openrouter',
      credential_source: 'secret_ref',
      runtime_materialization_mode: 'api_key_env',
      clear_env_keys: ['OPENAI_API_KEY'],
      command_behavior: { auth_strategy: 'manual' },
    });
    expect(payload).not.toHaveProperty('authentication_method');
    expect(payload).not.toHaveProperty('preset_version');
    expect(
      screen.queryByRole('dialog', { name: /API key enrollment for manual-openrouter/ }),
    ).toBeNull();
  });

  it('reloads the active preset after a version mismatch', async () => {
    const field = (value: unknown, editable = true) => ({
      value,
      source: 'test_policy',
      editable,
      required: false,
      lock_reason: editable ? null : 'Backend controlled.',
    });
    const makePreset = (version: string, cooldown: number) => ({
      version,
      supported: true,
      runtime_id: 'codex_cli',
      provider_id: 'openai',
      authentication_method: 'api_key',
      fields: {
        credential_source: field('none', false),
        runtime_materialization_mode: field('api_key_env', false),
        secret_refs: field({}),
        volume_ref: field(null, false),
        volume_mount_path: field(null, false),
        max_parallel_runs: field(1),
        cooldown_after_429_seconds: field(cooldown),
        rate_limit_policy: field('backoff'),
        enabled: field(false, false),
        is_default: field(false),
        command_behavior: field({ auth_strategy: 'api_key_env' }, false),
        user_tags: field([]),
        priority: field(100),
        clear_env_keys: field(['MINIMAX_API_KEY'], false),
      },
      diagnostics: [],
      manual_creation_allowed: false,
      required_manual_fields: [],
    });
    let presetRequests = 0;
    const fetchSpy = vi.spyOn(window, 'fetch').mockImplementation(
      async (input, init) => {
        const url = String(input);
        if (url.startsWith('/api/v1/provider-profiles/capabilities?') || /\/api\/v1\/provider-profiles\/[^/]+\/capabilities/.test(url)) {
          return { ok: true, json: async () => ({
            version: 'tier-cap-v1-test',
            profile_id: null,
            runtime_id: 'codex_cli',
            provider_id: 'openai',
            evidence: { source: 'runtime_draft', credential_generation: null, image_ref: null, observed_at: null, stale: false },
            tier_constraints: { min_count: 1, max_count: null },
            model: { runtime_default: 'gpt-5.5', allow_custom: true, options: [{ value: 'gpt-5.5', label: 'GPT-5.5', description: 'General coding model', status: 'available', recommended: true }, { value: 'gpt-4o', label: 'GPT-4o', description: null, status: 'available', recommended: false }] },
            effort: { supported: true, runtime_default: 'medium', allow_custom: false, application: 'native', options: [{ value: 'low', label: 'Low', description: null, status: 'available', compatible_models: null }, { value: 'medium', label: 'Medium', description: null, status: 'available', compatible_models: null }, { value: 'high', label: 'High', description: null, status: 'available', compatible_models: null }, { value: 'xhigh', label: 'Extra high', description: null, status: 'available', compatible_models: null }] },
            diagnostics: [],
          }) } as Response;
        }
        if (url.startsWith('/api/v1/provider-profiles/creation-capabilities?')) {
          return {
            ok: true,
            json: async () => ({
              version: 'provider-profile-create-v1-old',
              runtime_id: 'codex_cli',
              provider_id: 'openai',
              supported: true,
              authentication_methods: [
                {
                  id: 'api_key',
                  label: 'API key',
                  setup_action: 'api_key',
                  launch_ready_after_setup: true,
                  fields: makePreset('provider-profile-create-v1-old', 300).fields,
                  secret_roles: [],
                  imported_volume: {
                    supported: false,
                    mount_path: null,
                    source: 'test_policy',
                    lock_reason: 'API-key setup does not use a credential volume.',
                  },
                },
              ],
              diagnostics: [],
            }),
          } as Response;
        }
        if (url.startsWith('/api/v1/provider-profiles/creation-preset?')) {
          presetRequests += 1;
          return {
            ok: true,
            json: async () =>
              presetRequests === 1
                ? makePreset('provider-profile-create-v1-old', 300)
                : makePreset('provider-profile-create-v1-current', 600),
          } as Response;
        }
        if (url === '/api/v1/provider-profiles' && init?.method === 'POST') {
          return {
            ok: false,
            json: async () => ({
              detail: {
                code: 'provider_profile_creation_preset_version_mismatch',
                message: 'The preset changed.',
              },
            }),
          } as Response;
        }
        throw new Error(`Unexpected fetch: ${url}`);
      },
    );
    const { onNotice } = renderProviderProfilesManager();
    fireEvent.change(screen.getByLabelText(/Profile ID/), {
      target: { value: 'stale-preset-profile' },
    });
    fireEvent.change(screen.getByLabelText(/Runtime ID/), {
      target: { value: 'codex_cli' },
    });
    fireEvent.change(screen.getByLabelText(/Provider ID/), {
      target: { value: 'openai' },
    });
    fireEvent.click(await screen.findByLabelText('API key'));

    await screen.findByText(/provider-profile-create-v1-old loaded/);
    // MoonLadderStudios/MoonMind#4002: guided continuation.
    fireEvent.click(screen.getByRole('button', { name: 'Create and connect' }));

    await screen.findByText(/provider-profile-create-v1-current loaded/);
    expect(presetRequests).toBe(2);
    expect(onNotice).toHaveBeenCalledWith({
      level: 'error',
      text: 'The creation policy changed. Reloading the current preset for review.',
    });
    expect((screen.getByLabelText(/Show advanced options/) as HTMLInputElement).checked).toBe(true);
    expect((screen.getByLabelText(/Cooldown after 429/) as HTMLInputElement).value).toBe('600');
    expect(fetchSpy.mock.calls.filter(([, init]) => init?.method === 'POST')).toHaveLength(1);
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
    default_effort: 'high',
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
    expect(state.defaultEffort).toBe('high');
    expect(state.isDefault).toBe(true);
  });

  it('maps legacy fields correctly', () => {
    const state = toFormState(fullProfile);

    expect(state.profileId).toBe('test-profile');
    expect(state.runtimeId).toBe('codex_cli');
    expect(state.providerId).toBe('openai');
    expect(state.providerLabel).toBe('OpenAI Prod');
    expect(state.defaultModel).toBe('gpt-4o');
    expect(state.defaultEffort).toBe('high');
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
      default_effort: null,
      volume_ref: null,
      volume_mount_path: null,
    };

    const state = toFormState(profileWithNulls);

    expect(state.providerLabel).toBe('');
    expect(state.defaultModel).toBe('');
    expect(state.defaultEffort).toBe('');
    expect(state.volumeRef).toBe('');
    expect(state.volumeMountPath).toBe('');
  });
});

describe('provider profile tier mapping display', () => {
  it('MM-1173 renders tier number, label, model, and effort in Settings', () => {
    renderProviderProfilesManager([
      {
        profile_id: 'codex_openai_api',
        runtime_id: 'codex_cli',
        provider_id: 'openai',
        credential_source: 'secret_ref',
        runtime_materialization_mode: 'api_key_env',
        secret_refs: {},
        max_parallel_runs: 1,
        cooldown_after_429_seconds: 300,
        rate_limit_policy: 'backoff',
        enabled: true,
        is_default: true,
        model_tiers: [
          { label: 'Plan and verify', model: 'gpt-5.5', effort: 'medium' },
          { label: 'Implementation', model: 'gpt-5.5', effort: 'xhigh' },
        ],
        default_model_tier: 1,
      },
    ]);

    const mapping = screen.getByLabelText('codex_openai_api model tier mapping');
    expect(mapping.textContent).toContain(
      'Tier 1 default · Plan and verify · gpt-5.5 · medium',
    );
    expect(mapping.textContent).toContain(
      'Tier 2 · Implementation · gpt-5.5 · xhigh',
    );
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

describe('ProviderProfilesManager form controls', () => {
  const profile: ProviderProfile = {
    profile_id: 'codex-default',
    runtime_id: 'codex_cli',
    provider_id: 'openai',
    credential_source: 'secret_ref',
    runtime_materialization_mode: 'api_key_env',
    secret_refs: { OPENAI_API_KEY: 'db://OPENAI_API_KEY' },
    max_parallel_runs: 1,
    cooldown_after_429_seconds: 300,
    rate_limit_policy: 'backoff',
    enabled: true,
    is_default: true,
    auth_state: 'connected',
    disabled_reason: null,
  };

  const codexOauthProfile: ProviderProfile = {
    ...profile,
    profile_id: 'codex-oauth',
    credential_source: 'oauth_volume',
    runtime_materialization_mode: 'oauth_home',
    secret_refs: {},
    volume_ref: 'codex_auth_volume',
    volume_mount_path: '/home/app/.codex',
    account_label: 'Codex account',
  };

  const codexApiKeySetupProfile: ProviderProfile = {
    ...profile,
    profile_id: 'codex-openai-api-key',
    credential_source: 'none',
    runtime_materialization_mode: 'api_key_env',
    secret_refs: {},
    enabled: false,
    is_default: false,
    auth_state: 'not_configured',
    disabled_reason: 'missing_credentials',
    command_behavior: {
      auth_strategy: 'api_key_env',
      auth_state: 'not_configured',
      auth_actions: ['use_api_key'],
      auth_status_label: 'OpenAI credentials not connected',
    },
  };

  const claudeCredentialProfile: ProviderProfile = {
    ...profile,
    profile_id: 'claude-anthropic',
    runtime_id: 'claude_code',
    provider_id: 'anthropic',
    credential_source: 'oauth_volume',
    runtime_materialization_mode: 'oauth_home',
    secret_refs: {},
    volume_ref: 'claude_auth_volume',
    volume_mount_path: '/home/app/.claude',
    account_label: 'Claude Anthropic OAuth',
    enabled: false,
    auth_state: 'not_configured',
    disabled_reason: 'missing_credentials',
    command_behavior: {
      auth_strategy: 'claude_credential_methods',
      auth_state: 'not_connected',
      auth_actions: ['connect_oauth', 'use_api_key'],
      auth_status_label: 'Claude credentials not connected',
    },
  };

  const connectedClaudeCredentialProfile: ProviderProfile = {
    ...claudeCredentialProfile,
    profile_id: 'claude-anthropic-connected',
    enabled: true,
    auth_state: 'connected',
    disabled_reason: null,
    command_behavior: {
      auth_strategy: 'claude_credential_methods',
      auth_state: 'connected',
      auth_actions: ['connect_oauth', 'use_api_key', 'validate_oauth', 'disconnect_oauth'],
      auth_status_label: 'Claude OAuth ready',
    },
  };

  const readyClaudeCredentialProfile: ProviderProfile = {
    ...connectedClaudeCredentialProfile,
    profile_id: 'claude-anthropic-ready',
    command_behavior: {
      auth_strategy: 'claude_credential_methods',
      auth_state: 'connected',
      auth_actions: ['connect_oauth', 'use_api_key', 'validate_oauth', 'disconnect_oauth'],
      auth_status_label: 'Claude OAuth ready',
      auth_readiness: {
        connected: true,
        last_validated_at: '2026-04-22T08:30:00Z',
        failure_reason: 'Previous token sk-ant-secret should be hidden',
        backing_secret_exists: true,
        launch_ready: true,
      },
    },
  };

  const profileWithReadiness: ProviderProfile = {
    ...profile,
    profile_id: 'codex-diagnostic',
    provider_label: 'OpenAI Team',
    default_model: 'gpt-5.4',
    secret_refs: {
      provider_api_key: 'db://openai-team-key',
    },
    max_parallel_runs: 3,
    cooldown_after_429_seconds: 120,
    tags: ['team', 'fast'],
    priority: 250,
    readiness: {
      status: 'blocked',
      launch_ready: false,
      summary: 'Provider profile has launch blockers.',
      checks: [
        {
          id: 'secret_refs',
          label: 'SecretRef bindings',
          status: 'error',
          message: 'provider_api_key points at missing managed secret db://openai-team-key',
        },
        {
          id: 'provider_validation',
          label: 'Provider validation',
          status: 'error',
          message: 'Validation failed for token=[REDACTED]',
        },
      ],
    },
  };

  it('keeps raw SecretRef JSON out of standard creation and resets form values', () => {
    renderProviderProfilesManager();

    expect(screen.queryByLabelText('Secret refs (JSON object of string refs)')).toBeNull();

    const profileId = screen.getByLabelText(/Profile ID/) as HTMLInputElement;
    fireEvent.change(profileId, { target: { value: 'draft-profile' } });
    expect(profileId.value).toBe('draft-profile');

    fireEvent.click(screen.getByRole('button', { name: 'Reset form' }));
    expect(profileId.value).toBe('');
    expect(screen.queryByRole('button', { name: 'Cancel edit' })).toBeNull();
  });

  it('uses one cancel action while editing', () => {
    renderProviderProfilesManager([profile]);

    fireEvent.click(screen.getByRole('button', { name: 'Edit' }));

    expect(screen.getAllByRole('button', { name: 'Cancel edit' })).toHaveLength(1);
    expect(screen.queryByRole('button', { name: 'Reset form' })).toBeNull();
  });

  it('renders Codex OAuth concurrency as a fixed exclusive capacity', () => {
    renderProviderProfilesManager([{ ...codexOauthProfile, max_parallel_runs: 7 }]);

    fireEvent.click(screen.getByRole('button', { name: 'Edit' }));

    const maxParallelRuns = screen.getByLabelText(/Max parallel runs/) as HTMLInputElement;
    expect(maxParallelRuns.disabled).toBe(true);
    expect(maxParallelRuns.value).toBe('1');
    expect(
      screen.getByText(
        'Fixed at 1 because the Codex OAuth home is an exclusive mutable identity.',
      ),
    ).toBeTruthy();
  });

  it('exposes table cell labels for the mobile provider profile card layout', () => {
    renderProviderProfilesManager([profile]);

    const table = screen.getByRole('table');
    expect(table.classList.contains('provider-profiles-table')).toBe(true);
    expect(table.closest('.provider-profiles-table-wrap')).not.toBeNull();

    const profileRow = table.querySelector('tbody tr');
    const labels = Array.from(profileRow?.querySelectorAll('td') ?? []).map((cell) =>
      cell.getAttribute('data-label'),
    );
    expect(labels).toEqual([
      'Profile',
      'Runtime',
      'Provider',
      'Credential',
      'Secret refs',
      'Status',
      'Actions',
    ]);
    expect(screen.getByRole('columnheader', { name: 'Profile' }).getAttribute('id')).toBe(
      'provider-profile-header-profile',
    );
    expect(profileRow?.querySelector('td[data-label="Profile"]')?.getAttribute('headers')).toBe(
      'provider-profile-header-profile',
    );
  });

  it('sends runtime default changes when updating an edited profile', async () => {
    const fetchSpy = vi.spyOn(window, 'fetch').mockResolvedValue({
      ok: true,
      json: async () => ({ ...profile, profile_id: 'codex-secondary', is_default: true }),
    } as Response);
    const secondaryProfile: ProviderProfile = {
      ...profile,
      profile_id: 'codex-secondary',
      is_default: false,
    };

    renderProviderProfilesManagerWithQuery([profile, secondaryProfile]);

    const editButtons = screen.getAllByRole('button', { name: 'Edit' });
    const secondaryEditButton = editButtons[1];
    if (!secondaryEditButton) {
      throw new Error('Expected secondary provider profile edit button');
    }
    fireEvent.click(secondaryEditButton);
    const runtimeDefaultCheckbox = screen.getByLabelText('Runtime default') as HTMLInputElement;
    expect(runtimeDefaultCheckbox.checked).toBe(false);

    fireEvent.click(runtimeDefaultCheckbox);
    const submitButton = screen.getByRole('button', { name: 'Update provider profile' });
    fireEvent.click(submitButton);

    await waitFor(() => {
      expect(fetchSpy).toHaveBeenCalledWith(
        '/api/v1/provider-profiles/codex-secondary',
        expect.objectContaining({
          method: 'PATCH',
        }),
      );
    });

    const fetchCall = fetchSpy.mock.calls.find((call) => { const init = call[1] as RequestInit | undefined; return String(call[0]).includes('/api/v1/provider-profiles/codex-secondary') && init?.method === 'PATCH'; });
    if (!fetchCall) {
      throw new Error('Expected provider profile update request');
    }
    const [, requestInit] = fetchCall;
    const payload = JSON.parse(String((requestInit as RequestInit).body));
    expect(payload.is_default).toBe(true);

    await waitFor(() => {
      const rows = screen.getAllByRole('row');
      expect(rows[1]?.textContent).not.toContain('Runtime default');
      expect(rows[2]?.textContent).toContain('Runtime default');
    });
  });

  it('sends tier effort changes when updating an edited profile (MoonLadderStudios/MoonMind#3348)', async () => {
    const fetchSpy = vi.spyOn(window, 'fetch').mockImplementation(async (input) => {
      const url = String(input);
      if (url.includes('/capabilities')) {
        return { ok: true, json: async () => ({ version: 'tier-cap-v1-test', profile_id: profile.profile_id, runtime_id: profile.runtime_id, provider_id: profile.provider_id, evidence: { source: 'profile_catalog_evidence', credential_generation: 1, image_ref: null, observed_at: null, stale: false }, tier_constraints: { min_count: 1, max_count: null }, model: { runtime_default: 'gpt-5.5', allow_custom: true, options: [{ value: 'gpt-5.5', label: 'GPT-5.5', description: null, status: 'available' }] }, effort: { supported: true, runtime_default: 'medium', allow_custom: false, application: 'native', options: [{ value: 'low', label: 'Low', description: null, status: 'available', compatible_models: null }, { value: 'medium', label: 'Medium', description: null, status: 'available', compatible_models: null }, { value: 'high', label: 'High', description: null, status: 'available', compatible_models: null }, { value: 'xhigh', label: 'Extra high', description: null, status: 'available', compatible_models: null }] }, diagnostics: [] }) } as Response;
      }
      return { ok: true, json: async () => ({ ...profile, model_tiers: [{ label: null, model: null, effort: 'high', parameters: {}, annotations: {} }], default_model_tier: 1 }) } as Response;
    });

    renderProviderProfilesManager([profile]);

    fireEvent.click(screen.getByRole('button', { name: 'Edit' }));
    const effortSelect = screen.getByLabelText('Tier 1 effort') as HTMLSelectElement;
    expect(effortSelect.value).toBe('__runtime_default__');

    fireEvent.change(effortSelect, { target: { value: 'high' } });
    fireEvent.click(screen.getByRole('button', { name: 'Update provider profile' }));

    await waitFor(() => {
      expect(fetchSpy).toHaveBeenCalledWith(
        '/api/v1/provider-profiles/codex-default',
        expect.objectContaining({
          method: 'PATCH',
        }),
      );
    });

    const tierSaveCall = fetchSpy.mock.calls.find((call) => { const init = call[1] as RequestInit | undefined; return String(call[0]).includes('/api/v1/provider-profiles/codex-default') && (init?.method === 'PATCH'); });
    const [, requestInit] = tierSaveCall ?? [];
    const payload = JSON.parse(String((requestInit as RequestInit)?.body ?? '{}'));
    expect(payload.model_tiers).toEqual([{ label: null, model: null, effort: 'high', parameters: {}, annotations: {} }]);
    expect(payload.default_model_tier).toBe(1);
    expect(payload).not.toHaveProperty('default_model');
    expect(payload).not.toHaveProperty('default_effort');
  });

  it('preserves cached model choices and backend-authorized custom entry during refresh', async () => {
    const staleCapabilities = { version: 'tier-cap-v1-stale', profile_id: profile.profile_id, runtime_id: profile.runtime_id, provider_id: profile.provider_id, evidence: { source: 'profile_catalog_evidence', credential_generation: 1, image_ref: null, observed_at: null, stale: true }, tier_constraints: { min_count: 1, max_count: null }, model: { runtime_default: 'gpt-5.5', allow_custom: true, options: [{ value: 'gpt-5.5', label: 'GPT-5.5', description: null, status: 'available' }, { value: 'gpt-4o', label: 'GPT-4o', description: null, status: 'available', compatible_models: null }] }, effort: { supported: true, runtime_default: 'medium', allow_custom: false, application: 'native', options: [{ value: 'medium', label: 'Medium', description: null, status: 'available', compatible_models: null }] }, diagnostics: [] };
    vi.spyOn(window, 'fetch').mockImplementation(async (input) => {
      const url = String(input);
      if (url.includes('/capabilities')) {
        return { ok: true, json: async () => staleCapabilities } as Response;
      }
      return { ok: true, json: async () => ({ ...profile }) } as Response;
    });
    const staleProfile: ProviderProfile = {
      ...profile,
      model_tiers: [{ label: null, model: 'gpt-4o', effort: null, parameters: {}, annotations: {} }],
      default_model_tier: 1,
    };

    renderProviderProfilesManager([staleProfile]);
    fireEvent.click(screen.getByRole('button', { name: 'Edit' }));

    await screen.findByText(/Model discovery is refreshing/);
    const modelSelect = screen.getByLabelText('Tier 1 model') as HTMLSelectElement;
    expect(modelSelect.value).toBe('gpt-4o');
    const options = within(modelSelect).getAllByRole('option') as HTMLOptionElement[];
    const byValue = new Map(options.map((o) => [o.value, o]));
    expect(byValue.get('__runtime_default__')?.disabled).toBe(false);
    expect(byValue.get('gpt-5.5')?.disabled).toBe(false);
    expect(byValue.get('gpt-4o')?.disabled).toBe(false);
    expect(within(modelSelect).getByRole('option', { name: 'Custom value…' })).toBeTruthy();
    fireEvent.change(modelSelect, { target: { value: '__custom__' } });
    const customInput = screen.getByLabelText('Tier 1 custom model') as HTMLInputElement;
    expect(customInput.disabled).toBe(false);
    fireEvent.change(customInput, { target: { value: 'opencode-go/manual-model' } });
    expect(customInput.value).toBe('opencode-go/manual-model');
  });

  it('offers a custom model entry when the backend allows custom models', async () => {
    const fetchSpy = vi.spyOn(window, 'fetch').mockImplementation(async (input) => {
      const url = String(input);
      if (url.includes('/capabilities')) {
        return { ok: true, json: async () => ({ version: 'tier-cap-v1-test', profile_id: profile.profile_id, runtime_id: profile.runtime_id, provider_id: profile.provider_id, evidence: { source: 'profile_catalog_evidence', credential_generation: 1, image_ref: null, observed_at: null, stale: false }, tier_constraints: { min_count: 1, max_count: null }, model: { runtime_default: 'gpt-5.5', allow_custom: true, options: [{ value: 'gpt-5.5', label: 'GPT-5.5', description: null, status: 'available' }] }, effort: { supported: true, runtime_default: 'medium', allow_custom: false, application: 'native', options: [{ value: 'medium', label: 'Medium', description: null, status: 'available', compatible_models: null }] }, diagnostics: [] }) } as Response;
      }
      return { ok: true, json: async () => ({ ...profile, model_tiers: [{ label: null, model: null, effort: null, parameters: {}, annotations: {} }], default_model_tier: 1 }) } as Response;
    });

    renderProviderProfilesManager([profile]);
    fireEvent.click(screen.getByRole('button', { name: 'Edit' }));

    const modelSelect = await screen.findByLabelText('Tier 1 model') as HTMLSelectElement;
    // Model discovery is non-blocking, so the select renders before the backend's
    // allow_custom policy arrives. Wait for the authorized option, not just the select.
    await within(modelSelect).findByRole('option', { name: 'Custom value…' });
    fireEvent.change(modelSelect, { target: { value: '__custom__' } });
    const customInput = screen.getByLabelText('Tier 1 custom model') as HTMLInputElement;
    fireEvent.change(customInput, { target: { value: 'my-custom-model' } });
    fireEvent.click(screen.getByRole('button', { name: 'Update provider profile' }));

    await waitFor(() => {
      expect(fetchSpy).toHaveBeenCalledWith(
        '/api/v1/provider-profiles/codex-default',
        expect.objectContaining({ method: 'PATCH' }),
      );
    });
    const patchCall = fetchSpy.mock.calls.find((call) => { const init = call[1] as RequestInit | undefined; return String(call[0]).includes('/api/v1/provider-profiles/codex-default') && (init?.method === 'PATCH'); });
    const [, requestInit] = patchCall ?? [];
    const payload = JSON.parse(String((requestInit as RequestInit)?.body ?? '{}'));
    expect(payload.model_tiers).toEqual([{ label: null, model: 'my-custom-model', effort: null, parameters: {}, annotations: {} }]);
  });

  it('requires a backend-supported authentication method before creation', async () => {
    const fetchSpy = vi.spyOn(window, 'fetch').mockResolvedValue({
      ok: true,
      json: async () => ({
        version: 'provider-profile-creation-v1',
        runtime_id: 'unknown-runtime',
        provider_id: 'unknown-provider',
        supported: false,
        authentication_methods: [],
        diagnostics: ['No validated creation preset exists for this runtime and provider.'],
      }),
    } as Response);
    const { onNotice } = renderProviderProfilesManager();

    fireEvent.change(screen.getByLabelText(/Profile ID/), {
      target: { value: 'codex-default' },
    });
    fireEvent.change(screen.getByLabelText(/Runtime ID/), {
      target: { value: 'unknown-runtime' },
    });
    fireEvent.change(screen.getByLabelText(/Provider ID/), {
      target: { value: 'unknown-provider' },
    });
    await screen.findByText('No validated creation preset exists for this runtime and provider.');
    // MoonLadderStudios/MoonMind#4002: missing valid choices disable the
    // action with the specific reason instead of submitting.
    const action = screen.getByRole('button', { name: 'Create profile' });
    expect((action as HTMLButtonElement).disabled).toBe(true);
    expect(
      screen.getByText('Choose a supported authentication method to continue.'),
    ).toBeTruthy();
    fireEvent.click(action);

    expect(onNotice).not.toHaveBeenCalled();
    expect(fetchSpy.mock.calls.filter(([, init]) => init?.method === 'POST')).toHaveLength(0);
  });

  it('starts a Codex OAuth session from the profile OAuth action', async () => {
    const fetchSpy = vi.spyOn(window, 'fetch').mockResolvedValue({
      ok: true,
      json: async () => ({
        session_id: 'oas_settings_auth',
        runtime_id: 'codex_cli',
        profile_id: 'codex-oauth',
        status: 'pending',
        session_transport: 'moonmind_pty_ws',
      }),
    } as Response);
    const openSpy = vi.spyOn(window, 'open').mockReturnValue(null);

    renderProviderProfilesManager([codexOauthProfile]);

    fireEvent.click(screen.getByRole('button', { name: 'OAuth codex-oauth' }));

    await waitFor(() => {
      expect(fetchSpy).toHaveBeenCalledWith(
        '/api/v1/oauth-sessions',
        expect.objectContaining({ method: 'POST' }),
      );
    });
    const [, requestInit] = fetchSpy.mock.calls[0] ?? [];
    const payload = JSON.parse(String((requestInit as RequestInit).body));
    expect(payload).toMatchObject({
      runtime_id: 'codex_cli',
      profile_id: 'codex-oauth',
      volume_ref: 'codex_auth_volume',
      volume_mount_path: '/home/app/.codex',
      account_label: 'Codex account',
    });
    expect(openSpy).toHaveBeenCalledWith(
      '/oauth-terminal?session_id=oas_settings_auth',
      '_blank',
      'noopener,noreferrer',
    );
    expect(await screen.findByText('OAuth: Pending')).toBeTruthy();
  });

  it('shows a Tmate OAuth modal instead of opening the xterm terminal', async () => {
    vi.spyOn(window, 'fetch').mockResolvedValue({
      ok: true,
      json: async () => ({
        session_id: 'oas_settings_tmate',
        runtime_id: 'codex_cli',
        profile_id: 'codex-oauth',
        status: 'awaiting_user',
        terminal_session_id: 'https://tmate.io/t/oas_settings_tmate',
        terminal_bridge_id: 'ssh tmate.io/t/oas_settings_tmate',
        session_transport: 'tmate',
      }),
    } as Response);
    const openSpy = vi.spyOn(window, 'open').mockReturnValue(null);

    renderProviderProfilesManager([codexOauthProfile]);

    fireEvent.click(screen.getByRole('button', { name: 'OAuth codex-oauth' }));

    const dialog = await screen.findByRole('dialog', {
      name: 'Tmate OAuth session',
    });
    expect(dialog.textContent).toContain('https://tmate.io/t/oas_settings_tmate');
    expect(dialog.textContent).toContain('ssh tmate.io/t/oas_settings_tmate');
    expect(screen.getByRole('link', { name: 'Open Tmate' }).getAttribute('href')).toBe(
      'https://tmate.io/t/oas_settings_tmate',
    );
    expect(openSpy).not.toHaveBeenCalled();
  });

  it('updates the Tmate OAuth modal when terminal refs arrive from polling', async () => {
    const fetchSpy = vi.spyOn(window, 'fetch')
      .mockResolvedValueOnce({
        ok: true,
        json: async () => ({
          session_id: 'oas_settings_tmate_poll',
          runtime_id: 'codex_cli',
          profile_id: 'codex-oauth',
          status: 'awaiting_user',
          terminal_session_id: null,
          terminal_bridge_id: null,
          session_transport: 'tmate',
        }),
      } as Response)
      .mockResolvedValue({
        ok: true,
        json: async () => ({
          session_id: 'oas_settings_tmate_poll',
          runtime_id: 'codex_cli',
          profile_id: 'codex-oauth',
          status: 'awaiting_user',
          terminal_session_id: 'https://tmate.io/t/oas_settings_tmate_poll',
          terminal_bridge_id: 'ssh tmate.io/t/oas_settings_tmate_poll',
          session_transport: 'tmate',
        }),
      } as Response);
    vi.spyOn(window, 'open').mockReturnValue(null);

    renderProviderProfilesManager([codexOauthProfile]);

    fireEvent.click(screen.getByRole('button', { name: 'OAuth codex-oauth' }));

    const dialog = await screen.findByRole('dialog', {
      name: 'Tmate OAuth session',
    });
    expect(dialog.textContent).not.toContain('https://tmate.io/t/oas_settings_tmate_poll');

    await waitFor(
      () => {
        expect(fetchSpy).toHaveBeenCalledWith(
          '/api/v1/oauth-sessions/oas_settings_tmate_poll',
          expect.objectContaining({ headers: { Accept: 'application/json' } }),
        );
        expect(screen.getByRole('link', { name: 'Open Tmate' }).getAttribute('href')).toBe(
          'https://tmate.io/t/oas_settings_tmate_poll',
        );
      },
      { timeout: 7000 },
    );
    expect(dialog.textContent).toContain('ssh tmate.io/t/oas_settings_tmate_poll');
  }, 10000);

  it('supports OAuth finalize without offering reconnect after success', async () => {
    const fetchSpy = vi.spyOn(window, 'fetch')
      .mockResolvedValueOnce({
        ok: true,
        json: async () => ({
          session_id: 'oas_settings_finalize',
          runtime_id: 'codex_cli',
          profile_id: 'codex-oauth',
          status: 'awaiting_user',
          session_transport: 'moonmind_pty_ws',
        }),
      } as Response)
      .mockResolvedValueOnce({
        ok: true,
        json: async () => ({ status: 'succeeded' }),
      } as Response);
    vi.spyOn(window, 'open').mockReturnValue(null);
    const { queryClient } = renderProviderProfilesManager([codexOauthProfile]);
    const invalidateSpy = vi.spyOn(queryClient, 'invalidateQueries');

    fireEvent.click(screen.getByRole('button', { name: 'OAuth codex-oauth' }));

    expect(await screen.findByText('OAuth: Awaiting User')).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: 'Finalize codex-oauth' }));

    await waitFor(() => {
      expect(fetchSpy).toHaveBeenCalledWith(
        '/api/v1/oauth-sessions/oas_settings_finalize/finalize',
        expect.objectContaining({ method: 'POST' }),
      );
    });
    expect(await screen.findByText('OAuth: Succeeded')).toBeTruthy();
    expect(invalidateSpy).toHaveBeenCalledWith({ queryKey: PROVIDER_PROFILE_QUERY_KEY });
    expect(screen.queryByRole('button', { name: 'Retry codex-oauth' })).toBeNull();
  });

  it('refreshes provider profiles when an OAuth terminal finalizes in another tab', async () => {
    const { queryClient } = renderProviderProfilesManagerWithQuery([codexOauthProfile]);
    const invalidateSpy = vi.spyOn(queryClient, 'invalidateQueries');

    const storageEvent = new Event('storage');
    Object.defineProperty(storageEvent, 'key', {
      value: 'moonmind:provider-profile-updated',
    });
    Object.defineProperty(storageEvent, 'newValue', {
      value: JSON.stringify({
        profileId: 'codex-oauth',
        sessionId: 'oas_terminal_finalize',
      }),
    });
    window.dispatchEvent(storageEvent);

    await waitFor(() => {
      expect(invalidateSpy).toHaveBeenCalledWith({ queryKey: PROVIDER_PROFILE_QUERY_KEY });
    });
  });

  it('supports OAuth retry actions for failed Settings sessions', async () => {
    const fetchSpy = vi.spyOn(window, 'fetch')
      .mockResolvedValueOnce({
        ok: true,
        json: async () => ({
          session_id: 'oas_settings_failed',
          runtime_id: 'codex_cli',
          profile_id: 'codex-oauth',
          status: 'failed',
          failure_reason: 'runner startup failed',
          session_transport: 'moonmind_pty_ws',
        }),
      } as Response)
      .mockResolvedValueOnce({
        ok: true,
        json: async () => ({
          session_id: 'oas_settings_retry',
          runtime_id: 'codex_cli',
          profile_id: 'codex-oauth',
          status: 'pending',
          session_transport: 'moonmind_pty_ws',
        }),
      } as Response);
    vi.spyOn(window, 'open').mockReturnValue(null);

    renderProviderProfilesManager([codexOauthProfile]);

    fireEvent.click(screen.getByRole('button', { name: 'OAuth codex-oauth' }));

    expect(await screen.findByText('OAuth: Failed')).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: 'Retry codex-oauth' }));
    await waitFor(() => {
      expect(fetchSpy).toHaveBeenCalledWith(
        '/api/v1/oauth-sessions/oas_settings_failed/reconnect',
        expect.objectContaining({ method: 'POST' }),
      );
    });
  });

  it('shows distinct Claude credential method actions for supported claude_anthropic rows', () => {
    renderProviderProfilesManager([claudeCredentialProfile]);

    expect(screen.getByRole('button', { name: 'OAuth claude-anthropic' })).toBeTruthy();
    expect(screen.getByRole('button', { name: 'Use Anthropic API key claude-anthropic' })).toBeTruthy();
    expect(screen.getAllByRole('button', { name: 'OAuth claude-anthropic' })).toHaveLength(1);
    expect(screen.getByText('Setup required')).toBeTruthy();
    expect(screen.getByText(/Reason: Missing credentials/i)).toBeTruthy();
    expect(screen.getByText('Claude credentials not connected')).toBeTruthy();
  });

  it('shows setup-required OAuth action for Codex OAuth setup profiles', () => {
    renderProviderProfilesManager([
      {
        ...codexOauthProfile,
        profile_id: 'codex-openai-oauth',
        runtime_id: 'codex_cli',
        provider_id: 'openai',
        provider_label: 'OpenAI',
        volume_ref: 'codex_auth_volume',
        volume_mount_path: '/home/app/.codex',
        enabled: false,
        auth_state: 'not_configured',
        disabled_reason: 'missing_credentials',
      },
    ]);

    expect(screen.getByText('Setup required')).toBeTruthy();
    expect(screen.getByRole('button', { name: 'OAuth codex-openai-oauth' })).toBeTruthy();
    expect(screen.getByRole('button', { name: 'Enable' })).toHaveProperty('disabled', true);
  });

  it('enrolls a Codex OpenAI API key through the provider API-key endpoint', async () => {
    const fetchSpy = vi.spyOn(window, 'fetch').mockResolvedValue({
      ok: true,
      json: async () => ({
        status: 'ready',
        status_label: 'OpenAI API key ready',
        readiness: {
          connected: true,
          backing_secret_exists: true,
          launch_ready: true,
        },
      }),
    } as Response);

    renderProviderProfilesManager([codexApiKeySetupProfile]);
    fireEvent.click(
      screen.getByRole('button', {
        name: 'Use OpenAI API key codex-openai-api-key',
      }),
    );
    expect(
      screen.getByRole('dialog', {
        name: 'OpenAI API key enrollment for codex-openai-api-key',
      }),
    ).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: 'Continue to API key paste' }));
    fireEvent.change(screen.getByLabelText('OpenAI API key'), {
      target: { value: 'sk-openai-provider-test' },
    });
    fireEvent.click(
      screen.getByRole('button', { name: 'Validate and save OpenAI API key' }),
    );

    await waitFor(() => {
      expect(fetchSpy).toHaveBeenCalledWith(
        '/api/v1/provider-profiles/codex-openai-api-key/credentials/api-key',
        expect.objectContaining({
          method: 'POST',
          body: JSON.stringify({ api_key: 'sk-openai-provider-test' }),
        }),
      );
    });
    expect(
      fetchSpy.mock.calls.some(([input]) => String(input).includes('/oauth-sessions')),
    ).toBe(false);
  });

  it('shows supported Claude OAuth lifecycle actions for connected claude_anthropic rows', () => {
    renderProviderProfilesManager([connectedClaudeCredentialProfile]);

    expect(
      screen.getByRole('button', { name: 'OAuth claude-anthropic-connected' }),
    ).toBeTruthy();
    expect(
      screen.getByRole('button', { name: 'Use Anthropic API key claude-anthropic-connected' }),
    ).toBeTruthy();
    expect(screen.getByRole('button', { name: 'Validate OAuth claude-anthropic-connected' })).toBeTruthy();
    expect(screen.getByRole('button', { name: 'Disconnect OAuth claude-anthropic-connected' })).toBeTruthy();
    expect(screen.getAllByRole('button', { name: 'OAuth claude-anthropic-connected' })).toHaveLength(1);
    expect(screen.getByText('Claude OAuth ready')).toBeTruthy();
  });

  it('shows default API-key enrollment for Claude profiles without action metadata', () => {
    renderProviderProfilesManager([
      {
        ...claudeCredentialProfile,
        profile_id: 'claude-without-metadata',
        credential_source: 'secret_ref',
        runtime_materialization_mode: 'api_key_env',
        volume_ref: null,
        volume_mount_path: null,
        command_behavior: {},
      },
    ]);

    expect(screen.queryByRole('button', { name: /^OAuth / })).toBeNull();
    expect(
      screen.getByRole('button', { name: 'Use Anthropic API key claude-without-metadata' }),
    ).toBeTruthy();
    expect(screen.queryByRole('button', { name: /Validate OAuth/ })).toBeNull();
    expect(screen.queryByRole('button', { name: /Disconnect OAuth/ })).toBeNull();
  });

  it('shows Claude status without lifecycle actions when metadata has no actions', () => {
    renderProviderProfilesManager([
      {
        ...claudeCredentialProfile,
        profile_id: 'claude-status-only',
        command_behavior: {
          auth_strategy: 'claude_credential_methods',
          auth_state: 'enrollment_pending',
          auth_actions: [],
          auth_status_label: 'Claude enrollment pending',
        },
      },
    ]);

    expect(screen.getByText('Claude enrollment pending')).toBeTruthy();
    expect(screen.queryByRole('button', { name: /^OAuth / })).toBeNull();
    expect(screen.queryByRole('button', { name: /Use Anthropic API key/ })).toBeNull();
    expect(screen.queryByRole('button', { name: /Validate OAuth/ })).toBeNull();
    expect(screen.queryByRole('button', { name: /Disconnect OAuth/ })).toBeNull();
  });

  it('runs Claude OAuth lifecycle actions through API endpoints', async () => {
    const fetchSpy = vi.spyOn(window, 'fetch').mockResolvedValue({
      ok: true,
      json: async () => ({ status: 'ready' }),
    } as Response);
    const { onNotice } = renderProviderProfilesManager([connectedClaudeCredentialProfile]);

    fireEvent.click(screen.getByRole('button', { name: 'Validate OAuth claude-anthropic-connected' }));

    await waitFor(() => {
      expect(fetchSpy).toHaveBeenCalledWith(
        '/api/v1/provider-profiles/claude-anthropic-connected/oauth/validate',
        expect.objectContaining({ method: 'POST' }),
      );
    });
    expect(onNotice).toHaveBeenCalledWith({
      level: 'ok',
      text: 'Claude OAuth validated for "claude-anthropic-connected".',
    });

    fireEvent.click(screen.getByRole('button', { name: 'Disconnect OAuth claude-anthropic-connected' }));

    await waitFor(() => {
      expect(fetchSpy).toHaveBeenCalledWith(
        '/api/v1/provider-profiles/claude-anthropic-connected/oauth/disconnect',
        expect.objectContaining({ method: 'POST' }),
      );
    });
  });

  it('starts a Claude OAuth session from the OAuth credential method action', async () => {
    const fetchSpy = vi.spyOn(window, 'fetch').mockResolvedValue({
      ok: true,
      json: async () => ({
        session_id: 'oas_claude_settings_auth',
        runtime_id: 'claude_code',
        profile_id: 'claude-anthropic',
        status: 'pending',
        session_transport: 'moonmind_pty_ws',
      }),
    } as Response);
    const openSpy = vi.spyOn(window, 'open').mockReturnValue(null);

    renderProviderProfilesManager([claudeCredentialProfile]);

    fireEvent.click(screen.getByRole('button', { name: 'OAuth claude-anthropic' }));

    await waitFor(() => {
      expect(fetchSpy).toHaveBeenCalledWith(
        '/api/v1/oauth-sessions',
        expect.objectContaining({ method: 'POST' }),
      );
    });
    const [, requestInit] = fetchSpy.mock.calls[0] ?? [];
    const payload = JSON.parse(String((requestInit as RequestInit).body));
    expect(payload).toMatchObject({
      runtime_id: 'claude_code',
      profile_id: 'claude-anthropic',
      volume_ref: 'claude_auth_volume',
      volume_mount_path: '/home/app/.claude',
      account_label: 'Claude Anthropic OAuth',
    });
    expect(openSpy).toHaveBeenCalledWith(
      '/oauth-terminal?session_id=oas_claude_settings_auth',
      '_blank',
      'noopener,noreferrer',
    );
    expect(screen.queryByRole('dialog')).toBeNull();
  });

  it('opens an Anthropic API-key enrollment drawer without terminal OAuth wording', () => {
    renderProviderProfilesManager([claudeCredentialProfile]);

    fireEvent.click(screen.getByRole('button', { name: 'Use Anthropic API key claude-anthropic' }));

    const dialog = screen.getByRole('dialog', {
      name: 'Anthropic API key enrollment for claude-anthropic',
    });
    expect(dialog).toBeTruthy();
    expect(screen.getByText('not connected')).toBeTruthy();
    expect(screen.getByText('awaiting external step')).toBeTruthy();
    expect(screen.getByText(/Use an Anthropic API key for Claude Code launches/i)).toBeTruthy();
    expect(dialog.textContent).not.toMatch(/terminal OAuth/i);
  });

  it('advances to secure token paste and blocks empty submission', async () => {
    const { onNotice } = renderProviderProfilesManager([claudeCredentialProfile]);

    fireEvent.click(screen.getByRole('button', { name: 'Use Anthropic API key claude-anthropic' }));
    fireEvent.click(screen.getByRole('button', { name: 'Continue to API key paste' }));

    expect(screen.getByText('awaiting token paste')).toBeTruthy();
    expect((screen.getByLabelText('Anthropic API key') as HTMLInputElement).type).toBe('password');

    fireEvent.click(screen.getByRole('button', { name: 'Validate and save Anthropic API key' }));

    await waitFor(() => {
      expect(onNotice).toHaveBeenCalledWith({
        level: 'error',
        text: 'Anthropic API key is required.',
      });
    });
  });

  it('submits the Anthropic API key through lifecycle states and never calls OAuth sessions', async () => {
    const submittedToken = 'sk-ant-test-token';
    const fetchSpy = vi.spyOn(window, 'fetch').mockResolvedValue({
      ok: true,
      json: async () => ({
        status: 'ready',
        status_label: 'Anthropic API key ready',
        readiness: {
          connected: true,
          last_validated_at: '2026-04-22T08:30:00Z',
          backing_secret_exists: true,
          launch_ready: true,
        },
      }),
    } as Response);

    renderProviderProfilesManager([claudeCredentialProfile]);

    fireEvent.click(screen.getByRole('button', { name: 'Use Anthropic API key claude-anthropic' }));
    fireEvent.click(screen.getByRole('button', { name: 'Continue to API key paste' }));
    fireEvent.change(screen.getByLabelText('Anthropic API key'), {
      target: { value: submittedToken },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Validate and save Anthropic API key' }));

    await waitFor(() => {
      expect(fetchSpy).toHaveBeenCalledWith(
        '/api/v1/provider-profiles/claude-anthropic/manual-auth/commit',
        expect.objectContaining({ method: 'POST' }),
      );
    });
    const requestedUrls = fetchSpy.mock.calls.map(([url]) => String(url));
    expect(requestedUrls.some((url) => url.includes('/api/v1/oauth-sessions'))).toBe(false);
    const [, requestInit] = fetchSpy.mock.calls[0] ?? [];
    const payload = JSON.parse(String((requestInit as RequestInit).body));
    expect(payload).toEqual({ token: submittedToken });

    // MoonLadderStudios/MoonMind#4002: bounded pending is shown only while the
    // real request is outstanding, then the returned outcome — timed client
    // animations after the commit are not presented as backend phases.
    expect(await screen.findByText('validating token')).toBeTruthy();
    expect(await screen.findByText('ready')).toBeTruthy();
    expect(screen.queryByText('saving secret')).toBeNull();
    expect(screen.queryByText('updating profile')).toBeNull();
    expect(screen.queryByDisplayValue(submittedToken)).toBeNull();
    expect(await screen.findByText('Anthropic API key ready')).toBeTruthy();
  });

  it('ignores stale Claude enrollment responses after another profile is opened', async () => {
    const submittedToken = 'sk-ant-stale-token';
    let resolveCommit: (response: Response) => void = (_response: Response) => {
      throw new Error('Claude commit resolver was not initialized.');
    };
    const fetchSpy = vi.spyOn(window, 'fetch').mockImplementation(
      () =>
        new Promise<Response>((resolve) => {
          resolveCommit = resolve;
        }),
    );
    const secondClaudeProfile: ProviderProfile = {
      ...claudeCredentialProfile,
      profile_id: 'claude-anthropic-secondary',
    };
    const { onNotice } = renderProviderProfilesManager([
      claudeCredentialProfile,
      secondClaudeProfile,
    ]);

    fireEvent.click(screen.getByRole('button', { name: 'Use Anthropic API key claude-anthropic' }));
    fireEvent.click(screen.getByRole('button', { name: 'Continue to API key paste' }));
    fireEvent.change(screen.getByLabelText('Anthropic API key'), {
      target: { value: submittedToken },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Validate and save Anthropic API key' }));

    await waitFor(() => {
      expect(fetchSpy).toHaveBeenCalledWith(
        '/api/v1/provider-profiles/claude-anthropic/manual-auth/commit',
        expect.objectContaining({ method: 'POST' }),
      );
    });

    fireEvent.click(
      screen.getByRole('button', { name: 'Use Anthropic API key claude-anthropic-secondary' }),
    );

    resolveCommit({
      ok: true,
      json: async () => ({
        status: 'ready',
        status_label: 'Stale Anthropic API key ready',
        readiness: { connected: true },
      }),
    } as Response);

    await new Promise((resolve) => window.setTimeout(resolve, 800));

    expect(
      screen.getByRole('dialog', {
        name: 'Anthropic API key enrollment for claude-anthropic-secondary',
      }),
    ).toBeTruthy();
    expect(screen.queryByText('Stale Anthropic API key ready')).toBeNull();
    expect(onNotice).not.toHaveBeenCalledWith(
      expect.objectContaining({ text: expect.stringContaining('claude-anthropic"') }),
    );
  });

  it('clears pasted token state after cancellation', () => {
    renderProviderProfilesManager([claudeCredentialProfile]);

    fireEvent.click(screen.getByRole('button', { name: 'Use Anthropic API key claude-anthropic' }));
    fireEvent.click(screen.getByRole('button', { name: 'Continue to API key paste' }));
    fireEvent.change(screen.getByLabelText('Anthropic API key'), {
      target: { value: 'sk-ant-cancelled-token' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Cancel API key enrollment' }));

    expect(screen.queryByRole('dialog')).toBeNull();

    fireEvent.click(screen.getByRole('button', { name: 'Use Anthropic API key claude-anthropic' }));
    fireEvent.click(screen.getByRole('button', { name: 'Continue to API key paste' }));

    expect((screen.getByLabelText('Anthropic API key') as HTMLInputElement).value).toBe('');
  });

  it('redacts validation failure text before rendering it', async () => {
    const submittedToken = 'sk-ant-submitted-secret';
    vi.spyOn(window, 'fetch').mockResolvedValue({
      ok: false,
      json: async () => ({
        detail: {
          message: `Validation failed for ${submittedToken} and sk-ant-provider-secret`,
        },
      }),
    } as Response);

    renderProviderProfilesManager([claudeCredentialProfile]);

    fireEvent.click(screen.getByRole('button', { name: 'Use Anthropic API key claude-anthropic' }));
    fireEvent.click(screen.getByRole('button', { name: 'Continue to API key paste' }));
    fireEvent.change(screen.getByLabelText('Anthropic API key'), {
      target: { value: submittedToken },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Validate and save Anthropic API key' }));

    expect(await screen.findByText(/Validation failed for/)).toBeTruthy();
    expect(screen.getByText(/REDACTED/)).toBeTruthy();
    expect(screen.queryByText(submittedToken)).toBeNull();
    expect(screen.queryByText(/sk-ant-provider-secret/)).toBeNull();
    expect(screen.getByText('failed')).toBeTruthy();

    fireEvent.click(screen.getByRole('button', { name: 'Return to API key paste' }));

    expect((screen.getByLabelText('Anthropic API key') as HTMLInputElement).value).toBe('');
  });

  it('renders structured Claude readiness metadata in the status column', () => {
    renderProviderProfilesManager([readyClaudeCredentialProfile]);

    expect(screen.getByText('Claude OAuth ready')).toBeTruthy();
    expect(screen.getByText('Claude connection: Connected')).toBeTruthy();
    expect(screen.getByText('Last validated: 2026-04-22T08:30:00Z')).toBeTruthy();
    expect(screen.getByText('Backing secret: Present')).toBeTruthy();
    expect(screen.getByText('Launch readiness: Ready')).toBeTruthy();
    expect(screen.getByText(/Previous token/)).toBeTruthy();
    expect(screen.queryByText(/sk-ant-secret/)).toBeNull();
  });

  it('renders provider profile readiness and launch metadata', () => {
    renderProviderProfilesManager([profileWithReadiness]);

    expect(screen.getByText('Readiness: Blocked')).toBeTruthy();
    expect(screen.getByText('Provider profile has launch blockers.')).toBeTruthy();
    expect(screen.getByText('Concurrency: 3')).toBeTruthy();
    expect(screen.getByText('Cooldown: 120s')).toBeTruthy();
    expect(screen.getByText('Priority: 250')).toBeTruthy();
    expect(screen.getByText('Tags: team, fast')).toBeTruthy();
    expect(screen.getByText('provider_api_key')).toBeTruthy();
    expect(screen.getByText('db://openai-team-key')).toBeTruthy();
    expect(screen.getByText(/Validation failed/)).toBeTruthy();
    expect(screen.queryByText(/sk-ant/)).toBeNull();
  });

  it('condenses verbose status diagnostics behind a disclosure without losing detail', () => {
    renderProviderProfilesManager([profileWithReadiness]);

    const statusCell = document.querySelector('td[data-label="Status"]');
    expect(statusCell).not.toBeNull();

    // The readiness pill stays in the compact, always-visible summary so the
    // Status column no longer needs to render its full diagnostic stack inline.
    const readinessPill = screen.getByText('Readiness: Blocked');

    const disclosure = statusCell?.querySelector<HTMLDetailsElement>(
      'details.provider-profile-status-details',
    );
    expect(disclosure).not.toBeNull();
    // Collapsed by default to reduce width/height pressure.
    expect(disclosure?.open).toBe(false);
    // The pill is part of the visible summary, not buried in the disclosure.
    expect(disclosure?.contains(readinessPill)).toBe(false);
    // No information is lost: the dense diagnostics live inside the disclosure.
    expect(disclosure?.textContent).toContain('Provider profile has launch blockers.');
    expect(disclosure?.textContent).toContain('Validation failed');
  });

  it('omits the diagnostics disclosure for a healthy connected profile with no other details', () => {
    renderProviderProfilesManager([profile]);

    const statusCell = document.querySelector('td[data-label="Status"]');
    expect(statusCell).not.toBeNull();

    // A fully healthy, connected profile resolves to a 'Connected' activation
    // label and no other diagnostics. Rendering the disclosure here would only
    // produce an empty "Diagnostics" dropdown containing the word "Connected",
    // so the disclosure should be omitted entirely.
    const disclosure = statusCell?.querySelector<HTMLDetailsElement>(
      'details.provider-profile-status-details',
    );
    expect(disclosure).toBeNull();
    expect(screen.queryByText('Diagnostics')).toBeNull();
  });

  it('describes SecretRef role bindings without plaintext values', () => {
    const rawSecret = 'sk-test-plaintext-never-render';
    renderProviderProfilesManager([
      {
        ...profileWithReadiness,
        secret_refs: {
          anthropic_api_key: 'db://claude-team-key',
        },
        readiness: {
          status: 'ready',
          launch_ready: true,
          summary: 'Provider profile is ready for launch.',
          checks: [],
        },
      },
    ]);

    expect(screen.getByText('anthropic_api_key')).toBeTruthy();
    expect(screen.getByText('db://claude-team-key')).toBeTruthy();
    expect(screen.getByText(/Role-aware SecretRefs/)).toBeTruthy();
    expect(screen.queryByText(rawSecret)).toBeNull();
  });

  it('shows read-only profile details without write controls', () => {
    renderReadOnlyProviderProfilesManager([profileWithReadiness]);

    expect(screen.getByText('codex-diagnostic')).toBeTruthy();
    expect(screen.getByText('Readiness: Blocked')).toBeTruthy();
    expect(screen.getByText('Provider profile has launch blockers.')).toBeTruthy();
    expect(screen.queryByRole('button', { name: 'Edit' })).toBeNull();
    expect(screen.queryByRole('button', { name: 'Disable' })).toBeNull();
    expect(screen.queryByRole('button', { name: 'Delete' })).toBeNull();
    expect(screen.queryByRole('button', { name: 'Create profile' })).toBeNull();
    expect(screen.queryByText('Create Provider Profile')).toBeNull();
  });
});

describe('MoonLadderStudios/MoonMind#3820 guided provider-profile creation', () => {
  const openAiCapabilities = {
    version: 'provider-profile-creation-v1',
    runtime_id: 'codex_cli',
    provider_id: 'openai',
    supported: true,
    authentication_methods: [
      {
        id: 'oauth',
        label: 'OAuth',
        setup_action: 'oauth',
        launch_ready_after_setup: true,
        fields: {
          credential_source: {
            value: 'oauth_volume',
            source: 'runtime_provider_strategy',
            editable: false,
            lock_reason: 'OAuth enrollment owns the credential source.',
          },
          runtime_materialization_mode: {
            value: 'oauth_home',
            source: 'runtime_provider_strategy',
            editable: false,
            lock_reason: 'OAuth enrollment owns runtime materialization.',
          },
        },
        secret_roles: [],
        imported_volume: {
          supported: true,
          mount_path: '/home/app/.codex',
          source: 'runtime_provider_strategy',
          lock_reason: 'The runtime strategy owns the credential mount path.',
        },
      },
      {
        id: 'api_key',
        label: 'API key',
        setup_action: 'api_key',
        launch_ready_after_setup: true,
        fields: {
          credential_source: {
            value: 'secret_ref',
            source: 'runtime_provider_strategy',
            editable: false,
            lock_reason: 'Guided API-key setup owns the credential source.',
          },
          runtime_materialization_mode: {
            value: 'api_key_env',
            source: 'runtime_provider_strategy',
            editable: false,
            lock_reason: 'Guided API-key setup owns runtime materialization.',
          },
        },
        secret_roles: [
          {
            role: 'openai_api_key',
            label: 'OpenAI API key',
            required: true,
            compatible_schemes: ['db', 'env'],
          },
        ],
        imported_volume: {
          supported: false,
          mount_path: null,
          source: 'runtime_provider_strategy',
          lock_reason: 'API-key setup does not use a credential volume.',
        },
      },
    ],
    diagnostics: [],
  };

  function tierCapabilitiesResponse(url: string): Response | null {
    if (url.startsWith('/api/v1/provider-profiles/capabilities?')) {
      return {
        ok: true,
        json: async () => ({
          version: 'tier-cap-v1-test',
          profile_id: null,
          runtime_id: 'codex_cli',
          provider_id: 'openai',
          evidence: { source: 'runtime_draft', credential_generation: null, image_ref: null, observed_at: null, stale: false },
          tier_constraints: { min_count: 1, max_count: null },
          model: { runtime_default: 'gpt-5.5', allow_custom: true, options: [{ value: 'gpt-5.5', label: 'GPT-5.5', description: 'General coding model', status: 'available', recommended: true }, { value: 'gpt-4o', label: 'GPT-4o', description: null, status: 'available', recommended: false }] },
          effort: { supported: true, runtime_default: 'medium', allow_custom: false, application: 'native', options: [{ value: 'low', label: 'Low', description: null, status: 'available', compatible_models: null }, { value: 'medium', label: 'Medium', description: null, status: 'available', compatible_models: null }, { value: 'high', label: 'High', description: null, status: 'available', compatible_models: null }, { value: 'xhigh', label: 'Extra high', description: null, status: 'available', compatible_models: null }] },
          diagnostics: [],
        }),
      } as Response;
    }
    if (/\/api\/v1\/provider-profiles\/[^/]+\/capabilities/.test(url)) {
      return {
        ok: true,
        json: async () => ({
          version: 'tier-cap-v1-test-profile',
          profile_id: 'test-profile',
          runtime_id: 'codex_cli',
          provider_id: 'openai',
          evidence: { source: 'profile_catalog_evidence', credential_generation: 1, image_ref: null, observed_at: null, stale: false },
          tier_constraints: { min_count: 1, max_count: null },
          model: { runtime_default: 'gpt-5.5', allow_custom: true, options: [{ value: 'gpt-5.5', label: 'GPT-5.5', description: 'General coding model', status: 'available', recommended: true }, { value: 'gpt-4o', label: 'GPT-4o', description: null, status: 'available', recommended: false }] },
          effort: { supported: true, runtime_default: 'medium', allow_custom: false, application: 'native', options: [{ value: 'low', label: 'Low', description: null, status: 'available', compatible_models: null }, { value: 'medium', label: 'Medium', description: null, status: 'available', compatible_models: null }, { value: 'high', label: 'High', description: null, status: 'available', compatible_models: null }, { value: 'xhigh', label: 'Extra high', description: null, status: 'available', compatible_models: null }] },
          diagnostics: [],
        }),
      } as Response;
    }
    return null;
  }

  function openAiCreationResponse(url: string): Response | null {
    const tier = tierCapabilitiesResponse(url);
    if (tier) return tier;
    if (url.startsWith('/api/v1/provider-profiles/creation-capabilities?')) {
      return { ok: true, json: async () => openAiCapabilities } as Response;
    }
    if (!url.startsWith('/api/v1/provider-profiles/creation-preset?')) {
      return null;
    }
    const authenticationMethod = new URL(url, 'https://moonmind.test').searchParams.get(
      'authentication_method',
    ) as 'oauth' | 'api_key';
    const capability = openAiCapabilities.authentication_methods.find(
      (method) => method.id === authenticationMethod,
    );
    const field = (value: unknown, editable = true, source = 'test_policy') => ({
      value,
      source,
      editable,
      required: false,
      lock_reason: editable ? null : 'Backend controlled.',
    });
    return {
      ok: true,
      json: async () => ({
        version: openAiCapabilities.version,
        supported: Boolean(capability),
        runtime_id: 'codex_cli',
        provider_id: 'openai',
        authentication_method: authenticationMethod,
        fields: capability
          ? {
              credential_source: field('none', false),
              runtime_materialization_mode: field(
                capability.fields.runtime_materialization_mode.value,
                false,
              ),
              secret_refs: field({}),
              volume_ref: field(null, false),
              volume_mount_path: field(null, false),
              max_parallel_runs: field(1, authenticationMethod !== 'oauth'),
              cooldown_after_429_seconds: field(300),
              rate_limit_policy: field('backoff'),
              enabled: field(false, false),
              is_default: field(false),
              command_behavior: field({}, false),
              user_tags: field([]),
              priority: field(100),
              clear_env_keys: field([], false),
            }
          : {},
        diagnostics: [],
        manual_creation_allowed: false,
        required_manual_fields: [],
      }),
    } as Response;
  }

  function mockCreationCapabilities() {
    return vi.spyOn(window, 'fetch').mockImplementation(async (input) => {
      const url = String(input);
      const creationResponse = openAiCreationResponse(url);
      if (creationResponse) return creationResponse;
      throw new Error(`Unexpected fetch: ${url}`);
    });
  }

  async function selectOpenAiApiKeyCreation() {
    fireEvent.change(screen.getByLabelText(/Runtime ID/), {
      target: { value: 'codex_cli' },
    });
    fireEvent.change(screen.getByLabelText(/Provider ID/), {
      target: { value: 'openai' },
    });
    const apiKey = await screen.findByLabelText('API key');
    fireEvent.click(apiKey);
  }

  it('shows capability-derived authentication while low-level plumbing stays collapsed', async () => {
    mockCreationCapabilities();
    renderProviderProfilesManager();

    await selectOpenAiApiKeyCreation();

    expect(screen.getByLabelText('OAuth')).toBeTruthy();
    expect(screen.getByLabelText('API key')).toBeTruthy();
    expect(screen.getByText('Connection: Setup required')).toBeTruthy();
    expect(
      screen.getByText('Launch readiness: Blocked until API-key setup succeeds.'),
    ).toBeTruthy();
    expect(screen.queryByLabelText('Credential source')).toBeNull();
    expect(screen.queryByLabelText('Materialization mode')).toBeNull();
    expect(screen.queryByLabelText(/Secret refs \(JSON/)).toBeNull();
    expect(screen.queryByLabelText('Volume ref')).toBeNull();

    const advanced = screen.getByLabelText('Show advanced options') as HTMLInputElement;
    expect(advanced.checked).toBe(false);
    fireEvent.click(advanced);

    expect(screen.getByText('Credential source: secret_ref')).toBeTruthy();
    expect(screen.getByText('Source: runtime_provider_strategy')).toBeTruthy();
    expect(
      screen.getByText('Locked: Guided API-key setup owns the credential source.'),
    ).toBeTruthy();
    expect(screen.getByLabelText('OpenAI API key (required)')).toBeTruthy();
    expect(screen.getByText('Compatible references: db://, env://')).toBeTruthy();
    expect(screen.queryByText('Owned by enrollment')).toBeNull();
    expect(screen.getAllByText('Not used')).toHaveLength(2);
  });

  it.each([
    'in order',
    'late OAuth preset',
    'late OAuth error',
  ])('creates a backend-preset profile and opens the one-way OpenAI API-key drawer (%s)', async (responseOrder) => {
    let finishOAuthPreset!: () => void;
    const oauthPresetPending = new Promise<void>((resolve) => {
      finishOAuthPreset = resolve;
    });
    let oauthPresetSignal: AbortSignal | null | undefined;
    const savedProfile = {
      profile_id: 'codex-guided-key',
      runtime_id: 'codex_cli',
      provider_id: 'openai',
      authentication_method: 'api_key',
      credential_source: 'secret_ref',
      runtime_materialization_mode: 'api_key_env',
      secret_refs: {},
      max_parallel_runs: 1,
      cooldown_after_429_seconds: 300,
      rate_limit_policy: 'backoff',
      enabled: false,
      auth_state: 'api_key_pending',
      disabled_reason: 'missing_credentials',
      creation_capabilities: openAiCapabilities,
    } as ProviderProfile;
    const fetchSpy = vi.spyOn(window, 'fetch').mockImplementation(async (input, init) => {
      const url = String(input);
      const creationResponse = openAiCreationResponse(url);
      if (
        responseOrder !== 'in order' &&
        url.includes('/creation-preset?') &&
        url.includes('authentication_method=oauth')
      ) {
        oauthPresetSignal = init?.signal;
        return {
          ok: responseOrder !== 'late OAuth error',
          json: async () => {
            await oauthPresetPending;
            return responseOrder === 'late OAuth error'
              ? { detail: 'Superseded OAuth preset failed.' }
              : creationResponse!.json();
          },
        } as Response;
      }
      if (creationResponse) return creationResponse;
      if (url === '/api/v1/provider-profiles') {
        const payload = JSON.parse(String(init?.body));
        expect(payload).toEqual(
          expect.objectContaining({
            profile_id: 'codex-guided-key',
            authentication_method: 'api_key',
            preset_version: 'provider-profile-creation-v1',
          }),
        );
        expect(payload).not.toHaveProperty('credential_source');
        expect(payload).not.toHaveProperty('runtime_materialization_mode');
        expect(payload).not.toHaveProperty('priority');
        return { ok: true, json: async () => savedProfile } as Response;
      }
      throw new Error(`Unexpected fetch: ${url}`);
    });
    renderProviderProfilesManager();

    fireEvent.change(screen.getByLabelText(/Profile ID/), {
      target: { value: 'codex-guided-key' },
    });
    await selectOpenAiApiKeyCreation();
    await screen.findByText(/Backend preset provider-profile-creation-v1 loaded/);
    if (responseOrder !== 'in order') {
      // Decode the superseded request after the selected API-key preset loads.
      expect(oauthPresetSignal?.aborted).toBe(true);
      await act(async () => finishOAuthPreset());
      expect(screen.queryByText('Superseded OAuth preset failed.')).toBeNull();
    }
    // MoonLadderStudios/MoonMind#4002: action agrees with the guided continuation.
    fireEvent.click(screen.getByRole('button', { name: 'Create and connect' }));

    expect(
      await screen.findByRole('dialog', { name: 'OpenAI API key enrollment for codex-guided-key' }),
    ).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: 'Continue to API key paste' }));
    const keyInput = screen.getByLabelText('OpenAI API key') as HTMLInputElement;
    fireEvent.change(keyInput, { target: { value: 'one-way-test-key' } });
    expect(keyInput.value).toBe('one-way-test-key');
    fireEvent.click(screen.getByRole('button', { name: 'Cancel API key enrollment' }));

    expect(screen.queryByDisplayValue('one-way-test-key')).toBeNull();
    expect(fetchSpy).toHaveBeenCalledTimes(5);
  });

  it('keeps a selected existing SecretRef and skips plaintext enrollment', async () => {
    const savedProfile = {
      profile_id: 'codex-guided-existing-key',
      runtime_id: 'codex_cli',
      provider_id: 'openai',
      authentication_method: 'api_key',
      credential_source: 'secret_ref',
      runtime_materialization_mode: 'api_key_env',
      secret_refs: { openai_api_key: 'db://OPENAI_API_KEY' },
      max_parallel_runs: 1,
      cooldown_after_429_seconds: 300,
      rate_limit_policy: 'backoff',
      enabled: true,
      auth_state: 'connected',
      creation_capabilities: openAiCapabilities,
    } as ProviderProfile;
    const fetchSpy = vi.spyOn(window, 'fetch').mockImplementation(async (input, init) => {
      const url = String(input);
      const creationResponse = openAiCreationResponse(url);
      if (creationResponse) return creationResponse;
      if (url === '/api/v1/provider-profiles') {
        expect(JSON.parse(String(init?.body))).toEqual(
          expect.objectContaining({
            secret_refs: { openai_api_key: 'db://OPENAI_API_KEY' },
          }),
        );
        return { ok: true, json: async () => savedProfile } as Response;
      }
      throw new Error(`Unexpected fetch: ${url}`);
    });
    renderProviderProfilesManager();

    fireEvent.change(screen.getByLabelText(/Profile ID/), {
      target: { value: 'codex-guided-existing-key' },
    });
    await selectOpenAiApiKeyCreation();
    await screen.findByText(/Backend preset provider-profile-creation-v1 loaded/);
    fireEvent.click(screen.getByLabelText('Show advanced options'));
    fireEvent.change(screen.getByLabelText('OpenAI API key (required)'), {
      target: { value: 'db://OPENAI_API_KEY' },
    });
    // MoonLadderStudios/MoonMind#4002: guided continuation.
    fireEvent.click(screen.getByRole('button', { name: 'Create and connect' }));

    await waitFor(() => expect(fetchSpy.mock.calls.some(([, init]) => init?.method === 'POST')).toBe(true));
    expect(
      screen.queryByRole('dialog', {
        name: 'OpenAI API key enrollment for codex-guided-existing-key',
      }),
    ).toBeNull();
  });

  it('creates OAuth setup without typed volume metadata and starts enrollment', async () => {
    const openSpy = vi.spyOn(window, 'open').mockReturnValue(null);
    const savedProfile = {
      profile_id: 'codex-guided-oauth',
      runtime_id: 'codex_cli',
      provider_id: 'openai',
      authentication_method: 'oauth',
      credential_source: 'none',
      runtime_materialization_mode: 'api_key_env',
      secret_refs: {},
      max_parallel_runs: 1,
      cooldown_after_429_seconds: 300,
      rate_limit_policy: 'backoff',
      enabled: false,
      auth_state: 'oauth_pending',
      disabled_reason: 'missing_credentials',
      volume_ref: 'moonmind_oauth_guided',
      volume_mount_path: '/home/app/.codex',
      creation_capabilities: openAiCapabilities,
    } as ProviderProfile;
    const fetchSpy = vi.spyOn(window, 'fetch').mockImplementation(async (input, init) => {
      const url = String(input);
      const creationResponse = openAiCreationResponse(url);
      if (creationResponse) return creationResponse;
      if (url === '/api/v1/provider-profiles') {
        const payload = JSON.parse(String(init?.body));
        expect(payload.authentication_method).toBe('oauth');
        expect(payload).not.toHaveProperty('volume_ref');
        expect(payload).not.toHaveProperty('volume_mount_path');
        return { ok: true, json: async () => savedProfile } as Response;
      }
      if (url === '/api/v1/oauth-sessions') {
        expect(JSON.parse(String(init?.body))).toEqual(
          expect.objectContaining({
            runtime_id: 'codex_cli',
            provider_id: 'openai',
            profile_id: 'codex-guided-oauth',
            volume_ref: 'moonmind_oauth_guided',
            volume_mount_path: '/home/app/.codex',
          }),
        );
        return {
          ok: true,
          json: async () => ({
            session_id: 'oas_guided',
            runtime_id: 'codex_cli',
            profile_id: 'codex-guided-oauth',
            status: 'pending',
            session_transport: 'moonmind_pty_ws',
          }),
        } as Response;
      }
      throw new Error(`Unexpected fetch: ${url}`);
    });
    renderProviderProfilesManager();

    fireEvent.change(screen.getByLabelText(/Profile ID/), {
      target: { value: 'codex-guided-oauth' },
    });
    fireEvent.change(screen.getByLabelText(/Runtime ID/), {
      target: { value: 'codex_cli' },
    });
    fireEvent.change(screen.getByLabelText(/Provider ID/), {
      target: { value: 'openai' },
    });
    fireEvent.click(await screen.findByLabelText('OAuth'));
    await screen.findByText(/Backend preset provider-profile-creation-v1 loaded/);
    // MoonLadderStudios/MoonMind#4002: guided OAuth continuation.
    fireEvent.click(screen.getByRole('button', { name: 'Create and connect' }));

    await waitFor(() => expect(openSpy).toHaveBeenCalledTimes(1));
    expect(fetchSpy).toHaveBeenCalledTimes(5);
  });

  it('preserves an unknown existing SecretRef role for inspection', () => {
    const profileWithUnknownRole = {
      profile_id: 'codex-unknown-role',
      runtime_id: 'codex_cli',
      provider_id: 'openai',
      credential_source: 'secret_ref',
      runtime_materialization_mode: 'api_key_env',
      secret_refs: {
        openai_api_key: 'db://OPENAI_API_KEY',
        future_provider_role: 'env://FUTURE_PROVIDER_TOKEN',
      },
      max_parallel_runs: 1,
      cooldown_after_429_seconds: 300,
      rate_limit_policy: 'backoff',
      enabled: true,
      auth_state: 'connected',
      creation_capabilities: openAiCapabilities,
    } as ProviderProfile;

    renderProviderProfilesManager([profileWithUnknownRole]);
    fireEvent.click(screen.getByRole('button', { name: 'Edit' }));

    expect((screen.getByLabelText('Show advanced options') as HTMLInputElement).checked).toBe(true);

    expect(screen.getByText(/Unknown existing method: secret_ref → api_key_env/)).toBeTruthy();
    expect(screen.getByText('Unknown existing role: future_provider_role')).toBeTruthy();
    expect(screen.getByDisplayValue('env://FUTURE_PROVIDER_TOKEN')).toBeTruthy();
  });

  it('uses a production-shaped null method to preserve stale stored contract values', async () => {
    const staleProfile = {
      profile_id: 'codex-stale-contract',
      runtime_id: 'codex_cli',
      provider_id: 'openai',
      authentication_method: null,
      credential_source: 'secret_ref',
      runtime_materialization_mode: 'config_bundle',
      secret_refs: { openai_api_key: 'db://OPENAI_API_KEY' },
      max_parallel_runs: 1,
      cooldown_after_429_seconds: 300,
      rate_limit_policy: 'backoff',
      enabled: false,
      auth_state: 'disconnected',
      disabled_reason: 'disconnected',
      creation_capabilities: openAiCapabilities,
    } as ProviderProfile;
    let updatePayload: Record<string, unknown> | null = null;
    vi.spyOn(window, 'fetch').mockImplementation(async (input, init) => {
      const url = String(input);
      if (url === '/api/v1/provider-profiles/codex-stale-contract') {
        updatePayload = JSON.parse(String(init?.body)) as Record<string, unknown>;
        return { ok: true, json: async () => staleProfile } as Response;
      }
      throw new Error(`Unexpected fetch: ${url}`);
    });
    renderProviderProfilesManager([staleProfile]);

    fireEvent.click(screen.getByRole('button', { name: 'Edit' }));

    expect((screen.getByLabelText('Show advanced options') as HTMLInputElement).checked).toBe(true);
    expect(screen.getByText('Unknown existing method: secret_ref → config_bundle. The stored contract remains inspectable and is not replaced by a supported method automatically.')).toBeTruthy();
    expect(screen.getByText('Credential source: secret_ref')).toBeTruthy();
    expect(screen.getByText('Materialization mode: config_bundle')).toBeTruthy();

    fireEvent.click(screen.getByRole('button', { name: 'Update provider profile' }));
    await waitFor(() => expect(updatePayload).not.toBeNull());
    expect(updatePayload).not.toHaveProperty('authentication_method');
    expect(updatePayload).not.toHaveProperty('credential_source');
    expect(updatePayload).not.toHaveProperty('runtime_materialization_mode');
    expect(updatePayload).not.toHaveProperty('provider_id');
    expect(updatePayload).not.toHaveProperty('secret_refs');
    expect(updatePayload).not.toHaveProperty('volume_ref');
    expect(updatePayload).not.toHaveProperty('volume_mount_path');
    expect(updatePayload).not.toHaveProperty('command_behavior');
  });

  it('validates imported OAuth volumes through the distinct expert flow', async () => {
    const fetchSpy = vi.spyOn(window, 'fetch').mockImplementation(async (input, init) => {
      const url = String(input);
      const creationResponse = openAiCreationResponse(url);
      if (creationResponse) return creationResponse;
      if (url === '/api/v1/provider-profiles/credential-volume/validate') {
        expect(JSON.parse(String(init?.body))).toEqual({
          runtime_id: 'codex_cli',
          provider_id: 'openai',
          volume_ref: 'existing-codex-home',
        });
        return {
          ok: true,
          json: async () => ({
            status: 'validated',
            volume_ref: 'existing-codex-home',
            volume_mount_path: '/home/app/.codex',
            source: 'validated_import',
          }),
        } as Response;
      }
      throw new Error(`Unexpected fetch: ${url}`);
    });
    renderProviderProfilesManager();

    fireEvent.change(screen.getByLabelText(/Runtime ID/), {
      target: { value: 'codex_cli' },
    });
    fireEvent.change(screen.getByLabelText(/Provider ID/), {
      target: { value: 'openai' },
    });
    fireEvent.click(await screen.findByLabelText('OAuth'));
    fireEvent.click(screen.getByLabelText('Show advanced options'));
    fireEvent.click(screen.getByRole('button', { name: 'Use an existing credential volume' }));
    fireEvent.change(screen.getByLabelText('Existing credential volume'), {
      target: { value: 'existing-codex-home' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Validate imported volume' }));

    expect(await screen.findByText('Validated imported volume')).toBeTruthy();
    expect(screen.getByText('Derived mount path: /home/app/.codex')).toBeTruthy();
    expect(fetchSpy).toHaveBeenCalledWith(
      '/api/v1/provider-profiles/credential-volume/validate',
      expect.objectContaining({ method: 'POST' }),
    );
  });
});

describe('MoonLadderStudios/MoonMind#3348 tier editor', () => {
  const tierProfile: ProviderProfile = {
    profile_id: 'tier-profile',
    runtime_id: 'codex_cli',
    provider_id: 'openai',
    credential_source: 'secret_ref',
    runtime_materialization_mode: 'api_key_env',
    secret_refs: {},
    max_parallel_runs: 1,
    cooldown_after_429_seconds: 300,
    rate_limit_policy: 'backoff',
    enabled: true,
    model_tiers: [
      { label: 'Plan', model: 'gpt-5.5', effort: 'medium', parameters: {}, annotations: {} },
      { label: 'Impl', model: 'gpt-5.5', effort: 'xhigh', parameters: {}, annotations: {} },
      { label: 'Docs', model: 'gpt-5.3', effort: 'xhigh', parameters: {}, annotations: {} },
    ],
    default_model_tier: 2,
  };

  it('shows tier count and default tier in collection and +N more when needed', () => {
    renderProviderProfilesManager([tierProfile]);
    expect(screen.getByText('3 tiers · Default: Tier 2')).toBeTruthy();
    expect(screen.getByText('+1 more')).toBeTruthy();
    expect(screen.getByLabelText('tier-profile model tier mapping')).toBeTruthy();
  });

  it('displays Runtime default for null model/effort and repair when empty', () => {
    const emptyProfile: ProviderProfile = { ...tierProfile, profile_id: 'empty-profile', model_tiers: [], default_model_tier: 1 };
    renderProviderProfilesManager([emptyProfile]);
    expect(screen.getByText('Tier policy unavailable · needs repair')).toBeTruthy();
  });

  it('renders tier editor section with ordered cards and default state', () => {
    renderProviderProfilesManager([tierProfile]);
    fireEvent.click(screen.getByRole('button', { name: 'Edit' }));
    expect(screen.getByText('Model & effort tiers')).toBeTruthy();
    expect(screen.getAllByText(/Tier 1/)[0]).toBeTruthy();
    expect(screen.getByLabelText('Tier 2 label')).toBeTruthy();
    expect(screen.getByLabelText('Use Tier 1 as default')).toBeTruthy();
    expect(screen.getByLabelText('Default tier')).toBeTruthy();
  });

  it('can append and duplicate tiers without renumbering existing tiers', async () => {
    renderProviderProfilesManager([tierProfile]);
    fireEvent.click(screen.getByRole('button', { name: 'Edit' }));
    const addButtons = screen.getAllByRole('button', { name: 'Add tier' });
    fireEvent.click(addButtons[0]!);
    expect(screen.getByLabelText('Tier 4 label')).toBeTruthy();
    expect(screen.getByText('4 tiers · Default: Tier 2')).toBeTruthy();
    fireEvent.click(screen.getByLabelText('Duplicate Tier 1 as new last tier'));
    expect(screen.getByLabelText('Tier 5 label')).toBeTruthy();
    const tier5 = screen.getByLabelText('Tier 5 label') as HTMLInputElement;
    expect(tier5.value).toBe('Plan copy');
  });

  it('prevents removal of only remaining tier', () => {
    const singleTier: ProviderProfile = {
      ...tierProfile,
      model_tiers: [{ label: 'Only', model: null, effort: null, parameters: {}, annotations: {} }],
      default_model_tier: 1,
    };
    renderProviderProfilesManager([singleTier]);
    fireEvent.click(screen.getByRole('button', { name: 'Edit' }));
    const removeBtn = screen.getByLabelText('Remove Tier 1') as HTMLButtonElement;
    expect(removeBtn.disabled).toBe(true);
  });

  it('middle-tier removal previews ordinal changes', async () => {
    renderProviderProfilesManager([tierProfile]);
    fireEvent.click(screen.getByRole('button', { name: 'Edit' }));
    fireEvent.click(screen.getByLabelText('Remove Tier 2'));
    expect(await screen.findByText('Remove Tier 2?')).toBeTruthy();
    expect(screen.getByText('Tier 3: Docs → becomes Tier 2')).toBeTruthy();
    expect(screen.getByText('Existing historical runs do not change.')).toBeTruthy();
  });

  it('removing default requires reviewed replacement default', async () => {
    renderProviderProfilesManager([tierProfile]);
    fireEvent.click(screen.getByRole('button', { name: 'Edit' }));
    fireEvent.click(screen.getByLabelText('Remove Tier 2'));
    expect(await screen.findByText('Choose a replacement default tier:')).toBeTruthy();
    const replacement = screen.getAllByLabelText(/Tier \d/ as unknown as string);
    expect(replacement.length).toBeGreaterThan(0);
  });

  it('saves only canonical ordered tier policy not legacy fields (MoonLadderStudios/MoonMind#3348)', async () => {
    const fetchSpy = vi.spyOn(window, 'fetch').mockImplementation(async (input) => {
      const url = String(input);
      if (url.includes('/capabilities')) {
        return { ok: true, json: async () => ({ version: 'tier-cap-v1-test', profile_id: tierProfile.profile_id, runtime_id: tierProfile.runtime_id, provider_id: tierProfile.provider_id, evidence: { source: 'profile_catalog_evidence', credential_generation: 1, image_ref: null, observed_at: null, stale: false }, tier_constraints: { min_count: 1, max_count: null }, model: { runtime_default: 'gpt-5.5', allow_custom: true, options: [{ value: 'gpt-5.5', label: 'GPT-5.5', description: null, status: 'available' }] }, effort: { supported: true, runtime_default: 'medium', allow_custom: false, application: 'native', options: [{ value: 'medium', label: 'Medium', description: null, status: 'available', compatible_models: null }] }, diagnostics: [] }) } as Response;
      }
      return { ok: true, json: async () => ({ ...tierProfile, model_tiers: tierProfile.model_tiers, default_model_tier: 2 }) } as Response;
    });
    renderProviderProfilesManager([tierProfile]);
    fireEvent.click(screen.getByRole('button', { name: 'Edit' }));
    fireEvent.change(screen.getByLabelText('Tier 1 label'), { target: { value: 'Plan updated' } });
    fireEvent.click(screen.getByRole('button', { name: 'Update provider profile' }));
    await waitFor(() => expect(fetchSpy).toHaveBeenCalled());
    const saveCall2 = fetchSpy.mock.calls.find((call) => { const init = call[1] as RequestInit | undefined; return Boolean(init?.body && String(init.body).includes('model_tiers')); });
    const payload = JSON.parse(String((saveCall2?.[1] as RequestInit)?.body ?? '{}'));
    expect(payload.model_tiers[0].label).toBe('Plan updated');
    expect(payload.default_model_tier).toBe(2);
    expect(payload).not.toHaveProperty('default_model');
    expect(payload).not.toHaveProperty('default_effort');
  });

  it('read-only users see complete ordered policy as values', () => {
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(
      <QueryClientProvider client={queryClient}>
        <ProviderProfilesManager profiles={[tierProfile]} secretSlugs={[]} onNotice={vi.fn()} queryClient={queryClient} canWriteProviderProfiles={false} />
      </QueryClientProvider>,
    );
    expect(screen.queryByRole('button', { name: 'Edit tiers tier-profile' })).toBeNull();
    expect(screen.queryByRole('button', { name: 'Edit' })).toBeNull();
    expect(screen.getByText('3 tiers · Default: Tier 2')).toBeTruthy();
    expect(screen.queryByRole('button', { name: 'Add tier' })).toBeNull();
    // collection summary expansion is accessible without edit mode
    expect(screen.getByText('Show tier mapping')).toBeTruthy();
  });
});

describe('MoonLadderStudios/MoonMind#3815 cross-boundary verification (#3822 coverage)', () => {
  const baseProfile: ProviderProfile = {
    profile_id: 'cross-profile',
    runtime_id: 'codex_cli',
    provider_id: 'openai',
    credential_source: 'secret_ref',
    runtime_materialization_mode: 'api_key_env',
    secret_refs: {},
    max_parallel_runs: 1,
    cooldown_after_429_seconds: 300,
    rate_limit_policy: 'backoff',
    enabled: true,
  };

  it('standard creation form follows Identity -> Authentication -> Tiers -> Capacity -> default -> advanced order', () => {
    renderProviderProfilesManager();
    const form = document.querySelector('form');
    const html = form?.innerHTML ?? '';
    const identityIdx = html.indexOf('Identity');
    const authIdx = html.indexOf('Authentication and readiness');
    const tiersIdx = html.indexOf('Model &amp; effort tiers');
    const capacityIdx = html.indexOf('Capacity');
    const defaultIdx = html.indexOf('Use as runtime default');
    const advancedIdx = html.indexOf('Show advanced options');
    expect(identityIdx).toBeGreaterThan(-1);
    expect(authIdx).toBeGreaterThan(identityIdx);
    expect(tiersIdx).toBeGreaterThan(authIdx);
    expect(capacityIdx).toBeGreaterThan(tiersIdx);
    expect(defaultIdx).toBeGreaterThan(capacityIdx);
    expect(advancedIdx).toBeGreaterThan(defaultIdx);
  });

  it('Show advanced options is collapsed by default and connected via aria-controls', () => {
    renderProviderProfilesManager();
    const checkbox = screen.getByLabelText('Show advanced options') as HTMLInputElement;
    expect(checkbox.checked).toBe(false);
    expect(checkbox.getAttribute('aria-controls')).toBe('provider-profile-advanced-region');
    expect(document.getElementById('provider-profile-advanced-region')).toBeNull();
    // progressive disclosure: hidden validation error auto-expands is covered by save error handling
  });

  it('launch-safety metadata is read-only for guided preset and editable with warning for manual', async () => {
    // Guided preset: clear_env_keys read-only
    const field = (value: unknown, editable = true, source = 'runtime_provider_isolation_policy') => ({
      value, source, editable, required: false, lock_reason: editable ? null : 'Environment clearing is backend-owned launch security policy.',
    });
    const preset = {
      version: 'provider-profile-create-v1-test',
      supported: true,
      runtime_id: 'codex_cli', provider_id: 'openai', authentication_method: 'api_key',
      fields: {
        credential_source: field('none', false), runtime_materialization_mode: field('api_key_env', false),
        secret_refs: field({}), volume_ref: field(null, false), volume_mount_path: field(null, false),
        max_parallel_runs: field(1), cooldown_after_429_seconds: field(300), rate_limit_policy: field('backoff'),
        enabled: field(false, false), is_default: field(false), command_behavior: field({}, false),
        user_tags: field([]), priority: field(100), clear_env_keys: field(['MINIMAX_API_KEY'], false),
      }, diagnostics: [], manual_creation_allowed: false, required_manual_fields: [],
    };
    vi.spyOn(window, 'fetch').mockImplementation(async (input) => {
      const url = String(input);
      if (url.startsWith('/api/v1/provider-profiles/creation-capabilities?')) {
        return { ok: true, json: async () => ({
          version: preset.version, runtime_id: 'codex_cli', provider_id: 'openai', supported: true,
          authentication_methods: [{ id: 'api_key', label: 'API key', setup_action: 'api_key', launch_ready_after_setup: true, fields: preset.fields, secret_roles: [], imported_volume: { supported: false, mount_path: null, source: 'test', lock_reason: 'no' } }], diagnostics: [],
        })} as Response;
      }
      if (url.startsWith('/api/v1/provider-profiles/creation-preset?')) {
        return { ok: true, json: async () => preset } as Response;
      }
      throw new Error(`Unexpected ${url}`);
    });
    renderProviderProfilesManager();
    fireEvent.change(screen.getByLabelText(/Runtime ID/), { target: { value: 'codex_cli' } });
    fireEvent.change(screen.getByLabelText(/Provider ID/), { target: { value: 'openai' } });
    fireEvent.click(await screen.findByLabelText('API key'));
    await screen.findByText(/Backend preset provider-profile-create-v1-test loaded/);
    fireEvent.click(screen.getByLabelText('Show advanced options'));
    expect(screen.getByText(/Launch environment isolation — clear environment keys/)).toBeTruthy();
    expect(screen.getByText(/Value: MINIMAX_API_KEY/)).toBeTruthy();
    expect(screen.getAllByText(/Source: runtime_provider_isolation_policy/).length).toBeGreaterThan(0);
    expect(screen.queryByLabelText('Clear env keys')).toBeNull();
    expect(screen.queryByText(/manual expert path/)).toBeNull();
  });

  it('readiness gates is_default checkbox when blocked', () => {
    const blockedProfile: ProviderProfile = {
      ...baseProfile, profile_id: 'blocked-default', is_default: false,
      readiness: { status: 'blocked', launch_ready: false, summary: 'blocked', checks: [{ id: 'secret_refs', label: 'SecretRef', status: 'error', message: 'missing' }] },
    };
    renderProviderProfilesManager([blockedProfile]);
    fireEvent.click(screen.getByRole('button', { name: 'Edit' }));
    const checkbox = screen.getByLabelText('Use as runtime default') as HTMLInputElement;
    expect(checkbox.disabled).toBe(true);
    expect(screen.getByText(/Default assignment is disabled until launch readiness succeeds/)).toBeTruthy();
  });

  it('allows demoting an unready default profile while blocking assignment', async () => {
    const fetchSpy = vi.spyOn(window, 'fetch').mockResolvedValue({
      ok: true,
      json: async () => ({ ...baseProfile, profile_id: 'blocked-current-default', is_default: false }),
    } as Response);
    const currentDefault: ProviderProfile = {
      ...baseProfile, profile_id: 'blocked-current-default', is_default: true,
      readiness: { status: 'blocked', launch_ready: false, summary: 'blocked', checks: [{ id: 'secret_refs', label: 'SecretRef', status: 'error', message: 'missing' }] },
    };
    renderProviderProfilesManager([currentDefault]);
    fireEvent.click(screen.getByRole('button', { name: 'Edit' }));
    const checkbox = screen.getByLabelText('Use as runtime default') as HTMLInputElement;
    expect(checkbox.checked).toBe(true);
    expect(checkbox.disabled).toBe(false);
    expect(screen.getByText(/may clear this checkbox to demote it/)).toBeTruthy();

    fireEvent.click(checkbox);
    fireEvent.click(screen.getByRole('button', { name: 'Update provider profile' }));

    await waitFor(() => {
      expect(fetchSpy).toHaveBeenCalledWith(
        '/api/v1/provider-profiles/blocked-current-default',
        expect.objectContaining({ method: 'PATCH' }),
      );
    });
    const patchCall = fetchSpy.mock.calls.find(
      ([url, init]) =>
        url === '/api/v1/provider-profiles/blocked-current-default' &&
        (init as RequestInit | undefined)?.method === 'PATCH',
    );
    expect(patchCall).toBeTruthy();
    const [, requestInit] = patchCall ?? [];
    expect(JSON.parse(String((requestInit as RequestInit).body)).is_default).toBe(false);
  });

  it('applies the create-time default intent after API-key enrollment succeeds', async () => {
    const field = (value: unknown, editable = true) => ({ value, source: 'test', editable, required: false, lock_reason: null });
    const preset = {
      version: 'provider-profile-create-v1-test', supported: true, runtime_id: 'codex_cli', provider_id: 'openai', authentication_method: 'api_key',
      fields: {
        credential_source: field('none', false), runtime_materialization_mode: field('api_key_env', false),
        secret_refs: field({}), volume_ref: field(null, false), volume_mount_path: field(null, false),
        max_parallel_runs: field(1), cooldown_after_429_seconds: field(300), rate_limit_policy: field('backoff'),
        enabled: field(false, false), is_default: field(false), command_behavior: field({ auth_strategy: 'api_key_env' }, false),
        user_tags: field([]), priority: field(100), clear_env_keys: field(['OPENAI_API_KEY'], false),
      }, diagnostics: [], manual_creation_allowed: false, required_manual_fields: [],
    };
    const savedProfile: ProviderProfile = {
      profile_id: 'intent-profile', runtime_id: 'codex_cli', provider_id: 'openai',
      credential_source: 'none', runtime_materialization_mode: 'api_key_env', secret_refs: {},
      max_parallel_runs: 1, cooldown_after_429_seconds: 300, rate_limit_policy: 'backoff',
      enabled: false, is_default: false, auth_state: 'not_configured', disabled_reason: 'missing_credentials',
    };
    const fetchSpy = vi.spyOn(window, 'fetch').mockImplementation(async (input, init) => {
      const url = String(input);
      if (url.startsWith('/api/v1/provider-profiles/creation-capabilities?')) {
        return { ok: true, json: async () => ({
          version: preset.version, runtime_id: 'codex_cli', provider_id: 'openai', supported: true,
          authentication_methods: [{ id: 'api_key', label: 'API key', setup_action: 'api_key', launch_ready_after_setup: true, fields: preset.fields, secret_roles: [], imported_volume: { supported: false, mount_path: null, source: 'test', lock_reason: 'no' } }], diagnostics: [],
        })} as Response;
      }
      if (url.startsWith('/api/v1/provider-profiles/creation-preset?')) {
        return { ok: true, json: async () => preset } as Response;
      }
      if (url === '/api/v1/provider-profiles' && init?.method === 'POST') {
        return { ok: true, json: async () => savedProfile } as Response;
      }
      if (url === '/api/v1/provider-profiles/intent-profile/credentials/api-key' && init?.method === 'POST') {
        return { ok: true, json: async () => ({
          status: 'ready', status_label: 'OpenAI API key ready',
          readiness: { connected: true, backing_secret_exists: true, launch_ready: true },
        })} as Response;
      }
      if (url === '/api/v1/provider-profiles/intent-profile' && init?.method === 'PATCH') {
        return { ok: true, json: async () => ({ ...savedProfile, is_default: true })} as Response;
      }
      throw new Error(`Unexpected fetch: ${url} ${String(init?.method)}`);
    });

    renderProviderProfilesManager();
    fireEvent.change(screen.getByLabelText(/Profile ID/), { target: { value: 'intent-profile' } });
    fireEvent.change(screen.getByLabelText(/Runtime ID/), { target: { value: 'codex_cli' } });
    fireEvent.change(screen.getByLabelText(/Provider ID/), { target: { value: 'openai' } });
    fireEvent.click(await screen.findByLabelText('API key'));
    await screen.findByText(/Backend preset provider-profile-create-v1-test loaded/);
    fireEvent.click(screen.getByLabelText('Runtime default'));
    // MoonLadderStudios/MoonMind#4002: guided continuation.
    fireEvent.click(screen.getByRole('button', { name: 'Create and connect' }));

    // Guided API-key setup opens automatically; the checked default intent
    // must survive creation (which stores is_default=false) and apply after
    // enrollment succeeds.
    fireEvent.click(await screen.findByRole('button', { name: 'Continue to API key paste' }));
    fireEvent.change(screen.getByLabelText('OpenAI API key'), { target: { value: 'sk-openai-intent-test' } });
    fireEvent.click(screen.getByRole('button', { name: 'Validate and save OpenAI API key' }));

    await waitFor(() => {
      expect(fetchSpy.mock.calls.some(([input, init]) =>
        input === '/api/v1/provider-profiles/intent-profile' && (init as RequestInit)?.method === 'PATCH',
      )).toBe(true);
    });
    const patchCall = fetchSpy.mock.calls.find(([input, init]) =>
      input === '/api/v1/provider-profiles/intent-profile' && (init as RequestInit)?.method === 'PATCH',
    );
    expect(JSON.parse(String((patchCall?.[1] as RequestInit).body))).toEqual({ is_default: true });
  });

  it('does not copy another profile’s OAuth label into an unrelated draft', async () => {
    const profileA: ProviderProfile = {
      ...baseProfile, profile_id: 'profile-a', account_label: 'Account A',
      credential_source: 'oauth_volume', runtime_materialization_mode: 'oauth_home',
      secret_refs: {}, volume_ref: 'codex_auth_volume', volume_mount_path: '/home/app/.codex',
    };
    const fetchSpy = vi.spyOn(window, 'fetch').mockImplementation(async (input, init) => {
      const url = String(input);
      if (url === '/api/v1/oauth-sessions' && init?.method === 'POST') {
        return { ok: true, json: async () => ({
          session_id: 'oas_label_scope', runtime_id: 'codex_cli', profile_id: 'profile-a',
          status: 'awaiting_user', session_transport: 'none',
        })} as Response;
      }
      if (url === '/api/v1/oauth-sessions/oas_label_scope/finalize' && init?.method === 'POST') {
        return { ok: true, json: async () => ({ status: 'succeeded' })} as Response;
      }
      if (url.startsWith('/api/v1/oauth-sessions/oas_label_scope') && (!init?.method || init.method === 'GET')) {
        return { ok: true, json: async () => ({
          session_id: 'oas_label_scope', runtime_id: 'codex_cli', profile_id: 'profile-a',
          status: 'succeeded', session_transport: 'none',
        })} as Response;
      }
      throw new Error(`Unexpected fetch: ${url} ${String(init?.method)}`);
    });
    vi.spyOn(window, 'open').mockReturnValue(null);

    renderProviderProfilesManager([profileA]);
    // Draft a new, unrelated profile while A's OAuth session is in flight.
    fireEvent.change(screen.getByLabelText(/Profile ID/), { target: { value: 'profile-b' } });
    fireEvent.click(screen.getByRole('button', { name: 'OAuth profile-a' }));
    fireEvent.click(await screen.findByRole('button', { name: 'Finalize profile-a' }));
    await screen.findByText('OAuth: Succeeded');

    expect(fetchSpy).toHaveBeenCalledWith(
      '/api/v1/oauth-sessions/oas_label_scope/finalize',
      expect.objectContaining({ method: 'POST' }),
    );
    expect((screen.getByLabelText(/Account label/) as HTMLInputElement).value).toBe('');
  });

  it('resets edit-mode advanced options to the backend preset without flipping default', async () => {
    const field = (value: unknown, editable = true) => ({ value, source: 'test', editable, required: false, lock_reason: null });
    const preset = {
      version: 'provider-profile-create-v1-test', supported: true, runtime_id: 'codex_cli', provider_id: 'openai', authentication_method: 'api_key',
      fields: {
        credential_source: field('none', false), runtime_materialization_mode: field('api_key_env', false),
        secret_refs: field({}), volume_ref: field(null, false), volume_mount_path: field(null, false),
        max_parallel_runs: field(1), cooldown_after_429_seconds: field(300), rate_limit_policy: field('backoff'),
        enabled: field(false, false), is_default: field(false), command_behavior: field({}, false),
        user_tags: field([]), priority: field(100), clear_env_keys: field(['OPENAI_API_KEY'], false),
      }, diagnostics: [], manual_creation_allowed: false, required_manual_fields: [],
    };
    vi.spyOn(window, 'fetch').mockImplementation(async (input) => {
      const url = String(input);
      if (url.startsWith('/api/v1/provider-profiles/creation-preset?')) {
        return { ok: true, json: async () => preset } as Response;
      }
      throw new Error(`Unexpected fetch: ${url}`);
    });
    const editable = {
      ...baseProfile, profile_id: 'edit-preset', authentication_method: 'api_key',
      is_default: true, cooldown_after_429_seconds: 120,
      creation_capabilities: {
        version: 'v1', runtime_id: 'codex_cli', provider_id: 'openai', supported: true,
        authentication_methods: [{ id: 'api_key', label: 'API key', setup_action: 'api_key', launch_ready_after_setup: true, fields: {}, secret_roles: [], imported_volume: { supported: false, mount_path: null, source: 'test', lock_reason: 'no' } }], diagnostics: [],
      },
    } as ProviderProfile;
    renderProviderProfilesManager([editable]);
    fireEvent.click(screen.getByRole('button', { name: 'Edit' }));
    fireEvent.click(screen.getByLabelText('Show advanced options'));
    await screen.findByText('Reset advanced options to recommended');
    fireEvent.click(screen.getByText('Reset advanced options to recommended'));
    fireEvent.click(screen.getByRole('button', { name: 'Apply reset' }));

    expect((screen.getByLabelText(/Cooldown after 429/) as HTMLInputElement).value).toBe('300');
    expect((screen.getByLabelText('Use as runtime default') as HTMLInputElement).checked).toBe(true);
  });

  it('Reset advanced options to recommended shows preview distinguishing concrete vs omit vs recalculated', async () => {
    const field = (value: unknown, editable = true) => ({ value, source: 'test', editable, required: false, lock_reason: null });
    const preset = {
      version: 'provider-profile-create-v1-test', supported: true, runtime_id: 'codex_cli', provider_id: 'openai', authentication_method: 'api_key',
      fields: {
        credential_source: field('none', false), runtime_materialization_mode: field('api_key_env', false),
        secret_refs: field({}), volume_ref: field(null, false), volume_mount_path: field(null, false),
        max_parallel_runs: field(1), cooldown_after_429_seconds: field(300), rate_limit_policy: field('backoff'),
        enabled: field(false, false), is_default: field(false), command_behavior: field({}, false),
        user_tags: field([]), priority: field(100), clear_env_keys: field(['OPENAI_API_KEY'], false),
      }, diagnostics: [], manual_creation_allowed: false, required_manual_fields: [],
    };
    vi.spyOn(window, 'fetch').mockImplementation(async (input) => {
      const url = String(input);
      if (url.startsWith('/api/v1/provider-profiles/creation-capabilities?')) {
        return { ok: true, json: async () => ({
          version: preset.version, runtime_id: 'codex_cli', provider_id: 'openai', supported: true,
          authentication_methods: [{ id: 'api_key', label: 'API key', setup_action: 'api_key', launch_ready_after_setup: true, fields: preset.fields, secret_roles: [], imported_volume: { supported: false, mount_path: null, source: 'test', lock_reason: 'no' } }], diagnostics: [],
        })} as Response;
      }
      if (url.startsWith('/api/v1/provider-profiles/creation-preset?')) return { ok: true, json: async () => preset } as Response;
      throw new Error(`Unexpected ${url}`);
    });
    renderProviderProfilesManager();
    fireEvent.change(screen.getByLabelText(/Runtime ID/), { target: { value: 'codex_cli' } });
    fireEvent.change(screen.getByLabelText(/Provider ID/), { target: { value: 'openai' } });
    fireEvent.click(await screen.findByLabelText('API key'));
    await screen.findByText(/Backend preset provider-profile-create-v1-test loaded/);
    fireEvent.click(screen.getByLabelText('Show advanced options'));
    expect(screen.getByText('Reset advanced options to recommended')).toBeTruthy();
    fireEvent.click(screen.getByText('Reset advanced options to recommended'));
    expect(screen.getByText(/Preview — recommended values will replace draft overrides/)).toBeTruthy();
    expect(screen.getByText(/cooldown_after_429_seconds: 300 \(concrete recommended\)/)).toBeTruthy();
    expect(screen.getByText(/clear_env_keys: OPENAI_API_KEY — recalculated security fields/)).toBeTruthy();
    expect(screen.getByText(/omitted to inherit backend-derived values/)).toBeTruthy();
  });

  it('existing custom advanced values remain discoverable and round-trippable during edit', () => {
    const customProfile = {
      ...baseProfile, profile_id: 'custom-advanced', provider_label: 'Custom', secret_refs: { unknown_role: 'db://custom-secret' },
      creation_capabilities: {
        version: 'v1', runtime_id: 'codex_cli', provider_id: 'openai', supported: true,
        authentication_methods: [{ id: 'api_key', label: 'API key', setup_action: 'api_key', launch_ready_after_setup: true, fields: {}, secret_roles: [], imported_volume: { supported: false, mount_path: null, source: 'test', lock_reason: 'no' } }], diagnostics: [],
      },
    } as ProviderProfile;
    renderProviderProfilesManager([customProfile]);
    fireEvent.click(screen.getByRole('button', { name: 'Edit' }));
    expect((screen.getByLabelText('Show advanced options') as HTMLInputElement).checked).toBe(true);
    expect(screen.getByDisplayValue('db://custom-secret')).toBeTruthy();
  });
});

describe('MoonLadderStudios/MoonMind#4002 truthful creation actions', () => {
  it('resolves the full action matrix from one snapshot', () => {
    const base = {
      isEditing: false,
      isPending: false,
      authenticationMethod: 'api_key' as const,
      presetLoading: false,
      presetSupported: true as boolean | null,
      presetError: null,
      presetDiagnostic: null,
      manualCreationAllowed: false,
      setupContinuationAvailable: true,
      canWrite: true,
    };
    expect(resolveCreationAction(base)).toEqual({
      label: 'Create and connect',
      disabled: false,
      reason: null,
    });
    expect(
      resolveCreationAction({ ...base, setupContinuationAvailable: false }).label,
    ).toBe('Create profile');
    expect(
      resolveCreationAction({ ...base, manualCreationAllowed: true }),
    ).toEqual({ label: 'Save disabled profile', disabled: false, reason: null });
    expect(resolveCreationAction({ ...base, isEditing: true })).toEqual({
      label: 'Update provider profile',
      disabled: false,
      reason: null,
    });
    expect(resolveCreationAction({ ...base, isPending: true })).toEqual({
      label: 'Saving…',
      disabled: true,
      reason: null,
    });
    const noMethod = resolveCreationAction({ ...base, authenticationMethod: '' });
    expect(noMethod.disabled).toBe(true);
    expect(noMethod.reason).toContain('authentication method');
    const waiting = resolveCreationAction({ ...base, presetSupported: null });
    expect(waiting.disabled).toBe(true);
    expect(waiting.reason).toContain('Waiting for the backend creation preset');
    const loading = resolveCreationAction({ ...base, presetLoading: true, presetSupported: null });
    expect(loading.disabled).toBe(true);
    // A completed preset failure surfaces its error instead of the waiting state.
    const failed = resolveCreationAction({
      ...base,
      presetSupported: null,
      presetError: 'Failed to load the Provider Profile creation preset.',
    });
    expect(failed.disabled).toBe(true);
    expect(failed.reason).toBe('Failed to load the Provider Profile creation preset.');
    const unsupported = resolveCreationAction({
      ...base,
      presetSupported: false,
      presetDiagnostic: 'Use the authorized manual profile path.',
    });
    expect(unsupported.disabled).toBe(true);
    expect(unsupported.reason).toBe('Use the authorized manual profile path.');
    const noWrite = resolveCreationAction({ ...base, canWrite: false });
    expect(noWrite.disabled).toBe(true);
    expect(noWrite.reason).toContain('permission');
  });

  it('derives setup continuation from the backend-declared method, not the method name alone', () => {
    expect(setupContinuationAvailableForCreate('oauth', 'oauth', true, false)).toBe(true);
    // A validated imported-volume setup is already complete: no continuation.
    expect(setupContinuationAvailableForCreate('oauth', 'oauth', true, true)).toBe(false);
    expect(setupContinuationAvailableForCreate('api_key', 'api_key', true, false)).toBe(true);
    // Declared method without a setup handler is not a continuation.
    expect(setupContinuationAvailableForCreate('api_key', 'none', false, false)).toBe(false);
    expect(setupContinuationAvailableForCreate('api_key', 'api_key', false, false)).toBe(false);
    expect(setupContinuationAvailableForCreate('none', 'none', false, false)).toBe(false);
    expect(setupContinuationAvailableForCreate('', 'oauth', true, false)).toBe(false);
  });
});

describe('MoonLadderStudios/MoonMind#4002 immutable submitted operation', () => {
  function submissionFor(formOverrides = {}) {
    const form = { ...defaultFormState('codex_cli'), profileId: 'op-a', ...formOverrides };
    const tier = runtimeDefaultTierDraft();
    return captureSubmittedProfileOperation({
      form,
      editingProfileId: null,
      tierDrafts: [tier],
      defaultTierClientId: tier.clientId,
      tierBaseline: [{ ...tier }],
      tierBaselineDefaultId: tier.clientId,
      formBaseline: defaultFormState('codex_cli'),
      presetVersion: 'v1',
      presetSupported: true,
      creationPresetSnapshot: null,
      manualCreation: false,
      setupAction: 'api_key',
      launchReadyAfterSetup: true,
      importedVolumeValidated: false,
      importedVolumeRef: '',
    });
  }

  it('captures the full operation and rejects mismatched response identities', () => {
    const submission = submissionFor();
    expect(submission.kind).toBe('create');
    expect(submission.profileId).toBe('op-a');
    expect(saveResponseIdentityMatches('op-a', submission)).toBe(true);
    expect(saveResponseIdentityMatches('op-other', submission)).toBe(false);
    expect(saveResponseIdentityMatches(undefined, submission)).toBe(false);
  });

  it('keeps ownership only while no other draft was opened or edited', () => {
    const submission = submissionFor();
    // Untouched draft: still owned.
    expect(
      submissionOwnsCurrentForm({ ...submission.formSnapshot }, null, submission),
    ).toBe(true);
    // Untouched draft with matching tier snapshots: still owned.
    expect(
      submissionOwnsCurrentForm(
        { ...submission.formSnapshot },
        null,
        submission,
        submission.tierDraftsSnapshot.map((tier) => ({ ...tier })),
        submission.defaultTierClientIdSnapshot,
      ),
    ).toBe(true);
    // Draft B edited while A was pending: A no longer owns the form.
    const editedB = { ...submission.formSnapshot, accountLabel: 'B edits' };
    expect(submissionOwnsCurrentForm(editedB, null, submission)).toBe(false);
    // Another profile opened for editing: ownership lost and kind differs.
    expect(submissionOwnsCurrentForm({ ...submission.formSnapshot }, 'op-b', submission)).toBe(
      false,
    );
  });

  it('loses ownership when tier drafts change after submit', () => {
    const submission = submissionFor();
    const retiered = submission.tierDraftsSnapshot.map((tier) => ({
      ...tier,
      model: 'other-model',
    }));
    expect(
      submissionOwnsCurrentForm(
        { ...submission.formSnapshot },
        null,
        submission,
        retiered,
        submission.defaultTierClientIdSnapshot,
      ),
    ).toBe(false);
    expect(
      submissionOwnsCurrentForm(
        { ...submission.formSnapshot },
        null,
        submission,
        submission.tierDraftsSnapshot,
        'another-default-tier',
      ),
    ).toBe(false);
  });

  it('classifies lost acknowledgments without swallowing server validation', () => {
    expect(isLostSaveAcknowledgment(new TypeError('Failed to fetch'))).toBe(true);
    expect(isLostSaveAcknowledgment(new Error('NetworkError when attempting to fetch resource.'))).toBe(
      true,
    );
    // A 2xx with an undecodable body may already have committed the profile.
    expect(
      isLostSaveAcknowledgment(
        new AmbiguousSaveAcknowledgmentError('Save confirmed but unreadable.'),
      ),
    ).toBe(true);
    expect(
      isLostSaveAcknowledgment(
        new ProviderProfileRequestError('Profile ID is required.', 'validation', null),
      ),
    ).toBe(false);
    expect(isLostSaveAcknowledgment(new Error('Choose a supported authentication method.'))).toBe(
      false,
    );
  });
});

describe('MoonLadderStudios/MoonMind#4002 collapsed advanced summary', () => {
  it('reports recommended, custom, attention, and unavailable states', () => {
    const ready = {
      presetState: 'ready' as const,
      presetErrorText: null,
      hasUnknownAuthenticationMethod: false,
      presetUnsupportedWithoutManual: false,
      unsupportedDetail: null,
      tierPolicyChanged: false,
      deviations: [] as string[],
    };
    expect(computeAdvancedPolicySummary(ready).tone).toBe('recommended');
    const custom = computeAdvancedPolicySummary({
      ...ready,
      tierPolicyChanged: false,
      deviations: ['rate limiting', 'routing tags'],
    });
    expect(custom.tone).toBe('custom');
    expect(custom.text).toBe('Custom advanced policy · 2 overrides: rate limiting, routing tags');
    expect(custom.overrideCount).toBe(2);
    const attention = computeAdvancedPolicySummary({
      ...ready,
      hasUnknownAuthenticationMethod: true,
    });
    expect(attention.tone).toBe('attention');
    expect(attention.text).toContain('Needs attention');
    expect(attention.text).toContain('unknown authentication method');
    // Attention takes precedence over reassurance even with deviations.
    const attentionWithDeviations = computeAdvancedPolicySummary({
      ...ready,
      hasUnknownAuthenticationMethod: true,
      deviations: ['priority'],
    });
    expect(attentionWithDeviations.tone).toBe('attention');
    const unavailable = computeAdvancedPolicySummary({ ...ready, presetState: 'missing' });
    expect(unavailable.tone).toBe('unavailable');
    expect(unavailable.text).toContain('unavailable');
  });

  it('compares typed values with key-order invariance and excludes generated metadata', () => {
    const baseline = defaultFormState('codex_cli');
    const pristine = { ...baseline };
    expect(collectAdvancedControlDeviations(pristine, baseline)).toEqual([]);
    // JSON key order is not a change.
    const reordered = {
      ...baseline,
      commandBehavior: '{"b":1,"a":2}',
    };
    const baselineOrdered = { ...baseline, commandBehavior: '{"a":2,"b":1}' };
    expect(collectAdvancedControlDeviations(reordered, baselineOrdered)).toEqual([]);
    // Generated OAuth/isolation metadata never counts as a user override.
    const generatedOnly = { ...baseline, clearEnvKeysText: 'OPENAI_API_KEY' };
    expect(collectAdvancedControlDeviations(generatedOnly, baseline)).toEqual([]);
    // Real authoring counts each top-level control once with allowlisted labels.
    const authored = {
      ...baseline,
      cooldownAfter429Seconds: '900',
      tagsText: 'team, preferred',
      volumeRef: '  ',
      priority: '5',
    };
    const deviations = collectAdvancedControlDeviations(authored, baseline);
    expect(deviations).toEqual(['rate limiting', 'routing tags', 'priority']);
    const summary = computeAdvancedPolicySummary({
      presetState: 'ready',
      presetErrorText: null,
      hasUnknownAuthenticationMethod: false,
      presetUnsupportedWithoutManual: false,
      unsupportedDetail: null,
      tierPolicyChanged: false,
      deviations,
    });
    expect(summary.text).not.toContain('db://');
    expect(summary.text).not.toContain('OPENAI_API_KEY');
  });

  it('keeps identity-only edits out of the advanced summary', () => {
    const baseline = defaultFormState('codex_cli');
    // Account label renders in the always-visible Identity fieldset.
    const identityOnly = { ...baseline, accountLabel: 'team account' };
    expect(collectAdvancedControlDeviations(identityOnly, baseline)).toEqual([]);
  });

  it('counts provider label and execution configuration as advanced overrides', () => {
    const baseline = defaultFormState('codex_cli');
    const labeled = { ...baseline, providerLabel: 'Preferred provider' };
    expect(collectAdvancedControlDeviations(labeled, baseline)).toEqual(['provider label']);
    const configured = {
      ...baseline,
      executionConfiguration: JSON.stringify({ profileId: 'p', version: 2, digest: 'd' }),
    };
    expect(collectAdvancedControlDeviations(configured, baseline)).toEqual([
      'execution configuration',
    ]);
  });

  it('reports manual creation state instead of recommended settings', () => {
    const recommended = 'Using recommended API key launch settings';
    expect(
      resolveAdvancedRecommendedText({
        manualCreationAllowed: true,
        isEditing: false,
        presetReady: true,
        recommendedLaunchSettingsText: recommended,
      }),
    ).toBe('Manual creation — profile will be saved disabled');
    expect(
      resolveAdvancedRecommendedText({
        manualCreationAllowed: false,
        isEditing: true,
        presetReady: false,
        recommendedLaunchSettingsText: recommended,
      }),
    ).toBe('Preserving the existing launch contract');
    expect(
      resolveAdvancedRecommendedText({
        manualCreationAllowed: false,
        isEditing: false,
        presetReady: true,
        recommendedLaunchSettingsText: recommended,
      }),
    ).toBe(recommended);
  });
});

describe('MoonLadderStudios/MoonMind#4002 deferred default intent storage', () => {
  function memoryStorage() {
    const store = new Map<string, string>();
    return {
      getItem: (key: string) => (store.has(key) ? store.get(key)! : null),
      setItem: (key: string, value: string) => {
        store.set(key, value);
      },
      removeItem: (key: string) => {
        store.delete(key);
      },
    };
  }

  it('round-trips pending intents and tolerates corrupt or missing storage', () => {
    const storage = memoryStorage();
    expect(loadPersistedDefaultIntents(storage)).toEqual([]);
    persistDefaultIntents(
      [
        { profileId: 'profile-a', priorDefaultProfileId: 'profile-old' },
        { profileId: 'profile-b', priorDefaultProfileId: null },
        { profileId: 'profile-a', priorDefaultProfileId: 'profile-old' },
      ],
      storage,
    );
    expect(loadPersistedDefaultIntents(storage)).toEqual([
      { profileId: 'profile-a', priorDefaultProfileId: 'profile-old' },
      { profileId: 'profile-b', priorDefaultProfileId: null },
    ]);
    expect(storage.getItem(PENDING_DEFAULT_INTENT_STORAGE_KEY)).not.toContain('secret');
    storage.setItem(PENDING_DEFAULT_INTENT_STORAGE_KEY, 'not-json{{{');
    expect(loadPersistedDefaultIntents(storage)).toEqual([]);
    expect(loadPersistedDefaultIntents(null)).toEqual([]);
    persistDefaultIntents([{ profileId: 'x', priorDefaultProfileId: null }], null);
  });

  it('reads legacy string-only intents without a captured prior default', () => {
    const storage = memoryStorage();
    storage.setItem(
      PENDING_DEFAULT_INTENT_STORAGE_KEY,
      JSON.stringify(['legacy-a', '  ', 42, null]),
    );
    expect(loadPersistedDefaultIntents(storage)).toEqual([
      { profileId: 'legacy-a', priorDefaultProfileId: null },
    ]);
  });
});

describe('MoonLadderStudios/MoonMind#4002 save reconciliation', () => {
  function guidedMocks(savedProfile: ProviderProfile) {
    const preset = {
      version: 'provider-profile-create-v1-test',
      supported: true,
      runtime_id: 'codex_cli',
      provider_id: 'openai',
      authentication_method: 'api_key',
      fields: {
        credential_source: { value: 'secret_ref', source: 't', editable: false, lock_reason: null, required: false },
        runtime_materialization_mode: { value: 'api_key_env', source: 't', editable: false, lock_reason: null, required: false },
        secret_refs: { value: {}, source: 't', editable: true, lock_reason: null, required: false },
        volume_ref: { value: null, source: 't', editable: false, lock_reason: null, required: false },
        volume_mount_path: { value: null, source: 't', editable: false, lock_reason: null, required: false },
        max_parallel_runs: { value: 1, source: 't', editable: true, lock_reason: null, required: false },
        cooldown_after_429_seconds: { value: 300, source: 't', editable: true, lock_reason: null, required: false },
        rate_limit_policy: { value: 'backoff', source: 't', editable: true, lock_reason: null, required: false },
        enabled: { value: false, source: 't', editable: false, lock_reason: null, required: false },
        is_default: { value: false, source: 't', editable: true, lock_reason: null, required: false },
        command_behavior: { value: { auth_strategy: 'api_key_env' }, source: 't', editable: false, lock_reason: null, required: false },
        user_tags: { value: [], source: 't', editable: true, lock_reason: null, required: false },
        priority: { value: 100, source: 't', editable: true, lock_reason: null, required: false },
        clear_env_keys: { value: [], source: 't', editable: false, lock_reason: null, required: false },
      },
      diagnostics: [],
      manual_creation_allowed: false,
      required_manual_fields: [],
    };
    return vi.spyOn(window, 'fetch').mockImplementation(async (input, init) => {
      const url = String(input);
      if (url.startsWith('/api/v1/provider-profiles/capabilities?')) {
        return {
          ok: true,
          json: async () => ({
            version: 'tier-cap-v1-test',
            profile_id: null,
            runtime_id: 'codex_cli',
            provider_id: 'openai',
            evidence: { source: 't', credential_generation: null, image_ref: null, observed_at: null, stale: false },
            tier_constraints: { min_count: 1, max_count: null },
            model: { runtime_default: 'gpt-5.5', allow_custom: true, options: [] },
            effort: { supported: true, runtime_default: 'medium', allow_custom: false, application: 'native', options: [] },
            diagnostics: [],
          }),
        } as Response;
      }
      if (url.startsWith('/api/v1/provider-profiles/creation-capabilities?')) {
        return {
          ok: true,
          json: async () => ({
            version: preset.version,
            runtime_id: 'codex_cli',
            provider_id: 'openai',
            supported: true,
            authentication_methods: [
              {
                id: 'api_key',
                label: 'API key',
                setup_action: 'api_key',
                launch_ready_after_setup: true,
                fields: preset.fields,
                secret_roles: [],
                imported_volume: { supported: false, mount_path: null, source: 't', lock_reason: 'no' },
              },
            ],
            diagnostics: [],
          }),
        } as Response;
      }
      if (url.startsWith('/api/v1/provider-profiles/creation-preset?')) {
        return { ok: true, json: async () => preset } as Response;
      }
      if (url === '/api/v1/provider-profiles' && init?.method === 'POST') {
        return { ok: true, json: async () => savedProfile } as Response;
      }
      throw new Error(`Unexpected fetch: ${url} ${String((init as RequestInit | undefined)?.method)}`);
    });
  }

  const baseSaved: ProviderProfile = {
    profile_id: 'identity-profile',
    runtime_id: 'codex_cli',
    provider_id: 'openai',
    credential_source: 'secret_ref',
    runtime_materialization_mode: 'api_key_env',
    secret_refs: {},
    max_parallel_runs: 1,
    cooldown_after_429_seconds: 300,
    rate_limit_policy: 'backoff',
    enabled: false,
    is_default: false,
  };

  it('rejects a mismatched save identity without starting setup for another profile', async () => {
    const fetchSpy = guidedMocks({
      ...baseSaved,
      profile_id: 'different-profile',
    });
    const { onNotice } = renderProviderProfilesManager();
    fireEvent.change(screen.getByLabelText(/Profile ID/), { target: { value: 'identity-profile' } });
    fireEvent.change(screen.getByLabelText(/Runtime ID/), { target: { value: 'codex_cli' } });
    fireEvent.change(screen.getByLabelText(/Provider ID/), { target: { value: 'openai' } });
    fireEvent.click(await screen.findByLabelText('API key'));
    await screen.findByText(/Backend preset provider-profile-create-v1-test loaded/);
    fireEvent.click(screen.getByRole('button', { name: 'Create and connect' }));

    await waitFor(() => {
      expect(onNotice).toHaveBeenCalledWith({
        level: 'error',
        text: expect.stringContaining('unexpected profile identity'),
      });
    });
    // No setup continuation starts for another profile.
    expect(fetchSpy.mock.calls.some(([url]) => String(url) === '/api/v1/oauth-sessions')).toBe(false);
    expect(screen.queryByRole('dialog')).toBeNull();
  });

  it('treats a lost create acknowledgment as ambiguous and keeps the draft', async () => {
    const preset = {
      version: 'provider-profile-create-v1-test',
      supported: true,
      runtime_id: 'codex_cli',
      provider_id: 'openai',
      authentication_method: 'api_key',
      fields: {},
      diagnostics: [],
      manual_creation_allowed: false,
      required_manual_fields: [],
    };
    vi.spyOn(window, 'fetch').mockImplementation(async (input, init) => {
      const url = String(input);
      if (url.startsWith('/api/v1/provider-profiles/capabilities?')) {
        return {
          ok: true,
          json: async () => ({
            version: 'tier-cap-v1-test',
            profile_id: null,
            runtime_id: 'codex_cli',
            provider_id: 'openai',
            evidence: { source: 't', credential_generation: null, image_ref: null, observed_at: null, stale: false },
            tier_constraints: { min_count: 1, max_count: null },
            model: { runtime_default: 'gpt-5.5', allow_custom: true, options: [] },
            effort: { supported: true, runtime_default: 'medium', allow_custom: false, application: 'native', options: [] },
            diagnostics: [],
          }),
        } as Response;
      }
      if (url.startsWith('/api/v1/provider-profiles/creation-capabilities?')) {
        return {
          ok: true,
          json: async () => ({
            version: preset.version,
            runtime_id: 'codex_cli',
            provider_id: 'openai',
            supported: true,
            authentication_methods: [
              {
                id: 'api_key',
                label: 'API key',
                setup_action: 'api_key',
                launch_ready_after_setup: true,
                fields: preset.fields,
                secret_roles: [],
                imported_volume: { supported: false, mount_path: null, source: 't', lock_reason: 'no' },
              },
            ],
            diagnostics: [],
          }),
        } as Response;
      }
      if (url.startsWith('/api/v1/provider-profiles/creation-preset?')) {
        return { ok: true, json: async () => preset } as Response;
      }
      if (url === '/api/v1/provider-profiles' && (init as RequestInit | undefined)?.method === 'POST') {
        // The request may have reached the server; the ack is lost.
        throw new TypeError('Failed to fetch');
      }
      throw new Error(`Unexpected fetch: ${url}`);
    });
    const { onNotice } = renderProviderProfilesManager();
    fireEvent.change(screen.getByLabelText(/Profile ID/), { target: { value: 'ambiguous-profile' } });
    fireEvent.change(screen.getByLabelText(/Runtime ID/), { target: { value: 'codex_cli' } });
    fireEvent.change(screen.getByLabelText(/Provider ID/), { target: { value: 'openai' } });
    fireEvent.click(await screen.findByLabelText('API key'));
    await screen.findByText(/Backend preset provider-profile-create-v1-test loaded/);
    fireEvent.click(screen.getByRole('button', { name: 'Create and connect' }));

    await waitFor(() => {
      expect(onNotice).toHaveBeenCalledWith({
        level: 'error',
        text: expect.stringContaining('did not confirm'),
      });
    });
    // Ambiguous, not success and not an automatic retry: the draft is kept.
    expect((screen.getByLabelText(/Profile ID/) as HTMLInputElement).value).toBe(
      'ambiguous-profile',
    );
    expect(onNotice).not.toHaveBeenCalledWith(
      expect.objectContaining({ level: 'ok' }),
    );
  });
});

describe('MoonLadderStudios/MoonMind#4002 remediation gaps (R2-R6,R8)', () => {
  const remediationPresetVersion = 'provider-profile-create-v1-test';
  const remediationSavedBase: ProviderProfile = {
    profile_id: 'remediation-base',
    runtime_id: 'codex_cli',
    provider_id: 'openai',
    credential_source: 'none',
    runtime_materialization_mode: 'api_key_env',
    secret_refs: {},
    max_parallel_runs: 1,
    cooldown_after_429_seconds: 300,
    rate_limit_policy: 'backoff',
    enabled: false,
    is_default: false,
    auth_state: 'not_configured',
    disabled_reason: 'missing_credentials',
  };

  function remediationPresetFields() {
    const field = (value: unknown, editable = true) => ({
      value,
      source: 'test',
      editable,
      required: false,
      lock_reason: null,
    });
    return {
      credential_source: field('none', false),
      runtime_materialization_mode: field('api_key_env', false),
      secret_refs: field({}),
      volume_ref: field(null, false),
      volume_mount_path: field(null, false),
      max_parallel_runs: field(1),
      cooldown_after_429_seconds: field(300),
      rate_limit_policy: field('backoff'),
      enabled: field(false, false),
      is_default: field(false),
      command_behavior: field({ auth_strategy: 'api_key_env' }, false),
      user_tags: field([]),
      priority: field(100),
      clear_env_keys: field(['OPENAI_API_KEY'], false),
    };
  }

  function remediationTierCapabilities() {
    return {
      version: 'tier-cap-v1-test',
      profile_id: null,
      runtime_id: 'codex_cli',
      provider_id: 'openai',
      evidence: {
        source: 't',
        credential_generation: null,
        image_ref: null,
        observed_at: null,
        stale: false,
      },
      tier_constraints: { min_count: 1, max_count: null },
      model: { runtime_default: 'gpt-5.5', allow_custom: true, options: [] },
      effort: {
        supported: true,
        runtime_default: 'medium',
        allow_custom: false,
        application: 'native',
        options: [],
      },
      diagnostics: [],
    };
  }

  function remediationCreationCapabilities() {
    return {
      version: remediationPresetVersion,
      runtime_id: 'codex_cli',
      provider_id: 'openai',
      supported: true,
      authentication_methods: [
        {
          id: 'api_key',
          label: 'API key',
          setup_action: 'api_key',
          launch_ready_after_setup: true,
          fields: remediationPresetFields(),
          secret_roles: [],
          imported_volume: {
            supported: false,
            mount_path: null,
            source: 't',
            lock_reason: 'no',
          },
        },
      ],
      diagnostics: [],
    };
  }

  function remediationPreset() {
    return {
      version: remediationPresetVersion,
      supported: true,
      runtime_id: 'codex_cli',
      provider_id: 'openai',
      authentication_method: 'api_key',
      fields: remediationPresetFields(),
      diagnostics: [],
      manual_creation_allowed: false,
      required_manual_fields: [],
    };
  }

  async function fillCreateForm(profileId: string) {
    fireEvent.change(screen.getByLabelText(/Profile ID/), { target: { value: profileId } });
    fireEvent.change(screen.getByLabelText(/Runtime ID/), { target: { value: 'codex_cli' } });
    fireEvent.change(screen.getByLabelText(/Provider ID/), { target: { value: 'openai' } });
    fireEvent.click(await screen.findByLabelText('API key'));
    await screen.findByText(/Backend preset provider-profile-create-v1-test loaded/);
  }

  it('R2: delayed save A reconciles by captured identity while draft B stays untouched', async () => {
    const savedA: ProviderProfile = { ...remediationSavedBase, profile_id: 'delayed-a' };
    let resolvePost: ((response: Response) => void) | null = null;
    const fetchSpy = vi.spyOn(window, 'fetch').mockImplementation(async (input, init) => {
      const url = String(input);
      if (url.startsWith('/api/v1/provider-profiles/capabilities?')) {
        return { ok: true, json: async () => remediationTierCapabilities() } as Response;
      }
      if (url.startsWith('/api/v1/provider-profiles/creation-capabilities?')) {
        return { ok: true, json: async () => remediationCreationCapabilities() } as Response;
      }
      if (url.startsWith('/api/v1/provider-profiles/creation-preset?')) {
        return { ok: true, json: async () => remediationPreset() } as Response;
      }
      if (url === '/api/v1/provider-profiles' && (init as RequestInit | undefined)?.method === 'POST') {
        return new Promise<Response>((resolve) => {
          resolvePost = resolve;
        });
      }
      throw new Error(`Unexpected fetch: ${url}`);
    });
    const { onNotice, queryClient } = renderProviderProfilesManager();
    const invalidateSpy = vi.spyOn(queryClient, 'invalidateQueries');
    await fillCreateForm('delayed-a');
    fireEvent.click(screen.getByRole('button', { name: 'Create and connect' }));
    await waitFor(() => {
      expect(
        fetchSpy.mock.calls.filter(
          ([url, init]) =>
            String(url) === '/api/v1/provider-profiles' &&
            (init as RequestInit | undefined)?.method === 'POST',
        ),
      ).toHaveLength(1);
    });
    // Duplicate submit while pending is suppressed: the action reads Saving….
    expect(screen.getByRole('button', { name: 'Saving…' })).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: 'Saving…' }));
    expect(
      fetchSpy.mock.calls.filter(
        ([url, init]) =>
          String(url) === '/api/v1/provider-profiles' &&
          (init as RequestInit | undefined)?.method === 'POST',
      ),
    ).toHaveLength(1);
    // Draft B is opened/edited while A is still pending.
    fireEvent.change(screen.getByLabelText(/Profile ID/), { target: { value: 'draft-b' } });
    expect(resolvePost).not.toBeNull();
    resolvePost!({ ok: true, json: async () => savedA } as Response);
    await waitFor(() => {
      expect(onNotice).toHaveBeenCalledWith({
        level: 'ok',
        text: expect.stringContaining('delayed-a'),
      });
    });
    // A reconciled by captured identity (create, not update) and B is untouched.
    const okCall = onNotice.mock.calls.find(([arg]) => (arg as { level: string }).level === 'ok');
    expect(String((okCall?.[0] as { text: string }).text)).toContain('saved');
    expect(String((okCall?.[0] as { text: string }).text)).not.toContain('updated');
    expect((screen.getByLabelText(/Profile ID/) as HTMLInputElement).value).toBe('draft-b');
    expect(
      fetchSpy.mock.calls.filter(
        ([url, init]) =>
          String(url) === '/api/v1/provider-profiles' &&
          (init as RequestInit | undefined)?.method === 'POST',
      ),
    ).toHaveLength(1);
    expect(invalidateSpy).toHaveBeenCalled();
  });

  it('R3: next creation after a custom-tier save uses genuine defaults and preserves pending edits', async () => {
    const savedCustom: ProviderProfile = { ...remediationSavedBase, profile_id: 'custom-tier-save' };
    const fetchSpy = vi.spyOn(window, 'fetch').mockImplementation(async (input, init) => {
      const url = String(input);
      if (url.startsWith('/api/v1/provider-profiles/capabilities?')) {
        return { ok: true, json: async () => remediationTierCapabilities() } as Response;
      }
      if (url.startsWith('/api/v1/provider-profiles/creation-capabilities?')) {
        return { ok: true, json: async () => remediationCreationCapabilities() } as Response;
      }
      if (url.startsWith('/api/v1/provider-profiles/creation-preset?')) {
        return { ok: true, json: async () => remediationPreset() } as Response;
      }
      if (url === '/api/v1/provider-profiles' && (init as RequestInit | undefined)?.method === 'POST') {
        return { ok: true, json: async () => savedCustom } as Response;
      }
      throw new Error(`Unexpected fetch: ${url}`);
    });
    const { onNotice } = renderProviderProfilesManager();
    await fillCreateForm('custom-tier-save');
    // Author a second custom tier before saving.
    fireEvent.click(screen.getByLabelText('Duplicate Tier 1 as new last tier'));
    expect(screen.getByLabelText('Tier 2 label')).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: 'Create and connect' }));
    await waitFor(() => {
      expect(onNotice).toHaveBeenCalledWith({
        level: 'ok',
        text: expect.stringContaining('custom-tier-save'),
      });
    });
    // The next form is genuinely new: identity cleared, one default tier, no Tier 2.
    expect((screen.getByLabelText(/Profile ID/) as HTMLInputElement).value).toBe('');
    expect(screen.queryByLabelText('Tier 2 label')).toBeNull();
    expect(screen.getByLabelText('Tier 1 label')).toBeTruthy();
    expect((screen.getByLabelText('Show advanced options') as HTMLInputElement).checked).toBe(false);
    expect(fetchSpy.mock.calls.filter(([, init]) => (init as RequestInit | undefined)?.method === 'POST')).toHaveLength(1);
  });

  it('R4: confirmed create followed by setup failure keeps same-profile retry with no second create POST', async () => {
    const savedRetry: ProviderProfile = { ...remediationSavedBase, profile_id: 'retry-profile' };
    const fetchSpy = vi.spyOn(window, 'fetch').mockImplementation(async (input, init) => {
      const url = String(input);
      if (url.startsWith('/api/v1/provider-profiles/capabilities?')) {
        return { ok: true, json: async () => remediationTierCapabilities() } as Response;
      }
      if (url.startsWith('/api/v1/provider-profiles/creation-capabilities?')) {
        return { ok: true, json: async () => remediationCreationCapabilities() } as Response;
      }
      if (url.startsWith('/api/v1/provider-profiles/creation-preset?')) {
        return { ok: true, json: async () => remediationPreset() } as Response;
      }
      if (url === '/api/v1/provider-profiles' && (init as RequestInit | undefined)?.method === 'POST') {
        return { ok: true, json: async () => savedRetry } as Response;
      }
      if (
        url === '/api/v1/provider-profiles/retry-profile/credentials/api-key' &&
        (init as RequestInit | undefined)?.method === 'POST'
      ) {
        return { ok: false, json: async () => ({ detail: 'Invalid API key.' }) } as Response;
      }
      throw new Error(`Unexpected fetch: ${url}`);
    });
    renderProviderProfilesManager();
    await fillCreateForm('retry-profile');
    fireEvent.click(screen.getByRole('button', { name: 'Create and connect' }));
    // Same-profile guided enrollment opens for the exact saved identity.
    fireEvent.click(await screen.findByRole('button', { name: 'Continue to API key paste' }));
    fireEvent.change(screen.getByLabelText('OpenAI API key'), { target: { value: 'sk-retry-test' } });
    fireEvent.click(screen.getByRole('button', { name: 'Validate and save OpenAI API key' }));
    // Setup failure offers a same-profile retry, never a second create POST.
    expect(await screen.findByRole('button', { name: 'Return to API key paste' })).toBeTruthy();
    expect(
      screen.getByRole('dialog', { name: 'OpenAI API key enrollment for retry-profile' }),
    ).toBeTruthy();
    expect(
      fetchSpy.mock.calls.filter(
        ([url, init]) =>
          String(url) === '/api/v1/provider-profiles' &&
          (init as RequestInit | undefined)?.method === 'POST',
      ),
    ).toHaveLength(1);
  });

  it('R4: lost acknowledgment never overwrites an independently created conflicting profile', async () => {
    const conflicting: ProviderProfile = {
      ...remediationSavedBase,
      profile_id: 'conflict-profile',
      provider_id: 'openai',
      account_label: 'independently created',
    };
    vi.spyOn(window, 'fetch').mockImplementation(async (input, init) => {
      const url = String(input);
      if (url.startsWith('/api/v1/provider-profiles/capabilities?')) {
        return { ok: true, json: async () => remediationTierCapabilities() } as Response;
      }
      if (url.startsWith('/api/v1/provider-profiles/creation-capabilities?')) {
        return { ok: true, json: async () => remediationCreationCapabilities() } as Response;
      }
      if (url.startsWith('/api/v1/provider-profiles/creation-preset?')) {
        return { ok: true, json: async () => remediationPreset() } as Response;
      }
      if (url === '/api/v1/provider-profiles' && (init as RequestInit | undefined)?.method === 'POST') {
        throw new TypeError('Failed to fetch');
      }
      throw new Error(`Unexpected fetch: ${url}`);
    });
    const { onNotice, queryClient } = renderProviderProfilesManagerWithQuery([conflicting]);
    await fillCreateForm('conflict-profile');
    fireEvent.click(screen.getByRole('button', { name: 'Create and connect' }));
    await waitFor(() => {
      expect(onNotice).toHaveBeenCalledWith({
        level: 'error',
        text: expect.stringContaining('did not confirm'),
      });
    });
    // Safe draft values kept; the independently created row is not overwritten.
    expect((screen.getByLabelText(/Profile ID/) as HTMLInputElement).value).toBe('conflict-profile');
    const cached = queryClient.getQueryData<ProviderProfile[]>(PROVIDER_PROFILE_QUERY_KEY) ?? [];
    expect(cached).toHaveLength(1);
    expect(cached[0]?.account_label).toBe('independently created');
    expect(onNotice).not.toHaveBeenCalledWith(expect.objectContaining({ level: 'ok' }));
  });

  it('R5: closing the Claude drawer mid-commit still reconciles the cache without cross-profile mutation', async () => {
    const claudeProfile: ProviderProfile = {
      profile_id: 'claude-anthropic',
      runtime_id: 'claude_code',
      provider_id: 'anthropic',
      credential_source: 'oauth_volume',
      runtime_materialization_mode: 'oauth_home',
      secret_refs: {},
      max_parallel_runs: 1,
      cooldown_after_429_seconds: 300,
      rate_limit_policy: 'backoff',
      enabled: false,
      is_default: false,
      account_label: 'Claude Anthropic OAuth',
      auth_state: 'not_configured',
      disabled_reason: 'missing_credentials',
      command_behavior: {
        auth_strategy: 'claude_credential_methods',
        auth_state: 'not_connected',
        auth_actions: ['connect_oauth', 'use_api_key'],
        auth_status_label: 'Claude credentials not connected',
      },
    };
    const secondClaudeProfile: ProviderProfile = { ...claudeProfile, profile_id: 'claude-anthropic-secondary' };
    let resolveCommit: ((response: Response) => void) | null = null;
    const fetchSpy = vi.spyOn(window, 'fetch').mockImplementation(
      () =>
        new Promise<Response>((resolve) => {
          resolveCommit = resolve;
        }),
    );
    const { onNotice, queryClient } = renderProviderProfilesManager([claudeProfile, secondClaudeProfile]);
    const invalidateSpy = vi.spyOn(queryClient, 'invalidateQueries');
    fireEvent.click(screen.getByRole('button', { name: 'Use Anthropic API key claude-anthropic' }));
    fireEvent.click(screen.getByRole('button', { name: 'Continue to API key paste' }));
    fireEvent.change(screen.getByLabelText('Anthropic API key'), { target: { value: 'sk-ant-close-test' } });
    fireEvent.click(screen.getByRole('button', { name: 'Validate and save Anthropic API key' }));
    await waitFor(() => {
      expect(fetchSpy).toHaveBeenCalledWith(
        '/api/v1/provider-profiles/claude-anthropic/manual-auth/commit',
        expect.objectContaining({ method: 'POST' }),
      );
    });
    // Close the drawer mid-commit, then let the commit succeed.
    fireEvent.click(screen.getByRole('button', { name: 'Cancel API key enrollment' }));
    expect(screen.queryByRole('dialog')).toBeNull();
    expect(resolveCommit).not.toBeNull();
    resolveCommit!({
      ok: true,
      json: async () => ({
        status: 'ready',
        status_label: 'Anthropic API key ready',
        readiness: { connected: true },
      }),
    } as Response);
    await waitFor(() => {
      expect(invalidateSpy).toHaveBeenCalled();
    });
    // Committed-profile cache reconciles even with the drawer closed; nothing
    // reopens, nothing is misreported as cancelled, and no cross-profile panel
    // is mutated with this profile's outcome.
    expect(screen.queryByRole('dialog')).toBeNull();
    expect(screen.queryByText('Anthropic API key ready')).toBeNull();
    expect(onNotice).not.toHaveBeenCalledWith(
      expect.objectContaining({ text: expect.stringContaining('cancelled') }),
    );
    fireEvent.click(
      screen.getByRole('button', { name: 'Use Anthropic API key claude-anthropic-secondary' }),
    );
    expect(
      screen.getByRole('dialog', {
        name: 'Anthropic API key enrollment for claude-anthropic-secondary',
      }),
    ).toBeTruthy();
    expect(screen.queryByText('Anthropic API key ready')).toBeNull();
  });

  it('R5: closing the OpenCode drawer mid-commit still reconciles without misreporting cancellation', async () => {
    const savedEnrollment: ProviderProfile = { ...remediationSavedBase, profile_id: 'drawer-profile' };
    let resolveCredentials: ((response: Response) => void) | null = null;
    const fetchSpy = vi.spyOn(window, 'fetch').mockImplementation(async (input, init) => {
      const url = String(input);
      if (url.startsWith('/api/v1/provider-profiles/capabilities?')) {
        return { ok: true, json: async () => remediationTierCapabilities() } as Response;
      }
      if (url.startsWith('/api/v1/provider-profiles/creation-capabilities?')) {
        return { ok: true, json: async () => remediationCreationCapabilities() } as Response;
      }
      if (url.startsWith('/api/v1/provider-profiles/creation-preset?')) {
        return { ok: true, json: async () => remediationPreset() } as Response;
      }
      if (url === '/api/v1/provider-profiles' && (init as RequestInit | undefined)?.method === 'POST') {
        return { ok: true, json: async () => savedEnrollment } as Response;
      }
      if (
        url === '/api/v1/provider-profiles/drawer-profile/credentials/api-key' &&
        (init as RequestInit | undefined)?.method === 'POST'
      ) {
        return new Promise<Response>((resolve) => {
          resolveCredentials = resolve;
        });
      }
      throw new Error(`Unexpected fetch: ${url}`);
    });
    const { onNotice, queryClient } = renderProviderProfilesManager();
    const invalidateSpy = vi.spyOn(queryClient, 'invalidateQueries');
    await fillCreateForm('drawer-profile');
    fireEvent.click(screen.getByRole('button', { name: 'Create and connect' }));
    fireEvent.click(await screen.findByRole('button', { name: 'Continue to API key paste' }));
    fireEvent.change(screen.getByLabelText('OpenAI API key'), { target: { value: 'sk-drawer-test' } });
    fireEvent.click(screen.getByRole('button', { name: 'Validate and save OpenAI API key' }));
    await waitFor(() => {
      expect(fetchSpy).toHaveBeenCalledWith(
        '/api/v1/provider-profiles/drawer-profile/credentials/api-key',
        expect.objectContaining({ method: 'POST' }),
      );
    });
    fireEvent.click(screen.getByRole('button', { name: 'Cancel API key enrollment' }));
    expect(screen.queryByRole('dialog')).toBeNull();
    expect(resolveCredentials).not.toBeNull();
    resolveCredentials!({
      ok: true,
      json: async () => ({
        status: 'ready',
        status_label: 'OpenAI API key ready',
        readiness: { connected: true },
      }),
    } as Response);
    await waitFor(() => {
      expect(invalidateSpy).toHaveBeenCalled();
    });
    expect(screen.queryByRole('dialog')).toBeNull();
    expect(onNotice).not.toHaveBeenCalledWith(
      expect.objectContaining({ text: expect.stringContaining('cancelled') }),
    );
  });

  it('R6: failed default PATCH keeps truthful pending state with an explicit retry remedy', async () => {
    window.localStorage.clear();
    const savedDefault: ProviderProfile = { ...remediationSavedBase, profile_id: 'default-fail-profile' };
    const fetchSpy = vi.spyOn(window, 'fetch').mockImplementation(async (input, init) => {
      const url = String(input);
      if (url.startsWith('/api/v1/provider-profiles/creation-capabilities?')) {
        return { ok: true, json: async () => remediationCreationCapabilities() } as Response;
      }
      if (url.startsWith('/api/v1/provider-profiles/creation-preset?')) {
        return { ok: true, json: async () => remediationPreset() } as Response;
      }
      if (url === '/api/v1/provider-profiles' && (init as RequestInit | undefined)?.method === 'POST') {
        return { ok: true, json: async () => savedDefault } as Response;
      }
      if (
        url === '/api/v1/provider-profiles/default-fail-profile/credentials/api-key' &&
        (init as RequestInit | undefined)?.method === 'POST'
      ) {
        return {
          ok: true,
          json: async () => ({
            status: 'ready',
            status_label: 'OpenAI API key ready',
            readiness: { connected: true },
          }),
        } as Response;
      }
      if (
        url === '/api/v1/provider-profiles/default-fail-profile' &&
        (init as RequestInit | undefined)?.method === 'PATCH'
      ) {
        return { ok: false, json: async () => ({ detail: 'Default assignment failed.' }) } as Response;
      }
      throw new Error(`Unexpected fetch: ${url}`);
    });
    const { onNotice } = renderProviderProfilesManager();
    await fillCreateForm('default-fail-profile');
    fireEvent.click(screen.getByLabelText('Runtime default'));
    fireEvent.click(screen.getByRole('button', { name: 'Create and connect' }));
    fireEvent.click(await screen.findByRole('button', { name: 'Continue to API key paste' }));
    fireEvent.change(screen.getByLabelText('OpenAI API key'), { target: { value: 'sk-default-fail' } });
    fireEvent.click(screen.getByRole('button', { name: 'Validate and save OpenAI API key' }));
    await waitFor(() => {
      expect(
        fetchSpy.mock.calls.some(
          ([url, init]) =>
            String(url) === '/api/v1/provider-profiles/default-fail-profile' &&
            (init as RequestInit | undefined)?.method === 'PATCH',
        ),
      ).toBe(true);
    });
    await waitFor(() => {
      expect(onNotice).toHaveBeenCalledWith({
        level: 'error',
        text: expect.stringContaining('was not applied'),
      });
    });
    // Truthful pending state: the intent survives the failure with a retry remedy.
    expect(
      loadPersistedDefaultIntents(window.localStorage).map((intent) => intent.profileId),
    ).toContain('default-fail-profile');
    const failureCall = onNotice.mock.calls
      .map(([arg]) => arg as { level: string; text: string } | null)
      .find((arg) => arg !== null && arg.level === 'error' && arg.text.includes('was not applied'));
    expect(failureCall?.text).toContain('Make default');
    expect(onNotice).not.toHaveBeenCalledWith({
      level: 'ok',
      text: expect.stringContaining('is now the runtime default'),
    });
    window.localStorage.clear();
  });

  it('R6: a pre-existing default is replaced, not mistaken for a newer choice', async () => {
    window.localStorage.clear();
    const savedNewer: ProviderProfile = { ...remediationSavedBase, profile_id: 'newer-intent-profile' };
    const currentDefault: ProviderProfile = {
      ...remediationSavedBase,
      profile_id: 'operator-default',
      is_default: true,
    };
    const fetchSpy = vi.spyOn(window, 'fetch').mockImplementation(async (input, init) => {
      const url = String(input);
      if (url.startsWith('/api/v1/provider-profiles/creation-capabilities?')) {
        return { ok: true, json: async () => remediationCreationCapabilities() } as Response;
      }
      if (url.startsWith('/api/v1/provider-profiles/creation-preset?')) {
        return { ok: true, json: async () => remediationPreset() } as Response;
      }
      if (url === '/api/v1/provider-profiles' && (init as RequestInit | undefined)?.method === 'POST') {
        return { ok: true, json: async () => savedNewer } as Response;
      }
      if (
        url === '/api/v1/provider-profiles/newer-intent-profile/credentials/api-key' &&
        (init as RequestInit | undefined)?.method === 'POST'
      ) {
        return {
          ok: true,
          json: async () => ({
            status: 'ready',
            status_label: 'OpenAI API key ready',
            readiness: { connected: true },
          }),
        } as Response;
      }
      if (
        url === '/api/v1/provider-profiles/newer-intent-profile' &&
        (init as RequestInit | undefined)?.method === 'PATCH'
      ) {
        return { ok: true, json: async () => ({ ...savedNewer, is_default: true }) } as Response;
      }
      throw new Error(`Unexpected fetch: ${url}`);
    });
    const { onNotice } = renderProviderProfilesManagerWithQuery([currentDefault, savedNewer]);
    await fillCreateForm('newer-intent-profile');
    fireEvent.click(screen.getByLabelText('Runtime default'));
    fireEvent.click(screen.getByRole('button', { name: 'Create and connect' }));
    fireEvent.click(await screen.findByRole('button', { name: 'Continue to API key paste' }));
    fireEvent.change(screen.getByLabelText('OpenAI API key'), { target: { value: 'sk-newer-intent' } });
    fireEvent.click(screen.getByRole('button', { name: 'Validate and save OpenAI API key' }));
    // The default observed at submission is the intent's replacement target.
    await waitFor(() => {
      expect(
        fetchSpy.mock.calls.some(
          ([url, init]) =>
            String(url) === '/api/v1/provider-profiles/newer-intent-profile' &&
            (init as RequestInit | undefined)?.method === 'PATCH',
        ),
      ).toBe(true);
    });
    await waitFor(() => {
      expect(onNotice).toHaveBeenCalledWith({
        level: 'ok',
        text: expect.stringContaining('is now the runtime default'),
      });
    });
    // The intent's own notice owns the final slot: no enrollment notice on top.
    expect(onNotice).not.toHaveBeenCalledWith({
      level: 'ok',
      text: expect.stringContaining('enrollment completed'),
    });
    const cached = window.localStorage.getItem(PENDING_DEFAULT_INTENT_STORAGE_KEY) ?? '';
    expect(cached).not.toContain('newer-intent-profile');
    window.localStorage.clear();
  });

  it('R6: a genuinely newer default is never silently undone', async () => {
    window.localStorage.clear();
    const savedNewer: ProviderProfile = { ...remediationSavedBase, profile_id: 'newer-intent-profile' };
    const currentDefault: ProviderProfile = {
      ...remediationSavedBase,
      profile_id: 'operator-default',
      is_default: true,
    };
    const thirdProfile: ProviderProfile = {
      ...remediationSavedBase,
      profile_id: 'third-profile',
      is_default: false,
    };
    // A stale page-local intent from before this run: reload performs no
    // silent PATCH on mount.
    window.localStorage.setItem(
      PENDING_DEFAULT_INTENT_STORAGE_KEY,
      JSON.stringify(['newer-intent-profile']),
    );
    const fetchSpy = vi.spyOn(window, 'fetch').mockImplementation(async (input, init) => {
      const url = String(input);
      if (url.startsWith('/api/v1/provider-profiles/creation-capabilities?')) {
        return { ok: true, json: async () => remediationCreationCapabilities() } as Response;
      }
      if (url.startsWith('/api/v1/provider-profiles/creation-preset?')) {
        return { ok: true, json: async () => remediationPreset() } as Response;
      }
      if (url === '/api/v1/provider-profiles' && (init as RequestInit | undefined)?.method === 'POST') {
        return { ok: true, json: async () => savedNewer } as Response;
      }
      if (
        url === '/api/v1/provider-profiles/newer-intent-profile/credentials/api-key' &&
        (init as RequestInit | undefined)?.method === 'POST'
      ) {
        return {
          ok: true,
          json: async () => ({
            status: 'ready',
            status_label: 'OpenAI API key ready',
            readiness: { connected: true },
          }),
        } as Response;
      }
      throw new Error(`Unexpected fetch: ${url}`);
    });
    const { onNotice, queryClient } = renderProviderProfilesManagerWithQuery([
      currentDefault,
      savedNewer,
      thirdProfile,
    ]);
    // Reload with a stale pending intent performs no silent PATCH on mount.
    expect(
      fetchSpy.mock.calls.some(([, init]) => (init as RequestInit | undefined)?.method === 'PATCH'),
    ).toBe(false);
    await fillCreateForm('newer-intent-profile');
    fireEvent.click(screen.getByLabelText('Runtime default'));
    fireEvent.click(screen.getByRole('button', { name: 'Create and connect' }));
    fireEvent.click(await screen.findByRole('button', { name: 'Continue to API key paste' }));
    fireEvent.change(screen.getByLabelText('OpenAI API key'), { target: { value: 'sk-newer-intent' } });
    // While setup is pending the operator explicitly defaults another profile.
    queryClient.setQueryData<ProviderProfile[]>(PROVIDER_PROFILE_QUERY_KEY, [
      { ...currentDefault, is_default: false },
      savedNewer,
      { ...thirdProfile, is_default: true },
    ]);
    fireEvent.click(screen.getByRole('button', { name: 'Validate and save OpenAI API key' }));
    await waitFor(() => {
      expect(onNotice).toHaveBeenCalledWith({
        level: 'error',
        text: expect.stringContaining('is now the default'),
      });
    });
    const blockedCall = onNotice.mock.calls
      .map(([arg]) => arg as { level: string; text: string } | null)
      .find((arg) => arg !== null && arg.level === 'error' && arg.text.includes('is now the default'));
    expect(blockedCall?.text).toContain('third-profile');
    // The newer operator choice wins: no PATCH against it, intent cleared, never undone.
    expect(
      fetchSpy.mock.calls.some(
        ([url, init]) =>
          String(url) === '/api/v1/provider-profiles/newer-intent-profile' &&
          (init as RequestInit | undefined)?.method === 'PATCH',
      ),
    ).toBe(false);
    const cached = (window.localStorage.getItem(PENDING_DEFAULT_INTENT_STORAGE_KEY) ?? '');
    expect(cached).not.toContain('newer-intent-profile');
    window.localStorage.clear();
  });

  it('R8: opening and canceling a reset preview mutates nothing', async () => {
    vi.spyOn(window, 'fetch').mockImplementation(async (input) => {
      const url = String(input);
      if (url.startsWith('/api/v1/provider-profiles/creation-capabilities?')) {
        return { ok: true, json: async () => remediationCreationCapabilities() } as Response;
      }
      if (url.startsWith('/api/v1/provider-profiles/creation-preset?')) {
        return { ok: true, json: async () => remediationPreset() } as Response;
      }
      throw new Error(`Unexpected fetch: ${url}`);
    });
    const { onNotice } = renderProviderProfilesManager();
    fireEvent.change(screen.getByLabelText(/Runtime ID/), { target: { value: 'codex_cli' } });
    fireEvent.change(screen.getByLabelText(/Provider ID/), { target: { value: 'openai' } });
    fireEvent.click(await screen.findByLabelText('API key'));
    await screen.findByText(/Backend preset provider-profile-create-v1-test loaded/);
    fireEvent.click(screen.getByLabelText('Show advanced options'));
    const cooldown = screen.getByLabelText(/Cooldown after 429/) as HTMLInputElement;
    fireEvent.change(cooldown, { target: { value: '900' } });
    expect(cooldown.value).toBe('900');
    fireEvent.click(screen.getByText('Reset advanced options to recommended'));
    expect(screen.getByText(/Preview — recommended values will replace draft overrides/)).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: 'Cancel' }));
    // Cancel leaves every authored value and disclosure choice intact.
    expect((screen.getByLabelText(/Cooldown after 429/) as HTMLInputElement).value).toBe('900');
    expect((screen.getByLabelText('Show advanced options') as HTMLInputElement).checked).toBe(true);
    expect(screen.queryByText(/Preview — recommended values will replace draft overrides/)).toBeNull();
    expect(onNotice).not.toHaveBeenCalledWith(
      expect.objectContaining({ text: expect.stringContaining('reset to recommended') }),
    );
  });

  it('R8: preset-version conflict preserves authored values for review without a second POST', async () => {
    const field = (value: unknown, editable = true) => ({
      value,
      source: 'test_policy',
      editable,
      required: false,
      lock_reason: editable ? null : 'Backend controlled.',
    });
    const makePreset = (version: string, cooldown: number) => ({
      version,
      supported: true,
      runtime_id: 'codex_cli',
      provider_id: 'openai',
      authentication_method: 'api_key',
      fields: {
        credential_source: field('none', false),
        runtime_materialization_mode: field('api_key_env', false),
        secret_refs: field({}),
        volume_ref: field(null, false),
        volume_mount_path: field(null, false),
        max_parallel_runs: field(1),
        cooldown_after_429_seconds: field(cooldown),
        rate_limit_policy: field('backoff'),
        enabled: field(false, false),
        is_default: field(false),
        command_behavior: field({ auth_strategy: 'api_key_env' }, false),
        user_tags: field([]),
        priority: field(100),
        clear_env_keys: field(['MINIMAX_API_KEY'], false),
      },
      diagnostics: [],
      manual_creation_allowed: false,
      required_manual_fields: [],
    });
    const fetchSpy = vi.spyOn(window, 'fetch').mockImplementation(async (input, init) => {
      const url = String(input);
      if (url.startsWith('/api/v1/provider-profiles/capabilities?')) {
        return { ok: true, json: async () => remediationTierCapabilities() } as Response;
      }
      if (url.startsWith('/api/v1/provider-profiles/creation-capabilities?')) {
        return {
          ok: true,
          json: async () => ({
            version: 'provider-profile-create-v1-old',
            runtime_id: 'codex_cli',
            provider_id: 'openai',
            supported: true,
            authentication_methods: [
              {
                id: 'api_key',
                label: 'API key',
                setup_action: 'api_key',
                launch_ready_after_setup: true,
                fields: makePreset('provider-profile-create-v1-old', 300).fields,
                secret_roles: [],
                imported_volume: { supported: false, mount_path: null, source: 'test_policy', lock_reason: 'no' },
              },
            ],
            diagnostics: [],
          }),
        } as Response;
      }
      if (url.startsWith('/api/v1/provider-profiles/creation-preset?')) {
        return { ok: true, json: async () => makePreset('provider-profile-create-v1-current', 600) } as Response;
      }
      if (url === '/api/v1/provider-profiles' && (init as RequestInit | undefined)?.method === 'POST') {
        return {
          ok: false,
          json: async () => ({
            detail: {
              code: 'provider_profile_creation_preset_version_mismatch',
              message: 'The preset changed.',
            },
          }),
        } as Response;
      }
      throw new Error(`Unexpected fetch: ${url}`);
    });
    const { onNotice } = renderProviderProfilesManager();
    fireEvent.change(screen.getByLabelText(/Profile ID/), { target: { value: 'conflict-review-profile' } });
    fireEvent.change(screen.getByLabelText(/Runtime ID/), { target: { value: 'codex_cli' } });
    fireEvent.change(screen.getByLabelText(/Provider ID/), { target: { value: 'openai' } });
    fireEvent.click(await screen.findByLabelText('API key'));
    await screen.findByText(/Backend preset provider-profile-create-v1-current loaded/);
    fireEvent.click(screen.getByRole('button', { name: 'Create and connect' }));
    await waitFor(() => {
      expect(onNotice).toHaveBeenCalledWith({
        level: 'error',
        text: 'The creation policy changed. Reloading the current preset for review.',
      });
    });
    // Authored identity is preserved for review; the conflict triggers exactly one POST.
    expect((screen.getByLabelText(/Profile ID/) as HTMLInputElement).value).toBe('conflict-review-profile');
    expect(fetchSpy.mock.calls.filter(([, init]) => (init as RequestInit | undefined)?.method === 'POST')).toHaveLength(1);
    expect(onNotice).not.toHaveBeenCalledWith(expect.objectContaining({ level: 'ok' }));
  });
});
