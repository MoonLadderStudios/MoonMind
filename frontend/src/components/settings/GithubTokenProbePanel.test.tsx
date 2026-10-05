import {
  act,
  fireEvent,
  render,
  screen,
  waitFor,
} from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { GithubTokenProbePanel } from './GithubTokenProbePanel';

const ROOT = '/api/v1/repository-connections';
const first = {
  id: 'connection-a',
  displayName: 'First account',
  credentialKind: 'pat',
  account: 'alice',
  installation: null,
  repositories: ['owner/first'],
  allowedOperations: ['read', 'write'],
  lifecycle: 'active',
  policyRevision: 1,
  credentialRevision: 1,
};
const second = {
  ...first,
  id: 'connection-b',
  displayName: 'Second account',
  account: 'bob',
  repositories: ['owner/second'],
};
function response(body: unknown, status = 200): Response {
  return {
    ok: status < 400,
    status,
    statusText: String(status),
    json: async () => body,
    text: async () => JSON.stringify(body),
  } as Response;
}
function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((yes) => {
    resolve = yes;
  });
  return { promise, resolve };
}
function stubApi(items: unknown[] = []) {
  const mock = vi.fn().mockImplementation((url: string) => {
    if (url === ROOT) return Promise.resolve(response({ items }));
    if (url === `${ROOT}/setup-options`)
      return Promise.resolve(response({ apps: [] }));
    return Promise.resolve(response({}, 404));
  });
  vi.stubGlobal('fetch', mock);
  return mock;
}
function panel() {
  const onNotice = vi.fn();
  return {
    ...render(
      <GithubTokenProbePanel
        canReadConnections
        canWriteConnections
        canRotateCredentials
        onNotice={onNotice}
      />,
    ),
    onNotice,
  };
}
async function createDraft() {
  fireEvent.change(await screen.findByLabelText('Connection name'), {
    target: { value: 'My account' },
  });
  fireEvent.change(
    screen.getByLabelText('Repositories (one owner/repo per line)'),
    { target: { value: 'owner/first' } },
  );
  fireEvent.change(screen.getByLabelText('Personal access token'), {
    target: { value: 'github_pat_4019_transient_sentinel' },
  });
}

