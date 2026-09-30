import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { REPOSITORY_CONNECTIONS_QUERY_KEY, SourceControlSettings } from './SourceControlSettings';

const TOKEN = 'ghp_candidate_token_value_0001';

const PUBLISH_DESCRIPTION = 'Backend says: write access stays unverified until a real publish.';

const PROBE_MODES = [
  {
    mode: 'indexing',
    label: 'Read contents',
    description: 'Reads the repository and branch used to clone and inspect code.',
    requiredPermissions: { Contents: 'read' },
    optionalPermissions: {},
  },
  {
    mode: 'publish',
    label: 'Publish (contents + pull requests)',
    description: PUBLISH_DESCRIPTION,
    requiredPermissions: { Contents: 'write', 'Pull requests': 'write' },
    optionalPermissions: {},
  },
];

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

const ALPHA = connection('alpha', {
  state: 'ready',
  stateSummary: 'Assigned to 1 repository.',
  assignments: [{ repository: 'acme/app', providerRepoId: '9001', operations: ['read'], revision: 1 }],
});
const BETA = connection('beta');

function probeResult(connectionId: string, overrides: Record<string, unknown> = {}) {
  return {
    connectionId,
    policyRevision: 1,
    credentialRevision: 1,
    secretRevision: 1,
    repo: 'acme/app',
    mode: 'publish',
    repositoryAccessible: true,
    defaultBranchAccessible: true,
    pullRequestAccessible: true,
    resolvedBranch: 'develop',
    branchSource: 'remote_default',
    writeVerified: false,
    permissionChecklist: [
      { permission: 'Contents', level: 'write', required: true, status: 'verified_read_access' },
      { permission: 'Pull requests', level: 'write', required: true, status: 'verified_read_access' },
    ],
    diagnostics: [],
    limitations: [],
    ...overrides,
  };
}

function json(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  });
}

type Handler = (url: string, init: RequestInit) => Promise<Response> | Response;

let routes: Array<{ method: string; match: (url: string) => boolean; handle: Handler }>;
let fetchMock: ReturnType<typeof vi.fn>;

function route(method: string, match: string | ((url: string) => boolean), handle: Handler) {
  routes.unshift({
    method,
    match: typeof match === 'string' ? (url) => url === match : match,
    handle,
  });
}

function calls(method: string, prefix: string) {
  return fetchMock.mock.calls.filter(
    ([url, init]) => String(url).startsWith(prefix) && ((init as RequestInit | undefined)?.method ?? 'GET') === method,
  );
}

function requestBody(method: string, url: string, index = 0): Record<string, unknown> {
  const call = calls(method, url)[index];
  if (!call) throw new Error(`No ${method} ${url} request #${index}`);
  return JSON.parse(String((call[1] as RequestInit).body)) as Record<string, unknown>;
}

function outcomeUrl(connectionId: string, requestId: string, action: 'create' | 'rotate') {
  return `/api/v1/repository-connections/${connectionId}/requests/${requestId}?action=${action}`;
}

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((res) => {
    resolve = res;
  });
  return { promise, resolve };
}

function renderSettings(list: unknown = { items: [ALPHA, BETA], probeModes: PROBE_MODES }) {
  route('GET', '/api/v1/repository-connections', () => json(200, list));
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const onNotice = vi.fn();
  const utils = render(
    <QueryClientProvider client={queryClient}>
      <SourceControlSettings onNotice={onNotice} />
    </QueryClientProvider>,
  );
  return { ...utils, queryClient, onNotice };
}

function assertTokenNowhere(container: HTMLElement, queryClient: QueryClient) {
  expect(container.innerHTML).not.toContain(TOKEN);
  expect(JSON.stringify(queryClient.getQueryCache().getAll().map((query) => query.state.data))).not.toContain(TOKEN);
  expect(window.location.href).not.toContain(TOKEN);
}

async function openSetup() {
  fireEvent.click(await screen.findByRole('button', { name: 'Add connection' }));
  fireEvent.change(screen.getByLabelText('Connection name'), { target: { value: 'Gamma Team' } });
  fireEvent.change(screen.getByLabelText('Personal access token'), { target: { value: TOKEN } });
}

async function selectConnection(name: string) {
  const list = await screen.findByRole('list', { name: 'Repository connections' });
  fireEvent.click(within(list).getByRole('button', { name: new RegExp(name) }));
}

