import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { OperationsSettingsSection } from '../components/settings/OperationsSettingsSection';
import { fireEvent, renderWithClient, screen, waitFor, within } from '../utils/test-utils';
import '../styles/dashboard.css';

// Real-browser journey for Settings Operations against a controller-backed
// API (MoonLadderStudios/MoonMind#4502): the card names the controller
// operation that accepted an update, a reload reconnects to that same
// operation without submitting again, and a failed operation shows its
// original error and logs and offers the controller's Retry.

const REPOSITORY = 'ghcr.io/moonladderstudios/moonmind';
const TARGET = `${REPOSITORY}:20260930.1200`;

type Operation = {
  operationId: string;
  status: string;
  installedImage: string | null;
  errorSummary: string | null;
  attempts: { attempt: number; error: string; at: string }[];
  attemptGroup: number;
  retryAllowed: boolean;
};

let operation: Operation | null;
let submissions: string[];
let retries: string[];
let root: HTMLElement;

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  });
}

function action(current: Operation) {
  return {
    id: `ctl-${current.operationId}`,
    kind: 'update',
    status: current.status,
    owner: 'controller',
    operationId: current.operationId,
    requestedImage: TARGET,
    installedImage: current.installedImage,
    errorSummary: current.errorSummary,
    attempts: current.attempts,
    attemptGroup: current.attemptGroup,
    verification: [],
    retryAllowed: current.retryAllowed,
    startedAt: '2026-09-30T12:00:00Z',
    completedAt: null,
    runDetailUrl: null,
    logsArtifactUrl: null,
    rollbackEligibility: null,
  };
}

function stackState() {
  const actions = operation ? [action(operation)] : [];
  return {
    stack: 'moonmind',
    projectName: 'moonmind',
    buildId: '20260929.0900',
    currentImage: {
      requestedImage: `${REPOSITORY}:20260929.0900`,
      deployedImage: `${REPOSITORY}:20260929.0900`,
      repository: REPOSITORY,
      reference: '20260929.0900',
      evidence: 'desired_state',
    },
    latestAction: actions[0] ?? null,
    recentActions: actions,
    controller: { installed: true, reachable: true, message: null },
    policy: {
      repository: REPOSITORY,
      defaultReference: 'latest',
      allowedReferences: ['latest'],
      recentTags: ['20260930.1200'],
      mutableReferences: ['latest'],
      allowedModes: ['changed_services'],
    },
  };
}

function controllerBackedApi(input: RequestInfo | URL, init?: RequestInit): Promise<Response> {
  const url = String(input);
  if (url === '/api/v1/operations/deployment/stacks/moonmind') {
    return Promise.resolve(jsonResponse(stackState()));
  }
  if (url === '/api/v1/operations/deployment/image-targets?stack=moonmind') {
    return Promise.resolve(
      jsonResponse({
        stack: 'moonmind',
        repositories: [
          {
            repository: REPOSITORY,
            allowedReferences: ['latest'],
            recentTags: ['20260930.1200'],
            digestPinningRecommended: true,
            allowedModes: ['changed_services'],
          },
        ],
      }),
    );
  }
  if (url === '/api/v1/operations/deployment/update' && init?.method === 'POST') {
    submissions.push(String(init.body));
    // The controller records the dashboard's own operation identity.
    const { operationId } = JSON.parse(String(init.body));
    operation = {
      operationId,
      status: 'RUNNING',
      installedImage: null,
      errorSummary: null,
      attempts: [],
      attemptGroup: 1,
      retryAllowed: false,
    };
    return Promise.resolve(
      jsonResponse(
        {
          deploymentUpdateRunId: `ctl-${operationId}`,
          operationId,
          owner: 'controller',
          status: 'RUNNING',
        },
        202,
      ),
    );
  }
  if (
    operation &&
    url === `/api/v1/operations/deployment/operations/${operation.operationId}/retry` &&
    init?.method === 'POST'
  ) {
    retries.push(url);
    operation = { ...operation!, status: 'RUNNING', retryAllowed: false, attemptGroup: 2 };
    return Promise.resolve(
      jsonResponse(
        {
          deploymentUpdateRunId: `ctl-${operation.operationId}`,
          operationId: operation.operationId,
          owner: 'controller',
          status: 'RUNNING',
        },
        202,
      ),
    );
  }
  return Promise.resolve(jsonResponse({}, 404));
}

function renderOperations() {
  // Each page load gets its own container, like a fresh document.
  const page = document.createElement('section');
  root.appendChild(page);
  return renderWithClient(
    <OperationsSettingsSection canInvokeOperations workerPauseConfig={null} />,
    { container: page },
  );
}

beforeEach(() => {
  operation = null;
  submissions = [];
  retries = [];
  vi.stubGlobal('fetch', vi.fn(controllerBackedApi));
  vi.spyOn(window, 'confirm').mockReturnValue(true);
  root = document.createElement('main');
  root.className = 'dashboard-root';
  document.body.appendChild(root);
});

afterEach(() => {
  root.remove();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe('Settings Operations controller journey', () => {
  it('reconnects to the same controller operation after a reload and retries a failure', async () => {
    const first = renderOperations();
    const card = await screen.findByRole('region', { name: /moonmind update/i });
    fireEvent.change(await within(card).findByLabelText(/update to/i), {
      target: { value: '20260930.1200' },
    });
    fireEvent.click(within(card).getByRole('button', { name: /update moonmind/i }));
    await waitFor(() => expect(submissions).toHaveLength(1));
    const operationId = JSON.parse(submissions[0]!).operationId as string;
    expect(operationId).toMatch(/^ui-[0-9a-f]{32}$/);
    expect(
      await within(card).findByText(`Deployment update accepted by the controller: operation ${operationId} (RUNNING)`),
    ).toBeTruthy();
    expect(submissions).toHaveLength(1);

    // Browser reload: a fresh page reconnects to the running operation
    // through ordinary reads and never submits the update again.
    first.unmount();
    renderOperations();
    const reloaded = await screen.findByRole('region', { name: /moonmind update/i });
    expect(await within(reloaded).findByText(`Operation ${operationId}`)).toBeTruthy();
    expect(within(reloaded).getByText(/Installed:/).textContent).toContain('not confirmed');
    expect(submissions).toHaveLength(1);

    // The controller exhausts its bounded attempts; the original error,
    // attempt log, and Retry stay visible.
    operation = {
      ...operation!,
      status: 'FAILED',
      errorSummary: 'attempt 1: pull failed: manifest unknown (latest attempt 3: pull failed)',
      attempts: [
        { attempt: 1, error: 'pull failed: manifest unknown', at: '2026-09-30T12:01:00Z' },
        { attempt: 3, error: 'pull failed', at: '2026-09-30T12:03:00Z' },
      ],
      retryAllowed: true,
    };
    await waitFor(
      () => expect(within(reloaded).getByText(/Error: attempt 1: pull failed/)).toBeTruthy(),
      { timeout: 12_000 },
    );
    const logs = within(reloaded).getByText('Controller logs');
    fireEvent.click(logs);
    expect(within(reloaded).getByText(/Attempt 1 · 2026-09-30T12:01:00Z: pull failed: manifest unknown/)).toBeTruthy();

    fireEvent.click(within(reloaded).getByRole('button', { name: /retry operation/i }));
    expect(
      await within(reloaded).findByText(
        `Deployment retry accepted by the controller: operation ${operationId} (RUNNING)`,
      ),
    ).toBeTruthy();
    expect(retries).toEqual([`/api/v1/operations/deployment/operations/${operationId}/retry`]);
    expect(submissions).toHaveLength(1);
  }, 30_000);
});
