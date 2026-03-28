import { describe, it, expect, vi, beforeEach } from 'vitest';
import { screen, waitFor } from '@testing-library/react';
import { renderWithClient } from '../utils/test-utils';
import { TaskDetailPage } from './task-detail';
import { BootPayload } from '../boot/parseBootPayload';
import { MockInstance } from 'vitest';

describe('Task Detail Entrypoint', () => {
  const mockPayload: BootPayload = {
    page: 'task-detail',
    apiBase: '/api/v1',
  };

  let fetchSpy: MockInstance;

  beforeEach(() => {
    // Reset path
    window.history.pushState({}, 'Test', '/tasks/test-123');
    fetchSpy = vi.spyOn(window, 'fetch');
  });

  it('renders loading state initially', () => {
    fetchSpy.mockImplementation(() => new Promise(() => {})); // Never resolves
    renderWithClient(<TaskDetailPage payload={mockPayload} />);
    expect(screen.getByText(/Loading task details/i)).toBeTruthy();
  });

  it('renders task details on successful fetch', async () => {
    const mockTask = {
      taskId: 'test-123',
      source: 'temporal',
      sourceLabel: 'Temporal API',
      status: 'completed',
      createdAt: '2026-03-28T00:00:00Z',
    };

    fetchSpy.mockResolvedValueOnce({
      ok: true,
      json: async () => mockTask,
    } as Response);

    renderWithClient(<TaskDetailPage payload={mockPayload} />);

    await waitFor(() => {
      expect(screen.getByText('test-123')).toBeTruthy();
      expect(screen.getByText('completed')).toBeTruthy();
      expect(screen.getByText('Temporal API')).toBeTruthy();
    });

    expect(fetchSpy).toHaveBeenCalledWith('/api/v1/executions/test-123');
  });

  it('renders error state on failed fetch', async () => {
    fetchSpy.mockResolvedValueOnce({
      ok: false,
      statusText: 'Not Found',
      json: async () => ({}),
    } as Response);

    renderWithClient(<TaskDetailPage payload={mockPayload} />);

    await waitFor(() => {
      expect(screen.getByText(/Failed to fetch task test-123: Not Found/i)).toBeTruthy();
    });
  });
});
