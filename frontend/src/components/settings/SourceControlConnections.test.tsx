import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { Link, MemoryRouter, useLocation } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { SourceControlConnections, SOURCE_CONTROL_QUERY_KEY, connectionIdFor } from './SourceControlConnections';
import { SettingsDraftGuardProvider } from './SettingsDraftGuard';

const TOKEN = 'github_pat_' + 'A'.repeat(40);
const PUBLISH_OPERATIONS = ['read', 'write', 'branch_write', 'review_request'];

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
function stubApi(
  state: { items: ConnectionFixture[]; committedRequests?: Set<string> },
  routes: Record<string, Handler> = {},
) {
  const fetchMock = vi.fn(async (url: string, init: RequestInit = {}) => {
    const method = (init.method ?? 'GET').toUpperCase();
    const handler = routes[`${method} ${url}`];
    if (handler) return handler(url, init);
    if (method === 'GET' && url === '/api/v1/repository-connections') {
      return json({ items: state.items });
    }
    const request = url.match(/^\/api\/v1\/repository-connections\/([^/]+)\/requests\/([^/]+)$/);
    if (method === 'GET' && request) {
      const statusHandler = routes['GET request-status'];
      if (statusHandler) return statusHandler(url, init);
      return json({ committed: state.committedRequests?.has(decodeURIComponent(request[2]!)) ?? false });
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
  onNotice = vi.fn(),
}: {
  initialEntry?: string;
  navigate?: (url: string) => void;
  onNotice?: (notice: { level: 'ok' | 'error'; text: string } | null) => void;
} = {}) {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const utils = render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={[initialEntry]}>
        <SettingsDraftGuardProvider>
          <SourceControlConnections canRunProbe navigate={navigate} onNotice={onNotice} />
          <Link to="/settings/operations">Operations</Link>
          <LocationProbe />
        </SettingsDraftGuardProvider>
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
  let webcrypto: Crypto;

  beforeEach(async () => {
    ({ webcrypto } = await vi.importActual<{ webcrypto: Crypto }>('node:crypto'));
    vi.stubGlobal('crypto', webcrypto);
  });

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
      allowedOperations: PUBLISH_OPERATIONS,
    });
    expect(body.requestId).toEqual(expect.any(String));
    expect(Object.keys(body).sort()).toEqual(
      ['allowedOperations', 'connectionId', 'displayName', 'requestId', 'token'].sort(),
    );
    assertNoTokenExposure(queryClient, container);
    fireEvent.click(screen.getByRole('link', { name: 'Operations' }));
    expect(screen.queryByRole('dialog')).toBeNull();
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

  it.each([
    ['success', () => json({ observations: { read: 'verified', write: 'untested' }, repositoryAccessible: true })],
    ['error', () => json({ detail: 'Personal denied' }, 403)],
  ])('discards a late %s probe response after switching A to B and back to A', async (_kind, respond) => {
    const state = {
      items: [
        connection({
          assignments: [
            { repository: 'me/notes', providerRepoId: '3', operations: ['read'], revision: 1, verified: true },
          ],
        }),
        connection({ id: 'work-github', displayName: 'Work GitHub' }),
      ],
    };
    let resolveProbe: (value: unknown) => void = () => undefined;
    stubApi(state, {
      'POST /api/v1/settings/github/token-probe': () =>
        new Promise((resolve) => {
          resolveProbe = resolve;
        }),
    });
    const onNotice = vi.fn();
    renderSection({ onNotice });

    const nav = await screen.findByRole('navigation', { name: 'Connections' });
    await screen.findByText('me/notes');
    fireEvent.click(screen.getByRole('button', { name: 'Test connection' }));
    await waitFor(() => expect(screen.getByRole('button', { name: /Testing/ })).toBeTruthy());
    fireEvent.click(within(nav).getByText('Work GitHub'));
    await screen.findByRole('form', { name: 'Edit connection' });
    fireEvent.click(within(nav).getByText('Personal GitHub'));
    await screen.findByText('me/notes');

    resolveProbe(respond());
    await new Promise((resolve) => setTimeout(resolve, 0));
    await new Promise((resolve) => setTimeout(resolve, 0));

    expect(onNotice).not.toHaveBeenCalled();
    expect(screen.queryByText('Read access verified')).toBeNull();
    expect(screen.queryByText('Personal denied')).toBeNull();
    expect(screen.getByRole('button', { name: 'Test connection' })).toBeTruthy();
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
    const state = { items: [connection()], committedRequests: new Set<string>() };
    const fetchMock = stubApi(state, {
      'PATCH /api/v1/repository-connections/personal-github': (_url, init) => {
        state.committedRequests.add(JSON.parse(String(init.body)).requestId);
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
    const requestId = bodyOf(calls(fetchMock, 'PATCH', '/api/v1/repository-connections/personal-github')[0]).requestId;
    expect(calls(fetchMock, 'GET', `/api/v1/repository-connections/personal-github/requests/${requestId}`)).toHaveLength(1);
    fireEvent.click(screen.getByRole('link', { name: 'Operations' }));
    expect(screen.queryByRole('dialog')).toBeNull();
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


  it('sends every publication operation when editing and assigning repositories', async () => {
    const state = { items: [connection()] };
    const fetchMock = stubApi(state, {
      'PATCH /api/v1/repository-connections/personal-github': (_url, init) => {
        const body = JSON.parse(String(init.body));
        state.items = [connection({ policyRevision: 2, allowedOperations: body.allowedOperations })];
        return json(state.items[0]);
      },
      'POST /api/v1/repository-connections/personal-github/assignments': (_url, init) => {
        const body = JSON.parse(String(init.body));
        state.items = [connection({
          policyRevision: 3,
          allowedOperations: PUBLISH_OPERATIONS,
          assignments: [{ repository: body.repository, providerRepoId: '7', operations: body.operations, revision: 1, verified: true }],
        })];
        return json(state.items[0]);
      },
    });
    renderSection();
    const edit = await screen.findByRole('form', { name: 'Edit connection' });
    fireEvent.click(within(edit).getByLabelText(/Allow publishing/));
    fireEvent.click(within(edit).getByRole('button', { name: 'Save changes' }));
    await waitFor(() => expect((within(edit).getByRole('button', { name: 'Save changes' }) as HTMLButtonElement).disabled).toBe(true));
    expect(bodyOf(calls(fetchMock, 'PATCH', '/api/v1/repository-connections/personal-github')[0]).allowedOperations).toEqual(PUBLISH_OPERATIONS);

    const assign = screen.getByRole('form', { name: 'Assign repository' });
    fireEvent.change(within(assign).getByLabelText(/Assign repository/), { target: { value: 'acme/widgets' } });
    fireEvent.click(within(assign).getByLabelText('Publish'));
    fireEvent.click(within(assign).getByRole('button', { name: 'Assign' }));
    await screen.findByText('read and publish');
    expect(bodyOf(calls(fetchMock, 'POST', '/api/v1/repository-connections/personal-github/assignments')[0]).operations).toEqual(PUBLISH_OPERATIONS);
    fireEvent.click(screen.getByRole('link', { name: 'Operations' }));
    expect(screen.queryByRole('dialog')).toBeNull();
    expect(screen.getByTestId('location').textContent).toBe('/settings/operations');
  });

  it('does not imply complete publishing from generic write, or change operations while renaming', async () => {
    const state = { items: [connection({ allowedOperations: ['read', 'write'] })] };
    const fetchMock = stubApi(state, {
      'PATCH /api/v1/repository-connections/personal-github': (_url, init) => {
        const body = JSON.parse(String(init.body));
        state.items = [connection({ displayName: body.displayName, allowedOperations: ['read', 'write'], policyRevision: 2 })];
        return json(state.items[0]);
      },
    });
    renderSection();
    const form = await screen.findByRole('form', { name: 'Edit connection' });
    expect((within(form).getByLabelText(/Allow publishing/) as HTMLInputElement).checked).toBe(false);
    fireEvent.change(within(form).getByLabelText('Connection name'), { target: { value: 'Renamed' } });
    fireEvent.click(within(form).getByRole('button', { name: 'Save changes' }));
    await screen.findByRole('heading', { name: 'Renamed' });
    expect(bodyOf(calls(fetchMock, 'PATCH', '/api/v1/repository-connections/personal-github')[0])).not.toHaveProperty('allowedOperations');
  });

  it.each(['not committed', 'status unavailable'])(
    'preserves an uncertain rotation when another writer advances the revision and the request is %s',
    async (outcome) => {
      const state = { items: [connection()] };
      const fetchMock = stubApi(state, {
        'PATCH /api/v1/repository-connections/personal-github': () => {
          state.items = [connection({ displayName: 'Other writer', policyRevision: 2, credentialRevision: 2 })];
          throw new TypeError('lost acknowledgment');
        },
        'GET request-status': () => outcome === 'not committed'
          ? json({ committed: false })
          : json({ detail: 'unavailable' }, 502),
      });
      const onNotice = vi.fn();
      const { queryClient, container } = renderSection({ onNotice });
      const form = await screen.findByRole('form', { name: 'Edit connection' });
      fireEvent.change(within(form).getByLabelText('Connection name'), { target: { value: 'My edit' } });
      fireEvent.change(within(form).getByLabelText(/Replace token/), { target: { value: TOKEN } });
      fireEvent.click(within(form).getByRole('button', { name: 'Save changes' }));
      await screen.findByText(/could not be confirmed.*[Ee]nter the token again/);
      expect((within(form).getByLabelText('Connection name') as HTMLInputElement).value).toBe('My edit');
      expect((within(form).getByRole('button', { name: 'Save changes' }) as HTMLButtonElement).disabled).toBe(true);
      expect(onNotice).not.toHaveBeenCalled();
      assertNoTokenExposure(queryClient, container);

      fireEvent.click(screen.getByRole('link', { name: 'Operations' }));
      expect(screen.getByRole('dialog', { name: 'Unsaved changes' })).toBeTruthy();
      fireEvent.click(screen.getByRole('button', { name: 'Stay' }));
      fireEvent.change(within(form).getByLabelText(/Replace token/), { target: { value: TOKEN } });
      fireEvent.click(within(form).getByRole('button', { name: 'Save changes' }));
      await waitFor(() => expect(calls(fetchMock, 'PATCH', '/api/v1/repository-connections/personal-github')).toHaveLength(2));
      const patches = calls(fetchMock, 'PATCH', '/api/v1/repository-connections/personal-github');
      expect(bodyOf(patches[1]).requestId).toBe(bodyOf(patches[0]).requestId);
      expect(calls(fetchMock, 'GET', `/api/v1/repository-connections/personal-github/requests/${bodyOf(patches[0]).requestId}`).length).toBeGreaterThan(0);
    },
  );


  it.each(['name', 'publishing', 'token'] as const)('uses a new request identity when the uncertain %s intent changes', async (change) => {
    const state = { items: [connection()] };
    const fetchMock = stubApi(state, {
      'PATCH /api/v1/repository-connections/personal-github': () => { throw new TypeError('lost acknowledgment'); },
    });
    renderSection();
    const form = await screen.findByRole('form', { name: 'Edit connection' });
    fireEvent.change(within(form).getByLabelText('Connection name'), { target: { value: 'First edit' } });
    fireEvent.change(within(form).getByLabelText(/Replace token/), { target: { value: TOKEN } });
    fireEvent.click(within(form).getByRole('button', { name: 'Save changes' }));
    await screen.findByText(/could not be confirmed.*[Ee]nter the token again/);
    if (change === 'name') fireEvent.change(within(form).getByLabelText('Connection name'), { target: { value: 'Second edit' } });
    if (change === 'publishing') fireEvent.click(within(form).getByLabelText(/Allow publishing/));
    fireEvent.change(within(form).getByLabelText(/Replace token/), { target: { value: change === 'token' ? TOKEN + 'B' : TOKEN } });
    fireEvent.click(within(form).getByRole('button', { name: 'Save changes' }));
    await waitFor(() => expect(calls(fetchMock, 'PATCH', '/api/v1/repository-connections/personal-github')).toHaveLength(2));
    const patches = calls(fetchMock, 'PATCH', '/api/v1/repository-connections/personal-github');
    expect(bodyOf(patches[1]).requestId).not.toBe(bodyOf(patches[0]).requestId);
  });

  it('retries the same uncertain request after a background revision refresh without changing its precondition', async () => {
    const state = { items: [connection()] };
    const fetchMock = stubApi(state, {
      'PATCH /api/v1/repository-connections/personal-github': () => { throw new TypeError('lost acknowledgment'); },
    });
    const { queryClient } = renderSection();
    const form = await screen.findByRole('form', { name: 'Edit connection' });
    fireEvent.change(within(form).getByLabelText('Connection name'), { target: { value: 'My edit' } });
    fireEvent.change(within(form).getByLabelText(/Replace token/), { target: { value: TOKEN } });
    fireEvent.click(within(form).getByRole('button', { name: 'Save changes' }));
    await screen.findByText(/could not be confirmed.*[Ee]nter the token again/);
    state.items = [connection({ displayName: 'Other writer', policyRevision: 2 })];
    await act(async () => { await queryClient.invalidateQueries({ queryKey: SOURCE_CONTROL_QUERY_KEY }); });
    await screen.findByRole('heading', { name: 'Other writer' });
    fireEvent.change(within(form).getByLabelText(/Replace token/), { target: { value: TOKEN } });
    fireEvent.click(within(form).getByRole('button', { name: 'Save changes' }));
    await waitFor(() => expect(calls(fetchMock, 'PATCH', '/api/v1/repository-connections/personal-github')).toHaveLength(2));
    const patches = calls(fetchMock, 'PATCH', '/api/v1/repository-connections/personal-github');
    expect(bodyOf(patches[1])).toEqual(bodyOf(patches[0]));
  });

  it('prevents edits while a save is pending so its acknowledgment cannot replace newer inputs', async () => {
    const state = { items: [connection()] };
    let finish: (value: unknown) => void = () => undefined;
    stubApi(state, {
      'PATCH /api/v1/repository-connections/personal-github': () => new Promise((resolve) => { finish = resolve; }),
    });
    renderSection();
    const form = await screen.findByRole('form', { name: 'Edit connection' });
    fireEvent.change(within(form).getByLabelText('Connection name'), { target: { value: 'My edit' } });
    fireEvent.click(within(form).getByRole('button', { name: 'Save changes' }));
    await waitFor(() => expect((within(form).getByLabelText('Connection name') as HTMLInputElement).disabled).toBe(true));
    expect((within(form).getByLabelText(/Allow publishing/) as HTMLInputElement).disabled).toBe(true);
    expect((within(form).getByLabelText(/Replace token/) as HTMLInputElement).disabled).toBe(true);
    state.items = [connection({ displayName: 'My edit', policyRevision: 2 })];
    await act(async () => { finish(json(state.items[0])); });
    await screen.findByRole('heading', { name: 'My edit' });
    expect((within(form).getByLabelText('Connection name') as HTMLInputElement).disabled).toBe(false);
  });


  it.each(['edit', 'assignment'] as const)('keeps a newer disable when an earlier %s response arrives late', async (kind) => {
    const initial = connection();
    const state = { items: [initial] };
    let finish: (value: unknown) => void = () => undefined;
    const lateResponse = new Promise((resolve) => { finish = resolve; });
    let loaded = false;
    stubApi(state, {
      'GET /api/v1/repository-connections': () => {
        if (loaded) return new Promise(() => undefined);
        loaded = true;
        return json({ items: state.items });
      },
      'PATCH /api/v1/repository-connections/personal-github': () => lateResponse,
      'POST /api/v1/repository-connections/personal-github/assignments': () => lateResponse,
      'POST /api/v1/repository-connections/personal-github/disable': () => {
        state.items = [connection({ lifecycle: 'disabled', policyRevision: 3, displayName: 'Current disabled name' })];
        return json(state.items[0]);
      },
    });
    const { queryClient } = renderSection();
    const form = await screen.findByRole('form', { name: kind === 'edit' ? 'Edit connection' : 'Assign repository' });
    fireEvent.change(within(form).getByLabelText(kind === 'edit' ? 'Connection name' : /Assign repository/), {
      target: { value: kind === 'edit' ? 'Earlier saved name' : 'acme/widgets' },
    });
    fireEvent.click(within(form).getByRole('button', { name: kind === 'edit' ? 'Save changes' : 'Assign' }));
    await waitFor(() => expect((within(form).getByRole('button', { name: kind === 'edit' ? 'Saving…' : 'Checking…' }) as HTMLButtonElement).disabled).toBe(true));
    fireEvent.click(screen.getByRole('button', { name: 'Disable connection' }));
    await screen.findByRole('heading', { name: 'Current disabled name' });
    await act(async () => {
      finish(json(connection({ policyRevision: 2, displayName: 'Earlier saved name' })));
    });
    expect(queryClient.getQueryData<{ items: ConnectionFixture[] }>(SOURCE_CONTROL_QUERY_KEY)?.items[0]).toMatchObject({ lifecycle: 'disabled', policyRevision: 3 });
    expect(screen.queryByRole('button', { name: 'Disable connection' })).toBeNull();
    expect(screen.getByRole('heading', { name: 'Current disabled name' })).toBeTruthy();
  });

  it('supports token rotation without WebCrypto without replaying unverified token intent', async () => {
    vi.stubGlobal('crypto', { randomUUID: () => webcrypto.randomUUID() });
    const state = { items: [connection()] };
    const fetchMock = stubApi(state, {
      'PATCH /api/v1/repository-connections/personal-github': () => { throw new TypeError('lost acknowledgment'); },
    });
    renderSection();
    const form = await screen.findByRole('form', { name: 'Edit connection' });
    for (let attempt = 0; attempt < 2; attempt += 1) {
      fireEvent.change(within(form).getByLabelText(/Replace token/), { target: { value: TOKEN } });
      fireEvent.click(within(form).getByRole('button', { name: 'Save changes' }));
      await screen.findByText(/could not be confirmed.*[Ee]nter the token again/);
    }
    const patches = calls(fetchMock, 'PATCH', '/api/v1/repository-connections/personal-github');
    expect(patches).toHaveLength(2);
    expect(bodyOf(patches[1]).requestId).not.toBe(bodyOf(patches[0]).requestId);
    expect(bodyOf(patches[1]).expectedPolicyRevision).toBe(bodyOf(patches[0]).expectedPolicyRevision);
  });

  it('keeps token-only uncertain rotation guarded until explicitly canceled', async () => {
    stubApi({ items: [connection()] }, {
      'PATCH /api/v1/repository-connections/personal-github': () => { throw new TypeError('lost acknowledgment'); },
    });
    renderSection();
    const form = await screen.findByRole('form', { name: 'Edit connection' });
    fireEvent.change(within(form).getByLabelText(/Replace token/), { target: { value: TOKEN } });
    fireEvent.click(within(form).getByRole('button', { name: 'Save changes' }));
    await screen.findByText(/could not be confirmed.*[Ee]nter the token again/);
    fireEvent.click(screen.getByRole('link', { name: 'Operations' }));
    expect(screen.getByRole('dialog', { name: 'Unsaved changes' })).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: 'Stay' }));
    fireEvent.click(within(form).getByRole('button', { name: 'Cancel changes' }));
    fireEvent.click(screen.getByRole('link', { name: 'Operations' }));
    expect(screen.queryByRole('dialog')).toBeNull();
    expect(screen.getByTestId('location').textContent).toBe('/settings/operations');
  });

  it.each(['pat', 'app', 'edit', 'assignment'] as const)(
    'protects the %s draft on route departure, preserves Stay, and clears after discard',
    async (kind) => {
      stubApi({ items: [connection()] });
      renderSection();
      await screen.findByRole('form', { name: 'Edit connection' });
      let form: HTMLElement;
      let field: HTMLElement;
      if (kind === 'pat') {
        form = await fillPatForm('Unsaved connection');
        field = within(form).getByLabelText('Connection name');
      } else if (kind === 'app') {
        fireEvent.click(screen.getByRole('button', { name: 'Connect GitHub App' }));
        form = screen.getByRole('form', { name: 'Connect GitHub App' });
        field = within(form).getByLabelText('App ID');
        fireEvent.change(field, { target: { value: '123' } });
      } else if (kind === 'edit') {
        form = screen.getByRole('form', { name: 'Edit connection' });
        field = within(form).getByLabelText('Connection name');
        fireEvent.change(field, { target: { value: 'Unsaved connection' } });
      } else {
        form = screen.getByRole('form', { name: 'Assign repository' });
        field = within(form).getByLabelText(/Assign repository/);
        fireEvent.change(field, { target: { value: 'acme/widgets' } });
      }
      const value = (field as HTMLInputElement).value;
      const unload = new Event('beforeunload', { cancelable: true });
      window.dispatchEvent(unload);
      expect(unload.defaultPrevented).toBe(true);
      fireEvent.click(screen.getByRole('link', { name: 'Operations' }));
      expect(screen.getByRole('dialog', { name: 'Unsaved changes' })).toBeTruthy();
      expect(screen.getByTestId('location').textContent).toBe('/settings/providers-secrets');
      fireEvent.click(screen.getByRole('button', { name: 'Stay' }));
      expect((field as HTMLInputElement).value).toBe(value);
      fireEvent.click(screen.getByRole('link', { name: 'Operations' }));
      fireEvent.click(screen.getByRole('button', { name: 'Discard and leave' }));
      expect(screen.getByTestId('location').textContent).toBe('/settings/operations');
      const cleanUnload = new Event('beforeunload', { cancelable: true });
      window.dispatchEvent(cleanUnload);
      expect(cleanUnload.defaultPrevented).toBe(false);
      expect(Array.from(document.querySelectorAll('input')).some((input) => input.value === TOKEN)).toBe(false);
    },
  );


  it.each(['pat', 'app', 'edit', 'assignment'] as const)('clears only the canceled %s draft', async (kind) => {
    stubApi({ items: [connection()] });
    renderSection();
    await screen.findByRole('form', { name: 'Edit connection' });
    let form: HTMLElement;
    let cancel: string;
    if (kind === 'pat') {
      form = await fillPatForm('Unsaved connection');
      cancel = 'Cancel';
    } else if (kind === 'app') {
      fireEvent.click(screen.getByRole('button', { name: 'Connect GitHub App' }));
      form = screen.getByRole('form', { name: 'Connect GitHub App' });
      fireEvent.change(within(form).getByLabelText('App ID'), { target: { value: '123' } });
      cancel = 'Cancel';
    } else if (kind === 'edit') {
      form = screen.getByRole('form', { name: 'Edit connection' });
      fireEvent.change(within(form).getByLabelText(/Replace token/), { target: { value: TOKEN } });
      cancel = 'Cancel changes';
    } else {
      form = screen.getByRole('form', { name: 'Assign repository' });
      fireEvent.change(within(form).getByLabelText(/Assign repository/), { target: { value: 'acme/widgets' } });
      fireEvent.click(within(form).getByLabelText('Publish'));
      cancel = 'Cancel assignment';
    }
    fireEvent.click(within(form).getByRole('button', { name: cancel }));
    fireEvent.click(screen.getByRole('link', { name: 'Operations' }));
    expect(screen.queryByRole('dialog')).toBeNull();
    expect(screen.getByTestId('location').textContent).toBe('/settings/operations');
  });

  it('keeps an unsaved assignment guarded when the connection edit is saved', async () => {
    const state = { items: [connection()] };
    stubApi(state, {
      'PATCH /api/v1/repository-connections/personal-github': () => {
        state.items = [connection({ displayName: 'Renamed', policyRevision: 2 })];
        return json(state.items[0]);
      },
    });
    renderSection();
    const edit = await screen.findByRole('form', { name: 'Edit connection' });
    const assign = screen.getByRole('form', { name: 'Assign repository' });
    fireEvent.change(within(assign).getByLabelText(/Assign repository/), { target: { value: 'acme/widgets' } });
    fireEvent.change(within(edit).getByLabelText('Connection name'), { target: { value: 'Renamed' } });
    fireEvent.click(within(edit).getByRole('button', { name: 'Save changes' }));
    await screen.findByRole('heading', { name: 'Renamed' });
    fireEvent.click(screen.getByRole('link', { name: 'Operations' }));
    expect(screen.getByRole('dialog', { name: 'Unsaved changes' })).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: 'Stay' }));
    expect((within(assign).getByLabelText(/Assign repository/) as HTMLInputElement).value).toBe('acme/widgets');
    fireEvent.click(within(assign).getByRole('button', { name: 'Cancel assignment' }));
    fireEvent.click(screen.getByRole('link', { name: 'Operations' }));
    expect(screen.queryByRole('dialog')).toBeNull();
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
    const navigate = vi.fn(() => {
      const unload = new Event('beforeunload', { cancelable: true });
      window.dispatchEvent(unload);
      expect(unload.defaultPrevented).toBe(false);
    });
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
