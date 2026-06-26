import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { GithubTokenProbePanel, type GithubTokenProbePanelProps } from './GithubTokenProbePanel';

interface RenderOptions {
  canRunProbe?: boolean;
  onNotice?: GithubTokenProbePanelProps['onNotice'];
  initialRepo?: string;
}

function renderPanel(props: RenderOptions = {}) {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  const onNotice = props.onNotice ?? vi.fn();
  const utils = render(
    <QueryClientProvider client={queryClient}>
      <GithubTokenProbePanel
        canRunProbe={props.canRunProbe ?? true}
        onNotice={onNotice}
        {...(props.initialRepo !== undefined ? { initialRepo: props.initialRepo } : {})}
      />
    </QueryClientProvider>,
  );
  return { ...utils, onNotice };
}

const PUBLISH_RESPONSE = {
  repo: 'owner/repo',
  mode: 'publish',
  credentialSource: {
    sourceKind: 'settings_token_ref',
    sourceName: 'MOONMIND_GITHUB_TOKEN_REF',
    resolved: true,
  },
  repositoryAccessible: true,
  defaultBranchAccessible: true,
  pullRequestAccessible: true,
  permissionChecklist: [
    { permission: 'Contents', level: 'write', required: true, status: 'passed' },
    { permission: 'Pull requests', level: 'write', required: true, status: 'passed' },
    { permission: 'Workflows', level: 'write', required: false, status: 'not_checked' },
    { permission: 'Commit statuses', level: 'read', required: false, status: 'not_checked' },
    { permission: 'Checks', level: 'read', required: false, status: 'not_checked' },
    { permission: 'Issues', level: 'read', required: false, status: 'not_checked' },
  ],
  diagnostics: [],
  limitations: [
    'Fine-grained personal access tokens must target the repository resource owner and include the selected repository.',
  ],
};

const PENDING_ORG_RESPONSE = {
  repo: 'owner/repo',
  mode: 'publish',
  credentialSource: {
    sourceKind: 'settings_token_ref',
    sourceName: 'MOONMIND_GITHUB_TOKEN_REF',
    resolved: true,
  },
  repositoryAccessible: false,
  defaultBranchAccessible: false,
  pullRequestAccessible: false,
  permissionChecklist: [
    { permission: 'Contents', level: 'write', required: true, status: 'failed' },
    { permission: 'Pull requests', level: 'write', required: true, status: 'failed' },
  ],
  diagnostics: [
    {
      operation: 'repository',
      httpStatus: 403,
      message: 'Resource not accessible by integration — organization approval pending for the selected token.',
      retryable: false,
    },
    {
      operation: 'branch',
      httpStatus: 404,
      message: 'Branch main is not visible to this token; the repository may belong to a different owner or not be selected in the PAT.',
      retryable: false,
    },
  ],
  limitations: [],
};

const MISSING_CREDENTIAL_RESPONSE = {
  repo: 'owner/repo',
  mode: 'publish',
  credentialSource: {
    sourceKind: 'missing',
    sourceName: null,
    resolved: false,
  },
  repositoryAccessible: null,
  defaultBranchAccessible: null,
  pullRequestAccessible: null,
  permissionChecklist: [
    { permission: 'Contents', level: 'write', required: true, status: 'not_checked' },
    { permission: 'Pull requests', level: 'write', required: true, status: 'not_checked' },
  ],
  diagnostics: [
    {
      operation: 'resolve_github_credential',
      message:
        'GitHub auth is not configured for owner/repo; set GITHUB_TOKEN, GH_TOKEN, WORKFLOW_GITHUB_TOKEN, GITHUB_TOKEN_SECRET_REF, WORKFLOW_GITHUB_TOKEN_SECRET_REF, or MOONMIND_GITHUB_TOKEN_REF.',
      retryable: false,
    },
  ],
  limitations: [],
};

function stubFetch(response: unknown, init: { ok?: boolean; status?: number } = {}) {
  const fetchMock = vi.fn().mockResolvedValue({
    ok: init.ok ?? true,
    status: init.status ?? 200,
    json: async () => response,
  });
  vi.stubGlobal('fetch', fetchMock);
  return fetchMock;
}

