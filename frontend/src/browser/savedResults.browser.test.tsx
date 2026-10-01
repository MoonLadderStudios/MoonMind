import { afterEach, beforeEach, describe, expect, it, vi, type MockInstance } from 'vitest';
import { page, userEvent } from 'vitest/browser';

import type { BootPayload } from '../boot/parseBootPayload';
import { DashboardApp } from '../entrypoints/dashboard-app';
import { renderWithClient, screen, waitFor, within } from '../utils/test-utils';
import '../styles/dashboard.css';

// Real-browser journey for MoonLadderStudios/MoonMind#4020. The production
// dashboard router mounts Workflow Detail on the Artifacts tab for a run whose
// compute and requested publication failed, whose cleanup is still pending,
// and whose host is gone. API reads are the exact serialized projections the
// server returns (snake_case artifact listing with the saved-work summary);
// Publish Saved Work and Continue requests are captured as the dispatch
// contracts. The API side of the same evidence after workspace removal is
// proven by tests/unit/api/routers/test_saved_result_artifacts.py.

const DESKTOP = { width: 1280, height: 900 } as const;
const MOBILE = { width: 390, height: 844 } as const;

const execution = {
  taskId: 'saved-source',
  workflowId: 'saved-source',
  namespace: 'default',
  temporalRunId: 'saved-run',
  runId: 'saved-run',
  source: 'temporal',
  workflowType: 'MoonMind.UserWorkflow',
  entry: 'user_workflow',
  title: 'Failed run with saved work',
  summary: 'Compute failed after the workspace was saved.',
  status: 'failed',
  state: 'failed',
  rawState: 'failed',
  temporalStatus: 'failed',
  repository: 'MoonLadderStudios/MoonMind',
  startingBranch: 'main',
  publishMode: 'pr',
  createdAt: '2026-09-30T00:00:00Z',
  updatedAt: '2026-09-30T00:05:00Z',
  actions: { canRetryPublication: false },
  finishSummary: {
    controlStop: {
      auxiliaryOutcomes: {
        gitPublication: { status: 'failed' },
        hostCleanup: { status: 'pending' },
        providerProfileRelease: { status: 'pending' },
        janitorRequired: false,
      },
    },
  },
};

function listed(artifactId: string, linkType: string, extra: Record<string, unknown> = {}) {
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
    metadata: {},
    links: [
      {
        namespace: 'default',
        workflow_id: 'saved-source',
        run_id: 'saved-run',
        link_type: linkType,
        label: null,
      },
    ],
    ...extra,
  };
}

