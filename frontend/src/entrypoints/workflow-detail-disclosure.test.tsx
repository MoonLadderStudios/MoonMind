import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { cleanup, fireEvent, screen, waitFor } from '@testing-library/react';
import type { MockInstance } from 'vitest';
import { renderWithClient } from '../utils/test-utils';
import { WorkflowDetailPage } from './workflow-detail';
import type { BootPayload } from '../boot/parseBootPayload';

// MoonLadderStudios/MoonMind#4872: workflow detail leads with what the
// operator needs; raw identifiers, digests and refs sit one disclosure away.

class MockEventSource {
  onopen: ((event: Event) => void) | null = null;
  onerror: ((event: Event) => void) | null = null;
  onmessage: ((event: MessageEvent) => void) | null = null;
  constructor(public url: string) {}
  addEventListener() {}
  close() {}
}

const payload: BootPayload = { page: 'workflow-detail', apiBase: '/api' };
const planRef = `omnigent-execution-plan:sha256:${'a'.repeat(64)}`;
const planDigest = `sha256:${'a'.repeat(64)}`;
const bindingRef = `omnigent-runtime-binding:sha256:${'b'.repeat(64)}`;
const snapshotRef = 'artifact:skillset-snapshot-1';

const baseExecution = {
  taskId: 'test-123',
  workflowId: 'test-123',
  namespace: 'default',
  runId: 'run-1',
  source: 'temporal',
  workflowType: 'MoonMind.UserWorkflow',
  title: 'Fix the flaky login test',
  summary: 'Tests failed after two attempts.',
  status: 'failed',
  state: 'failed',
  rawState: 'failed',
  temporalStatus: 'failed',
  closeStatus: 'FAILED',
  targetRuntime: 'claude_code',
  model: 'claude-opus-5-5',
  effort: 'high',
  profileId: 'profile:claude-default',
  providerId: 'anthropic',
  providerLabel: 'Anthropic',
  priority: 4,
  repository: 'acme/repo',
  startingBranch: 'feature/login',
  resolvedSkillsetRef: snapshotRef,
  taskSkills: ['fix-ci'],
  skillRuntime: {
    resolvedSkillsetRef: snapshotRef,
    lifecycleIntent: {
      source: 'task',
      resolutionMode: 'snapshot-reuse',
      explanation: 'Reuses the resolved snapshot.',
    },
  },
  omnigentExecutionPlan: { planRef, planDigest, planArtifactRef: 'art_plan_1' },
  omnigentRuntimeBinding: { runtimeBindingRef: bindingRef, revision: 3, fencingGeneration: 1, state: 'session_bound' },
  createdAt: '2026-09-10T00:00:00Z',
  startedAt: '2026-09-10T00:00:01Z',
  updatedAt: '2026-09-10T00:00:05Z',
  closedAt: '2026-09-10T00:00:05Z',
  actions: {},
  relatedRuns: [],
};

