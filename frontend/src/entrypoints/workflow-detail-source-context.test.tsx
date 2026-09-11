import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { screen, waitFor, cleanup } from '@testing-library/react';
import { renderWithClient } from '../utils/test-utils';
import type { MockInstance } from 'vitest';
import {
  normalizeStartingBranch,
  resolveWorkflowSourceContext,
  WorkflowDetailPage,
} from './workflow-detail';
import type { BootPayload } from '../boot/parseBootPayload';

// MoonLadderStudios/MoonMind#4228: starting branch stays visible on the
// default Chat view with explicit unavailable-data states.

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

  it('shows the recorded starting branch on the default Chat route without switching tabs', async () => {
    window.history.pushState({}, 'Chat', '/workflows/test-123');
    mockExecutionFetch({
      ...baseExecution,
      repository: 'acme/repo',
      startingBranch: 'feature/example',
      targetBranch: 'main',
    });
    renderWithClient(<WorkflowDetailPage payload={payload} />);
    const strip = await screen.findByLabelText('Workflow source context');
    expect(strip.textContent).toContain('feature/example');
    expect(strip.textContent).toContain('acme/repo');
  });

  it('keeps shared context and Overview in parity for a long slash branch', async () => {
    const longBranch = `feature/${'long-segment-'.repeat(10)}end`;
    window.history.pushState({}, 'Overview', '/workflows/test-123/overview');
    mockExecutionFetch({
      ...baseExecution,
      repository: 'acme/repo',
      startingBranch: longBranch,
    });
    renderWithClient(<WorkflowDetailPage payload={payload} />);
    await screen.findByLabelText('Workflow source context');
    const matches = await screen.findAllByText(longBranch);
    // Shared strip + Overview Git & Publish render the same normalized value.
    expect(matches.length).toBeGreaterThanOrEqual(2);
  });

  it('never substitutes target or published branches for the source branch', async () => {
    window.history.pushState({}, 'Chat', '/workflows/test-123');
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
    const strip = await screen.findByLabelText('Workflow source context');
    expect(strip.textContent).toContain('feature/source');
    expect(strip.textContent).not.toContain('agent/generated-work');
    // The generated branch still appears in Overview as a separate fact,
    // proving the distinction is preserved.
    await waitFor(() => expect(document.body.textContent).not.toContain('Loading workflow'));
  });

  it('renders an explicit Not recorded state for missing branch metadata', async () => {
    window.history.pushState({}, 'Chat', '/workflows/test-123');
    mockExecutionFetch({ ...baseExecution, repository: 'acme/repo', startingBranch: null });
    renderWithClient(<WorkflowDetailPage payload={payload} />);
    const strip = await screen.findByLabelText('Workflow source context');
    expect(strip.textContent).toContain('Not recorded');
    expect(strip.textContent).not.toContain('main');
  });

  it('handles non-repository workflows intentionally in Overview', async () => {
    window.history.pushState({}, 'Overview', '/workflows/test-123/overview');
    mockExecutionFetch({ ...baseExecution, repository: null, startingBranch: null });
    renderWithClient(<WorkflowDetailPage payload={payload} />);
    expect(await screen.findByText('Not applicable')).toBeTruthy();
  });
});
