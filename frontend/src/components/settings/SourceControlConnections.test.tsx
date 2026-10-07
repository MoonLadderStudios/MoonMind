import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { MemoryRouter, useLocation } from 'react-router-dom';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { SourceControlConnections, SOURCE_CONTROL_QUERY_KEY, connectionIdFor } from './SourceControlConnections';

const TOKEN = 'github_pat_' + 'A'.repeat(40);

interface ConnectionFixture {
  id: string;
  displayName: string;
  endpoint: string;
  credentialKind: 'personal_access_token' | 'github_app';
  lifecycle: string;
  policyRevision: number;
  credentialRevision: number;
  allowedOperations: string[];
  account?: string | null;
  installationId?: string | null;
  permittedRepositories?: string[];
  assignments: Array<{
    repository: string;
    providerRepoId: string;
    operations: string[];
    revision: number;
    verified: boolean;
  }>;
}

function connection(overrides: Partial<ConnectionFixture> = {}): ConnectionFixture {
  return {
    id: 'personal-github',
    displayName: 'Personal GitHub',
    endpoint: 'https://github.com',
    credentialKind: 'personal_access_token',
    lifecycle: 'active',
    policyRevision: 1,
    credentialRevision: 1,
    allowedOperations: ['read'],
    assignments: [],
    ...overrides,
  };
}

type Handler = (url: string, init: RequestInit) => Promise<unknown> | unknown;

function json(body: unknown, status = 200) {
  return { ok: status >= 200 && status < 300, status, json: async () => body };
}

/** Route fetches by "METHOD url"; the list endpoint reads `state.items`. */
function stubApi(state: { items: ConnectionFixture[] }, routes: Record<string, Handler> = {}) {
  const fetchMock = vi.fn(async (url: string, init: RequestInit = {}) => {
    const method = (init.method ?? 'GET').toUpperCase();
    const handler = routes[`${method} ${url}`];
    if (handler) return handler(url, init);
    if (method === 'GET' && url === '/api/v1/repository-connections') {
      return json({ items: state.items });
    }
    const detail = url.match(/^\/api\/v1\/repository-connections\/([^/]+)$/);
    if (method === 'GET' && detail) {
      const found = state.items.find((item) => item.id === decodeURIComponent(detail[1]!));
      return found ? json(found) : json({ detail: 'Repository connection not found.' }, 404);
    }
    throw new Error(`unexpected ${method} ${url}`);
  });
  vi.stubGlobal('fetch', fetchMock);
  return fetchMock;
}

function calls(fetchMock: ReturnType<typeof vi.fn>, method: string, url: string) {
  return fetchMock.mock.calls.filter(
    ([callUrl, init]) => callUrl === url && ((init as RequestInit | undefined)?.method ?? 'GET') === method,
  );
}

function bodyOf(call: unknown[] | undefined) {
  return JSON.parse(String((call?.[1] as RequestInit).body));
}

function LocationProbe() {
  const location = useLocation();
  return <output data-testid="location">{`${location.pathname}${location.search}`}</output>;
}

function renderSection({
  initialEntry = '/settings/providers-secrets',
  navigate = vi.fn(),
}: { initialEntry?: string; navigate?: (url: string) => void } = {}) {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const utils = render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={[initialEntry]}>
        <SourceControlConnections canRunProbe navigate={navigate} />
        <LocationProbe />
      </MemoryRouter>
    </QueryClientProvider>,
  );
  return { ...utils, queryClient, navigate };
}

async function fillPatForm(name: string, token = TOKEN) {
  fireEvent.click(await screen.findByRole('button', { name: 'Add token connection' }));
  const form = screen.getByRole('form', { name: 'Add token connection' });
  fireEvent.change(within(form).getByLabelText('Connection name'), { target: { value: name } });
  fireEvent.change(within(form).getByLabelText('GitHub personal access token'), {
    target: { value: token },
  });
  return form;
}

function assertNoTokenExposure(queryClient: QueryClient, container: HTMLElement) {
  const cached = JSON.stringify(queryClient.getQueryData(SOURCE_CONTROL_QUERY_KEY) ?? {});
  expect(cached).not.toContain(TOKEN);
  expect(screen.getByTestId('location').textContent).not.toContain(TOKEN);
  expect(container.textContent).not.toContain(TOKEN);
  for (const input of Array.from(container.querySelectorAll('input'))) {
    expect(input.value).not.toContain(TOKEN);
  }
}

