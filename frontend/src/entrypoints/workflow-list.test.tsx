import { beforeEach, describe, expect, it, vi, type MockInstance } from 'vitest';
import { fireEvent, screen, waitFor, within } from '@testing-library/react';

import { BootPayload } from '../boot/parseBootPayload';
import { renderWithClient } from '../utils/test-utils';
import { EXECUTING_STATUS_PILL_TRACEABILITY } from '../utils/executionStatusPillClasses';
import { markWorkflowListReturnFocusIntent } from '../lib/workflowListContext';
import {
  DASHBOARD_PREFERENCES_STORAGE_KEY,
  DASHBOARD_PREFERENCES_VERSION,
} from '../utils/dashboardPreferences';
import { WorkflowListPage } from './workflow-list';

describe('Workflows Entrypoint', () => {
  const mockPayload: BootPayload = {
    page: 'workflow-list',
    apiBase: '/api',
  };

  let fetchSpy: MockInstance;

  beforeEach(() => {
    window.localStorage.clear();
    window.history.pushState({}, 'Test', '/workflows');
    fetchSpy = vi.spyOn(window, 'fetch').mockReset().mockResolvedValue({
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

  const executionListCalls = () =>
    fetchSpy.mock.calls.filter(([url]) => String(url).startsWith('/api/executions?'));

  const lastExecutionListUrl = () => executionListCalls().at(-1)?.[0];

  // The mobile filter drawer exposes the full filter UI. These helpers open it
  // and apply the staged draft.
  const openFilterDrawer = () => fireEvent.click(screen.getByRole('button', { name: 'Filters' }));
  const applyFilterDrawer = () => fireEvent.click(screen.getByRole('button', { name: 'Apply filters' }));

  it('shows the loading state while the workflow list request is pending', () => {
    fetchSpy.mockReturnValue(new Promise(() => {}) as Promise<Response>);

    renderWithClient(<WorkflowListPage payload={mockPayload} />);

    expect(screen.getByRole('region', { name: 'Workflow list' })).toBeTruthy();
    expect(screen.getByText('Workflow list results loading placeholder').closest('[role="status"]')).toBeTruthy();
    expect(screen.getByTestId('loading-placeholder-table')).toBeTruthy();
  });

  it('MM-997 keeps /workflows as the full-width list route instead of the workspace shell', async () => {
    renderWithClient(<WorkflowListPage payload={mockPayload} />);

    await screen.findAllByText('Example task');

    expect(document.querySelector('.workflow-workspace-shell')).toBeNull();
    expect(document.querySelector('.queue-table-wrapper')).toBeTruthy();
  });

  it('MM-1113 renders only authorized workflow rows returned by the list endpoint', async () => {
    fetchSpy.mockResolvedValue({
      ok: true,
      json: async () => ({
        items: [
          {
            taskId: 'authorized-table-row',
            workflowId: 'authorized-table-row',
            source: 'temporal',
            title: 'Authorized table workflow',
            status: 'completed',
            state: 'completed',
            rawState: 'completed',
            createdAt: '2026-03-28T00:00:00Z',
          },
        ],
      }),
    } as Response);

    renderWithClient(<WorkflowListPage payload={mockPayload} />);

    expect(await screen.findByRole('row', { name: /Authorized table workflow/i })).toBeTruthy();
    expect(screen.queryByText(/unauthorized/i)).toBeNull();
    expect(screen.queryByRole('link', { name: /unauthorized/i })).toBeNull();
  });

  it('shows every workflow without an attention toggle, even with a legacy stored attention-only preference', async () => {
    window.localStorage.setItem(
      DASHBOARD_PREFERENCES_STORAGE_KEY,
      JSON.stringify({
        version: DASHBOARD_PREFERENCES_VERSION,
        preferences: { workflowListNeedsAttentionOnly: true },
      }),
    );

    renderWithClient(<WorkflowListPage payload={mockPayload} />);

    expect(await screen.findByRole('row', { name: /Example task/i })).toBeTruthy();
    expect(screen.queryByRole('radio', { name: 'All' })).toBeNull();
    expect(screen.queryByRole('radio', { name: 'Needs attention' })).toBeNull();
    expect(screen.queryByRole('group', { name: 'Attention visibility' })).toBeNull();
  });

  it('shows structured API validation detail when the workflow list request fails', async () => {
    fetchSpy.mockResolvedValue({
      ok: false,
      statusText: 'Bad Request',
      json: async () => ({
        detail: {
          code: 'execution_filter_validation_failed',
          message: 'Cannot combine stateIn and stateNotIn.',
        },
      }),
    } as Response);

    renderWithClient(<WorkflowListPage payload={mockPayload} />);

    expect(await screen.findByText('Cannot combine stateIn and stateNotIn.')).toBeTruthy();
    expect(screen.queryByLabelText('Live updates')).toBeNull();
  });

  it('keeps table headers, creation, and footer controls visible for unfiltered empty results', async () => {
    fetchSpy.mockResolvedValue({
      ok: true,
      json: async () => ({ items: [], count: 0 }),
    } as Response);

    renderWithClient(<WorkflowListPage payload={mockPayload} />);

    expect(await screen.findByText('No workflows found for the current filters.')).toBeTruthy();
    expect(screen.getByRole('table')).toBeTruthy();
    for (const header of ['Workflow', 'Status', 'Progress', 'Repo', 'Provider Profile', 'Updated']) {
      expect(screen.getByRole('columnheader', { name: new RegExp(header, 'i') })).toBeTruthy();
    }
    // One replacement column: no ordinary Runtime/Harness/Backend/Host columns.
    for (const retired of ['Runtime', 'Harness', 'Backend', 'Container', 'Host']) {
      expect(screen.queryByRole('columnheader', { name: new RegExp(`^${retired}`, 'i') })).toBeNull();
    }
    expect(screen.queryByRole('columnheader', { name: 'Actions' })).toBeNull();
    expect(screen.getByRole('link', { name: 'Create a workflow' }).getAttribute('href')).toBe('/workflows/new');
    expect(screen.getByLabelText('Show')).toBeTruthy();
    expect(screen.getByRole('button', { name: 'Previous page' })).toBeTruthy();
  });

  it('renders desktop column filters and keeps the mobile filter drawer available', async () => {
    renderWithClient(<WorkflowListPage payload={mockPayload} />);

    await screen.findAllByText('Example task');

    expect(document.querySelectorAll('.workflow-list-column-filter-button')).toHaveLength(6);
    const workflowFilter = screen.getByRole('button', {
      name: 'Workflow filter. No filter applied.',
    });
    const workflowHeader = workflowFilter.closest('.workflow-list-column-header');
    expect(workflowHeader?.querySelector('.table-sort-button')?.textContent).toContain('Workflow');
    fireEvent.click(workflowFilter);
    expect(screen.getByRole('dialog', { name: 'Workflow filter' })).toBeTruthy();
    expect(screen.getByRole('button', { name: 'Progress filter. No filter applied.' })).toBeTruthy();

    const filtersTrigger = screen.getByRole('button', { name: 'Filters' });
    expect(filtersTrigger).toBeTruthy();
    expect(screen.queryByRole('dialog', { name: 'Advanced filters' })).toBeNull();

    fireEvent.click(filtersTrigger);
    expect(screen.getByRole('dialog', { name: 'Advanced filters' })).toBeTruthy();
  });

  it('opens and closes the advanced filter drawer and returns focus to the trigger', async () => {
    renderWithClient(<WorkflowListPage payload={mockPayload} />);

    await screen.findAllByText('Example task');
    const filtersTrigger = screen.getByRole('button', { name: 'Filters' });
    fireEvent.click(filtersTrigger);

    const drawer = screen.getByRole('dialog', { name: 'Advanced filters' });
    expect(drawer).toBeTruthy();
    // Opening moves focus into the drawer for keyboard users.
    await waitFor(() => {
      expect(drawer.contains(document.activeElement)).toBe(true);
    });

    fireEvent.keyDown(drawer, { key: 'Escape' });

    expect(screen.queryByRole('dialog', { name: 'Advanced filters' })).toBeNull();
    await waitFor(() => {
      expect(document.activeElement).toBe(filtersTrigger);
    });
  });

  it('moves focus into a column filter popover and returns it to the filter button on Escape', async () => {
    renderWithClient(<WorkflowListPage payload={mockPayload} />);

    await screen.findAllByText('Example task');
    const statusFilter = screen.getByRole('button', { name: 'Status filter. No filter applied.' });
    statusFilter.focus();
    fireEvent.click(statusFilter);

    const popover = screen.getByRole('dialog', { name: 'Status filter' });
    await waitFor(() => {
      expect(popover.contains(document.activeElement)).toBe(true);
    });

    fireEvent.keyDown(document.activeElement as Element, { key: 'Escape' });

    expect(screen.queryByRole('dialog', { name: 'Status filter' })).toBeNull();
    await waitFor(() => {
      expect(document.activeElement).toBe(
        screen.getByRole('button', { name: 'Status filter. No filter applied.' }),
      );
    });
  });

  it('traps Tab focus inside the open advanced filter drawer', async () => {
    renderWithClient(<WorkflowListPage payload={mockPayload} />);

    await screen.findAllByText('Example task');
    fireEvent.click(screen.getByRole('button', { name: 'Filters' }));

    const drawer = screen.getByRole('dialog', { name: 'Advanced filters' });
    const closeButton = screen.getByRole('button', { name: 'Close filters' });
    const applyButton = screen.getByRole('button', { name: 'Apply filters' });

    // Shift+Tab from the first focusable control wraps to the last one instead
    // of escaping into the inert background.
    closeButton.focus();
    fireEvent.keyDown(drawer, { key: 'Tab', shiftKey: true });
    expect(document.activeElement).toBe(applyButton);

    // Tab from the last focusable control wraps back to the first.
    applyButton.focus();
    fireEvent.keyDown(drawer, { key: 'Tab' });
    expect(document.activeElement).toBe(closeButton);
  });

  it('keeps active filter chips visible on an empty first page with active filters', async () => {
    fetchSpy.mockImplementation((input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes('stateIn=completed')) {
        return Promise.resolve({
          ok: true,
          json: async () => ({ items: [], count: 0 }),
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
              state: 'completed',
              rawState: 'completed',
              createdAt: '2026-03-28T00:00:00Z',
            },
          ],
        }),
      } as Response);
    });

    renderWithClient(<WorkflowListPage payload={mockPayload} />);

    await screen.findAllByText('Example task');
    openFilterDrawer();
    fireEvent.change(await screen.findByLabelText('Status filter value'), {
      target: { value: 'completed' },
    });
    applyFilterDrawer();

    expect(await screen.findByText('No workflows found for the current filters.')).toBeTruthy();
    expect(screen.getByRole('table')).toBeTruthy();
    expect(screen.getByRole('columnheader', { name: /Workflow/i })).toBeTruthy();
    expect(screen.getByRole('link', { name: 'Create a workflow' }).getAttribute('href')).toBe('/workflows/new');
    expect(screen.getByRole('button', { name: 'Status filter: completed' })).toBeTruthy();
    expect(screen.getByRole('button', { name: 'Filters' })).toBeTruthy();
    expect(screen.queryByLabelText('Live updates')).toBeNull();
    expect(screen.queryByRole('button', { name: 'Clear filters' })).toBeNull();
  });

  it('announces the current sort state on table headers', async () => {
    renderWithClient(<WorkflowListPage payload={mockPayload} />);

    await screen.findAllByText('Example task');
    expect(fetchSpy.mock.calls.at(-1)?.[0]).toBe(
      '/api/executions?source=temporal&pageSize=50',
    );

    const scheduledHeaderButton = await screen.findByRole('button', {
      name: /Updated\. Sorted descending, current page only\. Activate to sort ascending\./i,
    });
    expect(scheduledHeaderButton.closest('th')?.getAttribute('aria-sort')).toBe('descending');

    const runtimeHeaderButton = screen.getByRole('button', {
      name: /Provider Profile\. Not sorted\. Activate to sort the current page ascending\./i,
    });
    expect(runtimeHeaderButton.closest('th')?.getAttribute('aria-sort')).toBe('none');

    fireEvent.click(runtimeHeaderButton);

    await waitFor(() => {
      expect(runtimeHeaderButton.closest('th')?.getAttribute('aria-sort')).toBe('ascending');
      expect(runtimeHeaderButton.getAttribute('aria-label')).toBe(
        'Provider Profile. Sorted ascending, current page only. Activate to sort descending.',
      );
    });
  });

  it('labels sorting as current-page-only and keeps it out of the URL and API request (MM-954)', async () => {
    renderWithClient(<WorkflowListPage payload={mockPayload} />);

    await screen.findAllByText('Example task');

    // Visible explanatory text near pagination.
    expect(screen.getByText('Sorting applies to the current page only.')).toBeTruthy();

    const runtimeHeaderButton = screen.getByRole('button', {
      name: /Provider Profile\. Not sorted\. Activate to sort the current page ascending\./i,
    });
    // Tooltip text reinforces the current-page-only scope.
    expect(runtimeHeaderButton.getAttribute('title')).toBe('Sorting applies to the current page only.');

    fireEvent.click(runtimeHeaderButton);

    await waitFor(() => {
      expect(runtimeHeaderButton.closest('th')?.getAttribute('aria-sort')).toBe('ascending');
    });

    // URL state must not imply a global server-side sort.
    expect(window.location.search).not.toContain('sort=');
    expect(window.location.search).not.toContain('sortDir=');

    // The API request must not send sort/sortDir; sorting is purely client-side
    // over the currently loaded page.
    const requestedUrls = executionListCalls().map(([url]) => String(url));
    expect(requestedUrls.length).toBeGreaterThan(0);
    for (const url of requestedUrls) {
      expect(url).not.toContain('sort=');
      expect(url).not.toContain('sortDir=');
    }
  });

  it('keeps issue #2807 updated-time jitter inside a bucket ordered by newest queued workflow', async () => {
    fetchSpy.mockResolvedValue({
      ok: true,
      json: async () => ({
        items: [
          {
            workflowId: 'mm:older-queued-slightly-newer-update',
            source: 'temporal',
            title: 'Older queued, slightly newer update',
            status: 'queued',
            state: 'awaiting_slot',
            rawState: 'awaiting_slot',
            createdAt: '2026-03-28T09:00:00Z',
            queuedAt: '2026-03-28T09:00:00Z',
            updatedAt: '2026-03-28T12:00:45Z',
          },
          {
            workflowId: 'mm:newer-queued-same-update-bucket',
            source: 'temporal',
            title: 'Newer queued, same update bucket',
            status: 'queued',
            state: 'awaiting_slot',
            rawState: 'awaiting_slot',
            createdAt: '2026-03-28T10:00:00Z',
            queuedAt: '2026-03-28T10:00:00Z',
            updatedAt: '2026-03-28T12:00:05Z',
          },
          {
            workflowId: 'mm:meaningfully-newer-update',
            source: 'temporal',
            title: 'Meaningfully newer update',
            status: 'queued',
            state: 'awaiting_slot',
            rawState: 'awaiting_slot',
            createdAt: '2026-03-28T08:00:00Z',
            queuedAt: '2026-03-28T08:00:00Z',
            updatedAt: '2026-03-28T12:02:00Z',
          },
        ],
      }),
    } as Response);

    renderWithClient(<WorkflowListPage payload={mockPayload} />);

    await screen.findAllByText('Meaningfully newer update');

    const table = document.querySelector('.queue-table-wrapper table') as HTMLTableElement;
    const titles = Array.from(table.querySelectorAll('tbody .workflow-list-row-title')).map(
      (element) => element.textContent,
    );

    expect(titles).toEqual([
      'Meaningfully newer update',
      'Newer queued, same update bucket',
      'Older queued, slightly newer update',
    ]);
  });

  it('MM-1018 sorts Progress by bounded completion percent with blanks last', async () => {
    fetchSpy.mockResolvedValue({
      ok: true,
      json: async () => ({
        items: [
          {
            taskId: 'task-blank',
            source: 'temporal',
            title: 'Blank progress',
            status: 'executing',
            state: 'executing',
            rawState: 'executing',
            createdAt: '2026-03-28T00:00:00Z',
          },
          {
            taskId: 'task-half',
            source: 'temporal',
            title: 'Half progress',
            status: 'executing',
            state: 'executing',
            rawState: 'executing',
            createdAt: '2026-03-28T00:00:00Z',
            progress: { total: 4, completed: 2, currentStepTitle: 'Build', updatedAt: '2026-03-28T00:01:00Z' },
          },
          {
            taskId: 'task-most',
            source: 'temporal',
            title: 'Most progress',
            status: 'executing',
            state: 'executing',
            rawState: 'executing',
            createdAt: '2026-03-28T00:00:00Z',
            progress: { total: 4, completed: 3, currentStepTitle: 'Test', updatedAt: '2026-03-28T00:02:00Z' },
          },
        ],
      }),
    } as Response);

    renderWithClient(<WorkflowListPage payload={mockPayload} />);

    const progressHeaderButton = await screen.findByRole('button', {
      name: /Progress\. Not sorted\. Activate to sort the current page descending\./i,
    });
    fireEvent.click(progressHeaderButton);

    const table = screen.getByRole('table');
    const mostLink = within(table).getByRole('link', { name: 'Most progress' });
    const halfLink = within(table).getByRole('link', { name: 'Half progress' });
    const blankLink = within(table).getByRole('link', { name: 'Blank progress' });
    expect(mostLink.compareDocumentPosition(halfLink) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(halfLink.compareDocumentPosition(blankLink) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();

    fireEvent.click(progressHeaderButton);
    await waitFor(() => {
      expect(progressHeaderButton.closest('th')?.getAttribute('aria-sort')).toBe('ascending');
    });
    expect(halfLink.compareDocumentPosition(mostLink) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(mostLink.compareDocumentPosition(blankLink) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(window.location.search).not.toContain('sort=');
    expect(executionListCalls().some(([url]) => String(url).includes('/steps'))).toBe(false);
  });

  it('MM-1018 serializes Progress filters and filters bounded current-page progress', async () => {
    fetchSpy.mockResolvedValue({
      ok: true,
      json: async () => ({
        items: [
          {
            taskId: 'task-match',
            source: 'temporal',
            title: 'Matching progress',
            status: 'failed',
            state: 'failed',
            rawState: 'failed',
            createdAt: '2026-03-28T00:00:00Z',
            progress: {
              total: 4,
              completed: 2,
              failed: 1,
              currentStepTitle: 'Run tests',
              updatedAt: '2026-03-28T00:02:00Z',
            },
          },
          {
            taskId: 'task-miss',
            source: 'temporal',
            title: 'Other progress',
            status: 'executing',
            state: 'executing',
            rawState: 'executing',
            createdAt: '2026-03-28T00:00:00Z',
            progress: {
              total: 4,
              completed: 1,
              executing: 1,
              currentStepTitle: 'Implement',
              updatedAt: '2026-03-28T00:01:00Z',
            },
          },
        ],
      }),
    } as Response);

    renderWithClient(<WorkflowListPage payload={mockPayload} />);

    await screen.findAllByText('Matching progress');
    openFilterDrawer();
    expect(screen.getByLabelText('Completion from percent')).toBeTruthy();
    expect(screen.getByLabelText('Current step title')).toBeTruthy();
    expect(screen.getByLabelText('Progress blank values')).toBeTruthy();

    fireEvent.change(screen.getByLabelText('Completion from percent'), { target: { value: '25' } });
    fireEvent.change(screen.getByLabelText('Completion to percent'), { target: { value: '75' } });
    fireEvent.click(screen.getByLabelText('Has failed steps'));
    fireEvent.change(screen.getByLabelText('Current step title'), { target: { value: 'tests' } });
    applyFilterDrawer();

    await waitFor(() => {
      expect(lastExecutionListUrl()).toBe(
        '/api/executions?source=temporal&pageSize=50&progressPctFrom=25&progressPctTo=75&progressSignalIn=has_failed_steps&progressStepTitleContains=tests',
      );
    });
    expect(window.location.search).toBe(
      '?progressPctFrom=25&progressPctTo=75&progressSignalIn=has_failed_steps&progressStepTitleContains=tests&limit=50',
    );
    expect(screen.getByRole('button', { name: /Progress filter: 25-75%, Has failed steps, step tests/i })).toBeTruthy();
    expect((await screen.findAllByText('Matching progress')).length).toBeGreaterThan(0);
    expect(screen.queryByText('Other progress')).toBeNull();
    expect(executionListCalls().some(([url]) => String(url).includes('/steps'))).toBe(false);
  });

  it('preserves Progress exclude filters from shared URLs', async () => {
    window.history.pushState(
      {},
      'Progress excludes',
      '/workflows?progressBucketNotIn=complete&progressSignalNotIn=has_failed_steps&limit=50',
    );
    fetchSpy.mockResolvedValue({
      ok: true,
      json: async () => ({
        items: [
          {
            taskId: 'task-visible',
            source: 'temporal',
            title: 'Visible progress',
            status: 'executing',
            state: 'executing',
            rawState: 'executing',
            createdAt: '2026-03-28T00:00:00Z',
            progress: {
              total: 4,
              completed: 1,
              executing: 1,
              currentStepTitle: 'Implement',
              updatedAt: '2026-03-28T00:01:00Z',
            },
          },
          {
            taskId: 'task-complete',
            source: 'temporal',
            title: 'Complete progress',
            status: 'completed',
            state: 'completed',
            rawState: 'completed',
            createdAt: '2026-03-28T00:00:00Z',
            progress: {
              total: 4,
              completed: 4,
              updatedAt: '2026-03-28T00:01:00Z',
            },
          },
          {
            taskId: 'task-failed',
            source: 'temporal',
            title: 'Failed progress',
            status: 'failed',
            state: 'failed',
            rawState: 'failed',
            createdAt: '2026-03-28T00:00:00Z',
            progress: {
              total: 4,
              completed: 1,
              failed: 1,
              currentStepTitle: 'Test',
              updatedAt: '2026-03-28T00:01:00Z',
            },
          },
        ],
      }),
    } as Response);

    renderWithClient(<WorkflowListPage payload={mockPayload} />);

    expect((await screen.findAllByText('Visible progress')).length).toBeGreaterThan(0);

    await waitFor(() => {
      expect(lastExecutionListUrl()).toBe(
        '/api/executions?source=temporal&pageSize=50&progressBucketNotIn=complete&progressSignalNotIn=has_failed_steps',
      );
    });
    expect(window.location.search).toBe(
      '?progressBucketNotIn=complete&progressSignalNotIn=has_failed_steps&limit=50',
    );
    expect(
      screen.getByRole('button', {
        name: /Progress filter: not Complete, not Has failed steps/i,
      }),
    ).toBeTruthy();
    expect(screen.queryByText('Complete progress')).toBeNull();
    expect(screen.queryByText('Failed progress')).toBeNull();
  });

  it('preserves allowlisted list context on workflow detail links and keeps browser back target intact (MM-998, MM-975)', async () => {
    window.history.pushState(
      {},
      'Context list',
      '/workflows?stateIn=completed&repoContains=moon%2Frepo&targetRuntimeIn=codex_cli&limit=25&nextPageToken=cursor-2&sort=status&sortDir=asc&selectedWorkflowId=task-123&unsafe=1',
    );

    renderWithClient(<WorkflowListPage payload={mockPayload} />);

    await screen.findAllByText('Example task');
    const previousListUrl = window.location.href;
    const detailHref = screen.getAllByRole('link', { name: 'Example task' })[0]?.getAttribute('href');

    expect(detailHref).toBe(
      '/workflows/task-123?stateIn=completed&repoContains=moon%2Frepo&targetRuntimeIn=codex_cli&limit=25&nextPageToken=cursor-2&source=temporal',
    );
    expect(detailHref).not.toContain('sort=');
    expect(detailHref).not.toContain('sortDir=');
    expect(detailHref).not.toContain('selectedWorkflowId=');
    expect(detailHref).not.toContain('unsafe=');

    window.history.pushState({}, 'Detail', detailHref || '/workflows/task-123');
    window.history.back();

    await waitFor(() => {
      expect(window.location.href).toBe(previousListUrl);
    });
  });

  it('ignores sort/sortDir present in the initial URL so deep links never imply a global sort (MM-954)', async () => {
    window.history.pushState({}, 'Seeded sort', '/workflows?sort=status&sortDir=asc');

    renderWithClient(<WorkflowListPage payload={mockPayload} />);

    await screen.findAllByText('Example task');

    // The default current-page sort (Updated, descending) is used regardless of
    // the URL, and the misleading sort params are dropped from the URL.
    expect(
      screen.getByRole('button', {
        name: /Updated\. Sorted descending, current page only\. Activate to sort ascending\./i,
      }),
    ).toBeTruthy();
    expect(window.location.search).not.toContain('sort=');
    expect(window.location.search).not.toContain('sortDir=');
    expect(String(lastExecutionListUrl())).not.toContain('sort=');
  });

  it('exposes the exact updated timestamp with a two-digit year on hover', async () => {
    fetchSpy.mockResolvedValue({
      ok: true,
      json: async () => ({
        items: [
          {
            taskId: 'task-123',
            source: 'temporal',
            title: 'Example task',
            status: 'completed',
            state: 'completed',
            rawState: 'completed',
            scheduledFor: '2026-06-21T12:00:00Z',
            createdAt: '2026-06-21T12:01:00Z',
            closedAt: '2026-06-21T12:02:00Z',
          },
        ],
      }),
    } as Response);

    renderWithClient(<WorkflowListPage payload={mockPayload} />);

    const row = await screen.findByRole('row', { name: /Example task/ });
    const dateCells = row.querySelectorAll('.queue-table-cell-date');
    // The scan-first table consolidates raw timestamps into a single Updated column.
    expect(dateCells).toHaveLength(1);
    const expectedExact = new Date('2026-06-21T12:02:00Z').toLocaleString(undefined, {
      year: '2-digit',
      month: 'numeric',
      day: 'numeric',
      hour: 'numeric',
      minute: '2-digit',
      second: '2-digit',
    });
    const updatedCell = dateCells[0] as HTMLElement;
    // Visible text is the relative "Updated" signal; the exact timestamp is on hover.
    expect(updatedCell.getAttribute('title')).toBe(expectedExact);
    expect(updatedCell.getAttribute('title')).not.toContain('2026');
  });

  it('uses the API updatedAt value for the Updated column before fallback timestamps', async () => {
    const updatedAt = new Date(Date.now() - 60 * 1000).toISOString();
    const createdAt = new Date(Date.now() - 8 * 3600 * 1000).toISOString();
    fetchSpy.mockResolvedValue({
      ok: true,
      json: async () => ({
        items: [
          {
            taskId: 'task-active',
            source: 'temporal',
            title: 'Actively executing task',
            status: 'executing',
            state: 'executing',
            rawState: 'executing',
            createdAt,
            updatedAt,
          },
        ],
      }),
    } as Response);

    renderWithClient(<WorkflowListPage payload={mockPayload} />);

    const row = await screen.findByRole('row', { name: /Actively executing task/ });
    const updatedCell = row.querySelector('.queue-table-cell-date') as HTMLElement;
    const expectedExact = new Date(updatedAt).toLocaleString(undefined, {
      year: '2-digit',
      month: 'numeric',
      day: 'numeric',
      hour: 'numeric',
      minute: '2-digit',
      second: '2-digit',
    });
    const staleExact = new Date(createdAt).toLocaleString(undefined, {
      year: '2-digit',
      month: 'numeric',
      day: 'numeric',
      hour: 'numeric',
      minute: '2-digit',
      second: '2-digit',
    });
    expect(updatedCell.getAttribute('title')).toBe(expectedExact);
    expect(updatedCell.getAttribute('title')).not.toBe(staleExact);
  });

  it('shows completed resolver continuations as handed off', async () => {
    fetchSpy.mockResolvedValue({
      ok: true,
      json: async () => ({ items: [{
        taskId: 'resolver-833', source: 'temporal', title: 'Resolve PR 833',
        status: 'completed', state: 'completed', rawState: 'completed',
        completionDisposition: 'gated_continuation', createdAt: '2026-09-11T00:40:00Z',
      }] }),
    } as Response);
    renderWithClient(<WorkflowListPage payload={mockPayload} />);
    const row = await screen.findByRole('row', { name: /Resolve PR 833/ });
    expect(within(row).getByText('Handed off')).toBeTruthy();
    expect(within(row).queryByText('Completed')).toBeNull();
  });

  it('shows a completed idle objective without hiding no-commit and live outcomes', async () => {
    fetchSpy.mockResolvedValue({
      ok: true,
      json: async () => ({ items: [
        { workflowId: 'idle-scan', title: 'Idle issue scan', rawState: 'completed', objectiveOutcome: 'idle' },
        { workflowId: 'no-commit', title: 'Verified no-commit work', rawState: 'no_commit', objectiveOutcome: 'succeeded' },
        { workflowId: 'live', title: 'Live workflow', rawState: 'running', objectiveOutcome: 'idle' },
        { workflowId: 'unknown', title: 'Older objective contract', rawState: 'completed', objectiveOutcome: 'unknown' },
      ].map((row) => ({ source: 'temporal', status: 'completed', state: 'completed', createdAt: '2026-10-03T22:00:00Z', ...row })) }),
    } as Response);

    renderWithClient(<WorkflowListPage payload={mockPayload} />);

    const idleRow = await screen.findByRole('row', { name: /Idle issue scan/ });
    expect(within(idleRow).getByText('Idle').className).toContain('status-neutral');
    expect(within(idleRow).queryByText('Completed')).toBeNull();
    expect(within(screen.getByRole('row', { name: /Verified no-commit work/ })).getByText('No commit')).toBeTruthy();
    expect(within(screen.getByRole('row', { name: /Live workflow/ })).getByLabelText('Executing')).toBeTruthy();
    expect(within(screen.getByRole('row', { name: /Older objective contract/ })).getByText('Completed')).toBeTruthy();
  });

  it.each([
    ['failed', 'Failed', 'status-failed'],
    ['verification_blocked', 'Verification blocked', 'status-failed'],
    ['cancelled', 'Cancelled', 'status-canceled'],
  ])('shows terminal objective %s truthfully in a completed list row', async (objectiveOutcome, label, statusClass) => {
    fetchSpy.mockResolvedValue({
      ok: true,
      json: async () => ({ items: [{ source: 'temporal', workflowId: 'unmet-objective', title: 'Unmet objective',
        status: 'completed', state: 'completed', rawState: 'completed', objectiveOutcome, createdAt: '2026-10-03T22:00:00Z' }] }),
    } as Response);

    renderWithClient(<WorkflowListPage payload={mockPayload} />);

    const row = await screen.findByRole('row', { name: /Unmet objective/ });
    expect(within(row).getByText(label).className).toContain(statusClass);
    expect(within(row).queryByText('Completed')).toBeNull();
  });

  it('uses closedAt for terminal rows when synthetic updatedAt is older', async () => {
    const closedAt = '2026-04-15T20:00:00Z';
    const syntheticUpdatedAt = '2026-04-15T10:00:00Z';
    fetchSpy.mockResolvedValue({
      ok: true,
      json: async () => ({
        items: [
          {
            taskId: 'task-completed',
            source: 'temporal',
            title: 'Completed task',
            status: 'completed',
            state: 'completed',
            rawState: 'completed',
            createdAt: '2026-04-15T09:00:00Z',
            updatedAt: syntheticUpdatedAt,
            closedAt,
          },
        ],
      }),
    } as Response);

    renderWithClient(<WorkflowListPage payload={mockPayload} />);

    const row = await screen.findByRole('row', { name: /Completed task/ });
    const updatedCell = row.querySelector('.queue-table-cell-date') as HTMLElement;
    const expectedExact = new Date(closedAt).toLocaleString(undefined, {
      year: '2-digit',
      month: 'numeric',
      day: 'numeric',
      hour: 'numeric',
      minute: '2-digit',
      second: '2-digit',
    });
    expect(updatedCell.getAttribute('title')).toBe(expectedExact);
  });

  it('uses scheduledFor for scheduled rows when synthetic updatedAt is older', async () => {
    const scheduledFor = '2026-04-15T20:00:00Z';
    const syntheticUpdatedAt = '2026-04-15T10:00:00Z';
    fetchSpy.mockResolvedValue({
      ok: true,
      json: async () => ({
        items: [
          {
            taskId: 'task-scheduled',
            source: 'temporal',
            title: 'Scheduled task',
            status: 'scheduled',
            state: 'scheduled',
            rawState: 'scheduled',
            createdAt: '2026-04-15T09:00:00Z',
            updatedAt: syntheticUpdatedAt,
            scheduledFor,
          },
        ],
      }),
    } as Response);

    renderWithClient(<WorkflowListPage payload={mockPayload} />);

    const row = await screen.findByRole('row', { name: /Scheduled task/ });
    const updatedCell = row.querySelector('.queue-table-cell-date') as HTMLElement;
    const expectedExact = new Date(scheduledFor).toLocaleString(undefined, {
      year: '2-digit',
      month: 'numeric',
      day: 'numeric',
      hour: 'numeric',
      minute: '2-digit',
      second: '2-digit',
    });
    expect(updatedCell.getAttribute('title')).toBe(expectedExact);
  });

  it('floors relative Updated values so units roll over at the exact threshold', async () => {
    // 1h50m before now: Math.floor -> "1h ago"; Math.round would show "2h ago".
    // Computed against the real clock so the assertion is deterministic.
    const closedAt = new Date(Date.now() - (1 * 3600 + 50 * 60) * 1000).toISOString();
    const createdAt = new Date(Date.now() - 3 * 3600 * 1000).toISOString();
    fetchSpy.mockResolvedValue({
      ok: true,
      json: async () => ({
        items: [
          {
            taskId: 'task-floor',
            source: 'temporal',
            title: 'Floor task',
            status: 'completed',
            state: 'completed',
            rawState: 'completed',
            createdAt,
            closedAt,
          },
        ],
      }),
    } as Response);

    renderWithClient(<WorkflowListPage payload={mockPayload} />);

    const row = await screen.findByRole('row', { name: /Floor task/ });
    const updatedCell = row.querySelector('.queue-table-cell-date') as HTMLElement;
    expect(updatedCell.textContent).toBe('1h ago');
  });

  it('does not query or render operational metrics on the workflow overview', async () => {
    fetchSpy.mockImplementation((input: RequestInfo | URL) => {
      const url = String(input);
      if (url.startsWith('/api/executions/metrics?')) {
        throw new Error('Workflows should not request operational metrics.');
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
              state: 'completed',
              rawState: 'completed',
              createdAt: '2026-03-28T00:00:00Z',
            },
          ],
        }),
      } as Response);
    });

    renderWithClient(<WorkflowListPage payload={mockPayload} />);

    await screen.findAllByText('Example task');
    expect(screen.queryByLabelText('Operational metrics')).toBeNull();
    expect(screen.queryByText('Operational metrics are unavailable.')).toBeNull();
    expect(fetchSpy.mock.calls.some(([url]) => String(url).startsWith('/api/executions/metrics?'))).toBe(false);
  });

  it('surfaces intervention requests in list rows and status filters', async () => {
    fetchSpy.mockResolvedValue({
      ok: true,
      json: async () => ({
        items: [
          {
            taskId: 'task-needs-human',
            source: 'temporal',
            title: 'Needs operator input',
            status: 'intervention_requested',
            state: 'intervention_requested',
            rawState: 'intervention_requested',
            attentionRequired: true,
            createdAt: '2026-03-28T00:00:00Z',
          },
        ],
      }),
    } as Response);

    renderWithClient(<WorkflowListPage payload={mockPayload} />);

    await screen.findAllByText('Needs operator input');
    expect(screen.getAllByText('Intervention requested').length).toBeGreaterThan(0);

    openFilterDrawer();
    const statusFilter = screen.getByLabelText('Status filter value') as HTMLSelectElement;
    expect(
      Array.from(statusFilter.options).some((option) => option.value === 'intervention_requested'),
    ).toBe(true);
  });

  it('keeps header sorting independent from the advanced filter drawer', async () => {
    renderWithClient(<WorkflowListPage payload={mockPayload} />);

    await screen.findAllByText('Example task');

    const updatedHeaderButton = await screen.findByRole('button', {
      name: /Updated\. Sorted descending, current page only\. Activate to sort ascending\./i,
    });

    openFilterDrawer();
    expect(screen.getByRole('dialog', { name: 'Advanced filters' })).toBeTruthy();
    expect(updatedHeaderButton.closest('th')?.getAttribute('aria-sort')).toBe('descending');

    fireEvent.click(updatedHeaderButton);

    await waitFor(() => {
      expect(updatedHeaderButton.closest('th')?.getAttribute('aria-sort')).toBe('ascending');
    });
    // Sorting does not disturb the open drawer.
    expect(screen.getByRole('dialog', { name: 'Advanced filters' })).toBeTruthy();
  });

  it('exposes every advanced filter field in one drawer and applies them together', async () => {
    renderWithClient(<WorkflowListPage payload={mockPayload} />);

    await screen.findAllByText('Example task');

    openFilterDrawer();
    expect(screen.getByLabelText('ID filter value')).toBeTruthy();
    expect(screen.getByLabelText('Skill filter value')).toBeTruthy();
    expect(screen.getByLabelText('Title filter value')).toBeTruthy();
    expect(screen.getByLabelText('Updated from')).toBeTruthy();
    expect(screen.getByLabelText('Scheduled from')).toBeTruthy();
    expect(screen.getByLabelText('Created from')).toBeTruthy();
    expect(screen.getByLabelText('Finished blank values')).toBeTruthy();

    fireEvent.change(screen.getByLabelText('ID filter value'), {
      target: { value: 'task-123' },
    });
    fireEvent.change(screen.getByLabelText('Status filter value'), {
      target: { value: 'completed' },
    });
    fireEvent.change(screen.getByLabelText('Repository filter value'), {
      target: { value: 'owner/repo' },
    });
    fireEvent.click(screen.getByRole('checkbox', { name: /Pending selection/ }));
    fireEvent.change(screen.getByLabelText('Title filter value'), {
      target: { value: 'Example' },
    });
    applyFilterDrawer();

    await waitFor(() => {
      expect(lastExecutionListUrl()).toBe(
        '/api/executions?source=temporal&pageSize=50&workflowIdContains=task-123&stateIn=completed&repoContains=owner%2Frepo&providerProfileStateIn=pending&titleContains=Example',
      );
    });
  });

  it('filters the Updated column by the displayed updated timestamp', async () => {
    renderWithClient(<WorkflowListPage payload={mockPayload} />);

    await screen.findAllByText('Example task');
    const updatedFilter = screen.getByRole('button', {
      name: 'Updated filter. No filter applied.',
    });
    fireEvent.click(updatedFilter);

    fireEvent.change(screen.getByLabelText('Updated from'), {
      target: { value: '2026-04-01' },
    });
    fireEvent.change(screen.getByLabelText('Updated to'), {
      target: { value: '2026-04-30' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Apply Updated filter' }));

    await waitFor(() => {
      expect(lastExecutionListUrl()).toBe(
        '/api/executions?source=temporal&pageSize=50&updatedFrom=2026-04-01&updatedTo=2026-04-30',
      );
    });
    expect(screen.getByRole('button', { name: 'Updated filter: from 2026-04-01, to 2026-04-30' })).toBeTruthy();
    expect(window.location.search).toBe('?updatedFrom=2026-04-01&updatedTo=2026-04-30&limit=50');
  });

  it('applies Provider Profile and skill exclude modes from the drawer', async () => {
    fetchSpy.mockResolvedValue({
      ok: true,
      json: async () => ({
        items: [
          {
            taskId: 'task-123',
            source: 'temporal',
            title: 'Example task',
            status: 'completed',
            state: 'completed',
            rawState: 'completed',
            targetRuntime: 'codex_cli',
            providerProfile: {
              selectionState: 'recorded',
              profiles: [{ id: 'acct-1', label: 'OpenAI · Primary' }],
              profileCount: 1,
            },
            targetSkill: 'pr-resolver',
            createdAt: '2026-03-28T00:00:00Z',
          },
        ],
      }),
    } as Response);

    renderWithClient(<WorkflowListPage payload={mockPayload} />);

    await screen.findAllByText('Example task');
    openFilterDrawer();
    fireEvent.change(screen.getByLabelText('Provider Profile filter mode'), {
      target: { value: 'exclude' },
    });
    fireEvent.change(screen.getByLabelText('Provider Profile filter value'), {
      target: { value: 'acct-1' },
    });
    applyFilterDrawer();

    await waitFor(() => {
      expect(lastExecutionListUrl()).toContain('providerProfileIdNotIn=acct-1');
    });
    expect(lastExecutionListUrl()).not.toContain('targetRuntime');
    expect(
      screen.getByRole('button', { name: 'Provider Profile filter: not OpenAI · Primary' }),
    ).toBeTruthy();
    await screen.findAllByText('Example task');

    openFilterDrawer();
    fireEvent.change(screen.getByLabelText('Skill filter mode'), { target: { value: 'exclude' } });
    fireEvent.change(screen.getByLabelText('Skill filter value'), { target: { value: 'pr-resolver' } });
    applyFilterDrawer();

    await waitFor(() => {
      const url = lastExecutionListUrl();
      expect(url).toContain('providerProfileIdNotIn=acct-1');
      expect(url).toContain('targetSkillNotIn=pr-resolver');
    });
    expect(screen.getByRole('button', { name: 'Skill filter: not pr-resolver' })).toBeTruthy();
  }, 10_000);

  it('MoonLadderStudios/MoonMind#4640 offers no hardcoded runtime fallback as a filter', async () => {
    renderWithClient(<WorkflowListPage payload={mockPayload} />);

    await screen.findAllByText('Example task');
    openFilterDrawer();

    expect(screen.queryByLabelText('Runtime filter value')).toBeNull();
    expect(screen.queryByRole('region', { name: 'Legacy runtime filter' })).toBeNull();
    const profileFilter = screen.getByLabelText('Provider Profile filter value') as HTMLSelectElement;
    const optionValues = Array.from(profileFilter.options)
      .map((option) => option.value)
      .filter(Boolean);
    expect(optionValues).not.toContain('codex_cli');
    expect(optionValues).not.toContain('claude_code');
  });

  it('keeps workflow-kind browsing controls out of the normal workflow list', async () => {
    renderWithClient(<WorkflowListPage payload={mockPayload} />);

    await screen.findAllByText('Example task');

    expect(screen.queryByLabelText('Scope')).toBeNull();
    expect(screen.queryByLabelText('Workflow Type')).toBeNull();
    expect(screen.queryByLabelText('Entry')).toBeNull();
    expect(fetchSpy.mock.calls.at(-1)?.[0]).toBe(
      '/api/executions?source=temporal&pageSize=50',
    );
  });

  it('normalizes legacy workflow scope URLs to workflow visibility with recoverable notice', async () => {
    window.history.pushState(
      {},
      'Legacy',
      '/workflows?scope=all&workflowType=MoonMind.ProviderProfileManager&entry=manifest&state=completed&repo=moon%2Fdemo&nextPageToken=stale-token',
    );

    renderWithClient(<WorkflowListPage payload={mockPayload} />);

    await screen.findAllByText('Example task');

    expect(fetchSpy.mock.calls.at(-1)?.[0]).toBe(
      '/api/executions?source=temporal&pageSize=50&stateIn=completed&repoContains=moon%2Fdemo',
    );
    expect(screen.getByText(/Workflow scope filters are not available on Workflows/i)).toBeTruthy();
    expect(window.location.search).toBe('?stateIn=completed&repoContains=moon%2Fdemo&limit=50');
    expect(screen.queryByText('MoonMind.ProviderProfileManager')).toBeNull();
    expect(screen.queryByText('manifest')).toBeNull();
  });

  it('loads repeated canonical runtime params as raw URL values with product-label chips', async () => {
    window.history.pushState(
      {},
      'Repeated canonical filters',
      '/workflows?targetRuntimeIn=codex_cli&targetRuntimeIn=claude_code&targetRuntimeIn=&limit=50',
    );

    renderWithClient(<WorkflowListPage payload={mockPayload} />);

    await screen.findAllByText('Example task');

    expect(fetchSpy.mock.calls.at(-1)?.[0]).toBe(
      '/api/executions?source=temporal&pageSize=50&targetRuntimeIn=codex_cli%2Cclaude_code',
    );
    expect(window.location.search).toBe('?targetRuntimeIn=codex_cli%2Cclaude_code&limit=50');
    expect(screen.getByRole('button', { name: 'Legacy runtime filter: Codex CLI +1' })).toBeTruthy();
  });

  it('canonicalizes loaded Claude Code runtime filter labels before fetching', async () => {
    window.history.pushState(
      {},
      'Runtime label filter',
      '/workflows?targetRuntimeIn=Claude%20Code&limit=50',
    );

    renderWithClient(<WorkflowListPage payload={mockPayload} />);

    await screen.findAllByText('Example task');

    expect(fetchSpy.mock.calls.at(-1)?.[0]).toBe(
      '/api/executions?source=temporal&pageSize=50&targetRuntimeIn=claude_code',
    );
    expect(window.location.search).toBe('?targetRuntimeIn=claude_code&limit=50');
    expect(screen.getByRole('button', { name: 'Legacy runtime filter: Claude Code' })).toBeTruthy();
  });

  it('preserves raw stored runtime identifiers from loaded include filters', async () => {
    window.history.pushState(
      {},
      'Raw runtime filter',
      '/workflows?targetRuntimeIn=codex&targetRuntimeIn=claude&limit=50',
    );

    renderWithClient(<WorkflowListPage payload={mockPayload} />);

    await screen.findAllByText('Example task');

    expect(fetchSpy.mock.calls.at(-1)?.[0]).toBe(
      '/api/executions?source=temporal&pageSize=50&targetRuntimeIn=codex%2Cclaude',
    );
    expect(window.location.search).toBe('?targetRuntimeIn=codex%2Cclaude&limit=50');
    expect(screen.getByRole('button', { name: 'Legacy runtime filter: Codex CLI +1' })).toBeTruthy();
  });

  it('preserves raw stored runtime identifiers from loaded exclude filters', async () => {
    window.history.pushState(
      {},
      'Raw runtime exclude filter',
      '/workflows?targetRuntimeNotIn=codex&targetRuntimeNotIn=claude&limit=50',
    );

    renderWithClient(<WorkflowListPage payload={mockPayload} />);

    await screen.findAllByText('Example task');

    expect(fetchSpy.mock.calls.at(-1)?.[0]).toBe(
      '/api/executions?source=temporal&pageSize=50&targetRuntimeNotIn=codex%2Cclaude',
    );
    expect(window.location.search).toBe('?targetRuntimeNotIn=codex%2Cclaude&limit=50');
    expect(
      screen.getByRole('button', { name: 'Legacy runtime filter: not (Codex CLI +1)' }),
    ).toBeTruthy();
  });

  it('shows a clear validation error for contradictory canonical URL filters', async () => {
    const baselineCalls = executionListCalls().length;
    window.history.pushState(
      {},
      'Contradictory filters',
      '/workflows?stateIn=completed&stateNotIn=canceled&targetRuntimeIn=codex_cli&targetRuntimeNotIn=jules',
    );

    renderWithClient(<WorkflowListPage payload={mockPayload} />);

    expect(await screen.findByText('Cannot combine stateIn and stateNotIn.')).toBeTruthy();
    expect(screen.getByText('Cannot combine targetRuntimeIn and targetRuntimeNotIn.')).toBeTruthy();
    expect(executionListCalls().length).toBe(baselineCalls);
  });

  it('does not render the removed clear-filters recovery action for contradictory canonical URL filters', async () => {
    const baselineCalls = executionListCalls().length;
    window.history.pushState(
      {},
      'Recover contradictory filters',
      '/workflows?stateIn=completed&stateNotIn=canceled',
    );

    renderWithClient(<WorkflowListPage payload={mockPayload} />);

    expect(await screen.findByText('Cannot combine stateIn and stateNotIn.')).toBeTruthy();
    expect(executionListCalls().length).toBe(baselineCalls);
    expect(screen.queryByRole('button', { name: 'Clear filters' })).toBeNull();
  });

  it('renders active workflow-list pills with the shared shimmer selector contract while keeping inactive pills plain', async () => {
    fetchSpy.mockResolvedValue({
      ok: true,
      json: async () => ({
        items: [
          {
            taskId: 'task-planning',
            source: 'temporal',
            title: 'Planning task',
            status: 'running',
            state: 'planning',
            rawState: 'planning',
            createdAt: '2026-03-28T00:00:00Z',
          },
          {
            taskId: 'task-executing',
            source: 'temporal',
            title: 'Executing task',
            status: 'running',
            state: 'executing',
            rawState: 'executing',
            createdAt: '2026-03-28T00:00:00Z',
          },
          {
            taskId: 'task-waiting',
            source: 'temporal',
            title: 'Waiting task',
            status: 'waiting',
            state: 'waiting_on_dependencies',
            rawState: 'waiting_on_dependencies',
            createdAt: '2026-03-28T00:00:00Z',
          },
          {
            taskId: 'task-awaiting',
            source: 'temporal',
            title: 'Awaiting task',
            status: 'awaiting_action',
            state: 'awaiting_external',
            rawState: 'awaiting_external',
            createdAt: '2026-03-28T00:00:00Z',
          },
          {
            taskId: 'task-finalizing',
            source: 'temporal',
            title: 'Finalizing task',
            status: 'running',
            state: 'finalizing',
            rawState: 'finalizing',
            createdAt: '2026-03-28T00:00:00Z',
          },
        ],
      }),
    } as Response);

    renderWithClient(<WorkflowListPage payload={mockPayload} />);

    await waitFor(() => {
      expect(
        document.querySelectorAll(
          '.queue-table-cell-status [data-effect="shimmer-sweep"], .queue-card-status [data-effect="shimmer-sweep"]',
        ),
      ).toHaveLength(6);
    });

    const activePills = document.querySelectorAll<HTMLElement>(
      '.queue-table-cell-status [data-effect="shimmer-sweep"], .queue-card-status [data-effect="shimmer-sweep"]',
    );
    expect(activePills).toHaveLength(6);
    expect(Array.from(activePills).filter((pill) => pill.dataset.state === 'executing')).toHaveLength(2);
    expect(Array.from(activePills).filter((pill) => pill.dataset.state === 'planning')).toHaveLength(2);
    expect(Array.from(activePills).filter((pill) => pill.dataset.state === 'finalizing')).toHaveLength(2);
    for (const pill of activePills) {
      const label = pill.dataset.state;
      if (label !== 'executing' && label !== 'planning' && label !== 'finalizing') {
        throw new Error(`Unexpected active status pill state: ${label}`);
      }
      const visibleLabel = label.charAt(0).toUpperCase() + label.slice(1);
      expect(pill.dataset.state).toBe(label);
      expect(pill.className).toContain(`is-${label}`);
      expect(pill.className).toContain(`status-${label === 'executing' ? 'running' : label}`);
      expect(pill.dataset.shimmerLabel).toBe(visibleLabel);
      expect(pill.getAttribute('aria-label')).toBe(visibleLabel);
      expect(pill.textContent).toBe(visibleLabel);
      expect(pill.querySelector('.status-letter-wave')?.getAttribute('aria-hidden')).toBe('true');
      const glyphs = Array.from(pill.querySelectorAll<HTMLElement>('.status-letter-wave__glyph'));
      expect(glyphs).toHaveLength(visibleLabel.length);
      expect(glyphs.map((glyph) => glyph.textContent).join('')).toBe(visibleLabel);
      expect(glyphs.map((glyph) => glyph.style.getPropertyValue('--mm-letter-index'))).toEqual(
        Array.from({ length: visibleLabel.length }, (_, index) => String(index)),
      );
      expect(glyphs.every((glyph) => glyph.style.getPropertyValue('--mm-letter-count') === String(visibleLabel.length))).toBe(true);
    }

    expect(EXECUTING_STATUS_PILL_TRACEABILITY.relatedJiraIssues).toContain('MM-489');
    expect(EXECUTING_STATUS_PILL_TRACEABILITY.relatedJiraIssues).toContain('MM-490');
    expect(EXECUTING_STATUS_PILL_TRACEABILITY.relatedJiraIssues).toContain('MM-491');
    expect(EXECUTING_STATUS_PILL_TRACEABILITY.relatedJiraIssues).toContain('MM-1035');
    expect(EXECUTING_STATUS_PILL_TRACEABILITY.relatedJiraIssues).toContain('MM-1036');

    const waitingPills = screen.getAllByText('Awaiting dependencies');
    expect(waitingPills.length).toBeGreaterThan(0);
    for (const pill of waitingPills) {
      expect(pill.closest('span')?.dataset.effect).toBeUndefined();
      expect(pill.closest('span')?.className).toContain('status-awaiting-dependencies');
    }

    const nonExecutingStatusPills = Array.from(
      document.querySelectorAll<HTMLElement>('.queue-table-cell-status span.status, .queue-card-status span.status'),
    );

    const awaitingPills = nonExecutingStatusPills.filter((pill) => pill.textContent === 'Awaiting external');
    expect(awaitingPills.length).toBeGreaterThan(0);
    for (const pill of awaitingPills) {
      expect(pill.dataset.effect).toBeUndefined();
      expect(pill.className).toContain('status-awaiting-external');
    }

  });

  it('resolves raw status aliases to the canonical executing shimmer treatment', async () => {
    fetchSpy.mockResolvedValue({
      ok: true,
      json: async () => ({
        items: [
          {
            taskId: 'workflow-running-alias',
            source: 'temporal',
            title: 'Running alias task',
            status: 'running',
            state: 'completed',
            rawState: 'running',
            createdAt: '2026-03-28T00:00:00Z',
          },
          {
            taskId: 'task-unknown-raw',
            source: 'temporal',
            title: 'Unknown raw task',
            status: 'running',
            state: 'executing',
            rawState: 'intervention_requested',
            createdAt: '2026-03-28T00:00:00Z',
          },
        ],
      }),
    } as Response);

    renderWithClient(<WorkflowListPage payload={mockPayload} />);

    // Both the desktop table cell and the mobile card render each row, so two
    // rows produce four shimmer pills: the `running` alias canonicalizes to
    // executing, and the unrecognized rawState must not mask the canonical
    // `executing` state that follows it.
    await waitFor(() => {
      expect(
        document.querySelectorAll(
          '.queue-table-cell-status [data-effect="shimmer-sweep"][data-state="executing"], .queue-card-status [data-effect="shimmer-sweep"][data-state="executing"]',
        ),
      ).toHaveLength(4);
    });

    const pills = document.querySelectorAll<HTMLElement>(
      '.queue-table-cell-status [data-effect="shimmer-sweep"], .queue-card-status [data-effect="shimmer-sweep"]',
    );
    expect(pills).toHaveLength(4);
    for (const pill of pills) {
      expect(pill.className).toContain('status-running');
      expect(pill.className).toContain('is-executing');
      expect(pill.getAttribute('aria-label')).toBe('Executing');
      expect(pill.querySelector('.status-letter-wave')?.getAttribute('data-label')).toBe('Executing');
    }
  });

  it('keeps started time out of the workflow list presentation', async () => {
    renderWithClient(<WorkflowListPage payload={mockPayload} />);

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

    renderWithClient(<WorkflowListPage payload={mockPayload} />);

    const lateTitle = (await screen.findAllByText('Late scheduled task'))[0]!;
    const table = lateTitle.closest('table') as HTMLTableElement;
    const earlyLink = within(table).getByRole('link', { name: 'Early scheduled task' });
    const lateLink = within(table).getByRole('link', { name: 'Late scheduled task' });
    expect(
      lateLink.compareDocumentPosition(earlyLink) & Node.DOCUMENT_POSITION_FOLLOWING,
    ).toBeTruthy();
    expect((await screen.findAllByText('—')).length).toBeGreaterThan(0);
  });

  it('sorts the Updated column by the displayed updated timestamp, including closedAt', async () => {
    fetchSpy.mockResolvedValue({
      ok: true,
      json: async () => ({
        items: [
          {
            taskId: 'task-open',
            source: 'temporal',
            title: 'Open later task',
            status: 'queued',
            state: 'scheduled',
            rawState: 'scheduled',
            scheduledFor: '2026-04-15T10:00:00Z',
            createdAt: '2026-04-15T05:00:00Z',
          },
          {
            taskId: 'task-closed',
            source: 'temporal',
            title: 'Closed latest task',
            status: 'completed',
            state: 'completed',
            rawState: 'completed',
            scheduledFor: '2026-04-15T01:00:00Z',
            createdAt: '2026-04-15T00:30:00Z',
            closedAt: '2026-04-15T20:00:00Z',
          },
        ],
      }),
    } as Response);

    renderWithClient(<WorkflowListPage payload={mockPayload} />);

    const closedTitle = (await screen.findAllByText('Closed latest task'))[0]!;
    const table = closedTitle.closest('table') as HTMLTableElement;
    const closedLink = within(table).getByRole('link', { name: 'Closed latest task' });
    const openLink = within(table).getByRole('link', { name: 'Open later task' });
    // When updatedAt is absent, the Updated sort keeps the fallback behavior:
    // closedAt wins over scheduledFor/createdAt for completed rows.
    expect(
      closedLink.compareDocumentPosition(openLink) & Node.DOCUMENT_POSITION_FOLLOWING,
    ).toBeTruthy();
  });

  it('sorts the Updated column by API updatedAt when it is present', async () => {
    fetchSpy.mockResolvedValue({
      ok: true,
      json: async () => ({
        items: [
          {
            taskId: 'task-created-late',
            source: 'temporal',
            title: 'Created late task',
            status: 'executing',
            state: 'executing',
            rawState: 'executing',
            createdAt: '2026-04-15T19:00:00Z',
            updatedAt: '2026-04-15T19:05:00Z',
          },
          {
            taskId: 'task-updated-later',
            source: 'temporal',
            title: 'Updated later task',
            status: 'executing',
            state: 'executing',
            rawState: 'executing',
            createdAt: '2026-04-15T00:00:00Z',
            updatedAt: '2026-04-15T20:00:00Z',
          },
        ],
      }),
    } as Response);

    renderWithClient(<WorkflowListPage payload={mockPayload} />);

    const updatedLaterTitle = (await screen.findAllByText('Updated later task'))[0]!;
    const table = updatedLaterTitle.closest('table') as HTMLTableElement;
    const updatedLaterLink = within(table).getByRole('link', { name: 'Updated later task' });
    const createdLateLink = within(table).getByRole('link', { name: 'Created late task' });
    expect(
      updatedLaterLink.compareDocumentPosition(createdLateLink) & Node.DOCUMENT_POSITION_FOLLOWING,
    ).toBeTruthy();
  });

  it('reuses the trimmed repository filter for both the request and the query key', async () => {
    renderWithClient(<WorkflowListPage payload={mockPayload} />);

    await screen.findAllByText('Example task');
    const baselineCalls = executionListCalls().length;

    openFilterDrawer();
    expect(screen.getAllByPlaceholderText('repo starts with…')).not.toHaveLength(0);
    expect(screen.getByLabelText('Repository filter value').getAttribute('title')).toBe(
      'Prefix match: finds repository names that start with this text.',
    );
    fireEvent.change(screen.getByLabelText('Repository filter value'), {
      target: { value: 'owner/repo' },
    });

    expect(executionListCalls().length).toBe(baselineCalls);
    applyFilterDrawer();

    await waitFor(() => {
      expect(executionListCalls().length).toBe(baselineCalls + 1);
    });
    expect(lastExecutionListUrl()).toBe(
      '/api/executions?source=temporal&pageSize=50&repoContains=owner%2Frepo',
    );
    await screen.findAllByText('Example task');

    fireEvent.click(screen.getByRole('button', { name: /Repo filter: owner\/repo/i }));
    fireEvent.change(screen.getByLabelText('Repository filter value'), {
      target: { value: 'owner/repo ' },
    });

    const repositoryInput = screen.getByLabelText('Repository filter value') as HTMLInputElement;
    await waitFor(() => {
      expect(repositoryInput.value).toBe('owner/repo ');
    });
    expect(executionListCalls().length).toBe(baselineCalls + 1);
  }, 10_000);

  it('labels the lifecycle filter as status and exposes canonical status options', async () => {
    renderWithClient(<WorkflowListPage payload={mockPayload} />);

    await screen.findAllByText('Example task');

    openFilterDrawer();
    const statusFilter = (await screen.findByLabelText('Status filter value')) as HTMLSelectElement;
    // Skip the leading placeholder option that prompts the user to add a value.
    const options = Array.from(statusFilter.options)
      .map((option) => option.value)
      .filter((value) => value !== '');
    const optionLabels = Array.from(statusFilter.options)
      .filter((option) => option.value !== '')
      .map((option) => option.textContent);

    expect(statusFilter.multiple).toBe(false);
    expect(options).toEqual([
      'scheduled',
      'initializing',
      'waiting_on_dependencies',
      'planning',
      'awaiting_slot',
      'executing',
      'awaiting_external',
      'intervention_requested',
      'finalizing',
      'no_commit',
      'completed',
      'failed',
      'canceled',
    ]);
    expect(optionLabels).toEqual([
      'scheduled',
      'initializing',
      'waiting on dependencies',
      'planning',
      'awaiting slot',
      'executing',
      'awaiting external',
      'intervention requested',
      'finalizing',
      'no commit',
      'completed',
      'failed',
      'canceled',
    ]);
    expect(options).toContain('completed');
    expect(options).not.toContain('succeeded');

    const baselineCalls = executionListCalls().length;
    fireEvent.change(statusFilter, { target: { value: 'completed' } });
    expect(executionListCalls().length).toBe(baselineCalls);
    applyFilterDrawer();

    await waitFor(() => {
      expect(executionListCalls().length).toBe(baselineCalls + 1);
    });
    expect(lastExecutionListUrl()).toBe(
      '/api/executions?source=temporal&pageSize=50&stateIn=completed',
    );
  });

  it('builds status filters as removable pills', async () => {
    renderWithClient(<WorkflowListPage payload={mockPayload} />);

    await screen.findAllByText('Example task');

    openFilterDrawer();
    const statusFilter = (await screen.findByLabelText('Status filter value')) as HTMLSelectElement;

    fireEvent.change(statusFilter, { target: { value: 'completed' } });
    fireEvent.change(statusFilter, { target: { value: 'failed' } });

    const pillList = screen.getByLabelText('Selected status filters');
    expect(pillList.textContent).toContain('Completed');
    expect(pillList.textContent).toContain('Failed');
    expect(pillList.querySelector('.status-completed')).toBeTruthy();
    expect(pillList.querySelector('.status-failed')).toBeTruthy();

    fireEvent.click(screen.getByRole('button', { name: 'Remove completed' }));
    expect(pillList.textContent).not.toContain('Completed');
    expect(pillList.textContent).toContain('Failed');

    applyFilterDrawer();

    await waitFor(() => {
      expect(lastExecutionListUrl()).toBe(
        '/api/executions?source=temporal&pageSize=50&stateIn=failed',
      );
    });
  });

  it('renders selected status filters with the shared execution status pill colors', async () => {
    renderWithClient(<WorkflowListPage payload={mockPayload} />);

    await screen.findAllByText('Example task');

    openFilterDrawer();
    const statusFilter = (await screen.findByLabelText('Status filter value')) as HTMLSelectElement;

    for (const value of [
      'executing',
      'scheduled',
      'awaiting_slot',
      'waiting_on_dependencies',
      'awaiting_external',
      'initializing',
      'planning',
      'finalizing',
      'canceled',
      'no_commit',
    ]) {
      fireEvent.change(statusFilter, { target: { value } });
    }

    const pillList = screen.getByLabelText('Selected status filters');
    expect(pillList.querySelector('.status-running')).toBeTruthy();
    expect(pillList.querySelector('.status-scheduled')).toBeTruthy();
    expect(pillList.querySelector('.status-awaiting-slot')).toBeTruthy();
    expect(pillList.querySelector('.status-awaiting-dependencies')).toBeTruthy();
    expect(pillList.querySelector('.status-awaiting-external')).toBeTruthy();
    expect(pillList.querySelector('.status-initializing')).toBeTruthy();
    expect(pillList.querySelector('.status-planning')).toBeTruthy();
    expect(pillList.querySelector('.status-finalizing')).toBeTruthy();
    expect(pillList.querySelector('.status-canceled')).toBeTruthy();
    expect(pillList.querySelector('.status-no-commit')).toBeTruthy();
    expect(pillList.querySelector('[data-effect="shimmer-sweep"]')).toBeNull();
  });

  it('stages status changes until Apply and discards them on cancel, Escape, or outside click', async () => {
    renderWithClient(<WorkflowListPage payload={mockPayload} />);

    await screen.findAllByText('Example task');
    const baselineCalls = executionListCalls().length;

    openFilterDrawer();
    fireEvent.change(await screen.findByLabelText('Status filter value'), { target: { value: 'completed' } });
    expect(executionListCalls().length).toBe(baselineCalls);
    fireEvent.click(screen.getByRole('button', { name: 'Cancel filters' }));
    expect(screen.queryByRole('dialog', { name: 'Advanced filters' })).toBeNull();
    expect(executionListCalls().length).toBe(baselineCalls);

    openFilterDrawer();
    fireEvent.change(await screen.findByLabelText('Status filter value'), { target: { value: 'failed' } });
    fireEvent.keyDown(screen.getByRole('dialog', { name: 'Advanced filters' }), { key: 'Escape' });
    expect(screen.queryByRole('dialog', { name: 'Advanced filters' })).toBeNull();
    expect(executionListCalls().length).toBe(baselineCalls);

    openFilterDrawer();
    fireEvent.change(await screen.findByLabelText('Status filter value'), { target: { value: 'planning' } });
    fireEvent.mouseDown(document.querySelector('.workflow-list-filter-drawer-overlay') as Element);
    expect(screen.queryByRole('dialog', { name: 'Advanced filters' })).toBeNull();
    expect(executionListCalls().length).toBe(baselineCalls);
  });

  it('moves focus into the drawer and applies staged text filters with Enter', async () => {
    renderWithClient(<WorkflowListPage payload={mockPayload} />);

    await screen.findAllByText('Example task');

    openFilterDrawer();

    // The drawer focuses its first control so keyboard users land inside it.
    const idInput = (await screen.findByLabelText('ID filter value')) as HTMLInputElement;
    await waitFor(() => {
      expect(document.activeElement).toBe(idInput);
    });

    const titleInput = (await screen.findByLabelText('Title filter value')) as HTMLInputElement;
    fireEvent.change(titleInput, { target: { value: 'Example' } });
    fireEvent.keyDown(titleInput, { key: 'Enter' });

    await waitFor(() => {
      expect(lastExecutionListUrl()).toBe(
        '/api/executions?source=temporal&pageSize=50&titleContains=Example',
      );
      expect(screen.queryByRole('dialog', { name: 'Advanced filters' })).toBeNull();
      expect(screen.getByRole('button', { name: 'Title filter: Example' })).toBeTruthy();
    });
  });

  it('does not apply staged filters when Enter is pressed on drawer action buttons', async () => {
    renderWithClient(<WorkflowListPage payload={mockPayload} />);

    await screen.findAllByText('Example task');
    const baselineCalls = executionListCalls().length;

    openFilterDrawer();
    fireEvent.change(await screen.findByLabelText('Title filter value'), { target: { value: 'Example' } });
    const cancelButton = screen.getByRole('button', { name: 'Cancel filters' });
    cancelButton.focus();

    fireEvent.keyDown(cancelButton, { key: 'Enter' });

    expect(executionListCalls().length).toBe(baselineCalls);
    expect(lastExecutionListUrl()).toBe('/api/executions?source=temporal&pageSize=50');
    expect(screen.getByRole('dialog', { name: 'Advanced filters' })).toBeTruthy();
  });

  it('applies status exclude semantics and removes only the selected chip', async () => {
    window.history.pushState({}, 'Paged', '/workflows?nextPageToken=stale-token');
    renderWithClient(<WorkflowListPage payload={mockPayload} />);

    await screen.findAllByText('Example task');
    openFilterDrawer();
    fireEvent.change(screen.getByLabelText('Status filter mode'), { target: { value: 'exclude' } });
    fireEvent.change(screen.getByLabelText('Status filter value'), { target: { value: 'canceled' } });
    applyFilterDrawer();

    await waitFor(() => {
      expect(lastExecutionListUrl()).toBe(
        '/api/executions?source=temporal&pageSize=50&stateNotIn=canceled',
      );
    });
    expect(window.location.search).toBe('?stateNotIn=canceled&limit=50');
    expect(screen.getByRole('button', { name: 'Status filter: not canceled' })).toBeTruthy();

    fireEvent.click(screen.getByRole('button', { name: 'Remove Status filter' }));

    await waitFor(() => {
      expect(lastExecutionListUrl()).toBe(
        '/api/executions?source=temporal&pageSize=50',
      );
    });
  });

  it('selects multiple status values and submits them through stateIn', async () => {
    renderWithClient(<WorkflowListPage payload={mockPayload} />);

    await screen.findAllByText('Example task');
    openFilterDrawer();

    const statusFilter = (await screen.findByLabelText('Status filter value')) as HTMLSelectElement;
    fireEvent.change(statusFilter, { target: { value: 'completed' } });
    fireEvent.change(statusFilter, { target: { value: 'failed' } });
    applyFilterDrawer();

    await waitFor(() => {
      expect(lastExecutionListUrl()).toBe(
        '/api/executions?source=temporal&pageSize=50&stateIn=completed%2Cfailed',
      );
    });
    expect(screen.getByRole('button', { name: 'Status filter: completed, failed' })).toBeTruthy();
    expect(screen.queryByRole('button', { name: /Status filter: completed \+1/ })).toBeNull();

    fireEvent.click(screen.getByRole('button', { name: 'Status filter: completed, failed' }));
    const selectedStatuses = screen.getByLabelText('Selected status filters');
    expect(selectedStatuses.textContent).toContain('Completed');
    expect(selectedStatuses.textContent).toContain('Failed');
    expect(selectedStatuses.textContent).not.toContain('Canceled');
  });

  it('summarizes multi-value excluded status filters unambiguously with a bounded label', async () => {
    renderWithClient(<WorkflowListPage payload={mockPayload} />);

    await screen.findAllByText('Example task');
    openFilterDrawer();
    fireEvent.change(await screen.findByLabelText('Status filter mode'), { target: { value: 'exclude' } });

    const statusFilter = (await screen.findByLabelText('Status filter value')) as HTMLSelectElement;
    fireEvent.change(statusFilter, { target: { value: 'completed' } });
    fireEvent.change(statusFilter, { target: { value: 'failed' } });
    fireEvent.change(statusFilter, { target: { value: 'planning' } });
    fireEvent.change(statusFilter, { target: { value: 'canceled' } });
    applyFilterDrawer();

    await waitFor(() => {
      expect(lastExecutionListUrl()).toBe(
        '/api/executions?source=temporal&pageSize=50&stateNotIn=completed%2Cfailed%2Cplanning%2Ccanceled',
      );
    });
    expect(screen.getByRole('button', { name: 'Status filter: not (completed, failed, planning +1)' })).toBeTruthy();
  });

  it('resets every active filter from the drawer', async () => {
    renderWithClient(<WorkflowListPage payload={mockPayload} />);

    await screen.findAllByText('Example task');
    openFilterDrawer();
    fireEvent.change(await screen.findByLabelText('Status filter value'), { target: { value: 'completed' } });
    applyFilterDrawer();

    await waitFor(() => {
      expect(screen.getByRole('button', { name: 'Status filter: completed' })).toBeTruthy();
    });

    fireEvent.click(screen.getByRole('button', { name: 'Status filter: completed' }));
    fireEvent.click(screen.getByRole('button', { name: 'Reset filters' }));

    await waitFor(() => {
      expect(screen.queryByRole('button', { name: 'Status filter: completed' })).toBeNull();
      expect(lastExecutionListUrl()).toBe('/api/executions?source=temporal&pageSize=50');
    });
  });

  it('clears stale cursor state when the page size changes', async () => {
    window.history.pushState({}, 'Paged', '/workflows?nextPageToken=stale-token&limit=50');
    renderWithClient(<WorkflowListPage payload={mockPayload} />);

    await waitFor(() => {
      expect(fetchSpy.mock.calls.at(-1)?.[0]).toBe(
        '/api/executions?source=temporal&pageSize=50&nextPageToken=stale-token',
      );
    });
    await screen.findAllByText('Example task');

    fireEvent.change(screen.getByLabelText('Show'), { target: { value: '100' } });

    await waitFor(() => {
      expect(fetchSpy.mock.calls.at(-1)?.[0]).toBe(
        '/api/executions?source=temporal&pageSize=100',
      );
    });
    expect(window.location.search).toBe('?limit=100');
  });

  it('recovers stale cursor context when returning from workflow detail (MM-998, MM-975)', async () => {
    window.history.pushState(
      {},
      'Workspace return',
      '/workflows?stateIn=completed&limit=50&nextPageToken=stale-token&returnFromWorkflowDetail=1',
    );
    fetchSpy.mockImplementation((input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes('nextPageToken=stale-token')) {
        return Promise.resolve({
          ok: true,
          json: async () => ({ items: [], count: 1 }),
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
              state: 'completed',
              rawState: 'completed',
              createdAt: '2026-03-28T00:00:00Z',
            },
          ],
          count: 1,
        }),
      } as Response);
    });

    renderWithClient(<WorkflowListPage payload={mockPayload} />);

    await waitFor(() => {
      expect(executionListCalls().map(([url]) => String(url))).toContain(
        '/api/executions?source=temporal&pageSize=50&nextPageToken=stale-token&stateIn=completed',
      );
    });
    expect(await screen.findByText('Saved pagination was no longer available. Showing the first page.')).toBeTruthy();

    await waitFor(() => {
      expect(lastExecutionListUrl()).toBe(
        '/api/executions?source=temporal&pageSize=50&stateIn=completed',
      );
    });
    expect(window.location.search).toBe('?stateIn=completed&limit=50');

    fireEvent.click(screen.getByRole('button', { name: 'Dismiss' }));
    expect(
      screen.queryByText('Saved pagination was no longer available. Showing the first page.'),
    ).toBeNull();
  });

  it('MM-1008 focuses the workflow list region when expanded from workspace detail', async () => {
    window.history.pushState(
      {},
      'Workspace return focus',
      '/workflows?stateIn=completed&limit=50&returnFromWorkflowDetail=1',
    );
    const focusSpy = vi.spyOn(HTMLElement.prototype, 'focus');

    renderWithClient(<WorkflowListPage payload={mockPayload} />);

    await screen.findAllByText('Example task');
    const listRegion = screen.getByRole('region', { name: 'Workflow list' });
    await waitFor(() => expect(document.activeElement).toBe(listRegion));
    expect(listRegion.getAttribute('tabindex')).toBe('-1');
    expect(focusSpy).toHaveBeenCalledWith({ preventScroll: true });
    focusSpy.mockRestore();
  });

  it('MM-1008 does not make the workflow list region focusable on normal list visits', async () => {
    window.history.pushState({}, 'Normal workflows', '/workflows?stateIn=completed&limit=50');

    renderWithClient(<WorkflowListPage payload={mockPayload} />);

    await screen.findAllByText('Example task');
    expect(screen.getByRole('region', { name: 'Workflow list' }).getAttribute('tabindex')).toBeNull();
  });

  it('MM-1008 focuses the workflow list region on a plain expand return intent', async () => {
    markWorkflowListReturnFocusIntent();
    window.history.pushState({}, 'Plain workspace return focus', '/workflows');

    renderWithClient(<WorkflowListPage payload={mockPayload} />);

    await screen.findAllByText('Example task');
    const listRegion = screen.getByRole('region', { name: 'Workflow list' });
    await waitFor(() => expect(document.activeElement).toBe(listRegion));
    expect(listRegion.getAttribute('tabindex')).toBe('-1');
    expect(window.sessionStorage.getItem('moonmind.workflowList.returnFocusIntent')).toBeNull();
  });

  it('supports skill and date filter chips with blank semantics', async () => {
    fetchSpy.mockResolvedValue({
      ok: true,
      json: async () => ({
        items: [
          {
            taskId: 'task-123',
            source: 'temporal',
            title: 'Example task',
            status: 'completed',
            state: 'completed',
            rawState: 'completed',
            targetSkill: 'moonspec-implement',
            createdAt: '2026-03-28T00:00:00Z',
            closedAt: null,
            scheduledFor: null,
          },
        ],
      }),
    } as Response);

    renderWithClient(<WorkflowListPage payload={mockPayload} />);

    await screen.findAllByText('Example task');
    openFilterDrawer();
    fireEvent.change(screen.getByLabelText('Skill filter value'), {
      target: { value: 'moonspec-implement' },
    });
    applyFilterDrawer();

    await waitFor(() => {
      expect(lastExecutionListUrl()).toContain('targetSkillIn=moonspec-implement');
    });
    expect(screen.getByRole('button', { name: 'Skill filter: moonspec-implement' })).toBeTruthy();
    await screen.findAllByText('Example task');

    openFilterDrawer();
    fireEvent.change(screen.getByLabelText('Finished blank values'), { target: { value: 'include' } });
    applyFilterDrawer();

    await waitFor(() => {
      expect(lastExecutionListUrl()).toContain('finishedBlank=include');
    });
    expect(screen.getByRole('button', { name: 'Finished filter: blank' })).toBeTruthy();
  });

  it('shows a current-page values notice when facet values fail to load', async () => {
    fetchSpy.mockImplementation((input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes('/api/executions/facets')) {
        return Promise.resolve({
          ok: false,
          statusText: 'Service Unavailable',
          json: async () => ({ detail: { code: 'temporal_unavailable' } }),
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
              state: 'completed',
              rawState: 'completed',
              repository: 'owner/repo',
              createdAt: '2026-03-28T00:00:00Z',
            },
          ],
        }),
      } as Response);
    });

    renderWithClient(<WorkflowListPage payload={mockPayload} />);

    await screen.findAllByText('Example task');
    openFilterDrawer();

    expect(
      (await screen.findAllByText('Facet values unavailable. Showing current page values only.')).length,
    ).toBeGreaterThan(0);
    expect(screen.getByRole('option', { name: 'owner/repo' })).toBeTruthy();
    expect(screen.getAllByText('Example task').length).toBeGreaterThan(0);
  });

  it('shows an empty range summary on an empty page beyond the first page', async () => {
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

    renderWithClient(<WorkflowListPage payload={mockPayload} />);

    await screen.findByText('1 - 1');
    fireEvent.click(screen.getByRole('button', { name: 'Next page' }));

    expect(await screen.findByText('0 - 0')).toBeTruthy();
    expect(screen.getByText('21 total entries')).toBeTruthy();
  });

  it('shows clickable active filter chips and removes individual filters from the chip row', async () => {
    renderWithClient(<WorkflowListPage payload={mockPayload} />);

    await screen.findAllByText('Example task');
    openFilterDrawer();
    fireEvent.change(await screen.findByLabelText('Status filter value'), { target: { value: 'completed' } });
    applyFilterDrawer();
    await screen.findAllByText('Example task');
    openFilterDrawer();
    fireEvent.change(screen.getByLabelText('Repository filter value'), { target: { value: 'owner/repo' } });
    applyFilterDrawer();
    await screen.findAllByText('Example task');
    openFilterDrawer();
    fireEvent.click(screen.getByRole('checkbox', { name: /Not applicable/ }));
    applyFilterDrawer();

    await waitFor(() => {
      const activeFilterText = document.querySelector('.workflow-list-filter-chips')?.textContent || '';
      expect(activeFilterText).toContain('completed');
      expect(activeFilterText).toContain('owner/repo');
      expect(activeFilterText).toContain('Not applicable');
    });

    fireEvent.click(screen.getByRole('button', { name: 'Repo filter: owner/repo' }));
    expect(screen.getByRole('dialog', { name: 'Advanced filters' })).toBeTruthy();
    await waitFor(() => {
      expect(lastExecutionListUrl()).toBe(
        '/api/executions?source=temporal&pageSize=50&stateIn=completed&repoContains=owner%2Frepo&providerProfileStateIn=not_applicable',
      );
    });
    // The drawer opens focused on the chip's field.
    await waitFor(() => {
      expect(document.activeElement).toBe(screen.getByLabelText('Repository filter value'));
    });

    fireEvent.click(screen.getByRole('button', { name: 'Remove Status filter' }));

    await waitFor(() => {
      expect(lastExecutionListUrl()).toBe(
        '/api/executions?source=temporal&pageSize=50&repoContains=owner%2Frepo&providerProfileStateIn=not_applicable',
      );
      expect((screen.getByLabelText('Status filter value') as HTMLSelectElement).value).toBe('');
    });
  });

  it('marks mobile card details links as the only full-width card action', async () => {
    renderWithClient(<WorkflowListPage payload={mockPayload} />);

    const detailsLink = await screen.findByRole('button', { name: 'View details' });

    expect(detailsLink.classList.contains('queue-card-details-action')).toBe(true);
    expect(detailsLink.closest('.queue-card-actions')).toBeTruthy();
  });

  it('MM-1001 keeps mobile workflow cards, filters, and detail navigation available', async () => {
    renderWithClient(
      <WorkflowListPage
        payload={{
          ...mockPayload,
          initialData: {
            dashboardConfig: {
              features: {
                temporalDashboard: {
                  workspaceShellEnabled: true,
                },
              },
            },
          },
        }}
      />,
    );

    await screen.findAllByText('Example task');

    const cardList = document.querySelector('.queue-card-list') as HTMLElement;
    expect(cardList).toBeTruthy();

    const cardTitle = await within(cardList).findByRole('link', { name: 'Example task' });
    const detailsLink = within(cardList).getByRole('button', { name: 'View details' });

    expect(cardTitle.getAttribute('href')).toBe('/workflows/task-123?limit=50&source=temporal');
    expect(detailsLink.getAttribute('href')).toBe('/workflows/task-123?limit=50&source=temporal');
    expect(screen.getByRole('button', { name: 'Filters' })).toBeTruthy();

    openFilterDrawer();
    fireEvent.change(await screen.findByLabelText('Status filter value'), {
      target: { value: 'completed' },
    });
    applyFilterDrawer();

    expect(await screen.findByRole('button', { name: 'Status filter: completed' })).toBeTruthy();
    expect(await within(cardList).findByRole('link', { name: 'Example task' })).toBeTruthy();
    expect(screen.getByRole('button', { name: 'Filters' })).toBeTruthy();
  });

  it('MM-1010 keeps desktop workspace sidebar controls out of mobile workflow list accessibility', async () => {
    renderWithClient(
      <WorkflowListPage
        payload={{
          ...mockPayload,
          initialData: {
            dashboardConfig: {
              features: {
                temporalDashboard: {
                  workspaceShellEnabled: true,
                },
              },
            },
          },
        }}
      />,
    );

    await screen.findAllByText('Example task');
    const cardList = document.querySelector('.queue-card-list') as HTMLElement | null;
    expect(cardList).toBeTruthy();
    expect(within(cardList as HTMLElement).getByRole('link', { name: 'Example task' })).toBeTruthy();
    expect(screen.queryByRole('complementary', { name: 'Workflow navigation' })).toBeNull();
    expect(screen.queryByRole('button', { name: 'Close sidebar' })).toBeNull();
    expect(screen.queryByRole('button', { name: 'Open workflow sidebar' })).toBeNull();
    expect(screen.queryByRole('link', { name: 'Expand to full list' })).toBeNull();
    expect(document.querySelector('.workflow-workspace-shell')).toBeNull();
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

    renderWithClient(<WorkflowListPage payload={mockPayload} />);

    const nextButton = await screen.findByRole('button', { name: 'Next page' });
    fireEvent.click(nextButton);

    await waitFor(() => {
      expect(screen.getByText('No workflows found for the current filters.')).toBeTruthy();
    });

    expect(screen.getByRole('button', { name: 'Previous page' }).getAttribute('disabled')).toBeNull();
  });

  it('keeps next enabled with a continuation message on excluded-only empty pages', async () => {
    // MoonLadderStudios/MoonMind#3947 (R2): zero product items plus a
    // continuation token is navigable, not an empty repository.
    fetchSpy.mockResolvedValue({
      ok: true,
      json: async () => ({
        items: [],
        nextPageToken: 'onward-token',
        count: null,
        countMode: 'estimated_or_unknown',
      }),
    } as Response);

    renderWithClient(<WorkflowListPage payload={mockPayload} />);

    await waitFor(() => {
      expect(screen.getByText(/No product workflows on this page/)).toBeTruthy();
    });

    expect(screen.getByRole('button', { name: 'Next page' }).getAttribute('disabled')).toBeNull();
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

    renderWithClient(<WorkflowListPage payload={mockPayload} />);

    expect((await screen.findAllByText('Blocked by 2 prerequisites'))[0]).toBeTruthy();
  });

  it('renders the recorded Provider Profile instead of the runtime in list rows', async () => {
    fetchSpy.mockResolvedValue({
      ok: true,
      json: async () => ({
        items: [
          {
            taskId: 'task-321',
            source: 'temporal',
            targetRuntime: 'codex_cli',
            providerProfile: {
              selectionState: 'recorded',
              profiles: [{ id: 'acct-1', label: 'OpenAI · Primary', harness: 'codex' }],
              profileCount: 1,
            },
            title: 'Readable runtime task',
            status: 'running',
            state: 'executing',
            rawState: 'executing',
            createdAt: '2026-03-28T00:00:00Z',
          },
        ],
      }),
    } as Response);

    renderWithClient(<WorkflowListPage payload={mockPayload} />);

    expect((await screen.findAllByText('Readable runtime task'))[0]).toBeTruthy();
    expect((await screen.findAllByText('OpenAI · Primary'))[0]).toBeTruthy();
    expect(screen.getAllByText('Harness: codex')[0]).toBeTruthy();
    expect(screen.queryByText('Codex CLI')).toBeNull();
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

    renderWithClient(<WorkflowListPage payload={mockPayload} />);

    const titleMatches = await screen.findAllByText('Long child workflow id task');
    const table = titleMatches
      .map((element) => element.closest('table'))
      .find((candidate): candidate is HTMLTableElement => Boolean(candidate));
    // The scan-first table leads with the Workflow column and one Updated column.
    expect(table?.querySelectorAll('col.queue-table-column-workflow')).toHaveLength(1);
    expect(table?.querySelectorAll('col.queue-table-column-date')).toHaveLength(1);
    // The workflow id is not rendered in the list; the title link carries it to the detail view.
    const workflowCell = table?.querySelector('td.queue-table-cell-workflow');
    expect(workflowCell?.textContent).toContain('Long child workflow id task');
    expect(workflowCell?.textContent).not.toContain(longWorkflowId);
    const titleLink = workflowCell?.querySelector('a.workflow-list-row-title');
    expect(titleLink?.getAttribute('href')).toBe(
      `/workflows/${encodeURIComponent(longWorkflowId)}?limit=50&source=temporal`,
    );
  });

  it('does not render an Actions column when workflow actions are disabled', async () => {
    renderWithClient(<WorkflowListPage payload={mockPayload} />);

    await screen.findAllByText('Example task');
    expect(screen.queryByRole('columnheader', { name: 'Actions' })).toBeNull();
    expect(screen.queryByRole('button', { name: 'More actions' })).toBeNull();
  });

  it('renders a per-row Actions menu trigger when workflow actions are enabled', async () => {
    const actionsPayload: BootPayload = {
      page: 'workflow-list',
      apiBase: '/api',
      initialData: {
        dashboardConfig: {
          features: { temporalDashboard: { listEnabled: true, actionsEnabled: true } },
        },
      },
    };

    const { container } = renderWithClient(<WorkflowListPage payload={actionsPayload} />);

    await screen.findAllByText('Example task');
    expect(screen.getByRole('columnheader', { name: 'Actions' })).toBeTruthy();
    const triggers = screen.getAllByRole('button', { name: 'More actions' });
    expect(triggers.length).toBeGreaterThanOrEqual(1);

    for (const selector of ['.queue-table-cell-actions', '.queue-card-actions']) {
      const actions = container.querySelector(`${selector} .workflow-row-actions`) as HTMLElement;
      expect(within(actions).getAllByRole('button')).toHaveLength(1);
      expect(within(actions).getByRole('button', { name: 'More actions' })).toBeTruthy();
      expect(within(actions).queryByRole('menu')).toBeNull();
    }

    // Opening the menu lazily fetches the workflow's action capabilities.
    const detailCallsBefore = fetchSpy.mock.calls.filter(([url]) =>
      /\/executions\/task-123(?:\?|$)/.test(String(url)),
    );
    expect(detailCallsBefore).toHaveLength(0);
  });

  it('promotes "View options" into the Actions header and drops the Filters row on desktop', async () => {
    const actionsPayload: BootPayload = {
      page: 'workflow-list',
      apiBase: '/api',
      initialData: {
        dashboardConfig: {
          features: { temporalDashboard: { listEnabled: true, actionsEnabled: true } },
        },
      },
    };

    // Force the desktop breakpoint (jsdom has no matchMedia, so the component
    // otherwise defaults to the mobile layout).
    vi.stubGlobal(
      'matchMedia',
      (query: string) => ({
        matches: true,
        media: query,
        onchange: null,
        addEventListener: () => {},
        removeEventListener: () => {},
        addListener: () => {},
        removeListener: () => {},
        dispatchEvent: () => false,
      }),
    );

    try {
      renderWithClient(<WorkflowListPage payload={actionsPayload} />);
      await screen.findAllByText('Example task');

      // The results header row is dropped on desktop, so the Filters trigger is
      // gone in favor of the per-column filter buttons.
      expect(screen.queryByRole('button', { name: 'Filters' })).toBeNull();

      // The single "View options" control is now the icon button hosted to the
      // right of the Actions column header.
      const viewOptions = screen.getByRole('button', { name: 'View options' });
      expect(viewOptions.closest('th')?.classList.contains('queue-table-actions-header')).toBe(
        true,
      );

      // It still opens the preferences popover from its new location.
      fireEvent.click(viewOptions);
      expect(screen.getByRole('radio', { name: 'Compact' })).toBeTruthy();
    } finally {
      vi.unstubAllGlobals();
    }
  });

  it('keeps filter and view controls reachable on desktop filtered empty states', async () => {
    const actionsPayload: BootPayload = {
      page: 'workflow-list',
      apiBase: '/api',
      initialData: {
        dashboardConfig: {
          features: { temporalDashboard: { listEnabled: true, actionsEnabled: true } },
        },
      },
    };
    fetchSpy.mockResolvedValue({
      ok: true,
      json: async () => ({ items: [], count: 0 }),
    } as Response);
    window.history.pushState({}, 'Filtered workflows', '/workflows?stateIn=completed');

    vi.stubGlobal(
      'matchMedia',
      (query: string) => ({
        matches: true,
        media: query,
        onchange: null,
        addEventListener: () => {},
        removeEventListener: () => {},
        addListener: () => {},
        removeListener: () => {},
        dispatchEvent: () => false,
      }),
    );

    try {
      renderWithClient(<WorkflowListPage payload={actionsPayload} />);
      expect(await screen.findByText('No workflows found for the current filters.')).toBeTruthy();

      expect(document.querySelector('.workflow-list-results-header')).toBeTruthy();
      expect(screen.getByRole('columnheader', { name: 'Actions' })).toBeTruthy();
      expect(screen.getByRole('button', { name: 'Filters' })).toBeTruthy();
      expect(screen.getByRole('button', { name: 'View options' })).toBeTruthy();
      expect(screen.queryByRole('button', { name: 'Advanced filters' })).toBeNull();
    } finally {
      vi.unstubAllGlobals();
    }
  });

  it('keeps desktop header controls when actions are disabled', async () => {
    const actionsDisabledPayload: BootPayload = {
      page: 'workflow-list',
      apiBase: '/api',
      initialData: {
        dashboardConfig: {
          features: { temporalDashboard: { listEnabled: true, actionsEnabled: false } },
        },
      },
    };

    vi.stubGlobal(
      'matchMedia',
      (query: string) => ({
        matches: true,
        media: query,
        onchange: null,
        addEventListener: () => {},
        removeEventListener: () => {},
        addListener: () => {},
        removeListener: () => {},
        dispatchEvent: () => false,
      }),
    );

    try {
      renderWithClient(<WorkflowListPage payload={actionsDisabledPayload} />);
      await screen.findAllByText('Example task');

      expect(screen.queryByRole('columnheader', { name: 'Actions' })).toBeNull();
      expect(screen.getByRole('button', { name: 'Filters' })).toBeTruthy();
      const viewOptions = screen.getByRole('button', { name: 'View options' });
      expect(viewOptions.closest('th')).toBeNull();

      fireEvent.click(viewOptions);
      expect(screen.getByRole('radio', { name: 'Compact' })).toBeTruthy();
    } finally {
      vi.unstubAllGlobals();
    }
  });

  it('keeps a desktop entry point to the advanced filters drawer after dropping the Filters row', async () => {
    const actionsPayload: BootPayload = {
      page: 'workflow-list',
      apiBase: '/api',
      initialData: {
        dashboardConfig: {
          features: { temporalDashboard: { listEnabled: true, actionsEnabled: true } },
        },
      },
    };

    // Force the desktop breakpoint (jsdom has no matchMedia, so the component
    // otherwise defaults to the mobile layout).
    vi.stubGlobal(
      'matchMedia',
      (query: string) => ({
        matches: true,
        media: query,
        onchange: null,
        addEventListener: () => {},
        removeEventListener: () => {},
        addListener: () => {},
        removeListener: () => {},
        dispatchEvent: () => false,
      }),
    );

    try {
      renderWithClient(<WorkflowListPage payload={actionsPayload} />);
      await screen.findAllByText('Example task');

      // The top-row Filters trigger is gone, but the full filter surface must
      // stay reachable on desktop: the per-column buttons only cover
      // TABLE_COLUMN_FILTER_FIELDS, so drawer-only fields (ID, Skill, Scheduled,
      // Created, Finished) would otherwise be unreachable. The entry point moves
      // to a compact icon in the Actions header.
      expect(screen.queryByRole('button', { name: 'Filters' })).toBeNull();
      const advancedFilters = screen.getByRole('button', { name: 'Advanced filters' });
      expect(
        advancedFilters.closest('th')?.classList.contains('queue-table-actions-header'),
      ).toBe(true);

      // Opening it surfaces the advanced filters drawer, including a drawer-only
      // field (Skill) that has no per-column filter button.
      fireEvent.click(advancedFilters);
      const drawer = screen.getByRole('dialog', { name: 'Advanced filters' });
      expect(within(drawer).getByRole('region', { name: 'Skill filter' })).toBeTruthy();
    } finally {
      vi.unstubAllGlobals();
    }
  });

  // MM-952: scan-first desktop table information architecture.
  it('leads the desktop table with a title-first Workflow column and secondary compact id', async () => {
    fetchSpy.mockResolvedValue({
      ok: true,
      json: async () => ({
        items: [
          {
            taskId: 'mm:run:scan-first-001',
            source: 'temporal',
            title: 'Scan first task',
            status: 'running',
            state: 'executing',
            rawState: 'executing',
            createdAt: '2026-03-28T00:00:00Z',
          },
        ],
      }),
    } as Response);

    renderWithClient(<WorkflowListPage payload={mockPayload} />);

    const titleMatches = await screen.findAllByText('Scan first task');
    const table = titleMatches
      .map((element) => element.closest('table'))
      .find((candidate): candidate is HTMLTableElement => Boolean(candidate)) as HTMLTableElement;

    const headerLabels = Array.from(table.querySelectorAll('thead th')).map((th) => {
      const sortButton = th.querySelector('.table-sort-button');
      if (sortButton) return ((sortButton.getAttribute('aria-label') || '').split('.')[0] || '').trim();
      return th.querySelector('.workflow-list-static-header')?.textContent?.trim() || '';
    });

    // The first desktop column is Workflow, not ID, and no standalone ID column remains.
    expect(headerLabels[0]).toBe('Workflow');
    expect(headerLabels).not.toContain('ID');
    expect(headerLabels).not.toContain('Next action');
    expect(headerLabels).toContain('Progress');
    // Status is visible before any timestamp column.
    expect(headerLabels.indexOf('Status')).toBeLessThan(headerLabels.indexOf('Updated'));

    const workflowCell = within(table).getAllByText('Scan first task')[0]!.closest(
      'td.queue-table-cell-workflow',
    ) as HTMLTableCellElement;
    // The workflow title is the primary anchor linking to the detail view.
    const titleLink = workflowCell.querySelector('a.workflow-list-row-title');
    expect(titleLink?.textContent).toBe('Scan first task');
    expect(titleLink?.getAttribute('href')).toBe(
      '/workflows/mm%3Arun%3Ascan-first-001?limit=50&source=temporal',
    );
    expect(workflowCell.textContent).not.toContain('mm:run:scan-first-001');
  });

  it('renders status supplements and bounded progress without Next action surfaces', async () => {
    fetchSpy.mockResolvedValue({
      ok: true,
      json: async () => ({
        items: [
          {
            taskId: 'task-attention',
            source: 'temporal',
            title: 'Attention task',
            status: 'intervention_requested',
            state: 'intervention_requested',
            rawState: 'intervention_requested',
            attentionRequired: true,
            dependsOn: ['mm:dep-1'],
            blockedOnDependencies: true,
            progress: {
              total: 6,
              pending: 2,
              ready: 0,
              executing: 1,
              awaitingExternal: 0,
              reviewing: 0,
              completed: 3,
              failed: 0,
              skipped: 0,
              canceled: 0,
              currentStepTitle: 'Run test suite',
              updatedAt: '2026-03-28T00:00:00Z',
            },
            createdAt: '2026-03-28T00:00:00Z',
          },
          {
            taskId: 'task-failed',
            source: 'temporal',
            title: 'Failed task',
            status: 'failed',
            state: 'failed',
            rawState: 'failed',
            progress: {
              total: 2,
              pending: 0,
              ready: 0,
              executing: 0,
              awaitingExternal: 0,
              reviewing: 0,
              completed: 1,
              failed: 1,
              skipped: 0,
              canceled: 0,
              currentStepTitle: 'Publish result',
              updatedAt: '2026-03-28T00:00:00Z',
            },
            createdAt: '2026-03-28T00:00:00Z',
          },
          {
            taskId: 'task-completed-skipped',
            source: 'temporal',
            title: 'Completed task with skipped step',
            status: 'completed',
            state: 'completed',
            rawState: 'completed',
            progress: {
              total: 4,
              pending: 0,
              ready: 0,
              executing: 0,
              awaitingExternal: 0,
              reviewing: 0,
              completed: 3,
              failed: 0,
              skipped: 1,
              canceled: 0,
              currentStepTitle: 'Skipped cleanup',
              updatedAt: '2026-03-28T00:00:00Z',
            },
            createdAt: '2026-03-28T00:00:00Z',
          },
          {
            taskId: 'task-failed-zero-counter',
            source: 'temporal',
            title: 'Failed task without failed counter',
            status: 'failed',
            state: 'failed',
            rawState: 'failed',
            progress: {
              total: 2,
              pending: 0,
              ready: 0,
              executing: 0,
              awaitingExternal: 0,
              reviewing: 0,
              completed: 1,
              failed: 0,
              skipped: 0,
              canceled: 0,
              currentStepTitle: 'Platform timeout',
              updatedAt: '2026-03-28T00:00:00Z',
            },
            createdAt: '2026-03-28T00:00:00Z',
          },
          {
            taskId: 'task-missing-progress',
            source: 'temporal',
            title: 'Missing progress task',
            status: 'completed',
            state: 'completed',
            rawState: 'completed',
            progress: null,
            createdAt: '2026-03-28T00:00:00Z',
          },
        ],
      }),
    } as Response);

    renderWithClient(<WorkflowListPage payload={mockPayload} />);

    await screen.findAllByText('Attention task');
    expect(document.querySelector('.queue-table-cell-next-action')).toBeNull();
    expect(document.querySelector('.queue-card-next-action')).toBeNull();
    expect(screen.queryByText('Next action')).toBeNull();
    expect(screen.queryByText('Failed — needs review')).toBeNull();
    expect(screen.getAllByText('Intervention requested').length).toBeGreaterThan(0);
    expect(screen.getAllByText('Blocked by 1 prerequisite').length).toBeGreaterThan(0);
    expect(screen.getAllByText('3/6 · Run test suite').length).toBeGreaterThan(0);
    expect(screen.getAllByText('1/2 · Failed at Publish result').length).toBeGreaterThan(0);
    expect(screen.getAllByText('3/4 complete').length).toBeGreaterThan(0);
    expect(screen.getAllByText('1/2 · Failed at Platform timeout').length).toBeGreaterThan(0);

    // The signals are no longer buried under the workflow title cell.
    const workflowCell = document.querySelector('.queue-table-cell-workflow');
    expect(workflowCell?.textContent).not.toContain('Intervention requested');
    expect(workflowCell?.textContent).not.toContain('Blocked by 1 prerequisite');

    const missingProgressRow = screen
      .getAllByText('Missing progress task')
      .map((element) => element.closest('tr'))
      .find((candidate): candidate is HTMLTableRowElement => Boolean(candidate));
    expect(missingProgressRow?.querySelector('.queue-table-cell-progress')?.textContent).toContain('—');
  });

  describe('MoonLadderStudios/MoonMind#4640 recorded Provider Profile', () => {
    const profileRow = (
      id: string,
      title: string,
      providerProfile: unknown,
      extra: Record<string, unknown> = {},
    ) => ({
      taskId: id,
      workflowId: id,
      source: 'temporal',
      title,
      status: 'running',
      state: 'executing',
      rawState: 'executing',
      createdAt: '2026-03-28T00:00:00Z',
      targetRuntime: 'omnigent',
      providerProfile,
      ...extra,
    });

    const mockListAndFacets = (
      items: unknown[],
      facet?: (url: string) => Promise<Response>,
    ) => {
      fetchSpy.mockImplementation((input: RequestInfo | URL) => {
        const url = String(input);
        if (url.includes('/api/executions/facets') && facet) return facet(url);
        if (url.includes('/api/executions/facets')) {
          return Promise.resolve({
            ok: false,
            statusText: 'Service Unavailable',
            json: async () => ({}),
          } as Response);
        }
        return Promise.resolve({ ok: true, json: async () => ({ items }) } as Response);
      });
    };

    it('renders every recorded identity state from parsed rows on desktop and mobile', async () => {
      mockListAndFacets([
        profileRow('wf-single', 'Single profile', {
          selectionState: 'recorded',
          profiles: [{ id: 'opencode-go', label: 'OpenCode Go', harness: 'opencode-native' }],
          profileCount: 1,
        }, {
          // Execution-configuration identity must never masquerade as the account.
          profileId: 'profile-opencode-native',
          agentProfile: { profileId: 'profile-opencode-native' },
        }),
        profileRow('wf-id-only', 'ID only profile', {
          selectionState: 'recorded',
          profiles: [{ id: 'acct-legacy' }],
          profileCount: 1,
        }),
        profileRow('wf-multi', 'Multi profile', {
          selectionState: 'recorded',
          profiles: [
            { id: 'acct-a', label: 'Work' },
            { id: 'acct-b', label: 'Work' },
          ],
          profileCount: 3,
        }),
        profileRow('wf-pending', 'Pending profile', {
          selectionState: 'pending',
          profiles: [],
          profileCount: 0,
        }),
        profileRow('wf-old', 'Historical run', {
          selectionState: 'not_recorded',
          profiles: [],
          profileCount: 0,
        }),
        profileRow('wf-none', 'Tool only', {
          selectionState: 'not_applicable',
          profiles: [],
          profileCount: 0,
        }),
        profileRow('wf-unavailable', 'Unavailable projection', undefined),
      ]);
      const baselineCalls = fetchSpy.mock.calls.length;

      renderWithClient(<WorkflowListPage payload={mockPayload} />);

      const singleRow = await screen.findByRole('row', { name: /Single profile/ });
      expect(within(singleRow).getByText('OpenCode Go')).toBeTruthy();
      expect(within(singleRow).getByText('Harness: opencode-native')).toBeTruthy();
      expect(singleRow.textContent).not.toContain('profile-opencode-native');
      expect(within(screen.getByRole('row', { name: /ID only profile/ })).getByText('acct-legacy')).toBeTruthy();
      const multiRow = screen.getByRole('row', { name: /Multi profile/ });
      expect(within(multiRow).getByText('Multiple profiles')).toBeTruthy();
      // Equal names stay distinct with a compact stable-ID suffix.
      expect(within(multiRow).getByText('Work · acct-a +2')).toBeTruthy();
      expect(within(screen.getByRole('row', { name: /Pending profile/ })).getByText('Pending selection')).toBeTruthy();
      expect(within(screen.getByRole('row', { name: /Historical run/ })).getByText('Not recorded')).toBeTruthy();
      expect(within(screen.getByRole('row', { name: /Tool only/ })).getByText('Not applicable')).toBeTruthy();
      expect(
        within(screen.getByRole('row', { name: /Unavailable projection/ })).getByText('Unavailable'),
      ).toBeTruthy();

      const cards = document.querySelectorAll('.queue-card');
      const singleCard = Array.from(cards).find((card) => card.textContent?.includes('Single profile'));
      const terms = Array.from(singleCard?.querySelectorAll('dt') || []).map((term) => term.textContent);
      expect(terms).toContain('Provider Profile');
      expect(terms).not.toContain('Runtime');
      expect(singleCard?.textContent).toContain('OpenCode Go');
      // Bounded summaries only: no per-row detail, profile, or history lookups.
      const requested = fetchSpy.mock.calls.slice(baselineCalls).map(([url]) => String(url));
      expect(requested.filter((url) => !url.startsWith('/api/executions?'))).toEqual([]);
    });

    it('sorts the current page by recorded display text with stable-ID tie-breaks', async () => {
      mockListAndFacets([
        profileRow('wf-1', 'Zulu run', {
          selectionState: 'recorded',
          profiles: [{ id: 'acct-z', label: 'Zulu' }],
          profileCount: 1,
        }),
        profileRow('wf-2', 'Alpha run', {
          selectionState: 'recorded',
          profiles: [{ id: 'acct-a', label: 'Alpha' }],
          profileCount: 1,
        }),
        profileRow('wf-3', 'Pending run', { selectionState: 'pending', profiles: [], profileCount: 0 }),
      ]);

      renderWithClient(<WorkflowListPage payload={mockPayload} />);

      await screen.findByRole('row', { name: /Zulu run/ });
      fireEvent.click(
        screen.getByRole('button', {
          name: /Provider Profile\. Not sorted\. Activate to sort the current page ascending\./i,
        }),
      );

      await waitFor(() => {
        const titles = Array.from(document.querySelectorAll('tbody tr .workflow-list-row-title')).map(
          (link) => link.textContent,
        );
        expect(titles).toEqual(['Alpha run', 'Zulu run', 'Pending run']);
      });
      expect(screen.getByText('Sorting applies to the current page only.')).toBeTruthy();
      expect(window.location.search).not.toContain('sort=');
    });

    it.each([
      null,
      { selectionState: 'invalid', profiles: [], profileCount: 0 },
      { selectionState: 'recorded', profiles: [], profileCount: 0 },
    ])('shows unusable Provider Profile summaries as Unavailable on desktop and mobile: %j', async (summary) => {
      mockListAndFacets([profileRow('wf-broken', 'Broken projection', summary)]);

      renderWithClient(<WorkflowListPage payload={mockPayload} />);

      const row = await screen.findByRole('row', { name: /Broken projection/ });
      expect(within(row).getByText('Unavailable')).toBeTruthy();
      const card = Array.from(document.querySelectorAll('.queue-card')).find((item) => item.textContent?.includes('Broken projection'));
      expect(card?.textContent).toContain('Unavailable');
      expect(screen.queryByText('Not recorded')).toBeNull();
    });

    it('round-trips Provider Profile IDs and states through URL, chips, and detail links', async () => {
      window.history.pushState(
        {},
        'Provider Profile filter',
        '/workflows?providerProfileIdIn=acct-1&providerProfileStateIn=pending&limit=50',
      );
      mockListAndFacets([
        profileRow('wf-1', 'Profile run', {
          selectionState: 'recorded',
          profiles: [{ id: 'acct-1', label: 'OpenAI · Primary' }],
          profileCount: 1,
        }),
      ]);

      renderWithClient(<WorkflowListPage payload={mockPayload} />);

      await screen.findByRole('row', { name: /Profile run/ });
      expect(lastExecutionListUrl()).toBe(
        '/api/executions?source=temporal&pageSize=50&providerProfileIdIn=acct-1&providerProfileStateIn=pending',
      );
      expect(window.location.search).toBe(
        '?providerProfileIdIn=acct-1&providerProfileStateIn=pending&limit=50',
      );
      expect(
        await screen.findByRole('button', { name: 'Provider Profile filter: OpenAI · Primary +1' }),
      ).toBeTruthy();
      const detailLink = screen.getAllByRole('link', { name: 'Profile run' })[0];
      expect(detailLink?.getAttribute('href')).toBe(
        '/workflows/wf-1?providerProfileIdIn=acct-1&providerProfileStateIn=pending&limit=50&source=temporal',
      );

      fireEvent.click(screen.getByRole('button', { name: 'Remove Provider Profile filter' }));
      await waitFor(() => {
        expect(lastExecutionListUrl()).toBe('/api/executions?source=temporal&pageSize=50');
      });
    });

    it('preserves disjoint legacy include/exclude IDs and states while staging either selection', async () => {
      window.history.pushState({}, '', '/workflows?providerProfileIn=acct-a,acct-extra&providerProfileNotIn=acct-b&providerProfileStateIn=pending&providerProfileStateNotIn=not_applicable&targetRuntimeIn=codex_cli&limit=50');
      mockListAndFacets([profileRow('wf-mixed', 'Mixed profile run', {
        selectionState: 'recorded', profiles: [{ id: 'acct-a', label: 'Alpha' }, { id: 'acct-b', label: 'Beta' }],
      })]);
      renderWithClient(<WorkflowListPage payload={mockPayload} />);
      await screen.findByRole('row', { name: /Mixed profile run/ });
      const expected = '/api/executions?source=temporal&pageSize=50&providerProfileIdIn=acct-a&providerProfileIdIn=acct-extra&providerProfileIdNotIn=acct-b&providerProfileStateIn=pending&providerProfileStateNotIn=not_applicable&targetRuntimeIn=codex_cli';
      expect(lastExecutionListUrl()).toBe(expected);
      expect(screen.getByRole('button', { name: 'Provider Profile filter: Alpha +2; not (Beta +1)' })).toBeTruthy();
      const link = screen.getAllByRole('link', { name: 'Mixed profile run' })[0];
      const context = new URL(link!.getAttribute('href')!, window.location.origin).searchParams;
      expect(context.getAll('providerProfileIdIn')).toEqual(['acct-a', 'acct-extra']);
      expect(context.getAll('providerProfileIdNotIn')).toEqual(['acct-b']);
      openFilterDrawer();
      const section = screen.getByRole('region', { name: 'Provider Profile filter' });
      expect(within(section).getByText('Alpha')).toBeTruthy();
      fireEvent.change(within(section).getByLabelText('Provider Profile filter mode'), { target: { value: 'exclude' } });
      expect(within(section).getByText('Beta')).toBeTruthy();
      expect((within(section).getByRole('checkbox', { name: 'Not applicable' }) as HTMLInputElement).checked).toBe(true);
      fireEvent.click(within(section).getByRole('checkbox', { name: 'Not recorded' }));
      fireEvent.click(screen.getByRole('button', { name: 'Cancel filters' }));
      expect(lastExecutionListUrl()).toBe(expected);
      openFilterDrawer();
      fireEvent.change(screen.getByLabelText('Provider Profile filter mode'), { target: { value: 'exclude' } });
      expect((screen.getByRole('checkbox', { name: 'Not recorded' }) as HTMLInputElement).checked).toBe(false);
      fireEvent.click(screen.getByRole('checkbox', { name: 'Not recorded' }));
      applyFilterDrawer();
      await waitFor(() => expect(lastExecutionListUrl()).toContain('providerProfileStateNotIn=not_applicable%2Cnot_recorded'));
      const applied = new URL(String(lastExecutionListUrl()), window.location.origin).searchParams;
      expect(applied.getAll('providerProfileIdIn')).toEqual(['acct-a', 'acct-extra']);
      expect(applied.getAll('providerProfileIdNotIn')).toEqual(['acct-b']);
      expect(applied.get('providerProfileStateIn')).toBe('pending');
      expect(applied.get('targetRuntimeIn')).toBe('codex_cli');
    });

    it('round-trips comma IDs without confusing IDs with state tokens', async () => {
      window.history.pushState({}, '', '/workflows?providerProfileIdIn=account%2Cprimary&providerProfileIdIn=pending&providerProfileStateNotIn=pending&limit=50');
      mockListAndFacets([profileRow('wf-comma', 'Comma profile run', {
        selectionState: 'recorded', profiles: [{ id: 'account,primary', label: 'Primary account' }],
      })]);
      const { unmount } = renderWithClient(<WorkflowListPage payload={mockPayload} />);
      await screen.findByRole('row', { name: /Comma profile run/ });
      const expected = '/api/executions?source=temporal&pageSize=50&providerProfileIdIn=account%2Cprimary&providerProfileIdIn=pending&providerProfileStateNotIn=pending';
      expect(lastExecutionListUrl()).toBe(expected);
      const detail = screen.getAllByRole('link', { name: 'Comma profile run' })[0];
      expect(new URL(detail!.getAttribute('href')!, window.location.origin).searchParams.getAll('providerProfileIdIn')).toEqual(['account,primary', 'pending']);
      openFilterDrawer();
      const selected = screen.getByRole('list', { name: 'Selected Provider Profile filters' });
      expect(within(selected).getByText('Primary account')).toBeTruthy();
      expect(within(selected).getByText('pending')).toBeTruthy();
      fireEvent.click(screen.getByRole('button', { name: 'Cancel filters' }));
      unmount();
      renderWithClient(<WorkflowListPage payload={mockPayload} />);
      await screen.findByRole('row', { name: /Comma profile run/ });
      expect(lastExecutionListUrl()).toBe(expected);
    });

    it.each([
      ['providerProfileIdIn=account%2Cprimary&providerProfileIdNotIn=account%2Cprimary', 'Provider Profile IDs cannot be both included and excluded.'],
      ['providerProfileStateIn=pending&providerProfileStateNotIn=pending', 'Provider Profile states cannot be both included and excluded.'],
    ])('rejects genuinely overlapping profile filters: %s', async (query, message) => {
      window.history.pushState({}, '', `/workflows?${query}`);
      renderWithClient(<WorkflowListPage payload={mockPayload} />);
      expect(await screen.findByText(message)).toBeTruthy();
      expect(executionListCalls()).toHaveLength(0);
    });

    it('loads one profile facet page per click, preserves prior options on failure, and retries the cursor', async () => {
      let continuationAttempts = 0;
      let resolveContinuation: ((response: Response) => void) | undefined;
      const profileUrls: string[] = [];
      window.history.pushState({}, '', '/workflows?stateIn=executing&providerProfileIdIn=acct-gone&providerProfileStateNotIn=not_applicable&limit=50');
      mockListAndFacets([], (url) => {
        if (!url.includes('facet=providerProfile')) return Promise.resolve({ ok: false, statusText: 'Unavailable' } as Response);
        profileUrls.push(url);
        if (new URL(url, window.location.origin).searchParams.has('nextPageToken')) {
          continuationAttempts++;
          if (continuationAttempts === 1) return new Promise<Response>((resolve) => { resolveContinuation = resolve; });
          return Promise.resolve({ ok: true, json: async () => ({ facet: 'providerProfile', items: [
            { value: 'acct-first', label: 'First account', count: 2 },
            { value: 'account,primary', label: 'Later account', count: 1 },
          ], truncated: true, nextPageToken: 'third-page', source: 'authoritative' }) } as Response);
        }
        return Promise.resolve({ ok: true, json: async () => ({ facet: 'providerProfile', items: [
          { value: 'acct-first', label: 'First account', count: 2 },
        ], stateItems: [{ value: 'pending', label: 'Pending selection', count: 3 }], truncated: true, nextPageToken: 'second-page', source: 'authoritative' }) } as Response);
      });
      renderWithClient(<WorkflowListPage payload={mockPayload} />);
      openFilterDrawer();
      const section = screen.getByRole('region', { name: 'Provider Profile filter' });
      const loadMore = await within(section).findByRole('button', { name: 'Load more Provider Profiles' });
      expect(profileUrls).toHaveLength(1);
      expect(within(section).getByRole('option', { name: 'First account (2)' })).toBeTruthy();
      fireEvent.click(within(section).getByRole('checkbox', { name: 'Pending selection (3)' }));
      fireEvent.click(loadMore);
      fireEvent.click(loadMore);
      await waitFor(() => expect(continuationAttempts).toBe(1));
      expect((loadMore as HTMLButtonElement).disabled).toBe(true);
      resolveContinuation!({ ok: false, statusText: 'Service Unavailable' } as Response);
      expect(await within(section).findByText('More Provider Profile values unavailable. Previously loaded values are still available.')).toBeTruthy();
      expect(within(section).getByRole('option', { name: 'First account (2)' })).toBeTruthy();
      expect((within(section).getByRole('checkbox', { name: 'Pending selection (3)' }) as HTMLInputElement).checked).toBe(true);
      fireEvent.click(within(section).getByRole('button', { name: 'Retry loading Provider Profiles' }));
      expect(await within(section).findByRole('option', { name: 'Later account (1)' })).toBeTruthy();
      expect(within(section).getAllByRole('option', { name: 'First account (2)' })).toHaveLength(1);
      expect(profileUrls).toHaveLength(3);
      for (const url of profileUrls) {
        const params = new URL(url, window.location.origin).searchParams;
        expect(params.get('pageSize')).toBe('50');
        expect(params.get('stateIn')).toBe('executing');
        for (const key of ['providerProfileIdIn', 'providerProfileIdNotIn', 'providerProfileIn', 'providerProfileNotIn', 'providerProfileStateIn', 'providerProfileStateNotIn', 'providerProfileBlank']) expect(params.has(key)).toBe(false);
      }
      expect(new URL(profileUrls[1]!, window.location.origin).searchParams.get('nextPageToken')).toBe('second-page');
      expect(new URL(profileUrls[2]!, window.location.origin).searchParams.get('nextPageToken')).toBe('second-page');
      fireEvent.change(within(section).getByLabelText('Provider Profile filter value'), { target: { value: 'account,primary' } });
      applyFilterDrawer();
      await waitFor(() => expect(String(lastExecutionListUrl())).toContain('providerProfileIdIn=account%2Cprimary'));
      const applied = new URL(String(lastExecutionListUrl()), window.location.origin).searchParams;
      expect(applied.getAll('providerProfileIdIn')).toEqual(['account,primary', 'acct-gone']);
      expect(applied.get('providerProfileStateIn')).toBe('pending');
      expect(applied.get('providerProfileStateNotIn')).toBe('not_applicable');
      expect(profileUrls).toHaveLength(3);
    });

    it('refreshes only the first facet page after changing and returning to an earlier filter context', async () => {
      const profileUrls: string[] = [];
      mockListAndFacets([], (url) => {
        if (!url.includes('facet=providerProfile')) return Promise.resolve({ ok: false } as Response);
        profileUrls.push(url);
        const continued = new URL(url, window.location.origin).searchParams.has('nextPageToken');
        return Promise.resolve({ ok: true, json: async () => ({ facet: 'providerProfile', items: [
          { value: continued ? 'acct-later' : 'acct-first', label: continued ? 'Later account' : 'First account', count: 1 },
        ], truncated: !continued, nextPageToken: continued ? null : 'page-two', source: 'authoritative' }) } as Response);
      });
      const { queryClient } = renderWithClient(<WorkflowListPage payload={mockPayload} />);
      openFilterDrawer();
      fireEvent.click(await screen.findByRole('button', { name: 'Load more Provider Profiles' }));
      await screen.findByRole('option', { name: 'Later account (1)' });
      fireEvent.change(screen.getByLabelText('Repository filter value'), { target: { value: 'another/repo' } });
      applyFilterDrawer();
      await queryClient.invalidateQueries({ queryKey: ['workflow-list-facet'], refetchType: 'none' });
      openFilterDrawer();
      await screen.findByRole('option', { name: 'First account (1)' });
      fireEvent.change(screen.getByLabelText('Repository filter value'), { target: { value: '' } });
      applyFilterDrawer();
      const previousRequests = profileUrls.length;
      openFilterDrawer();
      await waitFor(() => expect(profileUrls.length).toBeGreaterThan(previousRequests));
      await waitFor(() => expect(screen.queryByText('Loading facet values...')).toBeNull());
      expect(profileUrls.slice(previousRequests)).toHaveLength(1);
      expect(profileUrls.filter((url) => url.includes('nextPageToken='))).toHaveLength(1);
      expect(screen.queryByRole('option', { name: 'Later account (1)' })).toBeNull();
      expect(screen.getByRole('button', { name: 'Load more Provider Profiles' })).toBeTruthy();
    });

    it('ignores a canceled continuation after a newer filter context opens', async () => {
      let resolveOldPage: ((response: Response) => void) | undefined;
      mockListAndFacets([], (url) => {
        if (!url.includes('facet=providerProfile')) return Promise.resolve({ ok: false } as Response);
        const params = new URL(url, window.location.origin).searchParams;
        if (params.has('nextPageToken')) return new Promise<Response>((resolve) => { resolveOldPage = resolve; });
        const newer = params.has('repoContains');
        return Promise.resolve({ ok: true, json: async () => ({ facet: 'providerProfile', items: [
          { value: newer ? 'acct-new' : 'acct-old', label: newer ? 'New scope account' : 'Original account', count: 1 },
        ], truncated: !newer, nextPageToken: newer ? null : 'old-page-two', source: 'authoritative' }) } as Response);
      });
      renderWithClient(<WorkflowListPage payload={mockPayload} />);
      openFilterDrawer();
      fireEvent.click(await screen.findByRole('button', { name: 'Load more Provider Profiles' }));
      await waitFor(() => expect(resolveOldPage).toBeDefined());
      fireEvent.change(screen.getByLabelText('Repository filter value'), { target: { value: 'new/repo' } });
      applyFilterDrawer();
      openFilterDrawer();
      await screen.findByRole('option', { name: 'New scope account (1)' });
      resolveOldPage!({ ok: true, json: async () => ({ facet: 'providerProfile', items: [
        { value: 'acct-stale', label: 'Stale continuation account', count: 1 },
      ], nextPageToken: null, source: 'authoritative' }) } as Response);
      await waitFor(() => expect(screen.queryByText('Loading facet values...')).toBeNull());
      expect(screen.queryByRole('option', { name: 'Stale continuation account (1)' })).toBeNull();
      expect(screen.getByRole('option', { name: 'New scope account (1)' })).toBeTruthy();
    });

    it('keeps the blank shortcut meaning as explicit absence states', async () => {
      window.history.pushState({}, 'Blank', '/workflows?providerProfileBlank=true&limit=50');
      mockListAndFacets([]);

      renderWithClient(<WorkflowListPage payload={mockPayload} />);

      await waitFor(() => {
        expect(lastExecutionListUrl()).toBe(
          '/api/executions?source=temporal&pageSize=50&providerProfileStateIn=pending%2Cnot_recorded%2Cnot_applicable',
        );
      });
      expect(
        screen.getByRole('button', {
          name: 'Provider Profile filter: Pending selection +2',
        }),
      ).toBeTruthy();
    });

    it('rejects unknown Provider Profile states before requesting results', async () => {
      const baselineCalls = executionListCalls().length;
      window.history.pushState(
        {},
        'Bad state',
        '/workflows?providerProfileStateIn=recorded&limit=50',
      );

      renderWithClient(<WorkflowListPage payload={mockPayload} />);

      expect(
        await screen.findByText(
          'providerProfileStateIn accepts only: pending, not_recorded, not_applicable.',
        ),
      ).toBeTruthy();
      expect(executionListCalls().length).toBe(baselineCalls);
    });

    it('keeps a legacy runtime URL constraint labeled and unchanged beside Provider Profile filters', async () => {
      window.history.pushState(
        {},
        'Legacy runtime',
        '/workflows?targetRuntimeIn=codex_cli&providerProfileIdIn=acct-1&limit=50',
      );
      mockListAndFacets([]);

      renderWithClient(<WorkflowListPage payload={mockPayload} />);

      await waitFor(() => {
        expect(lastExecutionListUrl()).toBe(
          '/api/executions?source=temporal&pageSize=50&providerProfileIdIn=acct-1&targetRuntimeIn=codex_cli',
        );
      });
      expect(screen.getByRole('button', { name: 'Legacy runtime filter: Codex CLI' })).toBeTruthy();
      // The runtime value is never relabeled as a Provider Profile ID.
      expect(screen.getByRole('button', { name: 'Provider Profile filter: acct-1' })).toBeTruthy();

      openFilterDrawer();
      const legacySection = screen.getByRole('region', { name: 'Legacy runtime filter' });
      expect(legacySection.textContent).toContain('Codex CLI');
      fireEvent.click(within(legacySection).getByRole('button', { name: 'Remove legacy runtime constraint' }));
      applyFilterDrawer();

      await waitFor(() => {
        expect(lastExecutionListUrl()).toBe(
          '/api/executions?source=temporal&pageSize=50&providerProfileIdIn=acct-1',
        );
      });
    });

    it.each(['unavailable', 'late-partial'] as const)(
      'preserves selected IDs and staged states with %s facets',
      async (facetState) => {
        let resolveFacet: ((response: Response) => void) | undefined;
        window.history.pushState({}, 'Selected', '/workflows?providerProfileIdIn=acct-gone&limit=50');
        mockListAndFacets(
          [
            profileRow('wf-1', 'Profile run', {
              selectionState: 'recorded',
              profiles: [{ id: 'acct-1', label: 'Primary' }],
              profileCount: 1,
            }),
          ],
          (url) => {
            if (facetState === 'late-partial' && url.includes('facet=providerProfile')) {
              return new Promise<Response>((resolve) => {
                resolveFacet = resolve;
              });
            }
            return Promise.resolve({
              ok: false,
              statusText: 'Service Unavailable',
              json: async () => ({ detail: { code: 'temporal_unavailable' } }),
            } as Response);
          },
        );

        renderWithClient(<WorkflowListPage payload={mockPayload} />);

        await screen.findByRole('row', { name: /Profile run/ });
        openFilterDrawer();
        fireEvent.click(screen.getByRole('checkbox', { name: /Pending selection/ }));

        const section = screen.getByRole('region', { name: 'Provider Profile filter' });
        if (facetState === 'late-partial') {
          await waitFor(() => expect(resolveFacet).toBeDefined());
          resolveFacet!({
            ok: true,
            json: async () => ({
              facet: 'providerProfile', items: [], stateItems: [], blankCount: 0,
              source: 'authoritative', countMode: 'exact', truncated: true, nextPageToken: 'more',
            }),
          } as Response);
          expect(await within(section).findByText('Facet values truncated by the server.')).toBeTruthy();
        } else {
          expect(
            await within(section).findByText('Facet values unavailable. Showing current page values only.'),
          ).toBeTruthy();
        }
        // Partial facets must not trigger browser enumeration of remaining pages.
        expect(fetchSpy.mock.calls.some(([url]) => String(url).includes('nextPageToken='))).toBe(false);
        const selected = within(section).getByRole('list', { name: 'Selected Provider Profile filters' });
        expect(within(selected).getByText('acct-gone')).toBeTruthy();
        expect(within(section).getByRole('option', { name: 'Primary' })).toBeTruthy();
        expect((screen.getByRole('checkbox', { name: /Pending selection/ }) as HTMLInputElement).checked).toBe(
          true,
        );
        applyFilterDrawer();

        await waitFor(() => {
          expect(lastExecutionListUrl()).toBe(
            '/api/executions?source=temporal&pageSize=50&providerProfileIdIn=acct-gone&providerProfileStateIn=pending',
          );
        });
      },
    );

    it('shows facet counts with ID disambiguation and keeps uncounted selected and current-page IDs', async () => {
      window.history.pushState({}, 'Facet', '/workflows?stateIn=executing&providerProfileIdIn=acct-1&providerProfileIdIn=acct-gone&limit=50');
      const facetUrls: string[] = [];
      mockListAndFacets([profileRow('wf-page', 'Page account', {
        selectionState: 'recorded', profiles: [{ id: 'acct-page', label: 'Page only' }], profileCount: 1,
      })], (url) => {
        facetUrls.push(url);
        if (!url.includes('facet=providerProfile')) {
          return Promise.resolve({ ok: false, statusText: 'nope', json: async () => ({}) } as Response);
        }
        return Promise.resolve({
          ok: true,
          json: async () => ({
            facet: 'providerProfile',
            items: [
              { value: 'acct-1', label: 'Work', count: 5 },
              { value: 'acct-2', label: 'Work', count: 3 },
              { value: 'acct-retired', label: 'acct-retired', count: 1 },
              { value: 'acct-zero', label: 'Zero account', count: 0 },
            ],
            stateItems: [
              { value: 'pending', label: 'Pending selection', count: 2 },
              { value: 'not_recorded', label: 'Not recorded', count: 4 },
              { value: 'not_applicable', label: 'Not applicable', count: 0 },
            ],
            blankCount: 6,
            countMode: 'exact',
            truncated: false,
            source: 'authoritative',
          }),
        } as Response);
      });

      renderWithClient(<WorkflowListPage payload={mockPayload} />);

      openFilterDrawer();
      const section = await screen.findByRole('region', { name: 'Provider Profile filter' });
      expect(await within(section).findByRole('option', { name: 'Work · acct-2 (3)' })).toBeTruthy();
      expect(within(section).getByRole('option', { name: 'acct-retired (1)' })).toBeTruthy();
      expect(within(section).getByText('Work · acct-1 (5)')).toBeTruthy();
      expect(within(section).getByRole('option', { name: 'Zero account (0)' })).toBeTruthy();
      expect(within(section).getByRole('option', { name: 'Page only' })).toBeTruthy();
      expect(within(section).getByText('acct-gone')).toBeTruthy();
      fireEvent.change(within(section).getByLabelText('Provider Profile filter value'), { target: { value: 'acct-zero' } });
      expect(within(section).getByText('Zero account (0)')).toBeTruthy();
      expect(within(section).getByRole('checkbox', { name: 'Not recorded (4)' })).toBeTruthy();
      const profileFacetUrl = facetUrls.find((url) => url.includes('facet=providerProfile')) || '';
      expect(profileFacetUrl).toContain('stateIn=executing');
      expect(profileFacetUrl).not.toContain('providerProfileIdIn');
    });

    it('carries a saved Runtime column preference to the Provider Profile column', async () => {
      window.localStorage.setItem(
        DASHBOARD_PREFERENCES_STORAGE_KEY,
        JSON.stringify({
          version: DASHBOARD_PREFERENCES_VERSION,
          preferences: { workflowListColumnVisibility: { targetRuntime: false } },
        }),
      );

      renderWithClient(<WorkflowListPage payload={mockPayload} />);

      await screen.findAllByText('Example task');
      expect(screen.queryByRole('columnheader', { name: /Provider Profile/i })).toBeNull();
      expect(screen.getByRole('columnheader', { name: /Updated/i })).toBeTruthy();
    });
  });
});

