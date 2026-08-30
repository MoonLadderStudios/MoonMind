import { BrowserRouter } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, it, vi, type MockInstance } from 'vitest';

import type { BootPayload } from '../boot/parseBootPayload';
import { fireEvent, renderWithClient, screen, waitFor } from '../utils/test-utils';
import {
  OperationsSettingsPage,
  ProvidersSecretsSettingsPage,
  SettingsEntryPage,
  UserWorkspaceSettingsPage,
} from './settings';

function renderRoute(Page: typeof ProvidersSecretsSettingsPage, payload: BootPayload) {
  return renderWithClient(
    <BrowserRouter>
      <Page payload={payload} />
    </BrowserRouter>,
  );
}

function response(body: unknown): Response {
  return {
    ok: true,
    status: 200,
    statusText: 'OK',
    json: async () => body,
  } as Response;
}

const userWorkspaceCatalog = {
  section: 'user-workspace',
  scope: 'workspace',
  categories: {
    Workflow: [
      {
        key: 'workflow.default_publish_mode',
        title: 'Default Publish Mode',
        description: 'Fallback publish mode used when tasks omit publish mode.',
        category: 'Workflow',
        section: 'user-workspace',
        type: 'string',
        ui: 'readonly',
        scopes: ['workspace'],
        default_value: 'pr',
        effective_value: 'pr',
        override_value: null,
        source: 'default',
        source_explanation: 'Resolved from default.',
        apply_mode: 'next_task',
        activation_state: 'active',
        active: true,
        pending_value: null,
        affected_process_or_worker: 'publishing',
        completion_guidance: null,
        options: null,
        constraints: null,
        sensitive: false,
        secret_role: null,
        read_only: true,
        read_only_reason: 'Read-only test descriptor.',
        requires_reload: false,
        requires_worker_restart: false,
        requires_process_restart: false,
        applies_to: ['publishing'],
        depends_on: [],
        order: 10,
        audit: { store_old_value: true, store_new_value: true, redact: false },
        value_version: 1,
        diagnostics: [],
      },
    ],
  },
};

