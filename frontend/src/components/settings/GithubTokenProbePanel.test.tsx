import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import {
  GithubTokenProbePanel,
  type GithubTokenProbePanelProps,
  type ProbeConnection,
} from './GithubTokenProbePanel';

const CONNECTION_A: ProbeConnection = {
  id: 'personal-github',
  displayName: 'Personal GitHub',
  policyRevision: 1,
  credentialRevision: 1,
  endpoint: 'https://github.com',
  credentialKind: 'personal_access_token',
  allowedOperations: ['read'],
  assignments: [{ repository: 'owner/repo', providerRepoId: '1', operations: ['read'], revision: 1, verified: true }],
  lifecycle: 'active',
};

const CONNECTION_B: ProbeConnection = {
  id: 'work-github',
  displayName: 'Work GitHub',
  policyRevision: 3,
  credentialRevision: 2,
  endpoint: 'https://github.com',
  credentialKind: 'personal_access_token',
  allowedOperations: ['read'],
  assignments: [],
  lifecycle: 'active',
};

function renderPanel(props: Partial<GithubTokenProbePanelProps> = {}) {
  const onNotice = props.onNotice ?? vi.fn();
  const element = (connection: ProbeConnection) => (
    <GithubTokenProbePanel
      connection={connection}
      canRunProbe={props.canRunProbe ?? true}
      onNotice={onNotice}
      initialRepo={props.initialRepo ?? 'owner/repo'}
    />
  );
  const utils = render(element(props.connection ?? CONNECTION_A));
  return {
    ...utils,
    onNotice,
    select: (connection: ProbeConnection) => utils.rerender(element(connection)),
  };
}

const READ_RESPONSE = {
  connectionId: 'personal-github',
  repo: 'owner/repo',
  credentialSource: { sourceKind: 'secret_ref_env', sourceName: 'personal-github', resolved: true },
  repositoryAccessible: true,
  defaultBranchAccessible: true,
  pullRequestAccessible: true,
  remoteDefaultBranch: 'trunk',
  testedBranch: 'trunk',
  observations: { read: 'verified', branch: 'verified', write: 'untested' },
  diagnostics: [],
  limitations: [],
};

const DENIED_RESPONSE = {
  ...READ_RESPONSE,
  repositoryAccessible: false,
  defaultBranchAccessible: null,
  pullRequestAccessible: null,
  remoteDefaultBranch: null,
  observations: { read: 'denied', write: 'untested' },
  diagnostics: [
    {
      operation: 'repository',
      httpStatus: 403,
      message: 'Resource not accessible by integration — organization approval pending for the selected token.',
      retryable: false,
    },
  ],
};

const OUTAGE_RESPONSE = {
  ...READ_RESPONSE,
  repositoryAccessible: null,
  defaultBranchAccessible: null,
  pullRequestAccessible: null,
  remoteDefaultBranch: null,
  observations: { read: 'unavailable', write: 'untested' },
  diagnostics: [{ operation: 'repository', message: 'ConnectError', retryable: true }],
};

function jsonResponse(body: unknown, init: { ok?: boolean; status?: number } = {}) {
  return { ok: init.ok ?? true, status: init.status ?? 200, json: async () => body };
}

function stubFetch(body: unknown, init: { ok?: boolean; status?: number } = {}) {
  const fetchMock = vi.fn().mockResolvedValue(jsonResponse(body, init));
  vi.stubGlobal('fetch', fetchMock);
  return fetchMock;
}

function deferredFetch() {
  const pending: Array<(value: unknown) => void> = [];
  const fetchMock = vi.fn(
    () =>
      new Promise((resolve) => {
        pending.push(resolve);
      }),
  );
  vi.stubGlobal('fetch', fetchMock);
  return { fetchMock, pending };
}

function requestBody(fetchMock: ReturnType<typeof vi.fn>, index = 0) {
  const init = (fetchMock.mock.calls[index]?.[1] ?? {}) as RequestInit;
  return JSON.parse(String(init.body ?? '{}'));
}

