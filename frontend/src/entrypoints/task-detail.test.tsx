import { describe, it, expect, vi, beforeEach } from 'vitest';
import { screen, waitFor } from '@testing-library/react';
import { renderWithClient } from '../utils/test-utils';
import { TaskDetailPage } from './task-detail';
import { BootPayload } from '../boot/parseBootPayload';
import { MockInstance } from 'vitest';

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
