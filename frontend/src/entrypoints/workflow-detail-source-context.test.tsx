import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { screen, waitFor, within, cleanup, fireEvent } from '@testing-library/react';
import { renderWithClient } from '../utils/test-utils';
import type { MockInstance } from 'vitest';
import {
  normalizeStartingBranch,
  resolveWorkflowSourceContext,
  WorkflowDetailPage,
} from './workflow-detail';
import type { BootPayload } from '../boot/parseBootPayload';

// MoonLadderStudios/MoonMind#4228: Repository and Starting Branch are Details →
// Git & Publish facts with explicit unavailable-data states. They are not shared
// chrome above the tabs.

class MockEventSource {
  onopen: ((event: Event) => void) | null = null;
  onerror: ((event: Event) => void) | null = null;
  onmessage: ((event: MessageEvent) => void) | null = null;
  closed = false;
  constructor(public url: string) {}
  addEventListener() {}
  close() {
    this.closed = true;
  }
}

describe('workflow source context normalization (MM-4228)', () => {
  it('normalizes populated branches verbatim, preserving slashes and long names', () => {
    expect(normalizeStartingBranch('feature/example')).toBe('feature/example');
    const longBranch = `feature/${'a-very-long-branch-name-'.repeat(8)}suffix`;
    expect(normalizeStartingBranch(longBranch)).toBe(longBranch);
    expect(normalizeStartingBranch('  feature/example  ')).toBe('feature/example');
  });

  it('treats blank branches as missing without guessing main', () => {
    expect(normalizeStartingBranch('')).toBeNull();
    expect(normalizeStartingBranch('   ')).toBeNull();
    expect(normalizeStartingBranch(null)).toBeNull();
    expect(normalizeStartingBranch(undefined)).toBeNull();
  });

  it('preserves a recorded resolved-default value verbatim', () => {
    const resolved = resolveWorkflowSourceContext({
      repository: 'acme/repo',
      startingBranch: 'develop (default)',
    });
    expect(resolved.startingBranch).toBe('develop (default)');
    expect(resolved.startingBranchState).toBe('branch');
    expect(resolved.hasRepository).toBe(true);
  });

  it('marks a repository-backed execution with missing branch as not-recorded', () => {
    const resolved = resolveWorkflowSourceContext({
      repository: 'acme/repo',
      startingBranch: null,
    });
    expect(resolved.startingBranch).toBeNull();
    expect(resolved.startingBranchState).toBe('not-recorded');
  });

  it('marks a workflow without repository source as not-applicable', () => {
    const resolved = resolveWorkflowSourceContext({
      repository: null,
      startingBranch: null,
    });
    expect(resolved.startingBranchState).toBe('not-applicable');
    expect(resolved.hasRepository).toBe(false);
  });
});

