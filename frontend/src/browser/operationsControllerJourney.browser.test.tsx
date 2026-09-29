import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { page } from 'vitest/browser';
import { BrowserRouter } from 'react-router-dom';

import type { BootPayload } from '../boot/parseBootPayload';
import { OperationsSettingsPage } from '../entrypoints/settings';
import type { components } from '../generated/openapi';
import { cleanup, fireEvent, renderWithClient, screen, waitFor, within } from '../utils/test-utils';
import '../styles/dashboard.css';

// Real-browser journey for MoonLadderStudios/MoonMind#4502. It mounts the
// production Settings > Operations page and drives it against an API whose
// responses are typed by the generated OpenAPI client and follow the
// standalone controller's operation semantics: one operation per target,
// resubmitting the same target reattaches, a different target is refused
// while one runs, and Retry starts a fresh attempt that keeps earlier
// failures. Remounting with a fresh query client stands in for a browser
// reload or API replacement: progress must come back from the controller's
// record, not from browser state, and must not repeat the update.

type Schemas = components['schemas'];
type Action = Schemas['DeploymentRecentActionModel'];
type Attempt = Schemas['DeploymentOperationAttemptModel'];

const REPOSITORY = 'ghcr.io/moonladderstudios/moonmind';
const INSTALLED = `${REPOSITORY}:stable`;
const TARGET = `${REPOSITORY}:latest`;
const ORIGINAL_ERROR = 'pull failed: registry refused';

const payload = {
  page: 'settings-operations',
  apiBase: '/api',
  initialData: { settingsPermissions: ['operations.read', 'operations.invoke'] },
} as unknown as BootPayload;

// A historical workflow-backed update: readable history, never executable.
const historicalWorkflowAction: Action = {
  id: 'depupd_historical',
  kind: 'update',
  status: 'SUCCEEDED',
  owner: 'workflow',
  requestedImage: INSTALLED,
  startedAt: '2026-09-01T10:00:00Z',
  completedAt: '2026-09-01T10:04:00Z',
  runDetailUrl: '/workflows/depupd_historical',
  logsArtifactUrl: '/api/artifacts/depupd-historical-logs',
  rawCommandLogPermitted: false,
  retryable: false,
  verification: [],
};

interface ControllerOperation {
  operationId: string;
  image: string;
  status: 'RUNNING' | 'FAILED' | 'SUCCEEDED';
  attempts: Attempt[];
  installedImage: string | null;
  attemptGroup: number;
}

class ControllerBackedApi {
  installedImage = INSTALLED;
  operations: ControllerOperation[] = [];
  updateRequests = 0;
  // Each launch is one controller mutation attempt (submission or retry).
  launches = 0;
  stackReads = 0;

  fail(operationId: string, error: string): void {
    const operation = this.find(operationId)!;
    operation.status = 'FAILED';
    operation.attempts.push({
      attempt: operation.attempts.length + 1,
      attemptGroup: operation.attemptGroup,
      error,
      at: `2026-09-29T12:0${operation.attempts.length}:00Z`,
    });
  }

  succeed(operationId: string): void {
    const operation = this.find(operationId)!;
    operation.status = 'SUCCEEDED';
    operation.installedImage = operation.image;
    this.installedImage = operation.image;
  }

  find(operationId: string): ControllerOperation | undefined {
    return this.operations.find((operation) => operation.operationId === operationId);
  }

  action(operation: ControllerOperation): Action {
    return {
      id: `ctl-${operation.operationId}`,
      kind: 'update',
      status: operation.status,
      owner: 'controller',
      operationId: operation.operationId,
      requestedImage: operation.image,
      installedImage: operation.installedImage,
      originalError: operation.attempts[0]?.error ?? null,
      errorSummary: operation.attempts.at(-1)?.error ?? null,
      startedAt: '2026-09-29T12:00:00Z',
      completedAt: operation.status === 'RUNNING' ? null : '2026-09-29T12:10:00Z',
      runDetailUrl: null,
      logsArtifactUrl: null,
      rawCommandLogPermitted: false,
      retryable: operation.status === 'FAILED',
      verification: [],
    };
  }

  receipt(operation: ControllerOperation): Schemas['DeploymentUpdateResponse'] {
    return {
      deploymentUpdateRunId: `ctl-${operation.operationId}`,
      operationId: operation.operationId,
      owner: 'controller',
      status: operation.status,
      desiredImage: operation.image,
      installedImage: operation.installedImage,
      taskId: null,
      workflowId: null,
    };
  }

