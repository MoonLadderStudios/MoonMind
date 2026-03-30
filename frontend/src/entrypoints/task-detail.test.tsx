import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { screen, waitFor, act } from '@testing-library/react';
import { renderWithClient } from '../utils/test-utils';
import { TaskDetailPage } from './task-detail';
import { BootPayload } from '../boot/parseBootPayload';
import { MockInstance } from 'vitest';

// ---------------------------------------------------------------------------
// Minimal EventSource mock
// ---------------------------------------------------------------------------

type LogChunkListener = (event: MessageEvent) => void;

class MockEventSource {
  static instances: MockEventSource[] = [];
  static reset() {
    MockEventSource.instances = [];
  }

  onopen: ((event: Event) => void) | null = null;
  onerror: ((event: Event) => void) | null = null;
  private listeners = new Map<string, LogChunkListener[]>();
  closed = false;

  constructor(public url: string, public options?: EventSourceInit) {
    MockEventSource.instances.push(this);
  }

  addEventListener(type: string, listener: LogChunkListener) {
    const existing = this.listeners.get(type) ?? [];
    this.listeners.set(type, [...existing, listener]);
  }

  close() {
    this.closed = true;
  }

  // Test helpers
  triggerOpen() {
    this.onopen?.(new Event('open'));
  }

  triggerLogChunk(data: { sequence: number; stream: string; text: string }) {
    const event = new MessageEvent('log_chunk', { data: JSON.stringify(data) });
    for (const listener of this.listeners.get('log_chunk') ?? []) listener(event);
  }

  triggerError() {
    this.onerror?.(new Event('error'));
  }
}

