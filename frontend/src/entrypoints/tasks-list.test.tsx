import { beforeEach, describe, expect, it, vi, type MockInstance } from 'vitest';
import { fireEvent, screen, waitFor } from '@testing-library/react';

import { BootPayload } from '../boot/parseBootPayload';
import { renderWithClient } from '../utils/test-utils';
import { TasksListPage } from './tasks-list';
import '../styles/mission-control.css';

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
            startedAt: '2026-03-28T00:00:01Z',
            createdAt: '2026-03-28T00:00:00Z',
          },
        ],
      }),
    } as Response);
  });

  it('announces the current sort state on table headers', async () => {
    renderWithClient(<TasksListPage payload={mockPayload} />);

    await screen.findAllByText('Example task');

    const scheduledHeaderButton = await screen.findByRole('button', {
      name: /Scheduled\. Sorted descending\. Activate to sort ascending\./i,
    });
    expect(scheduledHeaderButton.closest('th')?.getAttribute('aria-sort')).toBe('descending');

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

  it('keeps started time out of the task list presentation', async () => {
    renderWithClient(<TasksListPage payload={mockPayload} />);

    await screen.findAllByText('Example task');

    expect(screen.queryByRole('button', { name: /^Started\./i })).toBeNull();
    expect(screen.queryByText('Started')).toBeNull();
  });

  it('orders scheduled rows by latest scheduled time before created time by default', async () => {
    fetchSpy.mockResolvedValue({
      ok: true,
      json: async () => ({
        items: [
          {
            taskId: 'task-late',
            source: 'temporal',
            title: 'Late scheduled task',
            status: 'queued',
            state: 'scheduled',
            rawState: 'scheduled',
            scheduledFor: '2026-04-15T18:00:00Z',
            startedAt: null,
            createdAt: '2026-04-15T01:00:00Z',
          },
          {
            taskId: 'task-early',
            source: 'temporal',
            title: 'Early scheduled task',
            status: 'queued',
            state: 'scheduled',
            rawState: 'scheduled',
            scheduledFor: '2026-04-15T09:00:00Z',
            startedAt: null,
            createdAt: '2026-04-15T02:00:00Z',
          },
        ],
      }),
    } as Response);

    renderWithClient(<TasksListPage payload={mockPayload} />);

    const earlyLink = await screen.findByRole('link', { name: 'Early scheduled task' });
    const lateLink = await screen.findByRole('link', { name: 'Late scheduled task' });
    expect(
      lateLink.compareDocumentPosition(earlyLink) & Node.DOCUMENT_POSITION_FOLLOWING,
    ).toBeTruthy();
    expect((await screen.findAllByText('—')).length).toBeGreaterThan(0);
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

  it('labels the lifecycle filter as status and exposes canonical status options', async () => {
    renderWithClient(<TasksListPage payload={mockPayload} />);

    await screen.findAllByText('Example task');

    const statusFilter = screen.getByLabelText('Status') as HTMLSelectElement;
    const options = Array.from(statusFilter.options).map((option) => option.value);

    expect(options).toEqual([
      '',
      'scheduled',
      'initializing',
      'waiting_on_dependencies',
      'planning',
      'awaiting_slot',
      'executing',
      'proposals',
      'awaiting_external',
      'finalizing',
      'completed',
      'failed',
      'canceled',
    ]);
    expect(options).toContain('completed');
    expect(options).not.toContain('succeeded');

    const baselineCalls = fetchSpy.mock.calls.length;
    fireEvent.change(statusFilter, { target: { value: 'completed' } });

    await waitFor(() => {
      expect(fetchSpy.mock.calls.length).toBe(baselineCalls + 1);
    });
    expect(fetchSpy.mock.calls.at(-1)?.[0]).toBe('/api/executions?source=temporal&pageSize=50&state=completed');
  });

  it('renders pagination as arrow buttons beside the table summary', async () => {
    fetchSpy.mockResolvedValue({
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
        nextPageToken: 'next-token',
        count: 21,
      }),
    } as Response);

    renderWithClient(<TasksListPage payload={mockPayload} />);

    expect(await screen.findByText('Page 1 · 1-1 · 21')).toBeTruthy();
    expect(screen.getByRole('button', { name: 'Previous page' })).toBeTruthy();
    expect(screen.getByRole('button', { name: 'Next page' })).toBeTruthy();
  });

  it('uses the Mission Control control deck and data slab composition', async () => {
    renderWithClient(<TasksListPage payload={mockPayload} />);

    await screen.findAllByText('Example task');

    const controlDeck = document.querySelector<HTMLElement>('.task-list-control-deck.panel--controls');
    const dataSlab = document.querySelector<HTMLElement>('.task-list-data-slab.panel--data');
    const tableWrapper = dataSlab?.querySelector<HTMLElement>('.queue-table-wrapper[data-layout="table"]');
    const scheduledHeader = tableWrapper?.querySelector<HTMLElement>('th');

    expect(controlDeck).toBeTruthy();
    expect(controlDeck?.querySelector('form.task-list-control-grid')).toBeTruthy();
    expect(
      controlDeck?.querySelector('.task-list-utility-cluster')?.contains(screen.getByLabelText('Live updates')),
    ).toBe(true);
    expect(screen.getByText('Showing all task executions.')).toBeTruthy();
    expect(dataSlab).toBeTruthy();
    expect(dataSlab?.querySelector('.queue-results-toolbar')).toBeTruthy();
    expect(tableWrapper).toBeTruthy();
    expect(getComputedStyle(tableWrapper as HTMLElement).overflow).toBe('auto');
    expect(getComputedStyle(scheduledHeader as HTMLElement).position).toBe('sticky');
  });

  it('shows active filter chips and clears filters from the control deck', async () => {
    renderWithClient(<TasksListPage payload={mockPayload} />);

    await screen.findAllByText('Example task');
    fireEvent.change(screen.getByLabelText('Status'), { target: { value: 'completed' } });
    fireEvent.change(screen.getByLabelText('Repository'), { target: { value: 'owner/repo' } });

    await waitFor(() => {
      const activeFilterText = document.querySelector('.task-list-filter-chips')?.textContent || '';
      expect(activeFilterText).toContain('completed');
      expect(activeFilterText).toContain('owner/repo');
    });

    fireEvent.click(screen.getByRole('button', { name: 'Clear filters' }));

    await waitFor(() => {
      expect((screen.getByLabelText('Status') as HTMLSelectElement).value).toBe('');
      expect((screen.getByLabelText('Repository') as HTMLInputElement).value).toBe('');
    });
  });

  it('marks mobile card details links as the only full-width card action', async () => {
    renderWithClient(<TasksListPage payload={mockPayload} />);

    const detailsLink = await screen.findByRole('button', { name: 'View details' });

    expect(detailsLink.classList.contains('queue-card-details-action')).toBe(true);
    expect(detailsLink.closest('.queue-card-actions')).toBeTruthy();
  });

  it('keeps mobile task cards constrained to the viewport width', async () => {
    renderWithClient(<TasksListPage payload={mockPayload} />);

    const detailsLink = await screen.findByRole('button', { name: 'View details' });
    const card = detailsLink.closest<HTMLElement>('.queue-card');
    const fields = card?.querySelector<HTMLElement>('.queue-card-fields');
    const fieldValue = fields?.querySelector<HTMLElement>('dd');
    const taskId = card?.querySelector<HTMLElement>('code');

    expect(card).not.toBeNull();
    expect(fields).not.toBeNull();
    expect(fieldValue).not.toBeNull();
    expect(taskId).not.toBeNull();

    expect(getComputedStyle(card as HTMLElement).minWidth).toMatch(/^0(px)?$/);
    expect(getComputedStyle(card as HTMLElement).width).toBe('100%');
    expect(getComputedStyle(fields as HTMLElement).display).toBe('grid');
    expect(getComputedStyle(fieldValue as HTMLElement).minWidth).toMatch(/^0(px)?$/);
    expect(getComputedStyle(fieldValue as HTMLElement).overflowWrap).toBe('anywhere');
    expect(getComputedStyle(taskId as HTMLElement).overflowWrap).toBe('anywhere');
  });

  it('keeps the previous-page button enabled on empty pages after pagination', async () => {
    fetchSpy.mockImplementation((input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes('nextPageToken=next-token')) {
        return Promise.resolve({
          ok: true,
          json: async () => ({
            items: [],
            count: 21,
          }),
        } as Response);
      }
      return Promise.resolve({
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
          nextPageToken: 'next-token',
          count: 21,
        }),
      } as Response);
    });

    renderWithClient(<TasksListPage payload={mockPayload} />);

    const nextButton = await screen.findByRole('button', { name: 'Next page' });
    fireEvent.click(nextButton);

    await waitFor(() => {
      expect(screen.getByText('No tasks found for the current filters.')).toBeTruthy();
    });

    expect(screen.getByRole('button', { name: 'Previous page' }).getAttribute('disabled')).toBeNull();
  });

  it('shows blocked dependency summaries for waiting dependency rows', async () => {
    fetchSpy.mockResolvedValue({
      ok: true,
      json: async () => ({
        items: [
          {
            taskId: 'task-blocked',
            source: 'temporal',
            title: 'Blocked task',
            status: 'waiting',
            state: 'waiting_on_dependencies',
            rawState: 'waiting_on_dependencies',
            dependsOn: ['mm:dep-1', 'mm:dep-2'],
            blockedOnDependencies: true,
            createdAt: '2026-03-28T00:00:00Z',
          },
        ],
      }),
    } as Response);

    renderWithClient(<TasksListPage payload={mockPayload} />);

    expect((await screen.findAllByText('Blocked by 2 prerequisites'))[0]).toBeTruthy();
  });

  it('renders human-readable runtime labels in list rows', async () => {
    fetchSpy.mockResolvedValue({
      ok: true,
      json: async () => ({
        items: [
          {
            taskId: 'task-321',
            source: 'temporal',
            targetRuntime: 'codex_cli',
            title: 'Readable runtime task',
            status: 'running',
            state: 'executing',
            rawState: 'executing',
            createdAt: '2026-03-28T00:00:00Z',
          },
        ],
      }),
    } as Response);

    renderWithClient(<TasksListPage payload={mockPayload} />);

    expect((await screen.findAllByText('Readable runtime task'))[0]).toBeTruthy();
    expect((await screen.findAllByText('Codex CLI'))[0]).toBeTruthy();
  });

  it('renders the desktop table with constrained columns for long workflow IDs', async () => {
    const longWorkflowId =
      'mm:run:child-workflow:01HTESTVERYVERYLONGCHILDWORKFLOWIDENTIFIERWITHOUTBREAKS';
    fetchSpy.mockResolvedValue({
      ok: true,
      json: async () => ({
        items: [
          {
            taskId: longWorkflowId,
            source: 'temporal',
            targetRuntime: 'codex_cli',
            targetSkill: 'pr-resolver',
            repository: 'MoonLadderStudios/MoonMind',
            title: 'Long child workflow id task',
            status: 'running',
            state: 'executing',
            rawState: 'executing',
            createdAt: '2026-03-28T00:00:00Z',
          },
        ],
      }),
    } as Response);

    renderWithClient(<TasksListPage payload={mockPayload} />);

    const titleMatches = await screen.findAllByText('Long child workflow id task');
    const table = titleMatches
      .map((element) => element.closest('table'))
      .find((candidate): candidate is HTMLTableElement => Boolean(candidate));
    expect(table?.querySelectorAll('col.queue-table-column-id')).toHaveLength(1);
    expect(table?.querySelectorAll('col.queue-table-column-date')).toHaveLength(3);
    const idCell = table?.querySelector('td.queue-table-cell-id');
    expect(idCell?.textContent).toBe(longWorkflowId);
  });
});