describe('MoonLadderStudios/MoonMind#3818 route-owned Settings pages', () => {
  let fetchSpy: MockInstance;
  const requestedUrls: string[] = [];

  beforeEach(() => {
    requestedUrls.length = 0;
    fetchSpy = vi.spyOn(window, 'fetch').mockImplementation((input) => {
      const url = String(input);
      requestedUrls.push(url);
      if (url === '/api/v1/provider-profiles') return Promise.resolve(response([]));
      if (url === '/api/v1/secrets') return Promise.resolve(response({ items: [] }));
      if (url === '/me') return Promise.resolve(response({ id: 'user-1', email: 'user@example.com' }));
      if (url.startsWith('/api/v1/settings/audit')) {
        return Promise.resolve(response({ items: [] }));
      }
      if (url.startsWith('/api/v1/settings/catalog')) {
        const scope = url.includes('scope=user') ? 'user' : 'workspace';
        return Promise.resolve(response({ ...userWorkspaceCatalog, scope }));
      }
      if (url === '/api/system/worker-pause') {
        return Promise.resolve(response({ system: { workersPaused: false }, metrics: {}, commands: [] }));
      }
      if (url === '/api/v1/operations/codex/shards') return Promise.resolve(response({ shards: [] }));
      if (url === '/api/v1/operations/deployment/stacks/moonmind') {
        return Promise.resolve(response({
          stack: 'moonmind',
          projectName: 'moonmind',
          currentImage: { evidence: 'available' },
          recentActions: [],
          policy: {
            repository: 'moonmind',
            allowedReferences: [],
            recentTags: [],
            mutableReferences: [],
            allowedModes: [],
          },
        }));
      }
      if (url === '/api/v1/operations/deployment/image-targets?stack=moonmind') {
        return Promise.resolve(response({ stack: 'moonmind', repositories: [] }));
      }
      return Promise.resolve({ ok: false, status: 404, statusText: 'Not Found', json: async () => ({}) } as Response);
    });
  });

  afterEach(() => {
    fetchSpy.mockRestore();
  });

  it('mounts only Providers & Secrets primary data and managers', async () => {
    window.history.replaceState({}, '', '/settings/providers-secrets');
    renderRoute(ProvidersSecretsSettingsPage, {
      page: 'settings-providers-secrets',
      apiBase: '/api',
      initialData: {
        settingsPermissions: [
          'provider_profiles.read',
          'provider_profiles.write',
          'secrets.metadata.read',
          'settings.effective.read',
        ],
      },
    } as BootPayload);

    expect(await screen.findByRole('heading', { name: 'Providers & Secrets' })).toBeTruthy();
    await waitFor(() => expect(requestedUrls).toContain('/api/v1/provider-profiles'));
    expect(requestedUrls).toContain('/api/v1/secrets');
    expect(requestedUrls.some((url) => url.startsWith('/api/v1/settings/catalog'))).toBe(false);
    expect(requestedUrls).not.toContain('/me');
    expect(requestedUrls).not.toContain('/api/system/worker-pause');
    expect(screen.queryByLabelText('Generated user and workspace settings')).toBeNull();
    expect(screen.queryByLabelText('Worker Operations')).toBeNull();
    expect(screen.queryByRole('radio')).toBeNull();
  });

  it('mounts only User / Workspace primary data', async () => {
    window.history.replaceState({}, '', '/settings/user-workspace?scope=workspace');
    renderRoute(UserWorkspaceSettingsPage, {
      page: 'settings-user-workspace',
      apiBase: '/api',
      initialData: { settingsPermissions: ['settings.catalog.read'] },
    } as BootPayload);

    expect(await screen.findByRole('heading', { name: 'User / Workspace' })).toBeTruthy();
    await waitFor(() => {
      expect(requestedUrls).toContain(
        '/api/v1/settings/catalog?section=user-workspace&scope=workspace',
      );
    });
    expect(requestedUrls).toContain('/me');
    expect(requestedUrls).not.toContain('/api/v1/provider-profiles');
    expect(requestedUrls).not.toContain('/api/v1/secrets');
    expect(requestedUrls).not.toContain('/api/system/worker-pause');
    expect(screen.queryByRole('heading', { name: 'Provider Profiles' })).toBeNull();
    expect(screen.queryByLabelText('Worker Operations')).toBeNull();
    expect(screen.queryByRole('button', { name: /View audit for/ })).toBeNull();
  });

  it('maps settings.audit.read to an on-demand User / Workspace audit request', async () => {
    window.history.replaceState({}, '', '/settings/user-workspace?scope=workspace');
    renderRoute(UserWorkspaceSettingsPage, {
      page: 'settings-user-workspace',
      apiBase: '/api',
      initialData: {
        settingsPermissions: ['settings.catalog.read', 'settings.audit.read'],
      },
    } as BootPayload);

    const auditButton = await screen.findByRole('button', {
      name: 'View audit for Default Publish Mode',
    });
    expect(requestedUrls.some((url) => url.startsWith('/api/v1/settings/audit'))).toBe(false);
    fireEvent.click(auditButton);

    await waitFor(() => {
      const auditUrl = requestedUrls.find((url) => url.startsWith('/api/v1/settings/audit'));
      expect(auditUrl).toBeTruthy();
      const query = new URL(auditUrl ?? '', window.location.origin).searchParams;
      expect(query.get('scope')).toBe('workspace');
      expect(query.get('key')).toBe('workflow.default_publish_mode');
    });
  });

  it('mounts only Operations primary data', async () => {
    window.history.replaceState({}, '', '/settings/operations');
    renderRoute(OperationsSettingsPage, {
      page: 'settings-operations',
      apiBase: '/api',
      initialData: {
        settingsPermissions: ['operations.read', 'operations.invoke'],
        workerPause: {
          get: '/api/system/worker-pause',
          post: '/api/system/worker-pause',
          shardHealth: '/api/v1/operations/codex/shards',
        },
      },
    } as BootPayload);

    expect(await screen.findByRole('heading', { name: 'Operations' })).toBeTruthy();
    await waitFor(() => expect(requestedUrls).toContain('/api/system/worker-pause'));
    expect(requestedUrls).not.toContain('/api/v1/provider-profiles');
    expect(requestedUrls).not.toContain('/api/v1/secrets');
    expect(requestedUrls.some((url) => url.startsWith('/api/v1/settings/catalog'))).toBe(false);
    expect(screen.queryByRole('heading', { name: 'Provider Profiles' })).toBeNull();
    expect(screen.queryByLabelText('Generated user and workspace settings')).toBeNull();
  });

  it('shows an intentional unavailable state for a direct unauthorized route without fetching', () => {
    window.history.replaceState({}, '', '/settings/providers-secrets');
    renderRoute(ProvidersSecretsSettingsPage, {
      page: 'settings-providers-secrets',
      apiBase: '/api',
      initialData: { settingsPermissions: [] },
    } as BootPayload);

    expect(screen.getByRole('heading', { name: 'Providers & Secrets' })).toBeTruthy();
    expect(screen.getByRole('region', { name: 'Providers & Secrets unavailable' })).toBeTruthy();
    expect(requestedUrls).toEqual([]);
    expect(window.location.pathname).toBe('/settings/providers-secrets');
  });

  it('preserves an accessible region when a sibling query on the same page fails', async () => {
    fetchSpy.mockImplementation((input) => {
      const url = String(input);
      requestedUrls.push(url);
      if (url === '/api/v1/provider-profiles') {
        return Promise.resolve({
          ok: false,
          status: 503,
          statusText: 'Unavailable',
          json: async () => ({}),
        } as Response);
      }
      if (url === '/api/v1/secrets') return Promise.resolve(response({ items: [] }));
      return Promise.resolve({ ok: false, status: 404, statusText: 'Not Found', json: async () => ({}) } as Response);
    });
    window.history.replaceState({}, '', '/settings/providers-secrets');
    renderRoute(ProvidersSecretsSettingsPage, {
      page: 'settings-providers-secrets',
      apiBase: '/api',
      initialData: {
        settingsPermissions: ['provider_profiles.read', 'secrets.metadata.read'],
      },
    } as BootPayload);

    expect(await screen.findByText('Failed to load provider profiles.')).toBeTruthy();
    expect(screen.getByRole('heading', { name: 'Managed Secrets' })).toBeTruthy();
  });

  it('keeps Operations inspection available while disabling commands for read-only users', async () => {
    window.history.replaceState({}, '', '/settings/operations');
    renderRoute(OperationsSettingsPage, {
      page: 'settings-operations',
      apiBase: '/api',
      initialData: {
        settingsPermissions: ['operations.read'],
        workerPause: {
          get: '/api/system/worker-pause',
          post: '/api/system/worker-pause',
          shardHealth: '/api/v1/operations/codex/shards',
        },
      },
    } as BootPayload);

    expect(await screen.findByText(/commands are read-only/i)).toBeTruthy();
    await waitFor(() => {
      expect((screen.getByRole('button', { name: 'Update MoonMind' }) as HTMLButtonElement).disabled).toBe(true);
    });
  });

  it('resolves the bare Settings entry to the first destination the user can inspect', async () => {
    window.history.replaceState({}, '', '/settings');
    renderWithClient(
      <BrowserRouter>
        <SettingsEntryPage
          payload={{
            page: 'settings-entry',
            apiBase: '/api',
            initialData: { settingsPermissions: ['operations.read'] },
          } as BootPayload}
        />
      </BrowserRouter>,
    );

    await waitFor(() => expect(window.location.pathname).toBe('/settings/operations'));
  });
});