describe('Task Detail Entrypoint', () => {
  const mockPayload: BootPayload = {
    page: 'task-detail',
    apiBase: '/api',
  };

  let fetchSpy: MockInstance;

  beforeEach(() => {
    window.history.pushState({}, 'Test', '/tasks/test-123?source=temporal');
    fetchSpy = vi.spyOn(window, 'fetch');
  });

  it('renders loading state initially', () => {
    fetchSpy.mockImplementation(() => new Promise(() => {}));
    renderWithClient(<TaskDetailPage payload={mockPayload} />);
    expect(screen.getByText(/Loading task/i)).toBeTruthy();
  });

  it('renders task details on successful fetch', async () => {
    const mockExecution = {
      taskId: 'test-123',
      workflowId: 'test-123',
      namespace: 'default',
      temporalRunId: '01-run',
      runId: '01-run',
      source: 'temporal',
      workflowType: 'MoonMind.Run',
      entry: 'run',
      title: 'Example task',
      summary: 'Did work',
      status: 'completed',
      state: 'succeeded',
      rawState: 'succeeded',
      temporalStatus: 'completed',
      closeStatus: 'COMPLETED',
      createdAt: '2026-03-28T00:00:00Z',
      startedAt: '2026-03-28T00:00:01Z',
      updatedAt: '2026-03-28T00:00:02Z',
      closedAt: '2026-03-28T00:00:03Z',
      actions: { canSetTitle: true, canCancel: false, canRerun: false },
    };

    fetchSpy.mockImplementation((input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes('/artifacts')) {
        return Promise.resolve({
          ok: true,
          json: async () => ({ artifacts: [] }),
        } as Response);
      }
      return Promise.resolve({
        ok: true,
        json: async () => mockExecution,
      } as Response);
    });

    renderWithClient(<TaskDetailPage payload={mockPayload} />);

    await waitFor(() => {
      expect(screen.getByText('Example task')).toBeTruthy();
      expect(screen.getByText('Did work')).toBeTruthy();
    });

    expect(fetchSpy).toHaveBeenCalledWith('/api/executions/test-123?source=temporal');
  });

  it('renders artifact rows from snake_case temporal artifact payloads', async () => {
    const mockExecution = {
      taskId: 'test-123',
      workflowId: 'test-123',
      namespace: 'default',
      temporalRunId: '01-run',
      runId: '01-run',
      source: 'temporal',
      title: 'Artifact task',
      summary: 'Artifact payload',
      status: 'running',
      state: 'executing',
      createdAt: '2026-03-28T00:00:00Z',
      updatedAt: '2026-03-28T00:00:02Z',
      actions: {},
    };

    fetchSpy.mockImplementation((input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes('/artifacts')) {
        return Promise.resolve({
          ok: true,
          json: async () => ({
            artifacts: [
              {
                artifact_id: 'art-001',
                content_type: 'application/json',
                size_bytes: 512,
                status: 'complete',
              },
            ],
          }),
        } as Response);
      }
      return Promise.resolve({
        ok: true,
        json: async () => mockExecution,
      } as Response);
    });

    renderWithClient(<TaskDetailPage payload={mockPayload} />);

    await waitFor(() => {
      expect(screen.getByText('Artifact task')).toBeTruthy();
      expect(screen.getByText('art-001')).toBeTruthy();
      expect(screen.getByText('512')).toBeTruthy();
      expect(screen.getByText('complete')).toBeTruthy();
      expect(screen.getByRole('link', { name: /Download/i }).getAttribute('href')).toBe(
        '/api/artifacts/art-001/download'
      );
    });
  });

  it('renders error state on failed fetch', async () => {
    fetchSpy.mockResolvedValueOnce({
      ok: false,
      statusText: 'Not Found',
      json: async () => ({}),
    } as Response);

    renderWithClient(<TaskDetailPage payload={mockPayload} />);

    await waitFor(() => {
      expect(screen.getByText(/Failed to fetch task: Not Found/i)).toBeTruthy();
    });
  });

  it('decodes encoded task ids from the route before fetching', async () => {
    window.history.pushState({}, 'Encoded Test', '/tasks/mm%3Atest-123?source=temporal');

    fetchSpy.mockImplementation((input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes('/artifacts')) {
        return Promise.resolve({
          ok: true,
          json: async () => ({ artifacts: [] }),
        } as Response);
      }
      return Promise.resolve({
        ok: true,
        json: async () => ({
          taskId: 'mm:test-123',
          workflowId: 'mm:test-123',
          namespace: 'default',
          temporalRunId: '01-run',
          runId: '01-run',
          source: 'temporal',
          title: 'Encoded task',
          summary: 'Decoded route id',
          status: 'completed',
          state: 'succeeded',
          createdAt: '2026-03-28T00:00:00Z',
          updatedAt: '2026-03-28T00:00:02Z',
          actions: {},
        }),
      } as Response);
    });

    renderWithClient(<TaskDetailPage payload={mockPayload} />);

    await waitFor(() => {
      expect(screen.getByText('Encoded task')).toBeTruthy();
      expect(screen.getByText('Task mm:test-123')).toBeTruthy();
    });

    expect(fetchSpy).toHaveBeenCalledWith('/api/executions/mm%3Atest-123?source=temporal');
  });

  it('shows waiting message when no taskRunId is present', async () => {
    const mockExecution = {
      taskId: 'test-123',
      workflowId: 'test-123',
      namespace: 'default',
      temporalRunId: 'non-uuid-run',
      runId: 'non-uuid-run',
      source: 'temporal',
      title: 'Running task',
      summary: 'In progress',
      status: 'running',
      state: 'executing',
      createdAt: '2026-03-28T00:00:00Z',
      updatedAt: '2026-03-28T00:00:02Z',
      actions: {},
    };

    fetchSpy.mockImplementation((input: RequestInfo | URL) => {
      if (String(input).includes('/artifacts')) {
        return Promise.resolve({ ok: true, json: async () => ({ artifacts: [] }) } as Response);
      }
      return Promise.resolve({ ok: true, json: async () => mockExecution } as Response);
    });

    renderWithClient(<TaskDetailPage payload={mockPayload} />);

    await waitFor(() => {
      expect(
        screen.getByText(/Live log tailing requires a task run id/i),
      ).toBeTruthy();
    });
  });

  it('renders artifact download link using explicit downloadUrl when present', async () => {
    const mockExecution = {
      taskId: 'test-123',
      workflowId: 'test-123',
      namespace: 'default',
      temporalRunId: '01-run',
      runId: '01-run',
      source: 'temporal',
      title: 'Artifact task with download_url',
      summary: 'Artifact payload',
      status: 'running',
      state: 'executing',
      createdAt: '2026-03-28T00:00:00Z',
      updatedAt: '2026-03-28T00:00:02Z',
      actions: {},
    };

    fetchSpy.mockImplementation((input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes('/artifacts')) {
        return Promise.resolve({
          ok: true,
          json: async () => ({
            artifacts: [
              {
                artifact_id: 'art-with-url',
                content_type: 'application/json',
                size_bytes: 1024,
                status: 'complete',
                download_url: 'https://external-storage.com/art-with-url',
              },
            ],
          }),
        } as Response);
      }
      return Promise.resolve({
        ok: true,
        json: async () => mockExecution,
      } as Response);
    });

    renderWithClient(<TaskDetailPage payload={mockPayload} />);

    await waitFor(() => {
      expect(screen.getByText('Artifact task with download_url')).toBeTruthy();
      expect(screen.getByText('art-with-url')).toBeTruthy();
      expect(screen.getByRole('link', { name: /Download/i }).getAttribute('href')).toBe(
        'https://external-storage.com/art-with-url'
      );
    });
  });
});

