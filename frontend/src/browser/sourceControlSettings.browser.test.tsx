import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { page, userEvent } from 'vitest/browser';
import { BrowserRouter } from 'react-router-dom';

import type { BootPayload } from '../boot/parseBootPayload';
import { ProvidersSecretsSettingsPage } from '../entrypoints/settings';
import { renderWithClient, screen } from '../utils/test-utils';
import '../styles/dashboard.css';

// Real-browser journey for Source Control on /settings/providers-secrets:
// accessible names and keyboard operation, a Test Connection bound to the
// selected connection, and phone layouts without horizontal overflow.

const TOLERANCE_PX = 1.5;

const payload: BootPayload = {
  page: 'settings-providers-secrets',
  apiBase: '/api',
  initialData: {
    settingsPermissions: ['provider_profiles.read', 'secrets.metadata.read'],
  },
} as unknown as BootPayload;

function connection(id: string, overrides: Record<string, unknown> = {}) {
  return {
    id,
    displayName: `Connection ${id}`,
    credentialKind: 'pat',
    account: `${id}-bot`,
    installation: null,
    endpoint: 'https://github.com',
    lifecycle: 'active',
    state: 'no_repositories',
    stateSummary: 'No repositories assigned. This connection grants no repository access until you assign one.',
    allowedOperations: ['read'],
    assignments: [],
    policyRevision: 1,
    credentialRevision: 1,
    secretRevision: 1,
    ...overrides,
  };
}

const LIST = {
  items: [
    connection('alpha', {
      state: 'ready',
      stateSummary: 'Assigned to 1 repository.',
      assignments: [
        { repository: 'acme/a-very-long-repository-name-for-phones', providerRepoId: '9001', operations: ['read'], revision: 1 },
      ],
    }),
    connection('beta'),
  ],
  probeModes: [
    {
      mode: 'indexing',
      label: 'Read contents',
      description: 'Reads the repository and branch used to clone and inspect code.',
      requiredPermissions: { Contents: 'read' },
      optionalPermissions: {},
    },
  ],
};

const PROBE = {
  connectionId: 'alpha',
  policyRevision: 1,
  credentialRevision: 1,
  secretRevision: 1,
  repo: 'acme/a-very-long-repository-name-for-phones',
  mode: 'indexing',
  repositoryAccessible: true,
  defaultBranchAccessible: true,
  pullRequestAccessible: null,
  resolvedBranch: 'develop',
  branchSource: 'remote_default',
  writeVerified: false,
  permissionChecklist: [{ permission: 'Contents', level: 'read', required: true, status: 'passed' }],
  diagnostics: [],
  limitations: [],
};

let shell: HTMLElement;
let panel: HTMLElement;
let fetchMock: ReturnType<typeof vi.fn>;

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  });
}

async function renderPage(): Promise<void> {
  renderWithClient(
    <BrowserRouter>
      <ProvidersSecretsSettingsPage payload={payload} />
    </BrowserRouter>,
    { container: panel },
  );
  await screen.findByRole('list', { name: 'Repository connections' });
}

beforeEach(() => {
  document.body.style.margin = '0';
  window.history.pushState({}, 'Settings', '/settings/providers-secrets');
  fetchMock = vi.fn(async (input: RequestInfo | URL, init: RequestInit = {}) => {
    const url = String(input);
    if (url === '/api/v1/repository-connections') return jsonResponse(LIST);
    if (url === '/api/v1/repository-connections/alpha/probe' && init.method === 'POST') {
      return jsonResponse(PROBE);
    }
    if (url.startsWith('/api/v1/provider-profiles')) return jsonResponse([]);
    if (url.startsWith('/api/v1/secrets')) return jsonResponse({ items: [] });
    return jsonResponse({}, 404);
  });
  vi.stubGlobal('fetch', fetchMock);
  shell = document.createElement('main');
  shell.className = 'dashboard-root';
  const content = document.createElement('div');
  content.className = 'dashboard-content';
  panel = document.createElement('section');
  panel.className = 'panel panel--data-wide';
  content.appendChild(panel);
  shell.appendChild(content);
  document.body.appendChild(shell);
});

afterEach(async () => {
  shell.remove();
  document.body.style.margin = '';
  vi.unstubAllGlobals();
  await page.viewport(1280, 800);
});

describe('Source Control settings', () => {
  it('selects and tests a connection with accessible, keyboard-operable controls', async () => {
    await renderPage();
    const section = page.getByRole('region', { name: 'Source Control' });
    await expect.element(section).toBeVisible();

    const alpha = page.getByRole('button', { name: /Connection alpha/ });
    await expect.element(alpha).toHaveAttribute('aria-pressed', 'false');
    await alpha.click();
    await expect.element(alpha).toHaveAttribute('aria-pressed', 'true');
    await expect.element(page.getByRole('region', { name: 'Connection Connection alpha' })).toBeVisible();

    // Test Connection runs from the keyboard and reports only this connection's evidence.
    // The repository input offers assigned repositories, so it is a combobox.
    const repository = page.getByRole('combobox', { name: 'Repository (owner/name)' });
    await repository.click();
    await userEvent.keyboard('{Enter}');
    await expect.element(page.getByRole('region', { name: 'Test result' })).toBeVisible();
    expect(
      fetchMock.mock.calls.filter(([url]) => String(url).includes('/probe')).map(([url]) => String(url)),
    ).toEqual(['/api/v1/repository-connections/alpha/probe']);
    await expect.element(page.getByText('develop (repository default), readable: yes')).toBeVisible();
    await expect.element(page.getByText(/Write permission was not tested/)).toBeVisible();
  });

  it('opens the token form from the keyboard with a masked, non-autofilled token input', async () => {
    await renderPage();
    const add = page.getByRole('button', { name: 'Add connection' });
    (add.element() as HTMLElement).focus();
    await userEvent.keyboard('{Enter}');

    const token = page.getByLabelText('Personal access token');
    await expect.element(token).toHaveAttribute('type', 'password');
    await expect.element(token).toHaveAttribute('autocomplete', 'new-password');
    await expect.element(page.getByRole('form', { name: 'Add a token connection' })).toBeVisible();
    await expect.element(page.getByRole('button', { name: 'Save connection' })).toBeDisabled();
  });

  it.each([320, 390])('fits the phone viewport at %spx', async (width) => {
    await page.viewport(width, 800);
    await renderPage();
    await page.getByRole('button', { name: /Connection alpha/ }).click();
    await page.getByRole('button', { name: 'Test connection' }).click();
    await expect.element(page.getByRole('region', { name: 'Test result' })).toBeVisible();
    await page.getByRole('button', { name: 'Add connection' }).click();

    expect(document.documentElement.scrollWidth).toBeLessThanOrEqual(window.innerWidth + TOLERANCE_PX);
  });
});