describe('GithubTokenProbePanel', () => {
  beforeEach(() => {
    vi.useRealTimers();
  });

  afterEach(() => {
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
  });

  it('AC-1: renders the per-mode permission checklist and repo/branch/PR accessibility for a successful publish probe', async () => {
    const fetchMock = stubFetch(PUBLISH_RESPONSE);
    renderPanel({ initialRepo: 'owner/repo' });

    fireEvent.change(screen.getByLabelText(/MoonMind mode/i), {
      target: { value: 'publish' },
    });
    fireEvent.click(screen.getByRole('button', { name: /Run probe/i }));

    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalledWith(
        '/api/v1/settings/github/token-probe',
        expect.objectContaining({
          method: 'POST',
          headers: expect.objectContaining({ 'Content-Type': 'application/json' }),
        }),
      );
    });
    const firstCall = fetchMock.mock.calls[0];
    const init = (firstCall?.[1] ?? {}) as RequestInit;
    const body = JSON.parse(String(init.body ?? '{}'));
    expect(body).toMatchObject({ repo: 'owner/repo', mode: 'publish' });

    // Checklist rows present with status badges
    expect(await screen.findByText('Contents')).toBeTruthy();
    expect(screen.getByText('Pull requests')).toBeTruthy();
    expect(screen.getByText('Workflows')).toBeTruthy();
    expect(screen.getAllByText(/required/i).length).toBeGreaterThanOrEqual(2);
    expect(screen.getAllByText(/optional/i).length).toBeGreaterThanOrEqual(1);
    expect(screen.getAllByText(/passed/i).length).toBeGreaterThanOrEqual(2);

    // Repo/branch/PR-endpoint pills
    expect(screen.getByText(/Repository accessible/i)).toBeTruthy();
    expect(screen.getByText(/Default branch accessible/i)).toBeTruthy();
    expect(screen.getByText(/Pull request endpoint accessible/i)).toBeTruthy();
  });

  it('AC-2: renders specific diagnostics for pending org approval / wrong owner / unselected repo without collapsing to a generic message', async () => {
    stubFetch(PENDING_ORG_RESPONSE);
    renderPanel({ initialRepo: 'owner/repo' });

    fireEvent.click(screen.getByRole('button', { name: /Run probe/i }));

    expect(
      await screen.findByText(/organization approval pending/i),
    ).toBeTruthy();
    expect(
      screen.getByText(/repository may belong to a different owner or not be selected in the PAT/i),
    ).toBeTruthy();
    expect(screen.getByText(/HTTP 403/)).toBeTruthy();
    expect(screen.getByText(/HTTP 404/)).toBeTruthy();
    expect(screen.queryByText(/invalid token/i)).toBeNull();
  });

  it('AC-2b: surfaces missing-credential diagnostics from resolve_github_credential', async () => {
    stubFetch(MISSING_CREDENTIAL_RESPONSE);
    renderPanel({ initialRepo: 'owner/repo' });

    fireEvent.click(screen.getByRole('button', { name: /Run probe/i }));

    expect(
      await screen.findByText(/GitHub auth is not configured for owner\/repo/i),
    ).toBeTruthy();
    expect(screen.queryByText(/invalid token/i)).toBeNull();
  });

  it('AC-3: never renders raw token material — only the credentialSource kind + name', async () => {
    stubFetch(PUBLISH_RESPONSE);
    const { container } = renderPanel({ initialRepo: 'owner/repo' });

    fireEvent.click(screen.getByRole('button', { name: /Run probe/i }));

    await screen.findByText(/MOONMIND_GITHUB_TOKEN_REF/);
    expect(container.querySelector('input[type="password"]')).toBeNull();
    expect(screen.queryByLabelText(/token/i)).toBeNull();
    expect(screen.queryByText(/ghp_/)).toBeNull();
    expect(screen.queryByText(/github_pat_/)).toBeNull();
  });

  it('AC-4: documents the SecretRef alias precedence in user-facing copy', () => {
    renderPanel();

    // Either rendered inline or behind a stable accessible label.
    const helpRegion = screen.getByLabelText(/SecretRef alias precedence/i);
    const text = helpRegion.textContent ?? '';
    expect(text).toMatch(/GITHUB_TOKEN/);
    expect(text).toMatch(/GH_TOKEN/);
    expect(text).toMatch(/WORKFLOW_GITHUB_TOKEN/);
    expect(text).toMatch(/GITHUB_TOKEN_SECRET_REF/);
    expect(text).toMatch(/WORKFLOW_GITHUB_TOKEN_SECRET_REF/);
    expect(text).toMatch(/MOONMIND_GITHUB_TOKEN_REF/);
    expect(text).toMatch(/settings\.github\.github_token_secret_ref/);
  });

  it('AC-5: disables Run probe when canRunProbe is false', () => {
    renderPanel({ canRunProbe: false, initialRepo: 'owner/repo' });

    const runButton = screen.getByRole('button', { name: /Run probe/i });
    expect((runButton as HTMLButtonElement).disabled).toBe(true);
    expect(
      screen.getByText(/Workspace admin permission required to run the GitHub token probe/i),
    ).toBeTruthy();
  });

  it('renders backend error responses with their detail and surfaces a notice', async () => {
    stubFetch({ detail: 'Permission denied' }, { ok: false, status: 403 });
    const onNotice = vi.fn();
    renderPanel({ initialRepo: 'owner/repo', onNotice });

    fireEvent.click(screen.getByRole('button', { name: /Run probe/i }));

    await waitFor(() => {
      expect(onNotice).toHaveBeenCalledWith(
        expect.objectContaining({ level: 'error' }),
      );
    });
    expect(screen.getByText(/Permission denied/)).toBeTruthy();
  });
});
