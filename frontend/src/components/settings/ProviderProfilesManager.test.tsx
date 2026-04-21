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
});