  stackState(): Schemas['DeploymentStackStateResponse'] {
    const recentActions = [
      ...[...this.operations].reverse().map((operation) => this.action(operation)),
      historicalWorkflowAction,
    ];
    return {
      stack: 'moonmind',
      projectName: 'moonmind',
      buildId: '20260929.1',
      currentImage: {
        requestedImage: this.installedImage,
        deployedImage: this.installedImage,
        repository: REPOSITORY,
        reference: this.installedImage.split(':').at(-1) ?? null,
        evidence: 'desired_state',
      },
      latestAction: recentActions[0] ?? null,
      recentActions,
      controllerAvailability: 'available',
      policy: {
        repository: REPOSITORY,
        defaultReference: 'latest',
        allowedReferences: ['stable', 'latest'],
        recentTags: [],
        mutableReferences: ['stable', 'latest'],
        allowedModes: ['changed_services'],
      },
    };
  }

  imageTargets(): Schemas['ImageTargetsResponse'] {
    return {
      stack: 'moonmind',
      repositories: [
        {
          repository: REPOSITORY,
          allowedReferences: ['stable', 'latest'],
          recentTags: [],
          digestPinningRecommended: true,
          allowedModes: ['changed_services'],
        } as Schemas['ImageTargetsResponse']['repositories'][number],
      ],
    };
  }

  submit(body: Schemas['DeploymentUpdateRequest']): Response {
    this.updateRequests += 1;
    const image = `${body.image.repository}:${body.image.reference}`;
    const open = this.operations.find((operation) => operation.status === 'RUNNING');
    if (open && open.image === image) {
      return json(this.receipt(open), 202);
    }
    if (open) {
      return json(
        {
          detail: {
            code: 'deployment_controller_conflict',
            message: 'A different target is new intent; submit it after the current operation finishes.',
          },
        },
        409,
      );
    }
    const operation: ControllerOperation = {
      operationId: `op-${this.operations.length + 1}`,
      image,
      status: 'RUNNING',
      attempts: [],
      installedImage: null,
      attemptGroup: 1,
    };
    this.operations.push(operation);
    this.launches += 1;
    return json(this.receipt(operation), 202);
  }

  retry(operationId: string): Response {
    const operation = this.find(operationId);
    if (!operation || operation.status !== 'FAILED') {
      return json({ detail: { code: 'deployment_controller_conflict', message: 'Not retryable.' } }, 409);
    }
    operation.status = 'RUNNING';
    operation.attemptGroup += 1;
    this.launches += 1;
    return json(this.receipt(operation), 202);
  }

  detail(operationId: string): Response {
    const operation = this.find(operationId);
    if (!operation) {
      return json({ detail: { code: 'deployment_controller_operation_not_found', message: 'No such operation.' } }, 404);
    }
    const body: Schemas['DeploymentOperationDetailResponse'] = {
      operation: this.action(operation),
      logs: {
        errorSummary: operation.attempts.at(-1)?.error ?? null,
        attempts: operation.attempts,
        verification: [],
        reportingFailures: [],
      },
    };
    return json(body);
  }

  handle = async (input: RequestInfo | URL, init?: RequestInit): Promise<Response> => {
    const url = String(input);
    const method = (init?.method || 'GET').toUpperCase();
    if (url === '/api/v1/operations/deployment/stacks/moonmind') {
      this.stackReads += 1;
      return json(this.stackState());
    }
    if (url === '/api/v1/operations/deployment/image-targets?stack=moonmind') {
      return json(this.imageTargets());
    }
    if (url === '/api/v1/operations/deployment/update' && method === 'POST') {
      return this.submit(JSON.parse(String(init?.body)));
    }
    const retry = url.match(/^\/api\/v1\/operations\/deployment\/operations\/([^/]+)\/retry$/);
    if (retry && method === 'POST') {
      return this.retry(decodeURIComponent(retry[1]!));
    }
    const detail = url.match(/^\/api\/v1\/operations\/deployment\/operations\/([^/]+)$/);
    if (detail) {
      return this.detail(decodeURIComponent(detail[1]!));
    }
    return json({ detail: 'not found' }, 404);
  };
}

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  });
}

let api: ControllerBackedApi;
let panel: HTMLElement;

function openOperationsPage(): void {
  // A fresh query client per mount: nothing survives but the server record.
  renderWithClient(
    <BrowserRouter>
      <OperationsSettingsPage payload={payload} />
    </BrowserRouter>,
    { container: panel },
  );
}

async function updateCard(): Promise<HTMLElement> {
  return screen.findByRole('region', { name: /moonmind update/i });
}

function metric(card: HTMLElement, label: string): string {
  return within(card).getByText(label).parentElement?.textContent ?? '';
}

// The history entry that contains a unique element (operation id or link).
function historyEntry(anchor: HTMLElement): HTMLElement {
  return anchor.closest('.rounded-2xl') as HTMLElement;
}