describe('workflow detail progressive disclosure (#4872)', () => {
  let fetchSpy: MockInstance;
  let originalEventSource: typeof EventSource;

  function mockFetch(execution: Record<string, unknown>, profiles: unknown[] = []) {
    fetchSpy.mockImplementation((input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes('/provider-profiles')) {
        return Promise.resolve({ ok: true, json: async () => profiles } as Response);
      }
      if (url.includes('/artifacts')) {
        return Promise.resolve({ ok: true, json: async () => ({ artifacts: [] }) } as Response);
      }
      if (url.includes('/remediations?direction=')) {
        return Promise.resolve({ ok: true, json: async () => ({ direction: 'inbound', items: [] }) } as Response);
      }
      if (url.includes('/executions/test-123/steps')) {
        return Promise.resolve({
          ok: true,
          json: async () => ({ workflowId: 'test-123', runId: 'run-1', steps: [] }),
        } as Response);
      }
      return Promise.resolve({ ok: true, json: async () => execution } as Response);
    });
  }

  beforeEach(() => {
    vi.restoreAllMocks();
    window.sessionStorage.clear();
    window.localStorage.clear();
    document.title = '';
    originalEventSource = window.EventSource;
    (window as unknown as { EventSource: unknown }).EventSource = MockEventSource;
    fetchSpy = vi.spyOn(window, 'fetch');
  });

  afterEach(() => {
    cleanup();
    (window as unknown as { EventSource: unknown }).EventSource = originalEventSource;
    vi.restoreAllMocks();
  });

  it('renders the workflow title as the single h1 and the tab title, with the ID shown once', async () => {
    window.history.pushState({}, 'Overview', '/workflows/test-123/overview?source=temporal');
    mockFetch(baseExecution);
    renderWithClient(<WorkflowDetailPage payload={payload} />);

    expect(await screen.findByRole('heading', { level: 1, name: 'Fix the flaky login test' })).toBeTruthy();
    expect(screen.getAllByRole('heading', { level: 1 })).toHaveLength(1);
    expect(screen.queryByRole('heading', { name: 'Workflow Detail' })).toBeNull();
    await waitFor(() => expect(document.title).toBe('Fix the flaky login test | MoonMind'));
    expect(screen.getAllByText('test-123')).toHaveLength(1);
    // Repository and branch appear once, in the shared source context.
    expect(screen.getAllByText('feature/login')).toHaveLength(1);
  });

  it('keeps operational facts visible and raw refs, digests and IDs behind Technical details', async () => {
    window.history.pushState({}, 'Overview', '/workflows/test-123/overview?source=temporal');
    mockFetch(baseExecution, [{ profile_id: 'profile:claude-default', account_label: 'Claude (work)' }]);
    renderWithClient(<WorkflowDetailPage payload={payload} />);

    await screen.findByRole('heading', { level: 1, name: 'Fix the flaky login test' });
    // Always visible: failure outcome, effective harness, profile label, model and effort.
    expect(screen.getByText('Tests failed after two attempts.')).toBeTruthy();
    expect(screen.getByText('Harness').closest('div')?.textContent).toBe('HarnessClaude Code');
    expect(screen.getByText('claude-opus-5-5')).toBeTruthy();
    expect(screen.getByText('Effort').closest('div')?.textContent).toBe('Efforthigh');
    expect(screen.getByText('Provider').closest('div')?.textContent).toBe('ProviderAnthropic');
    await waitFor(() =>
      expect(screen.getByText('Provider Profile').closest('div')?.textContent).toBe('Provider ProfileClaude (work)'),
    );
    expect(screen.getByText('Skills').closest('div')?.textContent).toContain('fix-ci');

    // Hidden by default.
    for (const raw of [planRef, planDigest, bindingRef, snapshotRef, 'profile:claude-default']) {
      expect(screen.queryByText(raw)).toBeNull();
    }
    expect(screen.queryByText('Omnigent authority')).toBeNull();
    expect(screen.queryByText('Agent Skill Provenance')).toBeNull();
    expect(screen.queryByText('Priority')).toBeNull();
    expect(screen.queryByText(/Provider ID/)).toBeNull();

    fireEvent.click(screen.getByText('Technical details'));

    expect(screen.getByText(planRef)).toBeTruthy();
    expect(screen.getByText(planDigest)).toBeTruthy();
    expect(screen.getByText(bindingRef)).toBeTruthy();
    expect(screen.getByText('Agent Skill Provenance')).toBeTruthy();
    expect(screen.getAllByText(snapshotRef).length).toBeGreaterThan(0);
    expect(screen.getByText('Priority').closest('div')?.textContent).toBe('Priority4');
    expect(screen.getByText('Provider ID').closest('div')?.textContent).toBe('Provider IDanthropic');
    expect(screen.getByText('Provider Profile ID').closest('div')?.textContent).toBe(
      'Provider Profile IDprofile:claude-default',
    );
    expect(screen.getByText('Workflow ID').closest('div')?.textContent).toBe('Workflow IDtest-123');
  });

  it('describes an unbound runtime in plain language', async () => {
    window.history.pushState({}, 'Overview', '/workflows/test-123/overview?source=temporal');
    mockFetch({ ...baseExecution, omnigentRuntimeBinding: null });
    renderWithClient(<WorkflowDetailPage payload={payload} />);

    await screen.findByRole('heading', { level: 1, name: 'Fix the flaky login test' });
    fireEvent.click(screen.getByText('Technical details'));
    expect(screen.queryByText(/post-lease/)).toBeNull();
    expect(screen.getByText('Runtime Binding').closest('div')?.textContent).toContain(
      'Not bound yet',
    );
  });

  it('falls back to the recorded profile ID when no label is known', async () => {
    window.history.pushState({}, 'Overview', '/workflows/test-123/overview?source=temporal');
    mockFetch(baseExecution, []);
    renderWithClient(<WorkflowDetailPage payload={payload} />);

    await screen.findByRole('heading', { level: 1, name: 'Fix the flaky login test' });
    expect(screen.getByText('Provider Profile').closest('div')?.textContent).toBe(
      'Provider Profileprofile:claude-default',
    );
  });

  it('hides empty Issue lifecycle sections', async () => {
    window.history.pushState({}, 'Overview', '/workflows/test-123/overview?source=temporal');
    mockFetch(baseExecution);
    renderWithClient(<WorkflowDetailPage payload={payload} />);

    await screen.findByRole('heading', { level: 1, name: 'Fix the flaky login test' });
    expect(screen.queryByRole('heading', { name: 'Issue lifecycle' })).toBeNull();
  });

  it('does not repeat the workflow ID in the Execution run table', async () => {
    window.history.pushState({}, 'Runs', '/workflows/test-123/runs?source=temporal');
    mockFetch(baseExecution);
    renderWithClient(<WorkflowDetailPage payload={payload} />);

    expect(await screen.findByRole('heading', { name: 'Execution History' })).toBeTruthy();
    // Run history stays discoverable; the current row is named, not re-identified.
    expect(screen.getAllByText('run-1').length).toBeGreaterThan(0);
    expect(screen.getByText('This workflow')).toBeTruthy();
    expect(screen.getAllByText('test-123')).toHaveLength(1);
  });
});
