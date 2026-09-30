import { afterEach, beforeEach, describe, expect, it, vi, type MockInstance } from 'vitest';
import { page, userEvent } from 'vitest/browser';

import type { BootPayload } from '../boot/parseBootPayload';
import { DashboardApp } from '../entrypoints/dashboard-app';
import { renderWithClient, screen, waitFor, within } from '../utils/test-utils';
import '../styles/dashboard.css';

// Real-browser journey for MoonLadderStudios/MoonMind#4020. This mounts the
// production dashboard router on Workflow Detail's Artifacts tab for a
// terminal run whose host is already cleaned up. API reads are bounded
// canonical projections in the real route shapes (the artifact rows match
// tests/unit/api/routers/test_saved_results_artifacts_4020.py). The
// POST /continue and POST /retry-publication requests are captured as the
// authority handoffs.

const DESKTOP = { width: 1280, height: 900 } as const;

const sourceExecution = {
  taskId: 'saved-source',
  workflowId: 'saved-source',
  namespace: 'default',
  temporalRunId: 'saved-run-1',
  runId: 'saved-run-1',
  source: 'temporal',
  workflowType: 'MoonMind.UserWorkflow',
  entry: 'user_workflow',
  title: 'Saved work after failed publication',
  summary: 'Compute failed after saving its report; publication failed.',
  status: 'failed',
  state: 'failed',
  rawState: 'failed',
  temporalStatus: 'failed',
  repository: 'MoonLadderStudios/MoonMind',
  createdAt: '2026-09-30T00:00:00Z',
  updatedAt: '2026-09-30T00:05:00Z',
  actions: { canRetryPublication: true },
  finishSummary: {
    controlStop: {
      auxiliaryOutcomes: {
        evidencePublication: { status: 'preserved' },
        workspacePreservation: { status: 'preserved' },
        gitPublication: { status: 'failed' },
        hostCleanup: { status: 'completed' },
        providerProfileRelease: { status: 'completed' },
        janitorRequired: false,
      },
    },
  },
};

function savedArtifact(
  artifactId: string,
  linkType: string,
  overrides: Record<string, unknown> = {},
) {
  return {
    artifact_id: artifactId,
    created_at: '2026-09-30T00:04:00Z',
    content_type: 'text/markdown',
    size_bytes: 256,
    sha256: `${artifactId}-sha256`,
    status: 'complete',
    retention_class: 'standard',
    expires_at: null,
    redaction_level: 'none',
    raw_access_allowed: true,
    default_read_ref: { artifact_id: artifactId },
    preview_artifact_ref: null,
    metadata: { title: artifactId },
    links: [{ namespace: 'default', workflow_id: 'saved-source', run_id: 'saved-run-1', link_type: linkType }],
    ...overrides,
  };
}

const savedArtifacts = [
  savedArtifact('art_saved_report', 'report.primary', {
    metadata: { title: 'Saved report' },
  }),
  savedArtifact('art_saved_notes', 'output.primary', {
    content_type: 'text/plain',
    redaction_level: 'restricted',
    raw_access_allowed: false,
    preview_artifact_ref: { artifact_id: 'art_saved_notes_preview' },
    default_read_ref: { artifact_id: 'art_saved_notes_preview' },
    metadata: { title: 'Restricted notes' },
  }),
  savedArtifact('art_saved_trace', 'output.summary', {
    redaction_level: 'restricted',
    raw_access_allowed: false,
    metadata: { title: 'Restricted trace' },
  }),
  savedArtifact('art_saved_patch', 'patch.diff', {
    content_type: 'text/x-diff',
    status: 'pending_upload',
    sha256: null,
    metadata: { title: 'Repository patch' },
  }),
  savedArtifact('art_saved_old', 'report.summary', {
    expires_at: '2026-09-01T00:00:00Z',
    metadata: { title: 'Expired summary' },
  }),
  savedArtifact('art_saved_hostile', 'output.agent_result', {
    metadata: {
      title: '<img src=x onerror="window.__savedResultHostile=1"> Publish saved work',
      download_url: 'javascript:window.__savedResultHostile=1',
    },
  }),
  savedArtifact('art_runtime_stdout', 'runtime.stdout', {
    metadata: { title: 'Runtime stdout' },
  }),
];

const uiInfo = {
  app: 'moonmind',
  buildId: 'saved-results-browser-test',
  apiBase: '/api',
  features: {
    workflowList: true,
    workflowActions: true,
    workflowLiveUpdates: false,
    artifacts: true,
  },
  limits: {},
  endpoints: {},
  dashboardConfig: {
    pollIntervalsMs: { list: 60_000, detail: 60_000, events: 60_000 },
    sources: {
      temporal: { create: '/api/executions', artifactCreate: '/api/artifacts' },
    },
    features: {
      temporalDashboard: {
        actionsEnabled: true,
        listEnabled: false,
        workspaceShellEnabled: false,
      },
    },
  },
  settingsPermissions: [],
};