// ---------------------------------------------------------------------------
// LiveLogsPanel — full lifecycle tests via TaskDetailPage
// ---------------------------------------------------------------------------

describe('LiveLogsPanel', () => {
  const mockPayload: BootPayload = { page: 'task-detail', apiBase: '/api' };

  const activeExecution = {
    taskId: 'wf-1',
    workflowId: 'wf-1',
    namespace: 'default',
    temporalRunId: '01-run',
    runId: '01-run',
    source: 'temporal',
    title: 'Active task',
    summary: 'Running',
    status: 'running',
    state: 'executing',
    rawState: 'executing',
    taskRunId: '550e8400-e29b-41d4-a716-446655440000',
    createdAt: '2026-03-28T00:00:00Z',
    updatedAt: '2026-03-28T00:00:02Z',
    actions: {},
  };

  const terminalExecution = {
    ...activeExecution,
    status: 'completed',
    state: 'succeeded',
    rawState: 'succeeded',
  };

  let fetchSpy: MockInstance;
  let originalEventSource: typeof EventSource;

  beforeEach(() => {
    window.history.pushState({}, 'Test', '/tasks/wf-1?source=temporal');
    fetchSpy = vi.spyOn(window, 'fetch');
    MockEventSource.reset();
    originalEventSource = window.EventSource;
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    (window as any).EventSource = MockEventSource;
    Element.prototype.scrollIntoView = vi.fn();
  });

  afterEach(() => {
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    (window as any).EventSource = originalEventSource;
  });

  function mockFetchWith(execution: object) {
    fetchSpy.mockImplementation((input: RequestInfo | URL) => {
      if (String(input).includes('/artifacts')) {
        return Promise.resolve({ ok: true, json: async () => ({ artifacts: [] }) } as Response);
      }
      return Promise.resolve({ ok: true, json: async () => execution } as Response);
    });
  }

  it('shows Connecting then Connected status', async () => {
    mockFetchWith(activeExecution);
    renderWithClient(<TaskDetailPage payload={mockPayload} />);

    await waitFor(() => {
      expect(MockEventSource.instances.length).toBeGreaterThan(0);
    });

    const es = MockEventSource.instances[0]!;
    expect(screen.getByText(/Connecting…/)).toBeTruthy();

    act(() => es.triggerOpen());
    await waitFor(() => expect(screen.getByText(/Connected/)).toBeTruthy());
  });

  it('appends log_chunk text to the log output', async () => {
    mockFetchWith(activeExecution);
    renderWithClient(<TaskDetailPage payload={mockPayload} />);

    await waitFor(() => expect(MockEventSource.instances.length).toBeGreaterThan(0));
    const es = MockEventSource.instances[0]!;

    act(() => es.triggerOpen());
    act(() => es.triggerLogChunk({ sequence: 0, stream: 'stdout', text: 'hello world\n' }));
    act(() => es.triggerLogChunk({ sequence: 1, stream: 'stdout', text: 'second line\n' }));

    await waitFor(() => {
      expect(screen.getByText(/hello world/)).toBeTruthy();
      expect(screen.getByText(/second line/)).toBeTruthy();
    });
  });

  it('closes stream and shows Stream ended when task transitions to terminal', async () => {
    let currentExecution = activeExecution;
    fetchSpy.mockImplementation((input: RequestInfo | URL) => {
      if (String(input).includes('/artifacts')) {
        return Promise.resolve({ ok: true, json: async () => ({ artifacts: [] }) } as Response);
      }
      return Promise.resolve({ ok: true, json: async () => currentExecution } as Response);
    });

    renderWithClient(<TaskDetailPage payload={mockPayload} />);
    await waitFor(() => expect(MockEventSource.instances.length).toBeGreaterThan(0));
    const es = MockEventSource.instances[0]!;
    act(() => es.triggerOpen());

    // Transition to terminal state on next poll (default refetch interval is 2s)
    currentExecution = terminalExecution;
    await waitFor(() => expect(screen.getByText(/Stream ended/)).toBeTruthy(), { timeout: 5000 });
    expect(es.closed).toBe(true);
  });

  it('shows Disconnected when onerror fires on a non-terminal task', async () => {
    mockFetchWith(activeExecution);
    renderWithClient(<TaskDetailPage payload={mockPayload} />);

    await waitFor(() => expect(MockEventSource.instances.length).toBeGreaterThan(0));
    const es = MockEventSource.instances[0]!;
    act(() => es.triggerOpen());
    act(() => es.triggerError());

    await waitFor(() => expect(screen.getByText(/Disconnected/)).toBeTruthy());
  });
});
