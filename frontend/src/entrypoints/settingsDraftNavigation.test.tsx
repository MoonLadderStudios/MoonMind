import { BrowserRouter } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, it, vi, type MockInstance } from 'vitest';

import type { BootPayload } from '../boot/parseBootPayload';
import { act, fireEvent, renderWithClient, screen, waitFor, within } from '../utils/test-utils';
import { ProvidersSecretsSettingsPage, InstanceSettingsPage } from './settings';

const generatedDescriptor = {
  key: 'workflow.default_publish_mode',
  title: 'Default Publish Mode',
  description: 'Default publication behavior.',
  category: 'Workflow',
  section: 'user-workspace',
  type: 'enum',
  ui: 'select',
  default_value: 'pr',
  effective_value: 'pr',
  override_value: null,
  source: 'default',
  source_explanation: 'Default value.',
  apply_mode: 'next_request',
  activation_state: 'active',
  active: true,
  options: [
    { value: 'pr', label: 'Pull request' },
    { value: 'branch', label: 'Branch' },
  ],
  constraints: null,
  sensitive: false,
  read_only: false,
  requires_reload: false,
  requires_worker_restart: false,
  requires_process_restart: false,
  applies_to: ['workflows'],
  order: 1,
  value_version: 1,
  diagnostics: [],
};

function ok(body: unknown): Response {
  return { ok: true, status: 200, statusText: 'OK', json: async () => body } as Response;
}

function savedProfile(profileId: string) {
  return {
    profile_id: profileId,
    runtime_id: 'codex_cli',
    provider_id: 'openai',
    credential_source: 'secret_ref',
    runtime_materialization_mode: 'api_key_env',
    secret_refs: {},
    max_parallel_runs: 1,
    cooldown_after_429_seconds: 300,
    rate_limit_policy: 'backoff',
    enabled: true,
    model_tiers: [{ label: 'Default', model: null, effort: null, parameters: {}, annotations: {} }],
    default_model_tier: 1,
  };
}

const savedSecrets = ['alpha', 'beta'].map((slug) => ({
  slug,
  status: 'active',
  details: {},
  createdAt: '2026-10-01T00:00:00Z',
}));

function rowAction(text: string, action: string): HTMLElement {
  const row = screen.getByText(text, { selector: 'td, td *' }).closest('tr');
  if (!row) throw new Error(`no row for ${text}`);
  return within(row).getByRole('button', { name: action });
}

const providersSecretsPayload = {
  page: 'settings-providers-secrets',
  apiBase: '/api',
  initialData: {
    runtimeConfig: { system: { supportedRuntimes: ['codex_cli', 'claude_code'] } },
  },
} as BootPayload;

function destinationLink(path: string): HTMLAnchorElement {
  const link = document.createElement('a');
  link.href = path;
  link.textContent = 'Destination';
  document.body.appendChild(link);
  return link;
}