describe('SourceControlConnections', () => {
  afterEach(() => {
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
    window.sessionStorage.clear();
  });

  it('derives a stable connection ID from the name', () => {
    expect(connectionIdFor('  Work GitHub (Acme) ')).toBe('work-github-acme');
    expect(connectionIdFor('***')).toBe('github');
  });

  it('creates the first connection, clears the token, and selects the committed record', async () => {
    const state = { items: [] as ConnectionFixture[] };
    const fetchMock = stubApi(state, {
      'POST /api/v1/repository-connections/pat': (_url, init) => {
        const body = JSON.parse(String(init.body));
        const created = connection({ allowedOperations: body.allowedOperations });
        state.items = [created];
        return json(created, 201);
      },
    });
    const { queryClient, container } = renderSection();
    expect(await screen.findByText(/No connections yet/)).toBeTruthy();

    const form = await fillPatForm('Personal GitHub');
    fireEvent.click(within(form).getByLabelText(/Allow publishing/));
    fireEvent.click(within(form).getByRole('button', { name: 'Save connection' }));

    await screen.findByText(/No repositories assigned\. Workflows cannot use/);
    const posts = calls(fetchMock, 'POST', '/api/v1/repository-connections/pat');
    expect(posts).toHaveLength(1);
    const body = bodyOf(posts[0]);
    expect(body).toMatchObject({
      connectionId: 'personal-github',
      displayName: 'Personal GitHub',
      token: TOKEN,
      allowedOperations: ['read', 'write'],
    });
    expect(body.requestId).toEqual(expect.any(String));
    expect(Object.keys(body).sort()).toEqual(
      ['allowedOperations', 'connectionId', 'displayName', 'requestId', 'token'].sort(),
    );
    assertNoTokenExposure(queryClient, container);
  });

  it('lists two connections and tests only the selected one', async () => {
    const state = {
      items: [
        connection(),
        connection({
          id: 'work-github',
          displayName: 'Work GitHub',
          assignments: [
            { repository: 'acme/widgets', providerRepoId: '7', operations: ['read'], revision: 1, verified: true },
          ],
        }),
      ],
    };
    const fetchMock = stubApi(state, {
      'POST /api/v1/settings/github/token-probe': () =>
        json({ observations: { read: 'verified', write: 'untested' }, repositoryAccessible: true }),
    });
    renderSection();

    const nav = await screen.findByRole('navigation', { name: 'Connections' });
    expect(await within(nav).findByText('Personal GitHub')).toBeTruthy();
    fireEvent.click(within(nav).getByText('Work GitHub'));
    await screen.findByText('acme/widgets');
    fireEvent.click(screen.getByRole('button', { name: 'Test connection' }));

    await screen.findByText('Read access verified');
    const probe = calls(fetchMock, 'POST', '/api/v1/settings/github/token-probe');
    expect(bodyOf(probe[0])).toMatchObject({ connectionId: 'work-github', repo: 'acme/widgets' });
  });

  it('reconciles a lost create acknowledgment without a second POST or duplicate', async () => {
    const state = { items: [] as ConnectionFixture[] };
    const fetchMock = stubApi(state, {
      'POST /api/v1/repository-connections/pat': () => {
        state.items = [connection()];
        throw new TypeError('network connection lost');
      },
    });
    const { queryClient, container } = renderSection();
    const form = await fillPatForm('Personal GitHub');
    fireEvent.click(within(form).getByRole('button', { name: 'Save connection' }));

    await screen.findByRole('form', { name: 'Edit connection' });
    expect(calls(fetchMock, 'POST', '/api/v1/repository-connections/pat')).toHaveLength(1);
    expect(calls(fetchMock, 'GET', '/api/v1/repository-connections/personal-github')).toHaveLength(1);
    const nav = screen.getByRole('navigation', { name: 'Connections' });
    expect(within(nav).getAllByText('Personal GitHub')).toHaveLength(1);
    assertNoTokenExposure(queryClient, container);
  });

  it('keeps the draft and request identity when an uncertain create did not commit', async () => {
    const state = { items: [] as ConnectionFixture[] };
    let attempt = 0;
    const fetchMock = stubApi(state, {
      'POST /api/v1/repository-connections/pat': () => {
        attempt += 1;
        if (attempt === 1) return json({ detail: 'upstream reset' }, 502);
        state.items = [connection()];
        return json(connection(), 201);
      },
    });
    const { queryClient, container } = renderSection();
    const form = await fillPatForm('Personal GitHub');
    fireEvent.click(within(form).getByRole('button', { name: 'Save connection' }));

    expect(await screen.findByText(/was not saved\. Enter the token again/)).toBeTruthy();
    expect((within(form).getByLabelText('Connection name') as HTMLInputElement).value).toBe('Personal GitHub');
    expect((within(form).getByLabelText('GitHub personal access token') as HTMLInputElement).value).toBe('');
    assertNoTokenExposure(queryClient, container);

    fireEvent.change(within(form).getByLabelText('GitHub personal access token'), { target: { value: TOKEN } });
    fireEvent.click(within(form).getByRole('button', { name: 'Save connection' }));
    await screen.findByRole('form', { name: 'Edit connection' });
    const posts = calls(fetchMock, 'POST', '/api/v1/repository-connections/pat');
    expect(posts).toHaveLength(2);
    expect(bodyOf(posts[1]).requestId).toBe(bodyOf(posts[0]).requestId);
  });

  it('clears the token after cancel and after lost admission', async () => {
    const state = { items: [] as ConnectionFixture[] };
    stubApi(state, {
      'POST /api/v1/repository-connections/pat': () => json({ detail: 'auth_required' }, 401),
    });
    const { queryClient, container } = renderSection();
    let form = await fillPatForm('Personal GitHub');
    fireEvent.click(within(form).getByRole('button', { name: 'Save connection' }));
    expect(await screen.findByText(/session ended/)).toBeTruthy();
    expect((within(form).getByLabelText('GitHub personal access token') as HTMLInputElement).value).toBe('');

    fireEvent.change(within(form).getByLabelText('GitHub personal access token'), { target: { value: TOKEN } });
    fireEvent.click(within(form).getByRole('button', { name: 'Cancel' }));
    form = await fillPatForm('Personal GitHub', '');
    expect((within(form).getByLabelText('GitHub personal access token') as HTMLInputElement).value).toBe('');
    assertNoTokenExposure(queryClient, container);
  });

  it('keeps edits and the existing connection on a rotation conflict', async () => {
    const state = { items: [connection()] };
    const fetchMock = stubApi(state, {
      'PATCH /api/v1/repository-connections/personal-github': () => {
        state.items = [connection({ policyRevision: 2 })];
        return json({ detail: 'REPOSITORY_POLICY_CONFLICT: stale policy revision' }, 409);
      },
    });
    const { queryClient, container } = renderSection();
    const form = await screen.findByRole('form', { name: 'Edit connection' });
    fireEvent.change(within(form).getByLabelText('Connection name'), { target: { value: 'Renamed' } });
    fireEvent.change(within(form).getByLabelText(/Replace token/), { target: { value: TOKEN } });
    fireEvent.click(within(form).getByRole('button', { name: 'Save changes' }));

    expect(await screen.findByText(/changed since it was loaded/)).toBeTruthy();
    const patch = calls(fetchMock, 'PATCH', '/api/v1/repository-connections/personal-github');
    expect(patch).toHaveLength(1);
    expect(bodyOf(patch[0])).toMatchObject({ expectedPolicyRevision: 1, displayName: 'Renamed', token: TOKEN });
    expect((within(form).getByLabelText('Connection name') as HTMLInputElement).value).toBe('Renamed');
    expect((within(form).getByLabelText(/Replace token/) as HTMLInputElement).value).toBe('');
    await waitFor(() =>
      expect(calls(fetchMock, 'GET', '/api/v1/repository-connections').length).toBeGreaterThan(1),
    );
    const nav = screen.getByRole('navigation', { name: 'Connections' });
    expect(within(nav).getByText('Personal GitHub')).toBeTruthy();
    assertNoTokenExposure(queryClient, container);
  });

  it('shows a committed rotation after a lost save acknowledgment', async () => {
    const state = { items: [connection()] };
    const fetchMock = stubApi(state, {
      'PATCH /api/v1/repository-connections/personal-github': () => {
        state.items = [connection({ policyRevision: 2, credentialRevision: 2 })];
        throw new TypeError('network connection lost');
      },
    });
    renderSection();
    const form = await screen.findByRole('form', { name: 'Edit connection' });
    fireEvent.change(within(form).getByLabelText(/Replace token/), { target: { value: TOKEN } });
    fireEvent.click(within(form).getByRole('button', { name: 'Save changes' }));

    await waitFor(() =>
      expect(calls(fetchMock, 'GET', '/api/v1/repository-connections/personal-github')).toHaveLength(1),
    );
    expect(screen.queryByText(/was not saved/)).toBeNull();
    expect(calls(fetchMock, 'PATCH', '/api/v1/repository-connections/personal-github')).toHaveLength(1);
  });

  it('preserves assignments and the draft when repository verification is unavailable', async () => {
    const state = {
      items: [
        connection({
          assignments: [
            { repository: 'acme/widgets', providerRepoId: '7', operations: ['read'], revision: 1, verified: true },
          ],
        }),
      ],
    };
    stubApi(state, {
      'POST /api/v1/repository-connections/personal-github/assignments': () =>
        json({ detail: 'GitHub is unavailable; existing assignments are unchanged.' }, 503),
    });
    renderSection();
    const form = await screen.findByRole('form', { name: 'Assign repository' });
    fireEvent.change(within(form).getByLabelText(/Assign repository/), { target: { value: 'acme/gadgets' } });
    fireEvent.click(within(form).getByRole('button', { name: 'Assign' }));

    expect(await screen.findByText(/existing assignments are unchanged/)).toBeTruthy();
    expect(screen.getByText('acme/widgets')).toBeTruthy();
    expect((within(form).getByLabelText(/Assign repository/) as HTMLInputElement).value).toBe('acme/gadgets');
  });

  it('ignores a late save failure for a connection that is no longer selected', async () => {
    const state = { items: [connection(), connection({ id: 'work-github', displayName: 'Work GitHub' })] };
    let rejectSave: (reason: unknown) => void = () => undefined;
    stubApi(state, {
      'PATCH /api/v1/repository-connections/personal-github': () =>
        new Promise((_resolve, reject) => {
          rejectSave = reject;
        }),
    });
    renderSection();
    const form = await screen.findByRole('form', { name: 'Edit connection' });
    fireEvent.change(within(form).getByLabelText('Connection name'), { target: { value: 'Renamed' } });
    fireEvent.click(within(form).getByRole('button', { name: 'Save changes' }));
    fireEvent.click(within(screen.getByRole('navigation', { name: 'Connections' })).getByText('Work GitHub'));
    rejectSave(new TypeError('network connection lost'));

    await waitFor(() => expect(screen.getByRole('form', { name: 'Edit connection' })).toBeTruthy());
    expect(
      (within(screen.getByRole('form', { name: 'Edit connection' })).getByLabelText('Connection name') as HTMLInputElement)
        .value,
    ).toBe('Work GitHub');
    expect(screen.queryByText(/was not saved/)).toBeNull();
  });

  it('starts App setup without internal refs and completes it on return', async () => {
    const state = { items: [] as ConnectionFixture[] };
    const fetchMock = stubApi(state, {
      'POST /api/v1/repository-connections/github-app/begin': () =>
        json({
          setupUrl: 'https://github.com/apps/acme-bot/installations/new?state=s1',
          state: 's1',
          requestId: 'r1',
          connectionId: 'acme-app',
        }),
    });
    const navigate = vi.fn();
    renderSection({ navigate });
    fireEvent.click(await screen.findByRole('button', { name: 'Connect GitHub App' }));
    const form = screen.getByRole('form', { name: 'Connect GitHub App' });
    fireEvent.change(within(form).getByLabelText('Connection name'), { target: { value: 'Acme App' } });
    fireEvent.change(within(form).getByLabelText(/App name/), { target: { value: 'acme-bot' } });
    fireEvent.change(within(form).getByLabelText('App ID'), { target: { value: '123' } });
    fireEvent.click(within(form).getByRole('button', { name: 'Install on GitHub' }));

    await waitFor(() => expect(navigate).toHaveBeenCalledWith('https://github.com/apps/acme-bot/installations/new?state=s1'));
    const begin = bodyOf(calls(fetchMock, 'POST', '/api/v1/repository-connections/github-app/begin')[0]);
    expect(begin).toMatchObject({ appSlug: 'acme-bot', appId: '123', connectionId: 'acme-app', displayName: 'Acme App' });
    expect(begin).not.toHaveProperty('keySecretRef');
    expect(begin).not.toHaveProperty('expectedAppRef');
  });

  it('completes a returning App installation once and removes the callback parameters', async () => {
    window.sessionStorage.setItem(
      'moonmind.sourceControl.pendingApp',
      JSON.stringify({ connectionId: 'acme-app', state: 's1' }),
    );
    const app = connection({
      id: 'acme-app',
      displayName: 'Acme App',
      credentialKind: 'github_app',
      account: 'acme',
      installationId: '42',
    });
    const state = { items: [] as ConnectionFixture[] };
    const fetchMock = stubApi(state, {
      'POST /api/v1/repository-connections/github-app/callback': () => {
        state.items = [app];
        return json({ connectionId: 'acme-app' });
      },
    });
    renderSection({ initialEntry: '/settings/providers-secrets?installation_id=42&setup_action=install&state=s1' });

    expect(await screen.findByText(/Account acme · Installation 42/)).toBeTruthy();
    const callback = calls(fetchMock, 'POST', '/api/v1/repository-connections/github-app/callback');
    expect(callback).toHaveLength(1);
    expect(bodyOf(callback[0])).toEqual({ state: 's1', installationId: '42', connectionId: 'acme-app' });
    await waitFor(() => expect(screen.getByTestId('location').textContent).toBe('/settings/providers-secrets'));
  });
});
