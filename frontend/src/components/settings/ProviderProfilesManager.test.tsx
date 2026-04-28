import { QueryClient, QueryClientProvider, useQuery } from '@tanstack/react-query';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
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
} from './ProviderProfilesManager';
import { renderWithClient } from '../../utils/test-utils';

afterEach(() => {
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

  it('labels secret refs and resets create-form values', () => {
    renderProviderProfilesManager();

    const secretRefs = screen.getByLabelText('Secret refs (JSON object of string refs)');
    expect(secretRefs.tagName).toBe('TEXTAREA');

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

    const fetchCall = fetchSpy.mock.calls[0];
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

  it('reports form validation failures through the save mutation', async () => {
    const fetchSpy = vi.spyOn(window, 'fetch');
    const { onNotice } = renderProviderProfilesManager();

    fireEvent.change(screen.getByLabelText(/Profile ID/), {
      target: { value: 'codex-default' },
    });
    fireEvent.change(screen.getByLabelText(/Runtime ID/), {
      target: { value: 'codex_cli' },
    });
    fireEvent.change(screen.getByLabelText(/Provider ID/), {
      target: { value: 'openai' },
    });
    fireEvent.change(screen.getByLabelText('Secret refs (JSON object of string refs)'), {
      target: { value: '[]' },
    });

    fireEvent.click(screen.getByRole('button', { name: 'Create provider profile' }));

    await waitFor(() => {
      expect(onNotice).toHaveBeenCalledWith({
        level: 'error',
        text: 'Secret refs must be a JSON object.',
      });
    });
    expect(fetchSpy).not.toHaveBeenCalled();
  });

  it('starts a Codex OAuth session from the profile Auth action', async () => {
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

    fireEvent.click(screen.getByRole('button', { name: 'Auth codex-oauth' }));

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

    fireEvent.click(screen.getByRole('button', { name: 'Auth codex-oauth' }));

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

    fireEvent.click(screen.getByRole('button', { name: 'Auth codex-oauth' }));

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

    expect(screen.getByRole('button', { name: 'Connect with Claude OAuth claude-anthropic' })).toBeTruthy();
    expect(screen.getByRole('button', { name: 'Use Anthropic API key claude-anthropic' })).toBeTruthy();
    expect(screen.queryByRole('button', { name: 'Auth claude-anthropic' })).toBeNull();
    expect(screen.getByText('Claude credentials not connected')).toBeTruthy();
  });

  it('shows supported Claude OAuth lifecycle actions for connected claude_anthropic rows', () => {
    renderProviderProfilesManager([connectedClaudeCredentialProfile]);

    expect(
      screen.getByRole('button', { name: 'Connect with Claude OAuth claude-anthropic-connected' }),
    ).toBeTruthy();
    expect(
      screen.getByRole('button', { name: 'Use Anthropic API key claude-anthropic-connected' }),
    ).toBeTruthy();
    expect(screen.getByRole('button', { name: 'Validate OAuth claude-anthropic-connected' })).toBeTruthy();
    expect(screen.getByRole('button', { name: 'Disconnect OAuth claude-anthropic-connected' })).toBeTruthy();
    expect(screen.queryByRole('button', { name: 'Auth claude-anthropic-connected' })).toBeNull();
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

    expect(screen.queryByRole('button', { name: /Connect with Claude OAuth/ })).toBeNull();
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
    expect(screen.queryByRole('button', { name: /Connect with Claude OAuth/ })).toBeNull();
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

    fireEvent.click(screen.getByRole('button', { name: 'Connect with Claude OAuth claude-anthropic' }));

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
    expect(screen.getByText('not_connected')).toBeTruthy();
    expect(screen.getByText('awaiting_external_step')).toBeTruthy();
    expect(screen.getByText(/Use an Anthropic API key for Claude Code launches/i)).toBeTruthy();
    expect(dialog.textContent).not.toMatch(/terminal OAuth/i);
  });

  it('advances to secure token paste and blocks empty submission', async () => {
    const { onNotice } = renderProviderProfilesManager([claudeCredentialProfile]);

    fireEvent.click(screen.getByRole('button', { name: 'Use Anthropic API key claude-anthropic' }));
    fireEvent.click(screen.getByRole('button', { name: 'Continue to API key paste' }));

    expect(screen.getByText('awaiting_token_paste')).toBeTruthy();
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

    expect(await screen.findByText('validating_token')).toBeTruthy();
    expect(screen.getByText('saving_secret')).toBeTruthy();
    expect(screen.getByText('updating_profile')).toBeTruthy();
    expect(await screen.findByText('ready')).toBeTruthy();
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
});