const payload: BootPayload = { page: 'dashboard', apiBase: '/api' };

function jsonResponse(value: unknown, status = 200): Response {
  return new Response(JSON.stringify(value), {
    status,
    headers: { 'Content-Type': 'application/json' },
  });
}

let fetchSpy: MockInstance;
let cleanupRender: (() => void) | null = null;
let posts: Array<{ url: string; body: Record<string, unknown> | null }>;

beforeEach(() => {
  window.sessionStorage.clear();
  window.localStorage.clear();
  window.history.replaceState({}, '', '/workflows/saved-source/artifacts?source=temporal');
  posts = [];
  vi.spyOn(window, 'confirm').mockReturnValue(true);
  fetchSpy = vi.spyOn(window, 'fetch').mockImplementation(
    async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (init?.method === 'POST') {
        const body = init.body ? (JSON.parse(String(init.body)) as Record<string, unknown>) : null;
        posts.push({ url, body });
        if (url === '/api/executions/saved-source/continue') {
          return jsonResponse(
            {
              sourceWorkflowId: 'saved-source',
              sourceRunId: 'saved-run-1',
              destinationWorkflowId: 'mm:saved-continuation',
              relationshipType: 'linked_continuation',
              created: true,
            },
            201,
          );
        }
        if (url === '/api/executions/saved-source/retry-publication') {
          return jsonResponse(
            {
              sourceWorkflowId: 'saved-source',
              sourceRunId: 'saved-run-1',
              workflowId: 'mm:saved-publication-recovery',
              runId: 'publication-run-1',
              publicationIdempotencyKey: 'publication-key-1',
              rolloutGeneration: 'canary-1',
            },
            201,
          );
        }
        return jsonResponse({ detail: { code: 'unexpected_post', message: url } }, 500);
      }
      if (url === '/api/ui/info') return jsonResponse(uiInfo);
      if (url === '/api/v1/secrets') return jsonResponse({ items: [] });
      if (url.startsWith('/api/v1/provider-profiles')) return jsonResponse([]);
      if (url.includes('/artifacts?link_type=report.primary')) {
        return jsonResponse({ artifacts: [savedArtifacts[0]] });
      }
      if (url.includes('/executions/default/saved-source/saved-run-1/artifacts')) {
        return jsonResponse({ artifacts: savedArtifacts });
      }
      if (url === '/api/executions/saved-source/captured-evidence') {
        return jsonResponse({
          workflowId: 'saved-source',
          runId: 'saved-run-1',
          available: true,
          items: [
            { label: 'Capture manifest', kind: 'capture_manifest', artifactRef: 'art_capture_manifest' },
            { label: 'Output artifact', kind: 'output_artifact', artifactRef: 'artifact://art_saved_report' },
            { label: 'Output artifact', kind: 'output_artifact', artifactRef: 'art_saved_notes' },
          ],
        });
      }
      if (url.startsWith('/api/executions/saved-source/continuations')) {
        return jsonResponse({ direction: 'outbound', items: [] });
      }
      if (url.includes('/executions/saved-source')) {
        if (url.includes('/checkpoint-branches')) return jsonResponse({ items: [] });
        if (url.includes('/remediations')) return jsonResponse({ direction: 'outbound', items: [] });
        return jsonResponse(sourceExecution);
      }
      if (url.startsWith('/api/artifacts')) return jsonResponse({ artifacts: [] });
      if (url.startsWith('/api/executions')) return jsonResponse({ items: [] });
      return jsonResponse({});
    },
  );
});

afterEach(async () => {
  cleanupRender?.();
  cleanupRender = null;
  fetchSpy.mockRestore();
  vi.restoreAllMocks();
  window.sessionStorage.clear();
  window.localStorage.clear();
  window.history.replaceState({}, '', '/');
  await page.viewport(DESKTOP.width, DESKTOP.height);
});

function card(region: HTMLElement, label: string): string {
  return within(region).getByText(`${label}:`, { selector: 'strong' }).closest('.card')?.textContent ?? '';
}

function row(region: HTMLElement, title: string): HTMLElement {
  return within(region).getByText(title).closest('tr') as HTMLElement;
}