const artifacts = [
  listed('art_saved_manifest', 'output.checkpoint', {
    content_type: 'application/vnd.moonmind.saved-work-manifest+json;version=1',
    metadata: {
      artifact_kind: 'saved_work_manifest',
      saved_work_summary: {
        capture_id: 'implement:capture',
        required_formats: ['full_snapshot'],
        outputs: [
          { format: 'full_snapshot', status: 'self_contained', artifact_id: 'art_saved_archive' },
          {
            format: 'exact_baseline_delta',
            status: 'requires_dependencies',
            artifact_id: 'art_saved_delta',
          },
          { format: 'selected_history', status: 'inapplicable' },
        ],
        exclusion_count: 2,
        exclusion_reasons: [{ reason: 'sensitive-path-policy', count: 2 }],
        limitations: [],
        retention_ref: 'artifact-ownership',
        scan_disposition: 'clean',
        quiescence_verified: true,
      },
    },
  }),
  listed('art_saved_archive', 'output.checkpoint', {
    content_type: 'application/vnd.moonmind.worktree-archive',
    metadata: { artifact_kind: 'checkpoint_archive' },
  }),
  listed('art_saved_delta', 'output.checkpoint', {
    content_type: 'application/vnd.moonmind.saved-work-delta+json;version=1',
    raw_access_allowed: false,
    redaction_level: 'restricted',
    metadata: { artifact_kind: 'checkpoint_delta' },
  }),
  listed('art_report', 'report.summary', { metadata: { title: 'Final report' } }),
  listed('art_answer', 'output.primary', {
    content_type: 'application/json',
    metadata: { title: 'Answer' },
  }),
  listed('art_partial', 'output.primary', {
    status: 'pending_upload',
    sha256: null,
    size_bytes: null,
    metadata: { title: 'Partial upload' },
  }),
  listed('art_old', 'report.summary', {
    expires_at: '2026-01-01T00:00:00Z',
    metadata: { title: 'Expired report' },
  }),
  listed('art_stdout', 'runtime.stdout', { metadata: { title: 'Runtime stdout' } }),
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
    sources: { temporal: { create: '/api/executions', artifactCreate: '/api/artifacts' } },
    system: { defaultRepository: 'MoonLadderStudios/MoonMind' },
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
let publishRequests: Array<Record<string, unknown>>;
let continueRequests: Array<Record<string, unknown>>;

beforeEach(() => {
  window.sessionStorage.clear();
  window.localStorage.clear();
  window.history.replaceState({}, '', '/workflows/saved-source/artifacts?source=temporal');
  publishRequests = [];
  continueRequests = [];
  fetchSpy = vi.spyOn(window, 'fetch').mockImplementation(
    async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url === '/api/ui/info') return jsonResponse(uiInfo);
      if (url === '/api/v1/secrets') return jsonResponse({ items: [] });
      if (url.startsWith('/api/v1/provider-profiles')) return jsonResponse([]);
      if (url.endsWith('/executions/saved-source/retry-publication')) {
        publishRequests.push(JSON.parse(String(init?.body)) as Record<string, unknown>);
        return jsonResponse(
          {
            sourceWorkflowId: 'saved-source',
            sourceRunId: 'saved-run',
            workflowId: 'mm:saved-publication:7f3a',
            runId: 'publication-run',
            publicationIdempotencyKey: 'saved-publication:7f3a',
            rolloutGeneration: 'g1',
          },
          201,
        );
      }
      if (url.endsWith('/executions/saved-source/continue')) {
        continueRequests.push(JSON.parse(String(init?.body)) as Record<string, unknown>);
        return jsonResponse(
          {
            sourceWorkflowId: 'saved-source',
            sourceRunId: 'saved-run',
            destinationWorkflowId: 'mm:continued',
            relationshipType: 'linked_continuation',
            created: true,
          },
          201,
        );
      }
      if (url.endsWith('/executions/saved-source/captured-evidence')) {
        return jsonResponse({
          workflowId: 'saved-source',
          runId: 'saved-run',
          available: true,
          items: [{ label: 'Final report', kind: 'output_artifact', artifactRef: 'art_report' }],
        });
      }
      if (url.includes('/executions/default/saved-source/saved-run/artifacts')) {
        if (url.includes('link_type=report.primary')) return jsonResponse({ artifacts: [] });
        return jsonResponse({ artifacts });
      }
      if (url.includes('/executions/saved-source')) {
        if (url.includes('/remediations')) return jsonResponse({ items: [] });
        if (url.includes('/checkpoint-branches')) return jsonResponse({ items: [] });
        if (url.includes('/artifacts')) return jsonResponse({ artifacts: [] });
        return jsonResponse(execution);
      }
      if (url.startsWith('/api/executions')) return jsonResponse({ items: [] });
      return jsonResponse({});
    },
  );
});

afterEach(async () => {
  cleanupRender?.();
  cleanupRender = null;
  fetchSpy.mockRestore();
  window.sessionStorage.clear();
  window.localStorage.clear();
  window.history.replaceState({}, '', '/');
  await page.viewport(DESKTOP.width, DESKTOP.height);
});

function rowFor(region: HTMLElement, text: string): HTMLElement {
  return within(region).getByText(text).closest('tr') as HTMLElement;
}

