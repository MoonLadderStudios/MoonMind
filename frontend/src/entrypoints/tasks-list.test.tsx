import { beforeEach, describe, expect, it, vi, type MockInstance } from 'vitest';
import { fireEvent, screen, waitFor } from '@testing-library/react';

import { BootPayload } from '../boot/parseBootPayload';
import { renderWithClient } from '../utils/test-utils';
import { TasksListPage } from './tasks-list';

describe('Tasks List Entrypoint', () => {
  const mockPayload: BootPayload = {
    page: 'tasks-list',
    apiBase: '/api',
  };

  let fetchSpy: MockInstance;

  beforeEach(() => {
    window.history.pushState({}, 'Test', '/tasks');
    fetchSpy = vi.spyOn(window, 'fetch').mockResolvedValue({
      ok: true,
      json: async () => ({
        items: [
          {
            taskId: 'task-123',
            source: 'temporal',
            title: 'Example task',
            status: 'completed',
            state: 'succeeded',
            rawState: 'succeeded',
            createdAt: '2026-03-28T00:00:00Z',
          },
        ],
      }),
    } as Response);
  });

  it('announces the current sort state on table headers', async () => {
    renderWithClient(<TasksListPage payload={mockPayload} />);

    const createdHeaderButton = await screen.findByRole('button', {
      name: /Created\. Sorted descending\. Activate to sort ascending\./i,
    });
    expect(createdHeaderButton.closest('th')?.getAttribute('aria-sort')).toBe('descending');

    const runtimeHeaderButton = screen.getByRole('button', {
      name: /Runtime\. Not sorted\. Activate to sort ascending\./i,
    });
    expect(runtimeHeaderButton.closest('th')?.getAttribute('aria-sort')).toBe('none');

    fireEvent.click(runtimeHeaderButton);

    await waitFor(() => {
      expect(runtimeHeaderButton.closest('th')?.getAttribute('aria-sort')).toBe('ascending');
      expect(runtimeHeaderButton.getAttribute('aria-label')).toBe(
        'Runtime. Sorted ascending. Activate to sort descending.',
      );
    });
  });

  it('reuses the trimmed filters for both the request and the query key', async () => {
    renderWithClient(<TasksListPage payload={mockPayload} />);

    await screen.findAllByText('Example task');
    const baselineCalls = fetchSpy.mock.calls.length;

    fireEvent.change(screen.getByLabelText('Repository'), {
      target: { value: 'owner/repo' },
    });

    await waitFor(() => {
      expect(fetchSpy.mock.calls.length).toBe(baselineCalls + 1);
    });
    expect(fetchSpy.mock.calls.at(-1)?.[0]).toBe('/api/executions?source=temporal&pageSize=50&repo=owner%2Frepo');

    fireEvent.change(screen.getByLabelText('Repository'), {
      target: { value: 'owner/repo ' },
    });

    const repositoryInput = screen.getByLabelText('Repository') as HTMLInputElement;
    await waitFor(() => {
      expect(repositoryInput.value).toBe('owner/repo ');
    });
    expect(fetchSpy.mock.calls.length).toBe(baselineCalls + 1);
  });
});