describe('workflow detail source context presentation (MM-4228)', () => {
  const payload: BootPayload = { page: 'workflow-detail', apiBase: '/api' };
  let fetchSpy: MockInstance;
  let originalEventSource: typeof EventSource;

  const baseExecution = {
    taskId: 'test-123',
    workflowId: 'test-123',
    namespace: 'default',
    runId: 'run-1',
    source: 'temporal',
    workflowType: 'MoonMind.UserWorkflow',
    title: 'Source context workflow',
    summary: 'summary',
    status: 'running',
    state: 'executing',
    rawState: 'executing',
    temporalStatus: 'running',
    createdAt: '2026-09-10T00:00:00Z',
    updatedAt: '2026-09-10T00:00:01Z',
    actions: {},
    relatedRuns: [],
  };

  function mockExecutionFetch(execution: Record<string, unknown>) {
    fetchSpy.mockImplementation((input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes('/chat-binding')) {
        return Promise.resolve({
          ok: true,
          status: 200,
          json: async () => ({
            chatBindingId: 'cb-test',
            workflowId: 'test-123',
            chatUrl: '/omnigent-ui/workflow-chat/cb-test?embedded=1',
            apiBase: '/api/workflow-chat-bindings/cb-test/omnigent',
            state: 'available',
            readOnly: false,
            capabilities: { sendMessage: true },
          }),
        } as Response);
      }
      if (url.includes('/artifacts')) {
        return Promise.resolve({ ok: true, json: async () => ({ artifacts: [] }) } as Response);
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
    window.history.pushState({}, 'Test', '/workflows/test-123');
    window.sessionStorage.clear();
    window.localStorage.clear();
    originalEventSource = window.EventSource;
    (window as unknown as { EventSource: unknown }).EventSource = MockEventSource;
    fetchSpy = vi.spyOn(window, 'fetch');
  });

  afterEach(() => {
    cleanup();
    (window as unknown as { EventSource: unknown }).EventSource = originalEventSource;
    vi.restoreAllMocks();
  });

  function gitAndPublishGroup(): HTMLElement {
    const group = screen.getByText('Git & Publish').closest('.td-group');
    if (!(group instanceof HTMLElement)) throw new Error('Git & Publish group is missing');
    return group;
  }

  it.each([
    ['chat', '/workflows/test-123'],
    ['execution', '/workflows/test-123/execution'],
    ['evidence', '/workflows/test-123/evidence'],
  ])('keeps repository and starting branch off the %s tab', async (tab, path) => {
    window.history.pushState({}, tab, path);
    mockExecutionFetch({
      ...baseExecution,
      repository: 'acme/repo',
      startingBranch: 'feature/example',
    });
    renderWithClient(<WorkflowDetailPage payload={payload} />);
    await waitFor(() =>
      expect(
        screen.getByRole('link', { name: new RegExp(`^${tab}`, 'i') }).getAttribute('aria-current'),
      ).toBe('page'),
    );
    await screen.findByRole('heading', { level: 1, name: 'Source context workflow' });
    expect(screen.queryByText('feature/example')).toBeNull();
    expect(screen.queryByText('Starting Branch')).toBeNull();
    expect(screen.queryByText('acme/repo')).toBeNull();
  });

  it('shows repository and the recorded starting branch in Details → Git & Publish', async () => {
    window.history.pushState({}, 'Details', '/workflows/test-123/details');
    mockExecutionFetch({
      ...baseExecution,
      repository: 'acme/repo',
      startingBranch: 'feature/source',
      targetBranch: 'main',
      outputBranch: {
        name: 'agent/generated-work',
        url: null,
        headSha: null,
        baseBranch: null,
        intent: 'normal',
        status: 'published',
        evidenceRef: null,
      },
    });
    renderWithClient(<WorkflowDetailPage payload={payload} />);
    await screen.findByText('Git & Publish');
    const group = gitAndPublishGroup();
    expect(within(group).getByText('Repository')).toBeTruthy();
    expect(within(group).getByText('acme/repo')).toBeTruthy();
    expect(within(group).getByText('Starting Branch')).toBeTruthy();
    expect(within(group).getByText('feature/source')).toBeTruthy();
    // Target and published branches stay separate facts; neither substitutes
    // for the recorded source branch.
    expect(within(group).getByText('Target Branch')).toBeTruthy();
    expect(within(group).getByText('agent/generated-work')).toBeTruthy();
  });

  it('shows a long slash branch exactly once', async () => {
    const longBranch = `feature/${'long-segment-'.repeat(10)}end`;
    window.history.pushState({}, 'Details', '/workflows/test-123/details');
    mockExecutionFetch({ ...baseExecution, repository: 'acme/repo', startingBranch: longBranch });
    renderWithClient(<WorkflowDetailPage payload={payload} />);
    await screen.findByText('Git & Publish');
    expect(screen.getAllByText(longBranch)).toHaveLength(1);
    expect(screen.getByText(longBranch).getAttribute('title')).toBe(longBranch);
  });

  it('renders an explicit Not recorded state for missing branch metadata', async () => {
    window.history.pushState({}, 'Details', '/workflows/test-123/details');
    mockExecutionFetch({ ...baseExecution, repository: 'acme/repo', startingBranch: null });
    renderWithClient(<WorkflowDetailPage payload={payload} />);
    await screen.findByText('Git & Publish');
    const group = gitAndPublishGroup();
    expect(within(group).getByText('Not recorded')).toBeTruthy();
    expect(group.textContent).not.toContain('main');
  });

  it('handles non-repository workflows intentionally in Details', async () => {
    window.history.pushState({}, 'Details', '/workflows/test-123/details');
    mockExecutionFetch({ ...baseExecution, repository: null, startingBranch: null });
    renderWithClient(<WorkflowDetailPage payload={payload} />);
    await screen.findByText('Git & Publish');
    expect(within(gitAndPublishGroup()).getByText('Not applicable')).toBeTruthy();
    expect(screen.queryByText('Repository')).toBeNull();
  });

  it('keeps schedule provenance and workflow inputs off the Chat tab', async () => {
    window.history.pushState({}, 'Chat', '/workflows/test-123');
    mockExecutionFetch({
      ...baseExecution,
      recurrence: { definitionId: 'schedule-alpha', href: '/schedules/schedule-alpha' },
      taskInstructions: 'Inspect the repository.',
    });
    renderWithClient(<WorkflowDetailPage payload={payload} />);
    await screen.findByTitle('Source context workflow — Omnigent chat');
    expect(screen.queryByRole('link', { name: 'schedule-alpha' })).toBeNull();
    expect(screen.queryByText(/Created by/)).toBeNull();
    expect(screen.queryByRole('button', { name: /Workflow Inputs/ })).toBeNull();
  });

  it('shows schedule provenance and workflow inputs on the Details tab', async () => {
    window.history.pushState({}, 'Details', '/workflows/test-123/details');
    mockExecutionFetch({
      ...baseExecution,
      recurrence: { definitionId: 'schedule-alpha', href: '/schedules/schedule-alpha' },
      taskInstructions: 'Inspect the repository.',
    });
    renderWithClient(<WorkflowDetailPage payload={payload} />);
    await screen.findByText('Lifecycle');
    expect(screen.getByText('Created by').closest('div')?.textContent).toBe('Created bySchedule schedule-alpha');
    expect(screen.getByRole('link', { name: 'schedule-alpha' }).getAttribute('href')).toBe('/schedules/schedule-alpha');
    expect(screen.queryByText('Inspect the repository.')).toBeNull();
    fireEvent.click(screen.getByRole('button', { name: 'Show Workflow Inputs' }));
    expect(screen.getByText('Inspect the repository.')).toBeTruthy();
  });

  it('names the creating schedule when its definition is readable', async () => {
    window.history.pushState({}, 'Details', '/workflows/test-123/details');
    const execution = {
      ...baseExecution,
      recurrence: { definitionId: 'schedule-alpha', href: '/schedules/schedule-alpha' },
    };
    mockExecutionFetch(execution);
    const fallback = fetchSpy.getMockImplementation();
    fetchSpy.mockImplementation((input: RequestInfo | URL, init?: RequestInit) =>
      String(input) === '/api/recurring-workflows/schedule-alpha'
        ? Promise.resolve({ ok: true, json: async () => ({ id: 'schedule-alpha', name: 'Nightly triage' }) } as Response)
        : fallback!(input, init),
    );
    renderWithClient(<WorkflowDetailPage payload={payload} />);
    const link = await screen.findByRole('link', { name: 'Nightly triage' });
    expect(link.getAttribute('href')).toBe('/schedules/schedule-alpha');
    expect(screen.getByText('Created by').closest('div')?.textContent).toBe('Created bySchedule Nightly triage');
  });

  it('serves the legacy /overview URL as the Details tab', async () => {
    window.history.pushState({}, 'Overview', '/workflows/test-123/overview');
    mockExecutionFetch({ ...baseExecution, repository: 'acme/repo', startingBranch: 'feature/example' });
    renderWithClient(<WorkflowDetailPage payload={payload} />);
    expect((await screen.findByRole('link', { name: 'Details' })).getAttribute('aria-current')).toBe('page');
    await screen.findByText('Git & Publish');
    expect(within(gitAndPublishGroup()).getByText('feature/example')).toBeTruthy();
  });
});