describe('MoonLadderStudios/MoonMind#3818 Settings draft departure contract', () => {
  let fetchSpy: MockInstance;

  beforeEach(() => {
    fetchSpy = vi.spyOn(window, 'fetch').mockImplementation((input) => {
      const url = String(input);
      if (url.startsWith('/api/v1/settings/catalog')) {
        return Promise.resolve(ok({
          section: 'user-workspace',
          categories: { Workflow: [generatedDescriptor] },
        }));
      }
      if (url === '/api/v1/provider-profiles') return Promise.resolve(ok([]));
      if (url === '/api/v1/secrets') return Promise.resolve(ok({ items: [] }));
      return Promise.resolve({ ok: false, status: 404, statusText: 'Not Found', json: async () => ({}) } as Response);
    });
  });

  afterEach(() => {
    fetchSpy.mockRestore();
    document.querySelectorAll('a[href="/settings/operations"]').forEach((node) => node.remove());
  });

  it('keeps a generated-settings draft on Stay and discards it before route navigation', async () => {
    window.history.replaceState({}, '', '/settings/instance');
    renderWithClient(
      <BrowserRouter>
        <InstanceSettingsPage
          payload={{
            page: 'settings-instance',
            apiBase: '/api',
            initialData: {
            },
          } as BootPayload}
        />
      </BrowserRouter>,
    );

    const control = await screen.findByLabelText('Default Publish Mode') as HTMLSelectElement;
    fireEvent.change(control, { target: { value: 'branch' } });
    expect(window.dispatchEvent(new Event('beforeunload', { cancelable: true }))).toBe(false);

    const link = destinationLink('/settings/operations');
    fireEvent.click(link);
    expect(screen.getByRole('dialog', { name: 'Unsaved changes' })).toBeTruthy();

    fireEvent.click(screen.getByRole('button', { name: 'Stay' }));
    expect(window.location.pathname).toBe('/settings/instance');
    expect((screen.getByLabelText('Default Publish Mode') as HTMLSelectElement).value).toBe('branch');

    fireEvent.click(link);
    fireEvent.click(screen.getByRole('button', { name: 'Discard and leave' }));
    await waitFor(() => expect(window.location.pathname).toBe('/settings/operations'));
    expect((screen.getByLabelText('Default Publish Mode') as HTMLSelectElement).value).toBe('pr');
  });

  it('renders the Instance catalog without a human-scope switcher or scope history', async () => {
    window.history.replaceState({}, '', '/settings/instance');
    renderWithClient(
      <BrowserRouter>
        <InstanceSettingsPage
          payload={{
            page: 'settings-instance',
            apiBase: '/api',
            initialData: {
            },
          } as BootPayload}
        />
      </BrowserRouter>,
    );

    await screen.findByLabelText('Default Publish Mode');
    expect(screen.queryByLabelText('Settings scope')).toBeNull();
    expect(screen.queryByRole('button', { name: 'User' })).toBeNull();
    expect(screen.queryByRole('button', { name: 'Workspace' })).toBeNull();
    expect(window.location.search).toBe('');
  });

  it('protects a dirty Provider Profile form before changing routes or Harness filters', async () => {
    window.history.replaceState({}, '', '/settings/providers-secrets');
    renderWithClient(
      <BrowserRouter>
        <ProvidersSecretsSettingsPage
          payload={{
            page: 'settings-providers-secrets',
            apiBase: '/api',
            initialData: {
              runtimeConfig: { system: { supportedRuntimes: ['codex_cli', 'claude_code'] } },
            },
          } as BootPayload}
        />
      </BrowserRouter>,
    );

    const profileId = await screen.findByLabelText(/Profile ID/) as HTMLInputElement;
    fireEvent.change(profileId, { target: { value: 'draft-profile' } });
    fireEvent.change(screen.getByLabelText('Harness filter'), {
      target: { value: 'codex_cli' },
    });
    expect(screen.getByRole('dialog', { name: 'Unsaved changes' })).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: 'Stay' }));
    expect(profileId.value).toBe('draft-profile');
    expect(window.location.search).toBe('');

    const link = destinationLink('/settings/operations');
    fireEvent.click(link);
    fireEvent.click(screen.getByRole('button', { name: 'Discard and leave' }));
    await waitFor(() => expect(window.location.pathname).toBe('/settings/operations'));
  });

  it('protects and discards a Managed Secret draft before route navigation', async () => {
    window.history.replaceState({}, '', '/settings/providers-secrets');
    renderWithClient(
      <BrowserRouter>
        <ProvidersSecretsSettingsPage
          payload={{
            page: 'settings-providers-secrets',
            apiBase: '/api',
            initialData: {
            },
          } as BootPayload}
        />
      </BrowserRouter>,
    );

    const slug = await screen.findByLabelText('Secret slug') as HTMLInputElement;
    fireEvent.change(slug, { target: { value: 'draft-secret' } });

    const link = destinationLink('/settings/operations');
    fireEvent.click(link);
    expect(screen.getByRole('dialog', { name: 'Unsaved changes' })).toBeTruthy();

    fireEvent.click(screen.getByRole('button', { name: 'Stay' }));
    expect(slug.value).toBe('draft-secret');

    fireEvent.click(link);
    fireEvent.click(screen.getByRole('button', { name: 'Discard and leave' }));
    await waitFor(() => expect(window.location.pathname).toBe('/settings/operations'));
    expect(slug.value).toBe('');
  });

  it('keeps the unsaved-changes dialog keyboard-modal and restores focus on Escape', async () => {
    window.history.replaceState({}, '', '/settings/instance');
    renderWithClient(
      <BrowserRouter>
        <InstanceSettingsPage
          payload={{
            page: 'settings-instance',
            apiBase: '/api',
            initialData: {
            },
          } as BootPayload}
        />
      </BrowserRouter>,
    );

    fireEvent.change(await screen.findByLabelText('Default Publish Mode'), {
      target: { value: 'branch' },
    });
    const link = destinationLink('/settings/operations');
    link.focus();
    fireEvent.click(link);

    const dialog = screen.getByRole('dialog', { name: 'Unsaved changes' });
    const stay = screen.getByRole('button', { name: 'Stay' });
    const discard = screen.getByRole('button', { name: 'Discard and leave' });
    await waitFor(() => expect(document.activeElement).toBe(stay));

    fireEvent.keyDown(dialog, { key: 'Tab', shiftKey: true });
    expect(document.activeElement).toBe(discard);
    fireEvent.keyDown(dialog, { key: 'Tab' });
    expect(document.activeElement).toBe(stay);

    fireEvent.keyDown(dialog, { key: 'Escape' });
    await waitFor(() => expect(screen.queryByRole('dialog', { name: 'Unsaved changes' })).toBeNull());
    expect(document.activeElement).toBe(link);
    expect(window.location.pathname).toBe('/settings/instance');
  });

  it('keeps the Instance draft guarded across Back and Forward navigation', async () => {
    window.history.replaceState({}, '', '/settings/instance');
    renderWithClient(
      <BrowserRouter>
        <InstanceSettingsPage
          payload={{
            page: 'settings-instance',
            apiBase: '/api',
            initialData: { },
          } as BootPayload}
        />
      </BrowserRouter>,
    );

    await screen.findByLabelText('Default Publish Mode');
    expect(screen.queryByLabelText('Settings scope')).toBeNull();

    act(() => window.history.back());
    expect(window.location.pathname).toBe('/settings/instance');
    act(() => window.history.forward());
    expect(window.location.pathname).toBe('/settings/instance');
    expect(window.location.search).toBe('');
  });

  it('guards browser Back navigation and preserves the active draft when the user stays', async () => {
    window.history.replaceState({}, '', '/settings/providers-secrets');
    window.history.pushState({}, '', '/settings/instance');
    renderWithClient(
      <BrowserRouter>
        <InstanceSettingsPage
          payload={{
            page: 'settings-instance',
            apiBase: '/api',
            initialData: {
            },
          } as BootPayload}
        />
      </BrowserRouter>,
    );

    const control = await screen.findByLabelText('Default Publish Mode') as HTMLSelectElement;
    fireEvent.change(control, { target: { value: 'branch' } });
    act(() => window.history.back());
    expect(await screen.findByRole('dialog', { name: 'Unsaved changes' })).toBeTruthy();
    expect(window.location.pathname).toBe('/settings/instance');

    fireEvent.click(screen.getByRole('button', { name: 'Stay' }));
    expect(control.value).toBe('branch');
    expect(window.location.pathname).toBe('/settings/instance');

    act(() => window.history.back());
    expect(await screen.findByRole('dialog', { name: 'Unsaved changes' })).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: 'Discard and leave' }));
    await waitFor(() => expect(window.location.pathname).toBe('/settings/providers-secrets'));

    act(() => window.history.forward());
    await waitFor(() => expect(window.location.pathname).toBe('/settings/instance'));
  });

  it('restores the Harness filter across Back and Forward navigation', async () => {
    window.history.replaceState({}, '', '/settings/providers-secrets');
    renderWithClient(
      <BrowserRouter>
        <ProvidersSecretsSettingsPage
          payload={{
            page: 'settings-providers-secrets',
            apiBase: '/api',
            initialData: {
              runtimeConfig: { system: { supportedRuntimes: ['codex_cli', 'claude_code'] } },
            },
          } as BootPayload}
        />
      </BrowserRouter>,
    );

    const filter = await screen.findByLabelText('Harness filter') as HTMLSelectElement;
    fireEvent.change(filter, { target: { value: 'codex_cli' } });
    await waitFor(() => expect(window.location.search).toBe('?runtime=codex_cli'));
    fireEvent.change(filter, { target: { value: 'claude_code' } });
    await waitFor(() => expect(window.location.search).toBe('?runtime=claude_code'));

    act(() => window.history.back());
    await waitFor(() => expect(filter.value).toBe('codex_cli'));
    act(() => window.history.forward());
    await waitFor(() => expect(filter.value).toBe('claude_code'));
  });

  describe('in-page editor switches', () => {
    beforeEach(() => {
      fetchSpy.mockImplementation((input) => {
        const url = String(input);
        if (url === '/api/v1/provider-profiles') {
          return Promise.resolve(ok([savedProfile('profile-a'), savedProfile('profile-b')]));
        }
        if (url === '/api/v1/secrets') return Promise.resolve(ok({ items: savedSecrets }));
        if (url === '/api/v1/repository-connections') return Promise.resolve(ok({ items: [] }));
        return Promise.resolve({ ok: false, status: 404, statusText: 'Not Found', json: async () => ({}) } as Response);
      });
    });

    function renderProvidersSecrets() {
      window.history.replaceState({}, '', '/settings/providers-secrets');
      renderWithClient(
        <BrowserRouter>
          <ProvidersSecretsSettingsPage payload={providersSecretsPayload} />
        </BrowserRouter>,
      );
    }

    it('asks only about the Provider Profile draft when editing another profile, and discards only it', async () => {
      renderProvidersSecrets();
      await screen.findByText('profile-b', { selector: 'td, td *' });
      const slug = screen.getByLabelText('Secret slug') as HTMLInputElement;
      fireEvent.change(slug, { target: { value: 'draft-secret' } });

      // A clean profile editor switches without asking about the secret draft.
      fireEvent.click(rowAction('profile-a', 'Edit'));
      expect(screen.queryByRole('dialog', { name: 'Unsaved changes' })).toBeNull();
      expect(screen.getByRole('heading', { name: 'Edit Profile: profile-a' })).toBeTruthy();

      fireEvent.change(screen.getByLabelText(/Max parallel runs/), { target: { value: '4' } });
      fireEvent.click(rowAction('profile-b', 'Edit'));
      expect(screen.getByRole('dialog', { name: 'Unsaved changes' })).toBeTruthy();
      fireEvent.click(screen.getByRole('button', { name: 'Stay' }));
      expect(screen.getByRole('heading', { name: 'Edit Profile: profile-a' })).toBeTruthy();
      expect((screen.getByLabelText(/Max parallel runs/) as HTMLInputElement).value).toBe('4');

      fireEvent.click(rowAction('profile-b', 'Edit'));
      fireEvent.click(screen.getByRole('button', { name: 'Discard and continue' }));
      expect(await screen.findByRole('heading', { name: 'Edit Profile: profile-b' })).toBeTruthy();
      expect((screen.getByLabelText(/Max parallel runs/) as HTMLInputElement).value).toBe('1');
      expect(slug.value).toBe('draft-secret');
    });

    it('asks only about the secret draft when editing another secret, and discards only it', async () => {
      renderProvidersSecrets();
      fireEvent.click(await screen.findByRole('button', { name: 'Add profile' }));
      const profileId = await screen.findByLabelText(/Profile ID/) as HTMLInputElement;
      fireEvent.change(profileId, { target: { value: 'draft-profile' } });

      fireEvent.click(rowAction('alpha', 'Edit'));
      expect(screen.queryByRole('dialog', { name: 'Unsaved changes' })).toBeNull();
      const value = screen.getByLabelText('Secure value') as HTMLInputElement;
      fireEvent.change(value, { target: { value: 'new-alpha-value' } });

      fireEvent.click(rowAction('beta', 'Edit'));
      expect(screen.getByRole('dialog', { name: 'Unsaved changes' })).toBeTruthy();
      fireEvent.click(screen.getByRole('button', { name: 'Stay' }));
      expect((screen.getByLabelText('Secret slug') as HTMLInputElement).value).toBe('alpha');
      expect(value.value).toBe('new-alpha-value');

      fireEvent.click(rowAction('beta', 'Edit'));
      fireEvent.click(screen.getByRole('button', { name: 'Discard and continue' }));
      await waitFor(() => expect((screen.getByLabelText('Secret slug') as HTMLInputElement).value).toBe('beta'));
      expect((screen.getByLabelText('Secure value') as HTMLInputElement).value).toBe('');
      expect(profileId.value).toBe('draft-profile');
    });

    it('changes the Harness filter without asking about unrelated drafts', async () => {
      renderProvidersSecrets();
      await screen.findByText('profile-b', { selector: 'td, td *' });
      fireEvent.change(screen.getByLabelText('Secret slug'), { target: { value: 'draft-secret' } });

      fireEvent.change(screen.getByLabelText('Harness filter'), { target: { value: 'codex_cli' } });
      expect(screen.queryByRole('dialog', { name: 'Unsaved changes' })).toBeNull();
      await waitFor(() => expect(window.location.search).toBe('?runtime=codex_cli'));
      expect((screen.getByLabelText('Secret slug') as HTMLInputElement).value).toBe('draft-secret');
    });

    it('still discards every draft when leaving the Settings page', async () => {
      renderProvidersSecrets();
      fireEvent.click(await screen.findByRole('button', { name: 'Add profile' }));
      const profileId = await screen.findByLabelText(/Profile ID/) as HTMLInputElement;
      fireEvent.change(profileId, { target: { value: 'draft-profile' } });
      const slug = screen.getByLabelText('Secret slug') as HTMLInputElement;
      fireEvent.change(slug, { target: { value: 'draft-secret' } });

      fireEvent.click(destinationLink('/settings/operations'));
      fireEvent.click(screen.getByRole('button', { name: 'Discard and leave' }));
      await waitFor(() => expect(window.location.pathname).toBe('/settings/operations'));
      expect(slug.value).toBe('');
      // Discarding the profile draft also closes the Add profile editor.
      expect(screen.queryByLabelText(/Profile ID/)).toBeNull();
    });
  });
});