// MM-964: the workflow list density and column-visibility preferences are local
// first, survive reload, and can be reset to defaults.
describe('Workflows Entrypoint — dashboard preferences (MM-964)', () => {
  const mockPayload: BootPayload = {
    page: 'workflow-list',
    apiBase: '/api',
  };

  beforeEach(() => {
    window.localStorage.clear();
    window.history.pushState({}, 'Test', '/workflows');
    vi.spyOn(window, 'fetch').mockResolvedValue({
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
            repository: 'octo/widgets',
            createdAt: '2026-03-28T00:00:00Z',
          },
        ],
      }),
    } as Response);
  });

  const openViewOptions = () =>
    fireEvent.click(screen.getByRole('button', { name: 'View options' }));

  it('applies and persists the compact density preference across reload', async () => {
    const view = renderWithClient(<WorkflowListPage payload={mockPayload} />);
    await screen.findAllByText('Example task');

    expect(document.querySelector('.queue-table-wrapper')?.getAttribute('data-density')).toBe(
      'comfortable',
    );

    openViewOptions();
    fireEvent.click(screen.getByRole('radio', { name: 'Compact' }));

    expect(document.querySelector('.queue-table-wrapper')?.getAttribute('data-density')).toBe(
      'compact',
    );
    expect(window.localStorage.getItem('moonmind.dashboard.preferences')).toContain('compact');

    // Simulate a reload by remounting a fresh instance that reads localStorage.
    view.unmount();
    renderWithClient(<WorkflowListPage payload={mockPayload} />);
    await screen.findAllByText('Example task');
    expect(document.querySelector('.queue-table-wrapper')?.getAttribute('data-density')).toBe(
      'compact',
    );
  });

  it('hides a column when its visibility preference is turned off and remembers it', async () => {
    const view = renderWithClient(<WorkflowListPage payload={mockPayload} />);
    await screen.findAllByText('Example task');

    // Repository column is visible by default.
    expect(
      screen.getByRole('button', { name: /Repo\. .*sort/i }),
    ).toBeTruthy();

    // The repository value is present in the desktop table before hiding.
    expect(document.querySelector('table')?.textContent).toContain('octo/widgets');

    openViewOptions();
    fireEvent.click(screen.getByRole('checkbox', { name: 'Repo' }));

    // Header and cell for the repository column are removed from the table.
    // (The responsive mobile card layout is a separate surface and is out of
    // scope for the column-visibility preference.)
    expect(screen.queryByRole('button', { name: /Repo\. .*sort/i })).toBeNull();
    expect(document.querySelector('table')?.textContent).not.toContain('octo/widgets');

    view.unmount();
    renderWithClient(<WorkflowListPage payload={mockPayload} />);
    await screen.findAllByText('Example task');
    expect(screen.queryByRole('button', { name: /Repo\. .*sort/i })).toBeNull();
  });

  it('resets density and column preferences back to defaults', async () => {
    renderWithClient(<WorkflowListPage payload={mockPayload} />);
    await screen.findAllByText('Example task');

    openViewOptions();
    fireEvent.click(screen.getByRole('radio', { name: 'Compact' }));
    fireEvent.click(screen.getByRole('checkbox', { name: 'Repo' }));
    expect(document.querySelector('.queue-table-wrapper')?.getAttribute('data-density')).toBe(
      'compact',
    );

    fireEvent.click(screen.getByRole('button', { name: /Reset dashboard preferences/i }));

    expect(document.querySelector('.queue-table-wrapper')?.getAttribute('data-density')).toBe(
      'comfortable',
    );
    expect(screen.getByRole('button', { name: /Repo\. .*sort/i })).toBeTruthy();
    expect(window.localStorage.getItem('moonmind.dashboard.preferences')).toBeNull();
  });

  it('does not use duplicate manual window-focus refetches for a fresh list query', async () => {
    const executionListCalls = () =>
      vi.mocked(window.fetch).mock.calls.filter(([url]) =>
        String(url).startsWith('/api/executions?'),
      );

    renderWithClient(<WorkflowListPage payload={mockPayload} />);
    await screen.findAllByText('Example task');

    const afterInitialLoad = executionListCalls().length;
    window.dispatchEvent(new Event('visibilitychange'));
    window.dispatchEvent(new Event('focus'));
    expect(executionListCalls()).toHaveLength(afterInitialLoad);

    openViewOptions();
    fireEvent.click(screen.getByRole('checkbox', { name: 'Poll for live updates' }));
    window.dispatchEvent(new Event('visibilitychange'));
    window.dispatchEvent(new Event('focus'));
    expect(executionListCalls()).toHaveLength(afterInitialLoad);
  });

  it('does not refetch on focus when live updates are disabled', async () => {
    const executionListCalls = () =>
      vi.mocked(window.fetch).mock.calls.filter(([url]) =>
        String(url).startsWith('/api/executions?'),
      );

    renderWithClient(<WorkflowListPage payload={mockPayload} />);
    await screen.findAllByText('Example task');

    openViewOptions();
    fireEvent.click(screen.getByRole('checkbox', { name: 'Poll for live updates' }));
    const afterPausing = executionListCalls().length;
    window.dispatchEvent(new Event('visibilitychange'));
    window.dispatchEvent(new Event('focus'));
    expect(executionListCalls()).toHaveLength(afterPausing);
  });
});