beforeEach(() => {
  routes = [];
  fetchMock = vi.fn(async (input: RequestInfo | URL, init: RequestInit = {}) => {
    const url = String(input);
    const method = init.method ?? 'GET';
    const found = routes.find((entry) => entry.method === method && entry.match(url));
    if (!found) throw new Error(`Unexpected ${method} ${url}`);
    return found.handle(url, init);
  });
  vi.stubGlobal('fetch', fetchMock);
});

afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

describe('SourceControlSettings', () => {
  it('lists named connections with actual accounts, assignments, and state', async () => {
    renderSettings();

    const list = await screen.findByRole('list', { name: 'Repository connections' });
    expect(within(list).getByText('Connection alpha')).toBeTruthy();
    expect(within(list).getByText(/Token for alpha-bot · 1 repository/)).toBeTruthy();
    expect(within(list).getByText(/Token for beta-bot · 0 repositories/)).toBeTruthy();
    expect(within(list).getByText('No repositories')).toBeTruthy();

    await selectConnection('Connection beta');
    const detail = screen.getByRole('region', { name: 'Connection Connection beta' });
    expect(within(detail).getByText(/grants no repository access/)).toBeTruthy();
    // Advanced routing and revision details are secondary.
    expect(within(detail).getByText('Advanced details').closest('details')?.open).toBe(false);
  });

  it('tests only the selected connection with backend capability descriptions', async () => {
    renderSettings();
    route('POST', '/api/v1/repository-connections/beta/probe', () => json(200, probeResult('beta')));

    await selectConnection('Connection beta');
    fireEvent.change(screen.getByLabelText('Repository (owner/name)'), { target: { value: 'acme/app' } });
    fireEvent.change(screen.getByLabelText('Check'), { target: { value: 'publish' } });
    expect(screen.getByText(PUBLISH_DESCRIPTION)).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: 'Test connection' }));

    const result = await screen.findByRole('region', { name: 'Test result' });
    expect(requestBody('POST', '/api/v1/repository-connections/beta/probe')).toEqual({ repo: 'acme/app', mode: 'publish' });
    expect(fetchMock.mock.calls.some(([url]) => String(url).includes('token-probe'))).toBe(false);
    expect(within(result).getByText('develop (repository default), readable: yes')).toBeTruthy();
    expect(within(result).getAllByText('Read verified; write not tested')).toHaveLength(2);
    expect(within(result).getByText(/Write permission was not tested/)).toBeTruthy();
  });

  it('reports throttling and unavailability as their own facts', async () => {
    renderSettings();
    route('POST', '/api/v1/repository-connections/alpha/probe', () =>
      json(
        200,
        probeResult('alpha', {
          pullRequestAccessible: null,
          diagnostics: [
            { operation: 'pulls', kind: 'rate_limited', httpStatus: 429, message: 'API rate limit exceeded', retryable: true },
            { operation: 'issues', kind: 'unavailable', message: 'ReadTimeout', retryable: true },
          ],
        }),
      ),
    );

    await selectConnection('Connection alpha');
    fireEvent.click(screen.getByRole('button', { name: 'Test connection' }));

    const diagnostics = await screen.findByRole('list', { name: 'Test diagnostics' });
    expect(within(diagnostics).getByText('Rate limited')).toBeTruthy();
    expect(within(diagnostics).getByText('GitHub unavailable')).toBeTruthy();
    expect(within(diagnostics).queryByText('Permission denied')).toBeNull();
    expect(screen.getByText('Pull requests readable').nextSibling?.textContent).toBe('unknown');
  });

  it('ignores stale probe callbacks across A -> B -> A selection changes', async () => {
    renderSettings();
    const first = deferred<Response>();
    route('POST', '/api/v1/repository-connections/alpha/probe', () => first.promise);

    await selectConnection('Connection alpha');
    fireEvent.click(screen.getByRole('button', { name: 'Test connection' }));
    expect(await screen.findByRole('button', { name: 'Testing…' })).toBeTruthy();

    await selectConnection('Connection beta');
    await selectConnection('Connection alpha');
    // The returned A is a new selection: no inherited loading state.
    expect(screen.getByRole('button', { name: 'Test connection' })).toBeTruthy();

    await act(async () => {
      first.resolve(json(200, probeResult('alpha', { repo: 'acme/app' })));
      await first.promise;
    });
    expect(screen.queryByRole('region', { name: 'Test result' })).toBeNull();
    expect(screen.queryByRole('region', { name: 'Earlier test result' })).toBeNull();
  });

  it('ignores a stale error after the tested inputs change and marks old evidence historical', async () => {
    renderSettings();
    route('POST', '/api/v1/repository-connections/alpha/probe', () => json(200, probeResult('alpha', { mode: 'indexing' })));

    await selectConnection('Connection alpha');
    fireEvent.click(screen.getByRole('button', { name: 'Test connection' }));
    await screen.findByRole('region', { name: 'Test result' });

    const pending = deferred<Response>();
    route('POST', '/api/v1/repository-connections/alpha/probe', () => pending.promise);
    fireEvent.click(screen.getByRole('button', { name: 'Test connection' }));
    fireEvent.change(screen.getByLabelText('Branch (optional)'), { target: { value: 'release' } });

    await act(async () => {
      pending.resolve(json(502, { detail: { kind: 'unavailable', message: 'late failure' } }));
      await pending.promise;
    });
    expect(screen.queryByText(/late failure/)).toBeNull();
    const earlier = screen.getByRole('region', { name: 'Earlier test result' });
    expect(within(earlier).getByText(/the inputs changed since this test ran/)).toBeTruthy();
  });

  it('keeps the token only in the transient input and protected submission', async () => {
    const { container, queryClient, onNotice } = renderSettings();
    const created = connection('gamma-team', { displayName: 'Gamma Team' });
    route('POST', '/api/v1/repository-connections/pat', () => {
      route('GET', '/api/v1/repository-connections', () =>
        json(200, { items: [ALPHA, BETA, created], probeModes: PROBE_MODES }),
      );
      return json(201, created);
    });

    await openSetup();
    fireEvent.click(screen.getByRole('button', { name: 'Save connection' }));

    await waitFor(() => expect(onNotice).toHaveBeenCalledWith({ level: 'ok', text: 'Connection Gamma Team saved.' }));
    expect(requestBody('POST', '/api/v1/repository-connections/pat')).toMatchObject({ connectionId: 'gamma-team', displayName: 'Gamma Team', token: TOKEN, allowedOperations: ['read'] });
    expect(screen.getByRole('region', { name: 'Connection Gamma Team' })).toBeTruthy();
    assertTokenNowhere(container, queryClient);
  });

  it('reconciles a lost save acknowledgment instead of retrying the create', async () => {
    const { container, queryClient, onNotice } = renderSettings({ items: [], probeModes: PROBE_MODES });
    const committed = connection('gamma-team', { displayName: 'Gamma Team' });
    route('POST', '/api/v1/repository-connections/pat', (_url, init) => {
      // The server committed, but the response never arrived.
      const { requestId } = JSON.parse(String(init.body)) as { requestId: string };
      route('GET', '/api/v1/repository-connections', () =>
        json(200, { items: [committed], probeModes: PROBE_MODES }),
      );
      route('GET', outcomeUrl('gamma-team', requestId, 'create'), () =>
        json(200, { requestId, connectionId: 'gamma-team', action: 'create', committed: true, connection: committed }),
      );
      throw new TypeError('network connection lost');
    });

    await openSetup();
    fireEvent.click(screen.getByRole('button', { name: 'Save connection' }));

    await waitFor(() =>
      expect(onNotice).toHaveBeenCalledWith({
        level: 'ok',
        text: 'Connection Gamma Team was saved; MoonMind confirmed it after the response was lost.',
      }),
    );
    expect(calls('POST', '/api/v1/repository-connections/pat')).toHaveLength(1);
    const { requestId } = requestBody('POST', '/api/v1/repository-connections/pat');
    expect(calls('GET', outcomeUrl('gamma-team', String(requestId), 'create'))).toHaveLength(1);
    expect(screen.getByRole('region', { name: 'Connection Gamma Team' })).toBeTruthy();
    expect(screen.queryByRole('button', { name: 'Save connection' })).toBeNull();
    assertTokenNowhere(container, queryClient);
  });

  it('does not claim a listed connection as a lost create that never reached MoonMind', async () => {
    const existing = connection('gamma-team', { displayName: 'Gamma Team' });
    const { onNotice } = renderSettings({ items: [ALPHA, existing], probeModes: PROBE_MODES });
    route('POST', '/api/v1/repository-connections/pat', (_url, init) => {
      const { requestId } = JSON.parse(String(init.body)) as { requestId: string };
      route('GET', outcomeUrl('gamma-team', requestId, 'create'), () =>
        json(200, { requestId, connectionId: 'gamma-team', action: 'create', committed: false, connection: null }),
      );
      throw new TypeError('network connection lost');
    });

    await screen.findByRole('list', { name: 'Repository connections' });
    await openSetup();
    fireEvent.click(screen.getByRole('button', { name: 'Save connection' }));

    const alert = await screen.findByRole('alert');
    expect(alert.textContent).toMatch(/Not confirmed: MoonMind has no record of this save/);
    expect(onNotice).not.toHaveBeenCalledWith(expect.objectContaining({ level: 'ok' }));
    expect(screen.queryByRole('region', { name: 'Connection Gamma Team' })).toBeNull();
    expect((screen.getByLabelText('Connection name') as HTMLInputElement).value).toBe('Gamma Team');
    expect(calls('POST', '/api/v1/repository-connections/pat')).toHaveLength(1);
  });

  it('keeps the draft and request identity when a save is unconfirmed', async () => {
    renderSettings({ items: [], probeModes: PROBE_MODES });
    route('POST', '/api/v1/repository-connections/pat', (_url, init) => {
      const { requestId } = JSON.parse(String(init.body)) as { requestId: string };
      route('GET', outcomeUrl('gamma-team', requestId, 'create'), () =>
        json(200, { requestId, connectionId: 'gamma-team', action: 'create', committed: false, connection: null }),
      );
      return json(504, '<html>gateway timeout</html>');
    });

    await openSetup();
    fireEvent.click(screen.getByRole('button', { name: 'Save connection' }));

    const alert = await screen.findByRole('alert');
    expect(alert.textContent).toMatch(/no record of this save/);
    expect((screen.getByLabelText('Connection name') as HTMLInputElement).value).toBe('Gamma Team');
    expect((screen.getByLabelText('Personal access token') as HTMLInputElement).value).toBe('');
    expect(calls('POST', '/api/v1/repository-connections/pat')).toHaveLength(1);

    fireEvent.change(screen.getByLabelText('Personal access token'), { target: { value: TOKEN } });
    fireEvent.click(screen.getByRole('button', { name: 'Save connection' }));
    await waitFor(() => expect(calls('POST', '/api/v1/repository-connections/pat')).toHaveLength(2));
    expect(requestBody('POST', '/api/v1/repository-connections/pat', 1).requestId).toBe(
      requestBody('POST', '/api/v1/repository-connections/pat', 0).requestId,
    );
  });

  it('preserves the non-sensitive draft after failed validation without reconciling', async () => {
    const { container, queryClient } = renderSettings({ items: [], probeModes: PROBE_MODES });
    route('POST', '/api/v1/repository-connections/pat', () =>
      json(422, { detail: { kind: 'authentication', message: 'GitHub rejected this token. Nothing was saved.' } }),
    );

    await openSetup();
    fireEvent.click(screen.getByRole('checkbox', { name: /Allow publishing/ }));
    const getsBefore = calls('GET', '/api/v1/repository-connections').length;
    fireEvent.click(screen.getByRole('button', { name: 'Save connection' }));

    const alert = await screen.findByRole('alert');
    expect(alert.textContent).toContain('Authentication failed');
    expect((screen.getByLabelText('Connection name') as HTMLInputElement).value).toBe('Gamma Team');
    expect((screen.getByRole('checkbox', { name: /Allow publishing/ }) as HTMLInputElement).checked).toBe(true);
    expect((screen.getByLabelText('Personal access token') as HTMLInputElement).value).toBe('');
    expect(calls('GET', '/api/v1/repository-connections')).toHaveLength(getsBefore);
    assertTokenNowhere(container, queryClient);
  });

  it('does not suggest an automatic ID suffix on conflict', async () => {
    renderSettings({ items: [], probeModes: PROBE_MODES });
    route('POST', '/api/v1/repository-connections/pat', () =>
      json(409, { detail: { kind: 'conflict', message: "Connection ID 'gamma-team' is already in use. Choose another ID." } }),
    );

    await openSetup();
    fireEvent.click(screen.getByRole('button', { name: 'Save connection' }));

    expect((await screen.findByRole('alert')).textContent).toContain('Conflict');
    expect((screen.getByLabelText('Connection ID') as HTMLInputElement).value).toBe('gamma-team');
    expect(calls('POST', '/api/v1/repository-connections/pat')).toHaveLength(1);
  });

  it('clears the token and draft on cancel', async () => {
    renderSettings({ items: [], probeModes: PROBE_MODES });

    await openSetup();
    fireEvent.click(screen.getByRole('button', { name: 'Cancel' }));
    fireEvent.click(screen.getByRole('button', { name: 'Add connection' }));

    expect((screen.getByLabelText('Personal access token') as HTMLInputElement).value).toBe('');
    expect((screen.getByLabelText('Connection name') as HTMLInputElement).value).toBe('');
  });

  it('surfaces a rotation conflict and clears the replacement token', async () => {
    const { container, queryClient } = renderSettings();
    route('POST', '/api/v1/repository-connections/alpha/rotate', () =>
      json(409, { detail: { kind: 'conflict', message: 'The stored token changed since this form loaded.' } }),
    );

    await selectConnection('Connection alpha');
    fireEvent.change(screen.getByLabelText('Replacement token'), { target: { value: TOKEN } });
    fireEvent.click(screen.getByRole('button', { name: 'Rotate token' }));

    expect((await screen.findByRole('alert')).textContent).toMatch(/Conflict: The stored token changed/);
    expect(requestBody('POST', '/api/v1/repository-connections/alpha/rotate').expectedSecretRevision).toBe(1);
    expect((screen.getByLabelText('Replacement token') as HTMLInputElement).value).toBe('');
    assertTokenNowhere(container, queryClient);
  });

  it('confirms a rotation with a lost response only through its own request', async () => {
    const { container, queryClient, onNotice } = renderSettings();
    const rotated = { ...ALPHA, secretRevision: 2 };
    route('POST', '/api/v1/repository-connections/alpha/rotate', (_url, init) => {
      const { requestId } = JSON.parse(String(init.body)) as { requestId: string };
      route('GET', outcomeUrl('alpha', requestId, 'rotate'), () =>
        json(200, { requestId, connectionId: 'alpha', action: 'rotate', committed: true, connection: rotated }),
      );
      throw new TypeError('network connection lost');
    });

    await selectConnection('Connection alpha');
    fireEvent.change(screen.getByLabelText('Replacement token'), { target: { value: TOKEN } });
    fireEvent.click(screen.getByRole('button', { name: 'Rotate token' }));

    await waitFor(() =>
      expect(onNotice).toHaveBeenCalledWith({
        level: 'ok',
        text: 'Token rotated for Connection alpha; MoonMind confirmed it after the response was lost.',
      }),
    );
    expect(calls('POST', '/api/v1/repository-connections/alpha/rotate')).toHaveLength(1);
    const { requestId } = requestBody('POST', '/api/v1/repository-connections/alpha/rotate');
    expect(calls('GET', outcomeUrl('alpha', String(requestId), 'rotate'))).toHaveLength(1);
    assertTokenNowhere(container, queryClient);
  });

  it('does not report a revision advanced by another request as this rotation', async () => {
    const { container, queryClient, onNotice } = renderSettings();
    // Another request rotated the token meanwhile; this one never committed.
    const elsewhere = { ...ALPHA, secretRevision: 2 };
    route('POST', '/api/v1/repository-connections/alpha/rotate', (_url, init) => {
      const { requestId } = JSON.parse(String(init.body)) as { requestId: string };
      route('GET', '/api/v1/repository-connections', () =>
        json(200, { items: [elsewhere, BETA], probeModes: PROBE_MODES }),
      );
      route('GET', outcomeUrl('alpha', requestId, 'rotate'), () =>
        json(200, { requestId, connectionId: 'alpha', action: 'rotate', committed: false, connection: elsewhere }),
      );
      throw new TypeError('network connection lost');
    });

    await selectConnection('Connection alpha');
    fireEvent.change(screen.getByLabelText('Replacement token'), { target: { value: TOKEN } });
    fireEvent.click(screen.getByRole('button', { name: 'Rotate token' }));

    const alert = await screen.findByRole('alert');
    expect(alert.textContent).toMatch(/did not confirm the rotation/);
    expect(onNotice).not.toHaveBeenCalledWith(expect.objectContaining({ level: 'ok' }));
    expect((screen.getByLabelText('Replacement token') as HTMLInputElement).value).toBe('');
    assertTokenNowhere(container, queryClient);
  });

  it('clears typed tokens in setup and rotation when admission is lost, keeping the draft', async () => {
    const { container, queryClient } = renderSettings();
    await selectConnection('Connection alpha');
    fireEvent.change(screen.getByLabelText('Replacement token'), { target: { value: TOKEN } });
    await openSetup();
    fireEvent.click(screen.getByRole('checkbox', { name: /Allow publishing/ }));
    expect((screen.getByLabelText('Personal access token') as HTMLInputElement).value).toBe(TOKEN);
    expect((screen.getByLabelText('Replacement token') as HTMLInputElement).value).toBe(TOKEN);

    route('GET', '/api/v1/repository-connections', () => json(401, { detail: 'Not authenticated' }));
    await act(async () => {
      await queryClient.refetchQueries({ queryKey: REPOSITORY_CONNECTIONS_QUERY_KEY }).catch(() => undefined);
    });

    await waitFor(() =>
      expect((screen.getByLabelText('Personal access token') as HTMLInputElement).value).toBe(''),
    );
    expect((screen.getByLabelText('Replacement token') as HTMLInputElement).value).toBe('');
    expect((await screen.findByRole('status')).textContent).toMatch(/Your session ended/);
    expect((screen.getByLabelText('Connection name') as HTMLInputElement).value).toBe('Gamma Team');
    expect((screen.getByRole('checkbox', { name: /Allow publishing/ }) as HTMLInputElement).checked).toBe(true);
    expect(calls('POST', '/api/v1/repository-connections')).toHaveLength(0);
    assertTokenNowhere(container, queryClient);
  });

  it('keeps saved configuration visible and editable when a refresh fails', async () => {
    const { queryClient } = renderSettings();
    await screen.findByRole('list', { name: 'Repository connections' });
    route('GET', '/api/v1/repository-connections', () => json(503, { detail: { kind: 'unavailable', message: 'database restarting' } }));

    await act(async () => {
      await queryClient.refetchQueries({ queryKey: REPOSITORY_CONNECTIONS_QUERY_KEY }).catch(() => undefined);
    });

    expect((await screen.findByRole('status')).textContent).toMatch(/could not be refreshed.*saved connections are unchanged/);
    expect(screen.getByText('Connection alpha')).toBeTruthy();
    await selectConnection('Connection alpha');
    expect(screen.getByLabelText('Assign repository (owner/name)')).not.toHaveProperty('disabled', true);
    expect(screen.getByRole('button', { name: 'Add connection' })).toBeTruthy();
  });

  it('reports partial discovery without touching existing assignments', async () => {
    renderSettings();
    route('GET', '/api/v1/repository-connections/alpha/repositories', () =>
      json(200, {
        connectionId: 'alpha',
        repositories: [{ providerRepoId: '7001', fullName: 'acme/other', defaultBranch: 'trunk', private: false }],
        complete: false,
        pagesRead: 1,
        diagnostics: [{ operation: 'discovery', kind: 'unavailable', httpStatus: 502, message: 'Server Error', retryable: true }],
      }),
    );

    await selectConnection('Connection alpha');
    fireEvent.click(screen.getByRole('button', { name: 'Browse repositories' }));

    const partial = await screen.findByText(/Partial list: showing 1 repositories from 1 page/);
    expect(partial.textContent).toMatch(/GitHub unavailable: Server Error/);
    expect(screen.getByText(/default branch trunk/)).toBeTruthy();
    const assigned = screen.getByRole('region', { name: 'Assigned repositories' });
    expect(within(assigned).getByText('acme/app')).toBeTruthy();
  });

  it('blocks removal while repositories are assigned', async () => {
    renderSettings();
    await selectConnection('Connection alpha');
    const remove = screen.getByRole('button', { name: 'Remove connection' });
    expect((remove as HTMLButtonElement).disabled).toBe(true);
    expect(screen.getByText(/Remove its repository assignments before removing/)).toBeTruthy();
  });
});
