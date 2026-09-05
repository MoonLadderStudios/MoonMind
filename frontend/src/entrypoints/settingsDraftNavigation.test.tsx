import { BrowserRouter } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, it, vi, type MockInstance } from 'vitest';

import type { BootPayload } from '../boot/parseBootPayload';
import { act, fireEvent, renderWithClient, screen, waitFor } from '../utils/test-utils';
import { ProvidersSecretsSettingsPage, UserWorkspaceSettingsPage } from './settings';

const generatedDescriptor = {
  key: 'workflow.default_publish_mode',
  title: 'Default Publish Mode',
  description: 'Default publication behavior.',
  category: 'Workflow',
  section: 'user-workspace',
  type: 'enum',
  ui: 'select',
  scopes: ['workspace', 'user'],
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
      if (url === '/me') return Promise.resolve(ok({ id: 'user-1', email: 'user@example.com' }));
      if (url.startsWith('/api/v1/settings/catalog')) {
        return Promise.resolve(ok({
          section: 'user-workspace',
          scope: url.includes('scope=user') ? 'user' : 'workspace',
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
    window.history.replaceState({}, '', '/settings/user-workspace?scope=workspace');
    renderWithClient(
      <BrowserRouter>
        <UserWorkspaceSettingsPage
          payload={{
            page: 'settings-user-workspace',
            apiBase: '/api',
            initialData: {
              settingsPermissions: ['settings.catalog.read', 'settings.workspace.write'],
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
    expect(window.location.pathname).toBe('/settings/user-workspace');
    expect((screen.getByLabelText('Default Publish Mode') as HTMLSelectElement).value).toBe('branch');

    fireEvent.click(link);
    fireEvent.click(screen.getByRole('button', { name: 'Discard and leave' }));
    await waitFor(() => expect(window.location.pathname).toBe('/settings/operations'));
    expect((screen.getByLabelText('Default Publish Mode') as HTMLSelectElement).value).toBe('pr');
  });

  it('guards generated-settings scope changes and stores the confirmed scope in history', async () => {
    window.history.replaceState({}, '', '/settings/user-workspace?scope=workspace');
    renderWithClient(
      <BrowserRouter>
        <UserWorkspaceSettingsPage
          payload={{
            page: 'settings-user-workspace',
            apiBase: '/api',
            initialData: {
              settingsPermissions: [
                'settings.catalog.read',
                'settings.workspace.write',
                'settings.user.write',
              ],
            },
          } as BootPayload}
        />
      </BrowserRouter>,
    );

    fireEvent.change(await screen.findByLabelText('Default Publish Mode'), {
      target: { value: 'branch' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'User' }));
    fireEvent.click(screen.getByRole('button', { name: 'Stay' }));
    expect(window.location.search).toBe('?scope=workspace');

    fireEvent.click(screen.getByRole('button', { name: 'User' }));
    fireEvent.click(screen.getByRole('button', { name: 'Discard and leave' }));
    await waitFor(() => expect(window.location.search).toBe('?scope=user'));
    expect(await screen.findByRole('heading', { name: 'User scope' })).toBeTruthy();
  });

  it('protects a dirty Provider Profile form before changing routes or runtime filters', async () => {
    window.history.replaceState({}, '', '/settings/providers-secrets');
    renderWithClient(
      <BrowserRouter>
        <ProvidersSecretsSettingsPage
          payload={{
            page: 'settings-providers-secrets',
            apiBase: '/api',
            initialData: {
              settingsPermissions: [
                'provider_profiles.read',
                'provider_profiles.write',
                'secrets.metadata.read',
              ],
              runtimeConfig: { system: { supportedRuntimes: ['codex_cli', 'claude_code'] } },
            },
          } as BootPayload}
        />
      </BrowserRouter>,
    );

    const profileId = await screen.findByLabelText(/Profile ID/) as HTMLInputElement;
    fireEvent.change(profileId, { target: { value: 'draft-profile' } });
    fireEvent.change(screen.getByLabelText('Profile runtime filter'), {
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
              settingsPermissions: ['provider_profiles.read', 'secrets.metadata.read'],
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
    window.history.replaceState({}, '', '/settings/user-workspace?scope=workspace');
    renderWithClient(
      <BrowserRouter>
        <UserWorkspaceSettingsPage
          payload={{
            page: 'settings-user-workspace',
            apiBase: '/api',
            initialData: {
              settingsPermissions: ['settings.catalog.read', 'settings.workspace.write'],
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
    expect(window.location.pathname).toBe('/settings/user-workspace');
  });

  it('restores page-local scope across Back and Forward navigation', async () => {
    window.history.replaceState({}, '', '/settings/user-workspace?scope=workspace');
    renderWithClient(
      <BrowserRouter>
        <UserWorkspaceSettingsPage
          payload={{
            page: 'settings-user-workspace',
            apiBase: '/api',
            initialData: { settingsPermissions: ['settings.catalog.read'] },
          } as BootPayload}
        />
      </BrowserRouter>,
    );

    await screen.findByRole('heading', { name: 'Workspace scope' });
    fireEvent.click(await screen.findByRole('button', { name: 'User' }));
    expect(await screen.findByRole('heading', { name: 'User scope' })).toBeTruthy();

    act(() => window.history.back());
    expect(await screen.findByRole('heading', { name: 'Workspace scope' })).toBeTruthy();
    act(() => window.history.forward());
    expect(await screen.findByRole('heading', { name: 'User scope' })).toBeTruthy();
  });

  it('guards browser Back navigation and preserves the active draft when the user stays', async () => {
    window.history.replaceState({}, '', '/settings/providers-secrets');
    window.history.pushState({}, '', '/settings/user-workspace?scope=workspace');
    renderWithClient(
      <BrowserRouter>
        <UserWorkspaceSettingsPage
          payload={{
            page: 'settings-user-workspace',
            apiBase: '/api',
            initialData: {
              settingsPermissions: ['settings.catalog.read', 'settings.workspace.write'],
            },
          } as BootPayload}
        />
      </BrowserRouter>,
    );

    const control = await screen.findByLabelText('Default Publish Mode') as HTMLSelectElement;
    fireEvent.change(control, { target: { value: 'branch' } });
    act(() => window.history.back());
    expect(await screen.findByRole('dialog', { name: 'Unsaved changes' })).toBeTruthy();
    expect(window.location.pathname).toBe('/settings/user-workspace');

    fireEvent.click(screen.getByRole('button', { name: 'Stay' }));
    expect(control.value).toBe('branch');
    expect(window.location.pathname).toBe('/settings/user-workspace');

    act(() => window.history.back());
    expect(await screen.findByRole('dialog', { name: 'Unsaved changes' })).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: 'Discard and leave' }));
    await waitFor(() => expect(window.location.pathname).toBe('/settings/providers-secrets'));

    act(() => window.history.forward());
    await waitFor(() => expect(window.location.pathname).toBe('/settings/user-workspace'));
  });

  it('restores the Profile runtime filter across Back and Forward navigation', async () => {
    window.history.replaceState({}, '', '/settings/providers-secrets');
    renderWithClient(
      <BrowserRouter>
        <ProvidersSecretsSettingsPage
          payload={{
            page: 'settings-providers-secrets',
            apiBase: '/api',
            initialData: {
              settingsPermissions: ['provider_profiles.read', 'secrets.metadata.read'],
              runtimeConfig: { system: { supportedRuntimes: ['codex_cli', 'claude_code'] } },
            },
          } as BootPayload}
        />
      </BrowserRouter>,
    );

    const filter = await screen.findByLabelText('Profile runtime filter') as HTMLSelectElement;
    fireEvent.change(filter, { target: { value: 'codex_cli' } });
    await waitFor(() => expect(window.location.search).toBe('?runtime=codex_cli'));
    fireEvent.change(filter, { target: { value: 'claude_code' } });
    await waitFor(() => expect(window.location.search).toBe('?runtime=claude_code'));

    act(() => window.history.back());
    await waitFor(() => expect(filter.value).toBe('codex_cli'));
    act(() => window.history.forward());
    await waitFor(() => expect(filter.value).toBe('claude_code'));
  });
});