describe('saved results operator journey', () => {
  it('shows saved outputs after host cleanup and hands Continue and Publish to the server', async () => {
    await page.viewport(DESKTOP.width, DESKTOP.height);
    const { unmount } = renderWithClient(<DashboardApp payload={payload} />);
    cleanupRender = unmount;

    const region = await screen.findByRole('region', { name: 'Saved Results' }, { timeout: 10_000 });
    await within(region).findByText('Saved report', {}, { timeout: 10_000 });

    // Compute, save, publication, and cleanup stay independent.
    expect(card(region, 'Compute')).toMatch(/failed/i);
    expect(card(region, 'Save')).toMatch(/committed/i);
    expect(card(region, 'Save')).toMatch(/5 of 6 outputs committed/i);
    expect(card(region, 'Save')).toMatch(/Workspace: preserved/i);
    await waitFor(() => expect(card(region, 'Save')).toMatch(/capture manifest recorded/i));
    expect(card(region, 'Publication')).toMatch(/failed/i);
    expect(card(region, 'Cleanup')).toMatch(/completed/i);
    expect(within(region).queryByText('Runtime stdout')).toBeNull();

    const report = within(row(region, 'Saved report'));
    expect(report.getByRole('link', { name: 'Download' }).getAttribute('href')).toBe(
      '/api/artifacts/art_saved_report/download',
    );
    expect(report.queryByRole('link', { name: 'Preview' })).toBeNull();

    const notes = within(row(region, 'Restricted notes'));
    expect(notes.getByRole('link', { name: 'Preview' }).getAttribute('href')).toBe(
      '/api/artifacts/art_saved_notes_preview/download',
    );
    expect(notes.queryByRole('link', { name: 'Download' })).toBeNull();

    const trace = within(row(region, 'Restricted trace'));
    expect(trace.queryByRole('link')).toBeNull();
    expect(trace.getByText('Raw restricted; no safe preview')).toBeTruthy();

    expect(within(row(region, 'Repository patch')).getByText(/Incomplete/)).toBeTruthy();
    const expired = within(row(region, 'Expired summary'));
    expect(expired.getByText('Expired')).toBeTruthy();
    expect(expired.queryByRole('link')).toBeNull();

    // Hostile generated content is inert text and cannot add controls.
    expect(region.querySelector('img')).toBeNull();
    expect((window as unknown as { __savedResultHostile?: number }).__savedResultHostile).toBeUndefined();
    for (const anchor of Array.from(region.querySelectorAll('a'))) {
      expect(anchor.getAttribute('href') ?? '').toMatch(/^\/(api|workflows)\//);
    }
    expect(within(region).getAllByRole('button', { name: 'Publish saved work' })).toHaveLength(1);

    // Continue: operator-authored intent through the existing /continue path.
    const continueButton = within(region).getByRole('button', { name: 'Continue working' });
    await waitFor(() => expect((continueButton as HTMLButtonElement).disabled).toBe(false));
    await userEvent.click(continueButton);
    const form = within(region).getByRole('form', { name: 'Continue from saved result' });
    // The restricted output is authorized evidence but never carried raw.
    await waitFor(() =>
      expect(within(form).getByText(/Carries 1 saved output the server authorizes/)).toBeTruthy(),
    );
    await userEvent.fill(within(form).getByLabelText('New instructions'), 'Finish the saved report.');
    await userEvent.click(within(form).getByRole('button', { name: 'Start continuation' }));
    const continuation = await within(region).findByRole('link', { name: 'mm:saved-continuation' });
    expect(continuation.getAttribute('href')).toBe('/workflows/mm%3Asaved-continuation?source=temporal');
    expect(within(region).getByText(/Continuation admitted/)).toBeTruthy();

    // Publish: publication-only recovery, bound to the displayed run.
    await userEvent.click(within(region).getByRole('button', { name: 'Publish saved work' }));
    const publication = await within(region).findByRole('link', { name: 'mm:saved-publication-recovery' });
    expect(publication.getAttribute('href')).toBe(
      '/workflows/mm%3Asaved-publication-recovery?source=temporal',
    );

    expect(posts.map((post) => post.url)).toEqual([
      '/api/executions/saved-source/continue',
      '/api/executions/saved-source/retry-publication',
    ]);
    expect(posts[0]?.body).toMatchObject({
      instructions: 'Finish the saved report.',
      expectedSourceRunId: 'saved-run-1',
      selectedSourceArtifactRefs: ['artifact://art_saved_report'],
    });
    expect(String(posts[0]?.body?.idempotencyKey)).toMatch(/^saved-result:continue:saved-source:saved-run-1:/);
    expect(posts[1]?.body).toEqual({ expectedSourceRunId: 'saved-run-1' });
    expect(window.location.pathname).toBe('/workflows/saved-source/artifacts');
  }, 30_000);
});