describe('GithubTokenProbePanel (selected-connection Test connection)', () => {
  afterEach(() => {
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
  });

  it('tests the selected connection and reports a read without claiming write access', async () => {
    const fetchMock = stubFetch(READ_RESPONSE);
    renderPanel();

    fireEvent.click(screen.getByRole('button', { name: /Test connection/i }));

    expect(await screen.findByText('Read access verified')).toBeTruthy();
    expect(fetchMock).toHaveBeenCalledWith(
      '/api/v1/settings/github/token-probe',
      expect.objectContaining({ method: 'POST' }),
    );
    expect(requestBody(fetchMock)).toEqual({
      repo: 'owner/repo',
      mode: 'publish',
      connectionId: 'personal-github',
    });
    expect(screen.getByText(/Write access not tested/i)).toBeTruthy();
    expect(screen.getByText('trunk')).toBeTruthy();
  });

  it('renders a transport outage as unknown access, never as denied', async () => {
    stubFetch(OUTAGE_RESPONSE);
    renderPanel();

    fireEvent.click(screen.getByRole('button', { name: /Test connection/i }));

    expect(await screen.findByText(/This is not a denial/i)).toBeTruthy();
    expect(screen.queryByText(/Read access denied/i)).toBeNull();
    expect(screen.queryByText('not readable')).toBeNull();
  });

  it('renders specific provider diagnostics for a denied read', async () => {
    stubFetch(DENIED_RESPONSE);
    renderPanel();

    fireEvent.click(screen.getByRole('button', { name: /Test connection/i }));

    expect(await screen.findByText(/Read access denied/i)).toBeTruthy();
    expect(screen.getByText(/organization approval pending/i)).toBeTruthy();
    expect(screen.getByText(/HTTP 403/)).toBeTruthy();
  });

  it('explains that zero assignments grants no repository authority', async () => {
    stubFetch({ ...READ_RESPONSE, connectionId: 'work-github' });
    renderPanel({ connection: CONNECTION_B });

    fireEvent.click(screen.getByRole('button', { name: /Test connection/i }));

    expect(await screen.findByText(/no assigned repositories/i)).toBeTruthy();
  });

  it('reports an unassigned repository as not checked, never as verified', async () => {
    stubFetch({
      connectionId: 'work-github',
      repo: 'other/unassigned',
      credentialSource: { resolved: false },
      repositoryAccessible: null,
      observations: { read: 'not_checked', branch: 'not_checked', write: 'untested' },
      diagnostics: [
        {
          operation: 'repository_assignment',
          message: 'other/unassigned is not assigned to this connection; assign it before testing.',
          retryable: false,
        },
      ],
    });
    renderPanel({ connection: CONNECTION_B, initialRepo: 'other/unassigned' });

    fireEvent.click(screen.getByRole('button', { name: /Test connection/i }));

    expect(await screen.findByText('Read access was not checked')).toBeTruthy();
    expect(screen.getByText(/is not assigned to this connection/i)).toBeTruthy();
    expect(screen.getByText(/Tests read only assigned repositories/i)).toBeTruthy();
    expect(screen.queryByText(/Read access verified/i)).toBeNull();
  });

  it('discards a late response after switching A to B and back to A', async () => {
    const { fetchMock, pending } = deferredFetch();
    const { select } = renderPanel();

    fireEvent.click(screen.getByRole('button', { name: /Test connection/i }));
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));
    select(CONNECTION_B);
    select(CONNECTION_A);

    await act(async () => {
      pending[0]?.(jsonResponse(READ_RESPONSE));
    });

    expect(screen.queryByText('Read access verified')).toBeNull();
    expect(screen.queryByLabelText('Connection test result')).toBeNull();
    expect((screen.getByRole('button', { name: /Test connection/i }) as HTMLButtonElement).disabled).toBe(
      false,
    );
  });

  it('applies only the latest of two overlapping tests', async () => {
    const { fetchMock, pending } = deferredFetch();
    renderPanel();

    fireEvent.click(screen.getByRole('button', { name: /Test connection/i }));
    fireEvent.change(screen.getByLabelText(/Repository \(owner\/repo\)/i), {
      target: { value: 'owner/other' },
    });
    fireEvent.submit(screen.getByLabelText(/Repository \(owner\/repo\)/i).closest('form')!);
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2));

    await act(async () => {
      pending[1]?.(jsonResponse(OUTAGE_RESPONSE));
    });
    await act(async () => {
      pending[0]?.(jsonResponse(READ_RESPONSE));
    });

    expect(screen.getByText(/This is not a denial/i)).toBeTruthy();
    expect(screen.queryByText('Read access verified')).toBeNull();
  });

  it('marks a displayed result stale when the inputs change', async () => {
    stubFetch(READ_RESPONSE);
    renderPanel();

    fireEvent.click(screen.getByRole('button', { name: /Test connection/i }));
    await screen.findByText('Read access verified');
    fireEvent.change(screen.getByLabelText(/Branch/i), { target: { value: 'release' } });

    expect(screen.getByText(/inputs changed after this test/i)).toBeTruthy();
  });

  it('clears a result when the selected connection credential is rotated', async () => {
    stubFetch(READ_RESPONSE);
    const { select } = renderPanel();

    fireEvent.click(screen.getByRole('button', { name: /Test connection/i }));
    await screen.findByText('Read access verified');
    select({ ...CONNECTION_A, policyRevision: 2, credentialRevision: 2 });

    expect(screen.queryByText('Read access verified')).toBeNull();
  });

  it('never renders token material, global-token precedence, or retired mode copy', async () => {
    stubFetch(READ_RESPONSE);
    const { container } = renderPanel();

    fireEvent.click(screen.getByRole('button', { name: /Test connection/i }));
    await screen.findByText('Read access verified');

    expect(container.querySelector('input[type="password"]')).toBeNull();
    const text = container.textContent ?? '';
    expect(text).not.toMatch(/GITHUB_TOKEN|GH_TOKEN|SecretRef/);
    expect(text).not.toMatch(/Indexing|Validates write access/i);
    expect(text).not.toMatch(/ghp_|github_pat_/);
    expect(container.querySelector('table')).toBeNull();
  });

  it('disables testing without permission or for a disabled connection', () => {
    const { select } = renderPanel({ canRunProbe: false });
    const button = () => screen.getByRole('button', { name: /Test connection/i }) as HTMLButtonElement;
    expect(button().disabled).toBe(true);
    expect(screen.getByText(/requires the settings.effective.read permission/i)).toBeTruthy();
    select({ ...CONNECTION_A, lifecycle: 'disabled' });
    expect(button().disabled).toBe(true);
  });

  it('distinguishes an unknown repository, an empty repository and a missing branch', async () => {
    stubFetch({
      ...READ_RESPONSE,
      repositoryAccessible: false,
      defaultBranchAccessible: null,
      remoteDefaultBranch: null,
      testedBranch: null,
      observations: { read: 'not_found', branch: 'not_checked', write: 'untested' },
    });
    const { unmount } = renderPanel();
    fireEvent.click(screen.getByRole('button', { name: /Test connection/i }));
    expect(await screen.findByText(/Repository not found, or this connection cannot see it/)).toBeTruthy();
    expect(screen.queryByText(/Read access denied/)).toBeNull();
    unmount();

    stubFetch({
      ...READ_RESPONSE,
      defaultBranchAccessible: false,
      testedBranch: 'main',
      remoteDefaultBranch: 'main',
      observations: { read: 'verified', branch: 'empty_repository', write: 'untested' },
    });
    const empty = renderPanel();
    fireEvent.click(screen.getByRole('button', { name: /Test connection/i }));
    expect(await screen.findByText(/The repository is empty/)).toBeTruthy();
    expect(screen.getByText('Read access verified')).toBeTruthy();
    empty.unmount();

    stubFetch({
      ...READ_RESPONSE,
      defaultBranchAccessible: false,
      testedBranch: 'release',
      observations: { read: 'verified', branch: 'missing', write: 'untested' },
    });
    renderPanel();
    fireEvent.click(screen.getByRole('button', { name: /Test connection/i }));
    expect(await screen.findByText(/was not found in this repository/)).toBeTruthy();
    expect(screen.getByText('release')).toBeTruthy();
  });

  it('labels reported permissions as metadata, not a tested write', async () => {
    stubFetch({ ...READ_RESPONSE, reportedPermissions: { push: true, pull: true } });
    renderPanel();

    fireEvent.click(screen.getByRole('button', { name: /Test connection/i }));

    expect(await screen.findByText(/GitHub reports push permission/)).toBeTruthy();
    expect(screen.getByText(/Write access not tested/i)).toBeTruthy();
  });

  it.each([60, 90])('recommends a %s-second throttle retry without claiming its provenance', async (seconds) => {
    stubFetch({
      ...OUTAGE_RESPONSE,
      retryAfterSeconds: seconds,
      diagnostics: [
        { operation: 'repository', httpStatus: 429, message: 'Too many requests', retryable: true },
      ],
    });
    renderPanel();

    fireEvent.click(screen.getByRole('button', { name: /Test connection/i }));

    expect(await screen.findByText(new RegExp(`Wait about ${seconds} seconds before testing again`))).toBeTruthy();
    expect(screen.queryByText(/GitHub asked/)).toBeNull();
    expect(screen.queryByText(/Read access denied/)).toBeNull();
  });

  it('renders backend error responses with their detail and surfaces a notice', async () => {
    stubFetch({ detail: 'Repository connection not found.' }, { ok: false, status: 404 });
    const onNotice = vi.fn();
    renderPanel({ onNotice });

    fireEvent.click(screen.getByRole('button', { name: /Test connection/i }));

    await waitFor(() => {
      expect(onNotice).toHaveBeenCalledWith(expect.objectContaining({ level: 'error' }));
    });
    expect(screen.getByText(/Repository connection not found/)).toBeTruthy();
  });
});