function operationEntry(card: HTMLElement, operationId: string): HTMLElement {
  return historyEntry(within(card).getByText(`Operation ${operationId}`));
}

beforeEach(() => {
  api = new ControllerBackedApi();
  window.history.pushState({}, 'Operations', '/settings/operations');
  vi.stubGlobal('fetch', vi.fn(api.handle));
  vi.spyOn(window, 'confirm').mockReturnValue(true);
  const shell = document.createElement('main');
  shell.className = 'dashboard-root';
  const content = document.createElement('div');
  content.className = 'dashboard-content';
  panel = document.createElement('section');
  panel.className = 'panel';
  content.appendChild(panel);
  shell.appendChild(content);
  document.body.appendChild(shell);
});

afterEach(async () => {
  cleanup();
  document.querySelector('main.dashboard-root')?.remove();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
  await page.viewport(1280, 800);
});

describe('Operations update through the deployment controller', () => {
  it('follows one controller operation across reload, failure, logs, and retry', async () => {
    openOperationsPage();
    let card = await updateCard();
    await within(card).findByText('Running image');
    expect(metric(card, 'Running image')).toContain(INSTALLED);
    // Historical workflow-backed rows stay readable without an action to
    // revive their engine.
    const runDetail = within(card).getByRole('link', { name: 'Run detail' });
    expect(runDetail.getAttribute('href')).toBe('/workflows/depupd_historical');
    const historical = historyEntry(runDetail);
    expect(historical.textContent).toMatch(/succeeded/i);
    expect(within(historical).queryByRole('button', { name: /retry/i })).toBeNull();

    fireEvent.click(within(card).getByRole('button', { name: 'Update MoonMind' }));
    expect(
      await within(card).findByText(/accepted by the deployment controller: operation op-1 \(running\)/i),
    ).toBeTruthy();
    expect(await within(card).findByText('Operation op-1')).toBeTruthy();
    expect(within(card).getByText('Installed: not confirmed')).toBeTruthy();

    // Browser reload / API replacement: the page reconnects to the same
    // operation from the controller record without submitting again.
    cleanup();
    openOperationsPage();
    card = await updateCard();
    expect(await within(card).findByText('Operation op-1')).toBeTruthy();
    expect(operationEntry(card, 'op-1').textContent).toMatch(/running/i);
    expect(api.updateRequests).toBe(1);

    // A duplicate submission of the same target reattaches to the one owner.
    fireEvent.click(within(card).getByRole('button', { name: 'Update MoonMind' }));
    expect(
      await within(card).findByText(/accepted by the deployment controller: operation op-1 \(running\)/i),
    ).toBeTruthy();
    expect(api.launches).toBe(1);

    // The controller records the failure; ordinary bounded polling shows it.
    api.fail('op-1', ORIGINAL_ERROR);
    expect(
      await within(card).findByText(`Original error: ${ORIGINAL_ERROR}`, undefined, { timeout: 8_000 }),
    ).toBeTruthy();
    const failed = operationEntry(card, 'op-1');
    expect(failed.textContent).toMatch(/failed/i);
    expect(within(failed).getByText('Installed: not confirmed')).toBeTruthy();

    fireEvent.click(within(failed).getByRole('button', { name: 'Show logs' }));
    expect(await within(failed).findByText(`attempt 1: ${ORIGINAL_ERROR}`)).toBeTruthy();

    fireEvent.click(within(failed).getByRole('button', { name: 'Retry' }));
    expect(await within(card).findByText(/retry accepted for operation op-1 \(running\)/i)).toBeTruthy();
    expect(api.launches).toBe(2);
    expect(api.updateRequests).toBe(2);
    await waitFor(() => expect(operationEntry(card, 'op-1').textContent).toMatch(/running/i));
    // The fresh attempt keeps the first failure visible.
    expect(within(card).getByText(`Original error: ${ORIGINAL_ERROR}`)).toBeTruthy();

    // The retried attempt completes; the confirmed installed image shows.
    api.succeed('op-1');
    expect(
      await within(card).findByText(`Installed: ${TARGET}`, undefined, { timeout: 8_000 }),
    ).toBeTruthy();
    expect(operationEntry(card, 'op-1').textContent).toMatch(/succeeded/i);
    expect(within(operationEntry(card, 'op-1')).queryByRole('button', { name: 'Retry' })).toBeNull();
    expect(metric(card, 'Running image')).toContain(TARGET);
    expect(api.launches).toBe(2);
    // Progress uses bounded reads, not a tight loop.
    expect(api.stackReads).toBeLessThan(20);
  }, 30_000);
});