describe('saved results after the source host is gone', () => {
  for (const viewport of [DESKTOP, MOBILE]) {
    it(`shows saved work and dispatches publish/continue at ${viewport.width}px`, async () => {
      await page.viewport(viewport.width, viewport.height);
      const { unmount } = renderWithClient(<DashboardApp payload={payload} />);
      cleanupRender = unmount;

      const heading = await screen.findByRole(
        'heading',
        { name: 'Saved Results' },
        { timeout: 10_000 },
      );
      const region = heading.closest('section') as HTMLElement;
      await within(region).findByText('Saved work', {}, { timeout: 10_000 });

      // Compute, save, publication, and cleanup stay independent.
      const card = (label: string) =>
        within(region).getByText(new RegExp(`^${label}:`), { selector: 'strong' }).closest('.card')
          ?.textContent;
      expect(card('Compute')).toMatch(/failed/i);
      expect(card('Save')).toMatch(/committed/i);
      expect(card('Publication')).toMatch(/failed/i);
      expect(card('Cleanup')).toMatch(/pending/i);

      // The saved-work unit groups its parts; runtime logs are not saved work.
      const unit = rowFor(region, 'Saved work');
      expect(within(unit).getByText('Complete')).toBeTruthy();
      expect(within(unit).getByText('2 excluded')).toBeTruthy();
      const parts = within(unit).getByRole('list', { name: 'Saved work parts' });
      expect(within(parts).getByRole('link', { name: 'Download' }).getAttribute('href')).toBe(
        '/api/artifacts/art_saved_archive/download',
      );
      const delta = within(parts).getByText('art_saved_delta').closest('li') as HTMLElement;
      expect(within(delta).getByText('Raw unavailable')).toBeTruthy();
      expect(within(region).queryByText('Runtime stdout')).toBeNull();

      // Report-only and non-Git outputs remain useful on their own.
      const report = rowFor(region, 'Final report');
      expect(within(report).getByRole('link', { name: 'Download' }).getAttribute('href')).toBe(
        '/api/artifacts/art_report/download',
      );
      expect(within(rowFor(region, 'Answer')).getByText('Complete')).toBeTruthy();
      expect(
        within(rowFor(region, 'Partial upload')).getByText('Incomplete (status-PENDING_UPLOAD)'),
      ).toBeTruthy();
      const expired = rowFor(region, 'Expired report');
      expect(within(expired).getByText('Expired', { selector: 'span' })).toBeTruthy();
      expect(within(expired).queryByRole('link')).toBeNull();

      // Publish Saved Work: the publication-only path with the saved-work body.
      await userEvent.click(within(unit).getByRole('button', { name: 'Publish saved work' }));
      const publishForm = within(region).getByRole('form', { name: 'Publish saved work' });
      await userEvent.fill(
        within(publishForm).getByLabelText('Head branch'),
        'saved-work/browser-journey',
      );
      await userEvent.selectOptions(within(publishForm).getByLabelText('Publish as'), 'draft_pr');
      await userEvent.click(
        within(publishForm).getByRole('button', { name: 'Publish to this destination' }),
      );
      const started = await within(region).findByText(/Publication started/, {}, { timeout: 10_000 });
      expect(within(started).getByText('saved-publication:7f3a')).toBeTruthy();
      expect(publishRequests).toEqual([
        {
          savedWorkRef: 'art_saved_manifest',
          destination: {
            repository: 'MoonLadderStudios/MoonMind',
            objective: 'draft_pr',
            baseBranch: 'main',
            headBranch: 'saved-work/browser-journey',
            strategy: 'baseline_delta',
          },
        },
      ]);

      // Continue: fresh admission carrying only authorized complete outputs.
      const continueButton = within(region).getByRole('button', { name: 'Continue working' });
      await waitFor(() => expect((continueButton as HTMLButtonElement).disabled).toBe(false));
      await userEvent.click(continueButton);
      const continueForm = within(region).getByRole('form', { name: 'Continue working' });
      await userEvent.fill(
        within(continueForm).getByLabelText('New instructions'),
        'Finish the report from the saved result.',
      );
      await userEvent.click(within(continueForm).getByRole('button', { name: 'Start continuation' }));
      expect(
        await within(region).findByText(/Continuation admitted/, {}, { timeout: 10_000 }),
      ).toBeTruthy();
      expect(continueRequests).toHaveLength(1);
      expect(continueRequests[0]).toMatchObject({
        instructions: 'Finish the report from the saved result.',
        selectedSourceArtifactRefs: ['art_report'],
      });
      expect(String(continueRequests[0]?.idempotencyKey)).toMatch(
        /^saved-result:continue:saved-source:saved-run:/,
      );
      expect(document.documentElement.scrollWidth).toBeLessThanOrEqual(window.innerWidth);
    }, 30_000);
  }
});