describe('MoonMind#4019 Source Control Settings', () => {
  beforeEach(() => {
    sessionStorage.clear();
  });
  afterEach(() => {
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
  });
  it('creates the first connection with one protected credential transfer and displays the saved account', async () => {
    const mock = stubApi();
    panel();
    await createDraft();
    mock.mockImplementation((url, init) => {
      if (url === ROOT && init?.method === 'POST') {
        const body = JSON.parse(init.body);
        return Promise.resolve(
          response(
            { ...first, id: body.connectionId, displayName: body.displayName },
            201,
          ),
        );
      }
      return Promise.resolve(response({}, 404));
    });
    fireEvent.click(screen.getByRole('button', { name: 'Create connection' }));
    expect(
      (screen.getByLabelText('Personal access token') as HTMLInputElement)
        .value,
    ).toBe('');
    await screen.findByText('alice');
    const posts = mock.mock.calls.filter(([, init]) => init?.method === 'POST');
    expect(posts).toHaveLength(1);
    expect(JSON.parse(posts[0]![1].body)).toMatchObject({
      displayName: 'My account',
      repositories: ['owner/first'],
      plaintext: 'github_pat_4019_transient_sentinel',
    });
    expect(document.body.textContent).not.toContain('sentinel');
    expect(window.location.href).not.toContain('sentinel');
    expect(JSON.stringify(sessionStorage)).not.toContain('sentinel');
  });
  it('selects two connections without a global-token fallback when the selected probe contract is unavailable', async () => {
    const mock = stubApi([first, second]);
    panel();
    await screen.findByLabelText('Repository connection');
    fireEvent.change(screen.getByLabelText('Repository connection'), {
      target: { value: second.id },
    });
    expect(
      (screen.getByLabelText('Connection name') as HTMLInputElement).value,
    ).toBe(second.displayName);
    expect(
      (
        screen.getByLabelText(
          'Repositories (one owner/repo per line)',
        ) as HTMLTextAreaElement
      ).value,
    ).toBe('owner/second');
    expect(
      (
        screen.getByRole('button', {
          name: 'Test Connection',
        }) as HTMLButtonElement
      ).disabled,
    ).toBe(true);
    expect(mock.mock.calls.some(([url]) => url.includes('token-probe'))).toBe(
      false,
    );
  });
  it('reconciles a lost save acknowledgment with the same request without another POST', async () => {
    const mock = stubApi();
    panel();
    await createDraft();
    let operation!: { connectionId: string; requestId: string };
    mock.mockImplementation((url, init) => {
      if (init?.method === 'POST') {
        operation = JSON.parse(init.body);
        return Promise.reject(new TypeError('Lost acknowledgment'));
      }
      if (url.includes('/operations/'))
        return Promise.resolve(
          response({
            committed: true,
            requestId: operation.requestId,
            connection: { ...first, id: operation.connectionId },
          }),
        );
      return Promise.resolve(response({}, 404));
    });
    fireEvent.click(screen.getByRole('button', { name: 'Create connection' }));
    await screen.findByText('alice');
    expect(
      mock.mock.calls.filter(([, init]) => init?.method === 'POST'),
    ).toHaveLength(1);
    expect(
      mock.mock.calls.some(
        ([url]) =>
          url ===
          `${ROOT}/${encodeURIComponent(operation.connectionId)}/operations/${encodeURIComponent(operation.requestId)}`,
      ),
    ).toBe(true);
    expect(
      screen.queryByRole('button', { name: 'Create connection' }),
    ).toBeNull();
    expect(
      (
        screen.getByLabelText(
          'Replacement personal access token',
        ) as HTMLInputElement
      ).value,
    ).toBe('');
  });
  it('retains a safe draft and prevents a duplicate create until an uncertain operation is reconciled, including refresh', async () => {
    const mock = stubApi();
    const { unmount } = panel();
    await createDraft();
    mock.mockImplementation((_url, init) =>
      init?.method === 'POST'
        ? Promise.reject(new TypeError('outage'))
        : Promise.resolve(response({ committed: false })),
    );
    fireEvent.click(screen.getByRole('button', { name: 'Create connection' }));
    await screen.findByRole('button', { name: 'Check saved result' });
    expect(
      (screen.getByLabelText('Connection name') as HTMLInputElement).value,
    ).toBe('My account');
    expect(
      (
        screen.getByRole('button', {
          name: 'Create connection',
        }) as HTMLButtonElement
      ).disabled,
    ).toBe(true);
    expect(
      (screen.getByLabelText('Personal access token') as HTMLInputElement)
        .value,
    ).toBe('');
    const pending = sessionStorage.getItem(
      'moonmind.repository-connection.pending',
    );
    expect(pending).toBeTruthy();
    expect(pending).not.toContain('sentinel');
    unmount();
    stubApi();
    panel();
    await screen.findByRole('button', { name: 'Check saved result' });
    expect(
      (
        screen.getByRole('button', {
          name: 'Create connection',
        }) as HTMLButtonElement
      ).disabled,
    ).toBe(true);
  });
  it('ignores an A-to-B-to-A stale save error, notice and loading completion', async () => {
    const mock = stubApi([first, second]);
    const { onNotice } = panel();
    await screen.findByLabelText('Repository connection');
    fireEvent.change(screen.getByLabelText('Connection name'), {
      target: { value: 'Draft A' },
    });
    const old = deferred<Response>();
    mock.mockReturnValue(old.promise);
    fireEvent.click(screen.getByRole('button', { name: 'Save connection' }));
    fireEvent.change(screen.getByLabelText('Repository connection'), {
      target: { value: second.id },
    });
    fireEvent.change(screen.getByLabelText('Repository connection'), {
      target: { value: first.id },
    });
    onNotice.mockClear();
    await act(async () => {
      old.resolve(response({ detail: 'old error' }, 409));
    });
    expect(screen.queryByText(/old error/)).toBeNull();
    expect(onNotice).not.toHaveBeenCalled();
    expect(
      (screen.getByLabelText('Connection name') as HTMLInputElement).value,
    ).toBe(first.displayName);
  });
  it('retains draft, assignments and original revisions on rotation conflict', async () => {
    const mock = stubApi([first]);
    panel();
    await screen.findByLabelText('Repository connection');
    fireEvent.change(screen.getByLabelText('Connection name'), {
      target: { value: 'Safe draft' },
    });
    fireEvent.change(
      screen.getByLabelText('Replacement personal access token'),
      { target: { value: 'github_pat_4019_conflict_sentinel' } },
    );
    mock.mockResolvedValue(response({ detail: 'stale revision' }, 409));
    fireEvent.click(screen.getByRole('button', { name: 'Save connection' }));
    await screen.findByRole('alert');
    expect(
      (screen.getByLabelText('Connection name') as HTMLInputElement).value,
    ).toBe('Safe draft');
    expect(
      (
        screen.getByLabelText(
          'Repositories (one owner/repo per line)',
        ) as HTMLTextAreaElement
      ).value,
    ).toBe('owner/first');
    expect(
      (
        screen.getByLabelText(
          'Replacement personal access token',
        ) as HTMLInputElement
      ).value,
    ).toBe('');
    const patch = JSON.parse(
      mock.mock.calls.find(([, init]) => init?.method === 'PATCH')![1].body,
    );
    expect(patch).toMatchObject({
      expectedPolicyRevision: 1,
      expectedCredentialRevision: 1,
    });
  });
  it('ignores an older refresh and retains the draft when a new revision arrives', async () => {
    const mock = stubApi([first]);
    panel();
    await screen.findByLabelText('Repository connection');
    fireEvent.change(screen.getByLabelText('Connection name'), {
      target: { value: 'Current draft' },
    });
    const old = deferred<Response>();
    mock.mockImplementation((url) =>
      url === ROOT ? old.promise : Promise.resolve(response({ apps: [] })),
    );
    fireEvent.click(
      screen.getByRole('button', { name: 'Refresh connections' }),
    );
    mock.mockImplementation((url) =>
      Promise.resolve(
        response(
          url === ROOT
            ? { items: [{ ...first, policyRevision: 3 }] }
            : { apps: [] },
        ),
      ),
    );
    fireEvent.click(
      screen.getByRole('button', { name: 'Refresh connections' }),
    );
    await waitFor(() => expect(screen.getByText('Revision 3')).toBeTruthy());
    await act(async () => {
      old.resolve(response({ items: [{ ...first, policyRevision: 2 }] }));
    });
    expect(screen.getByText('Revision 3')).toBeTruthy();
    expect(
      (screen.getByLabelText('Connection name') as HTMLInputElement).value,
    ).toBe('Current draft');
  });
  it('clears credentials on cancellation and lost admission while retaining the safe draft', async () => {
    stubApi();
    const { rerender } = panel();
    await createDraft();
    rerender(
      <GithubTokenProbePanel
        canReadConnections
        canWriteConnections={false}
        canRotateCredentials={false}
      />,
    );
    expect(
      (screen.getByLabelText('Personal access token') as HTMLInputElement)
        .value,
    ).toBe('');
    expect(
      (screen.getByLabelText('Connection name') as HTMLInputElement).value,
    ).toBe('My account');
    rerender(
      <GithubTokenProbePanel
        canReadConnections
        canWriteConnections
        canRotateCredentials
      />,
    );
    fireEvent.change(screen.getByLabelText('Personal access token'), {
      target: { value: 'temporary-token' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Cancel changes' }));
    expect(
      (screen.getByLabelText('Personal access token') as HTMLInputElement)
        .value,
    ).toBe('');
  });
  it('does not let a delayed save downgrade a refreshed revision or replace newer loading', async () => {
    const mock = stubApi([first]);
    panel();
    await screen.findByLabelText('Repository connection');
    fireEvent.change(screen.getByLabelText('Connection name'), {
      target: { value: 'Saved draft' },
    });
    const saved = deferred<Response>();
    mock.mockImplementation((url, init) =>
      init?.method === 'PATCH'
        ? saved.promise
        : Promise.resolve(
            response(
              url === ROOT
                ? { items: [{ ...first, policyRevision: 3 }] }
                : { apps: [] },
            ),
          ),
    );
    fireEvent.click(screen.getByRole('button', { name: 'Save connection' }));
    fireEvent.click(
      screen.getByRole('button', { name: 'Refresh connections' }),
    );
    await screen.findByText('Revision 3');
    await act(async () => {
      saved.resolve(
        response({ ...first, displayName: 'Saved draft', policyRevision: 2 }),
      );
    });
    expect(screen.getByText('Revision 3')).toBeTruthy();
    expect(
      (screen.getByLabelText('Connection name') as HTMLInputElement).value,
    ).toBe('Saved draft');
  });
  it('preserves the draft and clears credentials when refresh loses admission to the selected connection', async () => {
    const mock = stubApi([first]);
    panel();
    await screen.findByLabelText('Repository connection');
    fireEvent.change(screen.getByLabelText('Connection name'), {
      target: { value: 'Retained draft' },
    });
    fireEvent.change(
      screen.getByLabelText('Replacement personal access token'),
      { target: { value: 'github_pat_4019_lost_admission_sentinel' } },
    );
    mock.mockImplementation((url) =>
      Promise.resolve(response(url === ROOT ? { items: [] } : { apps: [] })),
    );
    fireEvent.click(
      screen.getByRole('button', { name: 'Refresh connections' }),
    );
    await screen.findByText(/Selected connection is no longer available/);
    expect(
      (screen.getByLabelText('Connection name') as HTMLInputElement).value,
    ).toBe('Retained draft');
    expect(
      (
        screen.getByLabelText(
          'Replacement personal access token',
        ) as HTMLInputElement
      ).value,
    ).toBe('');
    expect(
      (
        screen.getByRole('button', {
          name: 'Save connection',
        }) as HTMLButtonElement
      ).disabled,
    ).toBe(true);
    expect(mock.mock.calls.some(([, init]) => init?.method === 'POST')).toBe(
      false,
    );
  });
  it('retains partial-discovery assignments and allows a name-only edit after an advisory outage', async () => {
    const mock = stubApi([first]);
    panel();
    await screen.findByLabelText('Repository connection');
    fireEvent.change(
      screen.getByLabelText('Repositories (one owner/repo per line)'),
      { target: { value: 'owner/first\nowner/new' } },
    );
    mock.mockResolvedValue(
      response(
        {
          detail: {
            code: 'repository_verification_unavailable',
            mutationCommitted: false,
          },
        },
        503,
      ),
    );
    fireEvent.click(screen.getByRole('button', { name: 'Save connection' }));
    await screen.findByRole('alert');
    expect(
      (
        screen.getByLabelText(
          'Repositories (one owner/repo per line)',
        ) as HTMLTextAreaElement
      ).value,
    ).toBe('owner/first\nowner/new');
    expect(
      (
        screen.getByRole('button', {
          name: 'Save connection',
        }) as HTMLButtonElement
      ).disabled,
    ).toBe(false);
    expect(
      sessionStorage.getItem('moonmind.repository-connection.pending'),
    ).toBeNull();
    fireEvent.change(
      screen.getByLabelText('Repositories (one owner/repo per line)'),
      { target: { value: 'owner/first' } },
    );
    fireEvent.change(screen.getByLabelText('Connection name'), {
      target: { value: 'Name only' },
    });
    mock.mockResolvedValue(
      response({ ...first, displayName: 'Name only', policyRevision: 2 }),
    );
    fireEvent.click(screen.getByRole('button', { name: 'Save connection' }));
    await screen.findByText('Revision 2');
    const request = JSON.parse(
      mock.mock.calls.filter(([, init]) => init?.method === 'PATCH').at(-1)![1]
        .body,
    );
    expect(request.displayName).toBe('Name only');
    expect(request.repositories).toBeUndefined();
  });
});
