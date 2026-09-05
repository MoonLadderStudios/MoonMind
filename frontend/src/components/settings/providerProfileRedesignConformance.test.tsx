/**
 * Provider Profile creation conformance suite.
 *
 * MoonLadderStudios/MoonMind#3822 (parent MoonLadderStudios/MoonMind#3815).
 *
 * Covers the standard-creation matrix for every authentication and
 * materialization class the repository supports, progressive disclosure and
 * draft preservation, hidden-field validation focus, the integrated model-tier
 * create journey, existing-profile compatibility, and the guard that rejects a
 * second hand-maintained creation-preset schema in React.
 *
 * Run it on its own with `npm run ui:test:settings-redesign`.
 */
import { readFileSync } from 'node:fs';
import { join } from 'node:path';

import { QueryClient } from '@tanstack/react-query';
import { act, fireEvent, screen, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { renderWithClient } from '../../utils/test-utils';
import { ProviderProfilesManager, type ProviderProfile } from './ProviderProfilesManager';

type CreationCapabilities = NonNullable<ProviderProfile['creation_capabilities']>;

afterEach(() => {
  vi.useRealTimers();
  vi.restoreAllMocks();
});

type AuthMethodId = 'oauth' | 'api_key' | 'none';

interface CreationClass {
  name: string;
  runtimeId: string;
  providerId: string;
  method: AuthMethodId;
  methodLabel: string;
  materialization: string;
  secretRole: string | null;
  secretRoleLabel: string;
  importedVolumeMountPath: string | null;
  clearEnvKeys: string[];
  /** Backend activation state returned by an atomic guided create. */
  savedAuthState: string;
}

/**
 * Every runtime/provider/authentication combination that
 * api_service/services/provider_profile_creation_presets.py currently ships a
 * validated standard creation preset for, plus the credential-free capability
 * declared by provider_profile_creation.py.
 */
const CREATION_CLASSES: readonly CreationClass[] = [
  {
    name: 'Codex + OpenAI OAuth',
    runtimeId: 'codex_cli',
    providerId: 'openai',
    method: 'oauth',
    methodLabel: 'OAuth',
    materialization: 'oauth_home',
    secretRole: null,
    secretRoleLabel: '',
    importedVolumeMountPath: '/home/app/.codex',
    clearEnvKeys: ['OPENAI_API_KEY'],
    savedAuthState: 'oauth_pending',
  },
  {
    name: 'Codex + OpenAI API key',
    runtimeId: 'codex_cli',
    providerId: 'openai',
    method: 'api_key',
    methodLabel: 'API key',
    materialization: 'api_key_env',
    secretRole: 'openai_api_key',
    secretRoleLabel: 'OpenAI API key',
    importedVolumeMountPath: null,
    clearEnvKeys: ['CODEX_HOME'],
    savedAuthState: 'api_key_pending',
  },
  {
    name: 'Claude Code + Anthropic OAuth',
    runtimeId: 'claude_code',
    providerId: 'anthropic',
    method: 'oauth',
    methodLabel: 'OAuth',
    materialization: 'oauth_home',
    secretRole: null,
    secretRoleLabel: '',
    importedVolumeMountPath: '/home/app/.claude',
    clearEnvKeys: ['ANTHROPIC_API_KEY', 'ANTHROPIC_AUTH_TOKEN'],
    savedAuthState: 'oauth_pending',
  },
  {
    name: 'Claude Code + Anthropic API key',
    runtimeId: 'claude_code',
    providerId: 'anthropic',
    method: 'api_key',
    methodLabel: 'API key',
    materialization: 'api_key_env',
    secretRole: 'anthropic_api_key',
    secretRoleLabel: 'Anthropic API key',
    importedVolumeMountPath: null,
    clearEnvKeys: ['ANTHROPIC_AUTH_TOKEN', 'ANTHROPIC_BASE_URL'],
    savedAuthState: 'api_key_pending',
  },
  {
    name: 'OpenCode Go API key with composite materialization',
    runtimeId: 'opencode',
    providerId: 'opencode-go',
    method: 'api_key',
    methodLabel: 'API key',
    materialization: 'composite',
    secretRole: 'opencode_api_key',
    secretRoleLabel: 'OpenCode API key',
    importedVolumeMountPath: null,
    clearEnvKeys: ['OPENCODE_API_KEY'],
    savedAuthState: 'api_key_pending',
  },
  {
    name: 'Credential-free OpenCode capability',
    runtimeId: 'opencode',
    providerId: 'opencode',
    method: 'none',
    methodLabel: 'No credential',
    materialization: 'composite',
    secretRole: null,
    secretRoleLabel: '',
    importedVolumeMountPath: null,
    clearEnvKeys: [],
    savedAuthState: 'connected',
  },
];

/** Advanced/plumbing fields a supported standard creation path must omit. */
const OMITTED_ADVANCED_FIELDS = [
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
];

function presetField(value: unknown, editable = true, source = 'runtime_provider_strategy') {
  return {
    value,
    source,
    editable,
    required: false,
    lock_reason: editable ? null : 'Backend launch policy owns this value.',
  };
}

function capabilitiesFor(creationClass: CreationClass) {
  return {
    version: `provider-profile-creation-v1-${creationClass.runtimeId}-${creationClass.providerId}`,
    runtime_id: creationClass.runtimeId,
    provider_id: creationClass.providerId,
    supported: true,
    authentication_methods: [
      {
        id: creationClass.method,
        label: creationClass.methodLabel,
        setup_action: creationClass.method,
        launch_ready_after_setup: true,
        fields: {
          credential_source: presetField(
            creationClass.method === 'oauth'
              ? 'oauth_volume'
              : creationClass.method === 'api_key'
                ? 'secret_ref'
                : 'none',
            false,
          ),
          runtime_materialization_mode: presetField(creationClass.materialization, false),
          clear_env_keys: presetField(
            creationClass.clearEnvKeys,
            false,
            'runtime_provider_isolation_policy',
          ),
        },
        secret_roles: creationClass.secretRole
          ? [
              {
                role: creationClass.secretRole,
                label: creationClass.secretRoleLabel,
                required: true,
                compatible_schemes: ['db', 'env'],
              },
            ]
          : [],
        imported_volume: {
          supported: creationClass.importedVolumeMountPath !== null,
          mount_path: creationClass.importedVolumeMountPath,
          source: 'runtime_provider_strategy',
          lock_reason: 'The runtime strategy owns the credential mount path.',
        },
      },
    ],
    diagnostics: [],
  };
}

function presetFor(creationClass: CreationClass) {
  const capabilities = capabilitiesFor(creationClass);
  const method = capabilities.authentication_methods[0]!;
  return {
    version: capabilities.version,
    supported: true,
    runtime_id: creationClass.runtimeId,
    provider_id: creationClass.providerId,
    authentication_method: creationClass.method,
    fields: {
      credential_source: method.fields.credential_source,
      runtime_materialization_mode: method.fields.runtime_materialization_mode,
      secret_refs: presetField({}),
      volume_ref: presetField(null, false),
      volume_mount_path: presetField(null, false),
      max_parallel_runs: presetField(1, creationClass.method !== 'oauth'),
      cooldown_after_429_seconds: presetField(300),
      rate_limit_policy: presetField('backoff'),
      enabled: presetField(creationClass.method === 'none', false),
      is_default: presetField(false),
      command_behavior: presetField({ auth_strategy: creationClass.materialization }, false),
      user_tags: presetField([]),
      priority: presetField(100),
      clear_env_keys: method.fields.clear_env_keys,
    },
    diagnostics: [],
    manual_creation_allowed: false,
    required_manual_fields: [],
  };
}

function tierCapabilitiesResponse(url: string): Response | null {
  if (
    !url.startsWith('/api/v1/provider-profiles/capabilities?') &&
    !/\/api\/v1\/provider-profiles\/[^/]+\/capabilities/.test(url)
  ) {
    return null;
  }
  return {
    ok: true,
    json: async () => ({
      version: 'tier-cap-v1-conformance',
      profile_id: null,
      runtime_id: 'codex_cli',
      provider_id: 'openai',
      evidence: {
        source: 'runtime_draft',
        credential_generation: null,
        image_ref: null,
        observed_at: null,
        stale: false,
      },
      tier_constraints: { min_count: 1, max_count: null },
      model: {
        runtime_default: 'gpt-5.5',
        allow_custom: true,
        options: [
          { value: 'gpt-5.5', label: 'GPT-5.5', description: null, status: 'available', recommended: true },
          { value: 'gpt-4o', label: 'GPT-4o', description: null, status: 'available', recommended: false },
        ],
      },
      effort: {
        supported: true,
        runtime_default: 'medium',
        allow_custom: false,
        application: 'native',
        options: [
          { value: 'low', label: 'Low', description: null, status: 'available', compatible_models: null },
          { value: 'medium', label: 'Medium', description: null, status: 'available', compatible_models: null },
          { value: 'high', label: 'High', description: null, status: 'available', compatible_models: null },
        ],
      },
      diagnostics: [],
    }),
  } as Response;
}

function savedProfileFor(creationClass: CreationClass, profileId: string): ProviderProfile {
  return {
    profile_id: profileId,
    runtime_id: creationClass.runtimeId,
    provider_id: creationClass.providerId,
    authentication_method: creationClass.method,
    credential_source:
      creationClass.method === 'oauth'
        ? 'oauth_volume'
        : creationClass.method === 'api_key'
          ? 'secret_ref'
          : 'none',
    runtime_materialization_mode: creationClass.materialization,
    secret_refs: {},
    max_parallel_runs: 1,
    cooldown_after_429_seconds: 300,
    rate_limit_policy: 'backoff',
    enabled: creationClass.method === 'none',
    is_default: false,
    auth_state: creationClass.savedAuthState,
    disabled_reason: creationClass.method === 'none' ? null : 'missing_credentials',
    volume_ref: creationClass.method === 'oauth' ? 'moonmind_oauth_generated' : null,
    volume_mount_path: creationClass.importedVolumeMountPath,
    clear_env_keys: creationClass.clearEnvKeys,
    creation_capabilities: capabilitiesFor(creationClass) as unknown as CreationCapabilities,
  };
}

function renderManager(profiles: ProviderProfile[] = []) {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
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

async function startStandardCreation(creationClass: CreationClass, profileId: string) {
  fireEvent.change(screen.getByLabelText(/Profile ID/), { target: { value: profileId } });
  fireEvent.change(screen.getByLabelText(/Runtime ID/), {
    target: { value: creationClass.runtimeId },
  });
  fireEvent.change(screen.getByLabelText(/Provider ID/), {
    target: { value: creationClass.providerId },
  });
  fireEvent.click(await screen.findByLabelText(creationClass.methodLabel));
  await screen.findByText(new RegExp(`Backend preset ${presetFor(creationClass).version} loaded`));
  // Flush passive effects so the save mutation observes the loaded preset,
  // exactly as it has by the time a real user can reach the submit button.
  await act(async () => undefined);
}

function creationFetch(creationClass: CreationClass, profileId: string) {
  const preset = presetFor(creationClass);
  const capabilities = capabilitiesFor(creationClass);
  const saved = savedProfileFor(creationClass, profileId);
  return vi.spyOn(window, 'fetch').mockImplementation(async (input, init) => {
    const url = String(input);
    const tier = tierCapabilitiesResponse(url);
    if (tier) return tier;
    if (url.startsWith('/api/v1/provider-profiles/creation-capabilities?')) {
      return { ok: true, json: async () => capabilities } as Response;
    }
    if (url.startsWith('/api/v1/provider-profiles/creation-preset?')) {
      return { ok: true, json: async () => preset } as Response;
    }
    if (url === '/api/v1/provider-profiles' && init?.method === 'POST') {
      return { ok: true, json: async () => saved } as Response;
    }
    if (url === '/api/v1/oauth-sessions' && init?.method === 'POST') {
      return {
        ok: true,
        json: async () => ({
          session_id: 'oas_conformance',
          runtime_id: creationClass.runtimeId,
          profile_id: profileId,
          status: 'awaiting_user',
          session_transport: 'tmate',
          tmate_web_url: 'https://tmate.example/session',
        }),
      } as Response;
    }
    if (url.startsWith('/api/v1/oauth-sessions/oas_conformance')) {
      return {
        ok: true,
        json: async () => ({
          session_id: 'oas_conformance',
          runtime_id: creationClass.runtimeId,
          profile_id: profileId,
          status: 'awaiting_user',
          session_transport: 'tmate',
          tmate_web_url: 'https://tmate.example/session',
        }),
      } as Response;
    }
    if (
      url === `/api/v1/provider-profiles/${profileId}/credentials/api-key` &&
      init?.method === 'POST'
    ) {
      return {
        ok: true,
        json: async () => ({
          status: 'ready',
          status_label: `${creationClass.secretRoleLabel} ready`,
          readiness: { connected: true, backing_secret_exists: true, launch_ready: true },
        }),
      } as Response;
    }
    if (
      url === `/api/v1/provider-profiles/${profileId}/manual-auth/commit` &&
      init?.method === 'POST'
    ) {
      return {
        ok: true,
        json: async () => ({
          status: 'ready',
          status_label: 'Anthropic API key ready',
          readiness: { connected: true, backing_secret_exists: true, launch_ready: true },
        }),
      } as Response;
    }
    throw new Error(`Unexpected fetch: ${url} ${String(init?.method)}`);
  });
}

interface FetchSpyLike {
  mock: { calls: unknown[][] };
}

function postPayload(fetchSpy: FetchSpyLike): Record<string, unknown> {
  const call = fetchSpy.mock.calls.find(
    (args) =>
      args[0] === '/api/v1/provider-profiles' &&
      (args[1] as RequestInit | undefined)?.method === 'POST',
  );
  expect(call).toBeTruthy();
  return JSON.parse(String((call?.[1] as RequestInit).body)) as Record<string, unknown>;
}

describe('MoonLadderStudios/MoonMind#3822 Provider Profile standard-creation matrix', () => {
  it.each(CREATION_CLASSES.map((creationClass) => [creationClass.name, creationClass] as const))(
    'creates %s without any low-level plumbing interaction',
    async (_name, creationClass) => {
      vi.spyOn(window, 'open').mockReturnValue(null);
      const profileId = `conformance-${creationClass.runtimeId}-${creationClass.method}`;
      const fetchSpy = creationFetch(creationClass, profileId);
      renderManager();
      await startStandardCreation(creationClass, profileId);

      // The standard form exposes no low-level credential or launch plumbing.
      expect(screen.queryByLabelText(/Credential source/)).toBeNull();
      expect(screen.queryByLabelText(/Materialization mode/)).toBeNull();
      expect(screen.queryByLabelText(/Secret refs/)).toBeNull();
      expect(screen.queryByLabelText(/Volume ref/)).toBeNull();
      expect(screen.queryByLabelText(/Mount path/)).toBeNull();
      expect(screen.queryByLabelText(/Cooldown after 429/)).toBeNull();
      expect(screen.queryByLabelText(/Rate limit policy/)).toBeNull();
      expect(screen.queryByLabelText(/Command behavior/)).toBeNull();
      expect(screen.queryByLabelText(/^Tags$/)).toBeNull();
      expect(screen.queryByLabelText(/^Priority$/)).toBeNull();
      expect(screen.queryByLabelText(/Clear env keys/)).toBeNull();
      // Standard identity and capacity controls stay visible.
      expect(screen.getByLabelText(/Account label/)).toBeTruthy();
      expect(screen.getByLabelText(/Max parallel runs/)).toBeTruthy();
      expect(screen.getByLabelText('Runtime default')).toBeTruthy();
      expect((screen.getByLabelText('Show advanced options') as HTMLInputElement).checked).toBe(false);

      fireEvent.click(screen.getByRole('button', { name: 'Create profile' }));
      await waitFor(() =>
        expect(fetchSpy.mock.calls.some(([, init]) => (init as RequestInit | undefined)?.method === 'POST')).toBe(true),
      );

      const payload = postPayload(fetchSpy);
      expect(payload).toMatchObject({
        profile_id: profileId,
        runtime_id: creationClass.runtimeId,
        provider_id: creationClass.providerId,
        authentication_method: creationClass.method,
        preset_version: presetFor(creationClass).version,
      });
      for (const field of OMITTED_ADVANCED_FIELDS) {
        expect(payload).not.toHaveProperty(field);
      }
      // Model tier policy is canonical; no legacy default-model mirror.
      expect(payload).toHaveProperty('model_tiers');
      expect(payload).toHaveProperty('default_model_tier');
      expect(payload).not.toHaveProperty('default_model');
      expect(payload).not.toHaveProperty('default_effort');
    },
  );

  it('continues Claude Code + Anthropic API-key creation through the one-way enrollment drawer', async () => {
    const creationClass = CREATION_CLASSES.find(
      (item) => item.runtimeId === 'claude_code' && item.method === 'api_key',
    )!;
    const profileId = 'conformance-claude-api-key';
    const consoleArgs: unknown[] = [];
    for (const level of ['log', 'info', 'warn', 'error', 'debug'] as const) {
      vi.spyOn(console, level).mockImplementation((...args: unknown[]) => {
        consoleArgs.push(...args);
      });
    }
    const fetchSpy = creationFetch(creationClass, profileId);
    renderManager();
    await startStandardCreation(creationClass, profileId);
    fireEvent.click(screen.getByRole('button', { name: 'Create profile' }));

    fireEvent.click(await screen.findByRole('button', { name: 'Continue to API key paste' }));
    const keyInput = screen.getByLabelText('Anthropic API key') as HTMLInputElement;
    expect(keyInput.type).toBe('password');
    fireEvent.change(keyInput, { target: { value: 'sk-ant-conformance-secret' } });
    fireEvent.click(screen.getByRole('button', { name: 'Validate and save Anthropic API key' }));

    await waitFor(() =>
      expect(
        fetchSpy.mock.calls.some(
          ([url]) => url === `/api/v1/provider-profiles/${profileId}/manual-auth/commit`,
        ),
      ).toBe(true),
    );
    // Plaintext leaves through the one-way credential flow only, never the
    // profile payload, the URL, or browser storage.
    expect(JSON.stringify(postPayload(fetchSpy))).not.toContain('sk-ant-conformance-secret');
    expect(window.location.href).not.toContain('sk-ant-conformance-secret');
    expect(JSON.stringify(window.localStorage)).not.toContain('sk-ant-conformance-secret');
    expect(JSON.stringify(window.sessionStorage)).not.toContain('sk-ant-conformance-secret');
    expect(JSON.stringify(consoleArgs)).not.toContain('sk-ant-conformance-secret');
    await waitFor(() => expect(document.body.textContent).not.toContain('sk-ant-conformance-secret'));
  });

  it('continues OpenCode Go API-key creation through the composite-materialization drawer', async () => {
    const creationClass = CREATION_CLASSES.find((item) => item.providerId === 'opencode-go')!;
    const profileId = 'conformance-opencode-api-key';
    const fetchSpy = creationFetch(creationClass, profileId);
    renderManager();
    await startStandardCreation(creationClass, profileId);
    fireEvent.click(screen.getByRole('button', { name: 'Create profile' }));

    fireEvent.click(await screen.findByRole('button', { name: 'Continue to API key paste' }));
    fireEvent.change(screen.getByLabelText('OpenCode API key'), {
      target: { value: 'oc-conformance-secret' },
    });
    fireEvent.click(
      screen.getByRole('button', { name: 'Validate and save OpenCode API key' }),
    );

    await waitFor(() =>
      expect(
        fetchSpy.mock.calls.some(
          ([url]) => url === `/api/v1/provider-profiles/${profileId}/credentials/api-key`,
        ),
      ).toBe(true),
    );
    expect(postPayload(fetchSpy)).not.toHaveProperty('runtime_materialization_mode');
    await waitFor(() => expect(document.body.textContent).not.toContain('oc-conformance-secret'));
  });

  it('starts Claude Code + Anthropic OAuth enrollment without asking for volume data', async () => {
    const creationClass = CREATION_CLASSES.find(
      (item) => item.runtimeId === 'claude_code' && item.method === 'oauth',
    )!;
    const profileId = 'conformance-claude-oauth';
    const fetchSpy = creationFetch(creationClass, profileId);
    renderManager();
    await startStandardCreation(creationClass, profileId);

    expect(screen.queryByLabelText(/Volume/)).toBeNull();
    fireEvent.click(screen.getByRole('button', { name: 'Create profile' }));

    await waitFor(() =>
      expect(fetchSpy.mock.calls.some(([url]) => url === '/api/v1/oauth-sessions')).toBe(true),
    );
    const payload = postPayload(fetchSpy);
    expect(payload).not.toHaveProperty('volume_ref');
    expect(payload).not.toHaveProperty('volume_mount_path');
    const sessionCall = fetchSpy.mock.calls.find(([url]) => url === '/api/v1/oauth-sessions');
    // Volume metadata reaches the OAuth session from the saved backend profile,
    // not from anything the user typed.
    expect(JSON.parse(String((sessionCall?.[1] as RequestInit).body))).toMatchObject({
      runtime_id: 'claude_code',
      provider_id: 'anthropic',
      profile_id: profileId,
      volume_ref: 'moonmind_oauth_generated',
      volume_mount_path: '/home/app/.claude',
    });
  });

  it('routes an env_bundle expert-manual combination through explicit manual creation', async () => {
    // claude_code + minimax declares only an expert manual env_bundle contract,
    // so no safe standard preset exists.
    const unsupportedPreset = {
      version: 'provider-profile-create-v1-minimax-manual',
      supported: false,
      runtime_id: 'claude_code',
      provider_id: 'minimax',
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
      required_manual_fields: ['credential_source', 'runtime_materialization_mode'],
    };
    const savedProfile: ProviderProfile = {
      profile_id: 'conformance-minimax-env-bundle',
      runtime_id: 'claude_code',
      provider_id: 'minimax',
      credential_source: 'secret_ref',
      runtime_materialization_mode: 'env_bundle',
      secret_refs: { provider_api_key: 'db://OPENAI_API_KEY' },
      max_parallel_runs: 1,
      cooldown_after_429_seconds: 300,
      rate_limit_policy: 'backoff',
      enabled: false,
    };
    const fetchSpy = vi.spyOn(window, 'fetch').mockImplementation(async (input, init) => {
      const url = String(input);
      const tier = tierCapabilitiesResponse(url);
      if (tier) return tier;
      if (url.startsWith('/api/v1/provider-profiles/creation-capabilities?')) {
        return {
          ok: true,
          json: async () => ({
            version: unsupportedPreset.version,
            runtime_id: 'claude_code',
            provider_id: 'minimax',
            supported: true,
            authentication_methods: [
              {
                id: 'api_key',
                label: 'MiniMax API key (expert)',
                setup_action: 'api_key',
                launch_ready_after_setup: false,
                fields: {
                  credential_source: presetField('secret_ref', true, 'expert_manual'),
                  runtime_materialization_mode: presetField('env_bundle', true, 'expert_manual'),
                },
                secret_roles: [
                  {
                    role: 'provider_api_key',
                    label: 'Provider API key',
                    required: true,
                    compatible_schemes: ['db'],
                  },
                ],
                imported_volume: {
                  supported: false,
                  mount_path: null,
                  source: 'expert_manual',
                  lock_reason: 'Manual profiles do not import a volume.',
                },
              },
            ],
            diagnostics: [],
          }),
        } as Response;
      }
      if (url.startsWith('/api/v1/provider-profiles/creation-preset?')) {
        return { ok: true, json: async () => unsupportedPreset } as Response;
      }
      if (url === '/api/v1/provider-profiles' && init?.method === 'POST') {
        return { ok: true, json: async () => savedProfile } as Response;
      }
      throw new Error(`Unexpected fetch: ${url}`);
    });

    renderManager();
    fireEvent.change(screen.getByLabelText(/Profile ID/), {
      target: { value: 'conformance-minimax-env-bundle' },
    });
    fireEvent.change(screen.getByLabelText(/Runtime ID/), { target: { value: 'claude_code' } });
    fireEvent.change(screen.getByLabelText(/Provider ID/), { target: { value: 'minimax' } });
    fireEvent.click(await screen.findByLabelText('MiniMax API key (expert)'));
    await screen.findByText('Use the authorized manual profile path.');

    // An unsupported combination reveals the expert region on its own and
    // requires explicit low-level fields instead of inheriting a guessed
    // materialization contract.
    expect((screen.getByLabelText('Show advanced options') as HTMLInputElement).checked).toBe(true);
    fireEvent.change(screen.getByLabelText('Credential source'), {
      target: { value: 'secret_ref' },
    });
    fireEvent.change(screen.getByLabelText('Materialization mode'), {
      target: { value: 'env_bundle' },
    });
    fireEvent.change(screen.getByLabelText('Provider API key (required)'), {
      target: { value: 'db://OPENAI_API_KEY' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Create profile' }));

    await waitFor(() =>
      expect(fetchSpy.mock.calls.some(([, init]) => (init as RequestInit | undefined)?.method === 'POST')).toBe(true),
    );
    const payload = postPayload(fetchSpy);
    expect(payload).toMatchObject({
      credential_source: 'secret_ref',
      runtime_materialization_mode: 'env_bundle',
      secret_refs: { provider_api_key: 'db://OPENAI_API_KEY' },
    });
    // Manual creation never carries a preset version it did not validate.
    expect(payload).not.toHaveProperty('preset_version');
  });
});

describe('MoonLadderStudios/MoonMind#3822 progressive disclosure and tier drafts', () => {
  const creationClass = CREATION_CLASSES[1]!; // Codex + OpenAI API key

  it('preserves advanced draft values across collapse and expand', async () => {
    creationFetch(creationClass, 'conformance-disclosure');
    renderManager();
    await startStandardCreation(creationClass, 'conformance-disclosure');

    const toggle = screen.getByLabelText('Show advanced options') as HTMLInputElement;
    fireEvent.click(toggle);
    fireEvent.change(screen.getByLabelText('Cooldown after 429 (seconds)'), {
      target: { value: '900' },
    });
    fireEvent.change(screen.getByLabelText('Tags'), { target: { value: 'team, preferred' } });

    fireEvent.click(toggle);
    expect(toggle.checked).toBe(false);
    expect(document.getElementById('provider-profile-advanced-region')).toBeNull();
    // The collapsed summary comes from backend preset metadata.
    expect(screen.getByText('Using recommended API key launch settings')).toBeTruthy();

    fireEvent.click(toggle);
    expect((screen.getByLabelText('Cooldown after 429 (seconds)') as HTMLInputElement).value).toBe('900');
    expect((screen.getByLabelText('Tags') as HTMLInputElement).value).toBe('team, preferred');
  });

  it('opens the disclosure and focuses the control a hidden-field validation error targets', async () => {
    const preset = presetFor(creationClass);
    const capabilities = capabilitiesFor(creationClass);
    vi.spyOn(window, 'fetch').mockImplementation(async (input, init) => {
      const url = String(input);
      const tier = tierCapabilitiesResponse(url);
      if (tier) return tier;
      if (url.startsWith('/api/v1/provider-profiles/creation-capabilities?')) {
        return { ok: true, json: async () => capabilities } as Response;
      }
      if (url.startsWith('/api/v1/provider-profiles/creation-preset?')) {
        return { ok: true, json: async () => preset } as Response;
      }
      if (url === '/api/v1/provider-profiles' && init?.method === 'POST') {
        return {
          ok: false,
          status: 422,
          statusText: 'Unprocessable Entity',
          json: async () => ({
            detail: {
              code: 'provider_profile_creation_preset_field_locked',
              message: 'cooldown_after_429_seconds is locked by the selected creation preset.',
              field: 'cooldown_after_429_seconds',
              lock_reason: 'Backend launch policy owns this value.',
            },
          }),
        } as Response;
      }
      throw new Error(`Unexpected fetch: ${url}`);
    });

    renderManager();
    await startStandardCreation(creationClass, 'conformance-hidden-error');
    expect((screen.getByLabelText('Show advanced options') as HTMLInputElement).checked).toBe(false);

    fireEvent.click(screen.getByRole('button', { name: 'Create profile' }));

    await waitFor(() =>
      expect((screen.getByLabelText('Show advanced options') as HTMLInputElement).checked).toBe(true),
    );
    await waitFor(() =>
      expect(document.activeElement).toBe(screen.getByLabelText('Cooldown after 429 (seconds)')),
    );
    expect(
      document.getElementById('provider-profile-advanced-region')?.contains(document.activeElement),
    ).toBe(true);
  });

  it('focuses the collapsed control a FastAPI request-validation array targets', async () => {
    const preset = presetFor(creationClass);
    const capabilities = capabilitiesFor(creationClass);
    vi.spyOn(window, 'fetch').mockImplementation(async (input, init) => {
      const url = String(input);
      const tier = tierCapabilitiesResponse(url);
      if (tier) return tier;
      if (url.startsWith('/api/v1/provider-profiles/creation-capabilities?')) {
        return { ok: true, json: async () => capabilities } as Response;
      }
      if (url.startsWith('/api/v1/provider-profiles/creation-preset?')) {
        return { ok: true, json: async () => preset } as Response;
      }
      if (url === '/api/v1/provider-profiles' && init?.method === 'POST') {
        // The real `ProviderProfileCreate` boundary: `cooldown_after_429_seconds`
        // is `Field(ge=0)`, so a negative draft raises FastAPI's standard
        // RequestValidationError payload rather than a custom error object.
        return {
          ok: false,
          status: 422,
          statusText: 'Unprocessable Entity',
          json: async () => ({
            detail: [
              {
                type: 'greater_than_equal',
                loc: ['body', 'cooldown_after_429_seconds'],
                msg: 'Input should be greater than or equal to 0',
                input: -1,
                ctx: { ge: 0 },
              },
            ],
          }),
        } as Response;
      }
      throw new Error(`Unexpected fetch: ${url}`);
    });

    renderManager();
    await startStandardCreation(creationClass, 'conformance-validation-array');

    // Author the invalid value, then collapse the disclosure so the unmounted
    // input can no longer enforce its native `min` before submission.
    const toggle = screen.getByLabelText('Show advanced options') as HTMLInputElement;
    fireEvent.click(toggle);
    fireEvent.change(screen.getByLabelText('Cooldown after 429 (seconds)'), {
      target: { value: '-1' },
    });
    fireEvent.click(toggle);
    expect(toggle.checked).toBe(false);
    expect(document.getElementById('provider-profile-advanced-region')).toBeNull();

    fireEvent.click(screen.getByRole('button', { name: 'Create profile' }));

    await waitFor(() =>
      expect((screen.getByLabelText('Show advanced options') as HTMLInputElement).checked).toBe(true),
    );
    await waitFor(() =>
      expect(document.activeElement).toBe(screen.getByLabelText('Cooldown after 429 (seconds)')),
    );
    expect(
      document.getElementById('provider-profile-advanced-region')?.contains(document.activeElement),
    ).toBe(true);
  });

  it('reveals and focuses backend-owned launch isolation when validation targets clear_env_keys', async () => {
    const preset = presetFor(creationClass);
    const capabilities = capabilitiesFor(creationClass);
    vi.spyOn(window, 'fetch').mockImplementation(async (input, init) => {
      const url = String(input);
      const tier = tierCapabilitiesResponse(url);
      if (tier) return tier;
      if (url.startsWith('/api/v1/provider-profiles/creation-capabilities?')) {
        return { ok: true, json: async () => capabilities } as Response;
      }
      if (url.startsWith('/api/v1/provider-profiles/creation-preset?')) {
        return { ok: true, json: async () => preset } as Response;
      }
      if (url === '/api/v1/provider-profiles' && init?.method === 'POST') {
        return {
          ok: false,
          status: 422,
          statusText: 'Unprocessable Entity',
          json: async () => ({
            detail: {
              code: 'provider_profile_clear_env_keys_locked',
              message: 'clear_env_keys is backend-owned launch-security metadata.',
              field: 'clear_env_keys',
              source: 'runtime_provider_isolation_policy',
            },
          }),
        } as Response;
      }
      throw new Error(`Unexpected fetch: ${url}`);
    });

    renderManager();
    await startStandardCreation(creationClass, 'conformance-clear-env');
    fireEvent.click(screen.getByRole('button', { name: 'Create profile' }));

    await waitFor(() =>
      expect((screen.getByLabelText('Show advanced options') as HTMLInputElement).checked).toBe(true),
    );
    await waitFor(() =>
      expect(
        (document.activeElement as HTMLElement | null)?.getAttribute('data-advanced-field'),
      ).toBe('clear_env_keys'),
    );
    // Standard creation still cannot author the policy.
    expect(screen.queryByLabelText(/Clear env keys/)).toBeNull();
    expect(screen.getByText(/Launch environment isolation — clear environment keys/)).toBeTruthy();
  });

  it('keeps tier drafts and advanced drafts independent across collapse', async () => {
    creationFetch(creationClass, 'conformance-tier-draft');
    renderManager();
    await startStandardCreation(creationClass, 'conformance-tier-draft');

    // One runtime-default tier at the start of the standard form.
    expect(screen.getAllByRole('radio', { name: 'Default tier' })).toHaveLength(1);

    const toggle = screen.getByLabelText('Show advanced options') as HTMLInputElement;
    fireEvent.click(toggle);
    fireEvent.change(screen.getByLabelText('Priority'), { target: { value: '55' } });

    // Build a custom ordered tier policy while advanced options are open.
    fireEvent.click(screen.getAllByRole('button', { name: 'Add tier' })[0]!);
    fireEvent.change(screen.getByLabelText('Tier 2 label'), { target: { value: 'Deep work' } });
    fireEvent.change(screen.getByLabelText('Tier 2 model'), { target: { value: 'gpt-4o' } });
    fireEvent.click(screen.getByRole('radio', { name: 'Use Tier 2 as default' }));

    // Collapsing advanced options must not erase the tier draft...
    fireEvent.click(toggle);
    expect((screen.getByLabelText('Tier 2 label') as HTMLInputElement).value).toBe('Deep work');
    expect((screen.getByLabelText('Tier 2 model') as HTMLSelectElement).value).toBe('gpt-4o');
    expect(screen.getByText('2 tiers · Default: Tier 2')).toBeTruthy();

    // ...and editing tiers while collapsed must not erase the advanced draft.
    fireEvent.change(screen.getByLabelText('Tier 1 label'), { target: { value: 'Quick' } });
    fireEvent.click(toggle);
    expect((screen.getByLabelText('Priority') as HTMLInputElement).value).toBe('55');
    expect((screen.getByLabelText('Tier 1 label') as HTMLInputElement).value).toBe('Quick');
  });

  it('saves the custom ordered tier policy canonically from the integrated create journey', async () => {
    const profileId = 'conformance-tier-save';
    const fetchSpy = creationFetch(creationClass, profileId);
    renderManager();
    await startStandardCreation(creationClass, profileId);

    fireEvent.click(screen.getAllByRole('button', { name: 'Add tier' })[0]!);
    fireEvent.change(screen.getByLabelText('Tier 2 label'), { target: { value: 'Deep work' } });
    fireEvent.change(screen.getByLabelText('Tier 2 model'), { target: { value: 'gpt-4o' } });
    fireEvent.change(screen.getByLabelText('Tier 2 effort'), { target: { value: 'high' } });
    fireEvent.click(screen.getByRole('radio', { name: 'Use Tier 2 as default' }));
    fireEvent.click(screen.getByRole('button', { name: 'Create profile' }));

    await waitFor(() =>
      expect(fetchSpy.mock.calls.some(([, init]) => (init as RequestInit | undefined)?.method === 'POST')).toBe(true),
    );
    const payload = postPayload(fetchSpy);
    expect(payload.model_tiers).toEqual([
      { label: null, model: null, effort: null, parameters: {}, annotations: {} },
      { label: 'Deep work', model: 'gpt-4o', effort: 'high', parameters: {}, annotations: {} },
    ]);
    expect(payload.default_model_tier).toBe(2);
    expect(payload).not.toHaveProperty('default_model');
    expect(payload).not.toHaveProperty('default_effort');
  });
});

describe('MoonLadderStudios/MoonMind#3822 existing-profile compatibility', () => {
  it('does not normalize, erase, or resubmit a malformed environment-clearing policy during edit', async () => {
    const legacyProfile: ProviderProfile = {
      profile_id: 'legacy-unsafe-isolation',
      runtime_id: 'codex_cli',
      provider_id: 'openai',
      authentication_method: 'api_key',
      credential_source: 'secret_ref',
      runtime_materialization_mode: 'api_key_env',
      secret_refs: { openai_api_key: 'db://OPENAI_API_KEY' },
      max_parallel_runs: 1,
      cooldown_after_429_seconds: 120,
      rate_limit_policy: 'backoff',
      enabled: false,
      is_default: false,
      auth_state: 'validation_failed',
      disabled_reason: 'auth_invalid',
      // Malformed/unsafe stored policy: a lowercase name, an invalid name, and
      // an unsafe key the backend classifies rather than silently rewrites.
      clear_env_keys: ['path', 'not a key', 'LD_PRELOAD'],
      model_tiers: [{ label: 'Only', model: 'gpt-4o', effort: 'medium', parameters: {}, annotations: {} }],
      default_model_tier: 1,
      launch_isolation: {
        effective_keys: ['path', 'not a key', 'LD_PRELOAD'],
        source: 'legacy_custom',
        derived: false,
        editable: false,
        lock_reason: 'Stored policy is not derivable; readiness is blocked until it is reviewed.',
        strategy_id: 'unknown',
        classification: 'legacy_custom',
        explanations: { 'not a key': 'Invalid environment variable name.' },
        audit_reason_present: false,
      },
      readiness: {
        status: 'blocked',
        launch_ready: false,
        summary: 'Isolation policy cannot be derived.',
        checks: [
          {
            id: 'clear_env_keys',
            label: 'Launch isolation',
            status: 'error',
            message: 'Stored environment-clearing policy is invalid.',
          },
        ],
      },
      creation_capabilities: capabilitiesFor(CREATION_CLASSES[1]!) as unknown as CreationCapabilities,
    };

    const fetchSpy = vi.spyOn(window, 'fetch').mockImplementation(async (input, init) => {
      const url = String(input);
      const tier = tierCapabilitiesResponse(url);
      if (tier) return tier;
      if (url.startsWith('/api/v1/provider-profiles/creation-preset?')) {
        return { ok: true, json: async () => presetFor(CREATION_CLASSES[1]!) } as Response;
      }
      if (url === '/api/v1/provider-profiles/legacy-unsafe-isolation' && init?.method === 'PATCH') {
        return { ok: true, json: async () => ({ ...legacyProfile, cooldown_after_429_seconds: 600 }) } as Response;
      }
      throw new Error(`Unexpected fetch: ${url} ${String(init?.method)}`);
    });

    renderManager([legacyProfile]);
    fireEvent.click(screen.getByRole('button', { name: 'Edit' }));
    fireEvent.click(screen.getByLabelText('Show advanced options'));

    // The invalid stored policy stays visible, attributed, and read-only.
    expect(screen.getByText(/Effective keys: path, not a key, LD_PRELOAD/)).toBeTruthy();
    expect(screen.getByText(/Classification: legacy_custom/)).toBeTruthy();
    expect(screen.getByText(/Locked by backend launch-safety policy/)).toBeTruthy();
    expect(screen.getByText(/Invalid environment variable name\./)).toBeTruthy();
    expect(screen.queryByLabelText(/Clear env keys/)).toBeNull();

    // An unrelated advanced edit must not resubmit, rewrite, or clear it, and
    // must not enable a profile the backend left disabled.
    fireEvent.change(screen.getByLabelText('Cooldown after 429 (seconds)'), {
      target: { value: '600' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Update provider profile' }));

    await waitFor(() =>
      expect(
        fetchSpy.mock.calls.some(
          ([url, init]) =>
            url === '/api/v1/provider-profiles/legacy-unsafe-isolation' &&
            (init as RequestInit | undefined)?.method === 'PATCH',
        ),
      ).toBe(true),
    );
    const patchCall = fetchSpy.mock.calls.find(
      ([url, init]) =>
        url === '/api/v1/provider-profiles/legacy-unsafe-isolation' &&
        (init as RequestInit | undefined)?.method === 'PATCH',
    );
    const payload = JSON.parse(String((patchCall?.[1] as RequestInit).body)) as Record<string, unknown>;
    expect(payload).toEqual({
      profile_id: 'legacy-unsafe-isolation',
      cooldown_after_429_seconds: 600,
    });
    expect(payload).not.toHaveProperty('clear_env_keys');
    expect(payload).not.toHaveProperty('enabled');
    expect(payload).not.toHaveProperty('is_default');
  });
});

describe('MoonLadderStudios/MoonMind#3822 generated-contract guard', () => {
  const managerSource = readFileSync(
    join(process.cwd(), 'frontend', 'src', 'components', 'settings', 'ProviderProfilesManager.tsx'),
    'utf8',
  );

  it('binds the creation preset, capability, and authentication contracts to generated types', () => {
    for (const generatedSchema of [
      'ProviderProfileCreationPresetResponse',
      'ProviderProfileCreationCapabilitiesResponse',
      'ProviderProfileAuthenticationMethodCapability',
      'ProviderProfileAuthenticationMethod',
    ]) {
      expect(managerSource).toContain(`components['schemas']['${generatedSchema}']`);
    }
  });

  it('rejects a second hand-maintained Provider Profile creation-preset schema in React', () => {
    // Any local interface or type literal redeclaring a generated
    // creation-preset/capability shape is a second source of truth.
    const handMaintainedDeclaration =
      /\b(?:interface|type)\s+(\w*(?:CreationPreset|CreationCapabilit|CreationField|AuthenticationMethodCapabilit|SecretRoleCapabilit|ImportedVolumeCapabilit)\w*)\b(?!\s*=\s*\n?\s*components\[)/g;
    const offenders: string[] = [];
    for (const match of managerSource.matchAll(handMaintainedDeclaration)) {
      offenders.push(match[1]!);
    }
    expect(offenders).toEqual([]);

    // The generated schema field set is not restated as a local object type.
    expect(managerSource).not.toMatch(/interface\s+\w+\s*\{[^}]*\bcompatible_schemes\b/);
    expect(managerSource).not.toMatch(/interface\s+\w+\s*\{[^}]*\blaunch_ready_after_setup\b/);
    expect(managerSource).not.toMatch(/interface\s+\w+\s*\{[^}]*\bmanual_creation_allowed\b/);
  });
});
