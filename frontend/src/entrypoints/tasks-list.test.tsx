import { beforeEach, describe, expect, it, vi, type MockInstance } from 'vitest';
import { fireEvent, screen, waitFor } from '@testing-library/react';

import { BootPayload } from '../boot/parseBootPayload';
import { renderWithClient } from '../utils/test-utils';
import { EXECUTING_STATUS_PILL_TRACEABILITY } from '../utils/executionStatusPillClasses';
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
    expect(fetchSpy.mock.calls.at(-1)?.[0]).toBe(
      '/api/executions?source=temporal&pageSize=50&scope=tasks',
    );

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

  it('separates desktop header sorting from filter popovers', async () => {
    renderWithClient(<TasksListPage payload={mockPayload} />);

    await screen.findAllByText('Example task');

    const scheduledHeaderButton = await screen.findByRole('button', {
      name: /Scheduled\. Sorted descending\. Activate to sort ascending\./i,
    });
    const repositoryFilterButton = screen.getByRole('button', {
      name: /Filter Repository\. No filter applied\./i,
    });

    fireEvent.click(repositoryFilterButton);

    expect(screen.getByRole('dialog', { name: 'Repository filter' })).toBeTruthy();
    expect(scheduledHeaderButton.closest('th')?.getAttribute('aria-sort')).toBe('descending');

    fireEvent.click(scheduledHeaderButton);

    await waitFor(() => {
      expect(scheduledHeaderButton.closest('th')?.getAttribute('aria-sort')).toBe('ascending');
    });
    expect(screen.queryByRole('dialog', { name: 'Scheduled filter' })).toBeNull();
  });

  it('keeps task filters available outside the desktop-only table layout', async () => {
    renderWithClient(<TasksListPage payload={mockPayload} />);

    await screen.findAllByText('Example task');

    fireEvent.change(screen.getByLabelText('Mobile Status filter value'), {
      target: { value: 'completed' },
    });
    fireEvent.change(screen.getByLabelText('Mobile Repository filter value'), {
      target: { value: 'owner/repo' },
    });
    fireEvent.change(screen.getByLabelText('Mobile Runtime filter value'), {
      target: { value: 'codex_cloud' },
    });

    await waitFor(() => {
      expect(fetchSpy.mock.calls.at(-1)?.[0]).toBe(
        '/api/executions?source=temporal&pageSize=50&scope=tasks&state=completed&repo=owner%2Frepo&targetRuntime=codex_cloud',
      );
    });
  });

  it('offers every supported runtime identifier in the runtime filter', async () => {
    renderWithClient(<TasksListPage payload={mockPayload} />);

    await screen.findAllByText('Example task');
    fireEvent.click(screen.getByRole('button', { name: /Filter Runtime\. No filter applied\./i }));

    const runtimeFilter = screen.getByLabelText('Runtime filter value') as HTMLSelectElement;
    expect(Array.from(runtimeFilter.options).map((option) => option.value)).toEqual([
      '',
      'codex_cli',
      'codex',
      'claude_code',
      'claude',
      'gemini_cli',
      'jules',
      'codex_cloud',
    ]);
  });

  it('keeps workflow-kind browsing controls out of the normal task list', async () => {
    renderWithClient(<TasksListPage payload={mockPayload} />);

    await screen.findAllByText('Example task');

    expect(screen.queryByLabelText('Scope')).toBeNull();
    expect(screen.queryByLabelText('Workflow Type')).toBeNull();
    expect(screen.queryByLabelText('Entry')).toBeNull();
    expect(fetchSpy.mock.calls.at(-1)?.[0]).toBe(
      '/api/executions?source=temporal&pageSize=50&scope=tasks',
    );
  });

  it('normalizes legacy workflow scope URLs to task visibility with recoverable notice', async () => {
    window.history.pushState(
      {},
      'Legacy',
      '/tasks/list?scope=all&workflowType=MoonMind.ProviderProfileManager&entry=manifest&state=completed&repo=moon%2Fdemo&nextPageToken=stale-token',
    );

    renderWithClient(<TasksListPage payload={mockPayload} />);

    await screen.findAllByText('Example task');

    expect(fetchSpy.mock.calls.at(-1)?.[0]).toBe(
      '/api/executions?source=temporal&pageSize=50&scope=tasks&state=completed&repo=moon%2Fdemo',
    );
    expect(screen.getByText(/Workflow scope filters are not available on Tasks List/i)).toBeTruthy();
    expect(window.location.search).toBe('?state=completed&repo=moon%2Fdemo&limit=50');
    expect(screen.queryByText('MoonMind.ProviderProfileManager')).toBeNull();
    expect(screen.queryByText('manifest')).toBeNull();
  });

  it('renders active task-list pills with the shared shimmer selector contract while keeping inactive pills plain', async () => {
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

    renderWithClient(<TasksListPage payload={mockPayload} />);

    await waitFor(() => {
      expect(
        document.querySelectorAll(
          '.queue-table-cell-status [data-effect="shimmer-sweep"], .queue-card-status [data-effect="shimmer-sweep"]',
        ),
      ).toHaveLength(4);
    });

    const activePills = document.querySelectorAll<HTMLElement>(
      '.queue-table-cell-status [data-effect="shimmer-sweep"], .queue-card-status [data-effect="shimmer-sweep"]',
    );
    expect(activePills).toHaveLength(4);
    expect(Array.from(activePills).filter((pill) => pill.dataset.state === 'planning')).toHaveLength(2);
    expect(Array.from(activePills).filter((pill) => pill.dataset.state === 'executing')).toHaveLength(2);
    for (const pill of activePills) {
      const label = pill.dataset.state;
      if (label !== 'planning' && label !== 'executing') {
        throw new Error(`Unexpected active status pill state: ${label}`);
      }
      expect(pill.dataset.state).toBe(label);
      expect(pill.className).toContain(`is-${label}`);
      expect(pill.className).toContain('status-running');
      expect(pill.dataset.shimmerLabel).toBe(label);
      expect(pill.getAttribute('aria-label')).toBe(label);
      expect(pill.textContent).toBe(label);
      expect(pill.querySelector('.status-letter-wave')?.getAttribute('aria-hidden')).toBe('true');
      const glyphs = Array.from(pill.querySelectorAll<HTMLElement>('.status-letter-wave__glyph'));
      expect(glyphs).toHaveLength(label.length);
      expect(glyphs.map((glyph) => glyph.textContent).join('')).toBe(label);
      expect(glyphs.map((glyph) => glyph.style.getPropertyValue('--mm-letter-index'))).toEqual(
        Array.from({ length: label.length }, (_, index) => String(index)),
      );
      expect(glyphs.every((glyph) => glyph.style.getPropertyValue('--mm-letter-count') === String(label.length))).toBe(true);
    }

    expect(EXECUTING_STATUS_PILL_TRACEABILITY.relatedJiraIssues).toContain('MM-489');
    expect(EXECUTING_STATUS_PILL_TRACEABILITY.relatedJiraIssues).toContain('MM-490');
    expect(EXECUTING_STATUS_PILL_TRACEABILITY.relatedJiraIssues).toContain('MM-491');

    const waitingPills = screen.getAllByText('waiting_on_dependencies');
    expect(waitingPills.length).toBeGreaterThan(0);
    for (const pill of waitingPills) {
      expect(pill.closest('span')?.dataset.effect).toBeUndefined();
    }

    const nonExecutingStatusPills = Array.from(
      document.querySelectorAll<HTMLElement>('.queue-table-cell-status span.status, .queue-card-status span.status'),
    );

    const awaitingPills = nonExecutingStatusPills.filter((pill) => pill.textContent === 'awaiting_external');
    expect(awaitingPills.length).toBeGreaterThan(0);
    for (const pill of awaitingPills) {
      expect(pill.dataset.effect).toBeUndefined();
    }

    const finalizingPills = nonExecutingStatusPills.filter((pill) => pill.textContent === 'finalizing');
    expect(finalizingPills.length).toBeGreaterThan(0);
    for (const pill of finalizingPills) {
      expect(pill.dataset.effect).toBeUndefined();
    }
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

  it('reuses the trimmed repository column filter for both the request and the query key', async () => {
    renderWithClient(<TasksListPage payload={mockPayload} />);

    await screen.findAllByText('Example task');
    const baselineCalls = fetchSpy.mock.calls.length;

    fireEvent.click(screen.getByRole('button', { name: /Filter Repository\. No filter applied\./i }));
    fireEvent.change(screen.getByLabelText('Repository filter value'), {
      target: { value: 'owner/repo' },
    });

    await waitFor(() => {
      expect(fetchSpy.mock.calls.length).toBe(baselineCalls + 1);
    });
    expect(fetchSpy.mock.calls.at(-1)?.[0]).toBe(
      '/api/executions?source=temporal&pageSize=50&scope=tasks&repo=owner%2Frepo',
    );
    await screen.findAllByText('Example task');

    fireEvent.click(screen.getByRole('button', { name: /Repository filter: owner\/repo/i }));
    fireEvent.change(screen.getByLabelText('Repository filter value'), {
      target: { value: 'owner/repo ' },
    });

    const repositoryInput = screen.getByLabelText('Repository filter value') as HTMLInputElement;
    await waitFor(() => {
      expect(repositoryInput.value).toBe('owner/repo ');
    });
    expect(fetchSpy.mock.calls.length).toBe(baselineCalls + 1);
  });

  it('labels the lifecycle column filter as status and exposes canonical status options', async () => {
    renderWithClient(<TasksListPage payload={mockPayload} />);

    await screen.findAllByText('Example task');

    fireEvent.click(screen.getByRole('button', { name: /Filter Status\. No filter applied\./i }));
    const statusFilter = screen.getByLabelText('Status filter value') as HTMLSelectElement;
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
    expect(fetchSpy.mock.calls.at(-1)?.[0]).toBe(
      '/api/executions?source=temporal&pageSize=50&scope=tasks&state=completed',
    );
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

    const controlDeck = document.querySelector<HTMLElement>('.task-list-control-deck');
    const dataSlab = document.querySelector<HTMLElement>('.task-list-data-slab.panel--data');
    const tableWrapper = dataSlab?.querySelector<HTMLElement>('.queue-table-wrapper[data-layout="table"]');
    const scheduledHeader = tableWrapper?.querySelector<HTMLElement>('th');

    expect(controlDeck).toBeTruthy();
    expect(controlDeck?.querySelector('form.task-list-control-grid')).toBeNull();
    expect(screen.queryByRole('button', { name: /^Kind\./i })).toBeNull();
    expect(screen.queryByRole('button', { name: /^Workflow Type\./i })).toBeNull();
    expect(screen.queryByRole('button', { name: /^Entry\./i })).toBeNull();

    expect(controlDeck?.classList.contains('panel--controls')).toBe(false);
    expect(
      controlDeck?.querySelector('.task-list-utility-cluster')?.contains(screen.getByLabelText('Live updates')),
    ).toBe(true);
    expect(screen.getByText('Showing all task executions.')).toBeTruthy();
    expect(dataSlab).toBeTruthy();
    expect(dataSlab?.querySelector('.queue-results-toolbar')).toBeTruthy();
    const pageSizeSelect = screen.getByLabelText('Show');
    const pageSizeLabel = pageSizeSelect.closest('label');
    expect(pageSizeLabel?.classList.contains('queue-page-size-selector')).toBe(true);
    expect(pageSizeLabel?.classList.contains('queue-inline-filter')).toBe(false);
    expect(tableWrapper).toBeTruthy();
    expect(getComputedStyle(tableWrapper as HTMLElement).overflow).toBe('auto');
    expect(getComputedStyle(scheduledHeader as HTMLElement).position).toBe('sticky');
  });

  it('keeps the task list surfaces to one control deck and one data slab', async () => {
    renderWithClient(
      <section className="panel" aria-live="polite">
        <TasksListPage payload={mockPayload} />
      </section>,
    );

    await screen.findAllByText('Example task');

    const shellPanel = document.querySelector<HTMLElement>('.panel');
    const controlDecks = document.querySelectorAll<HTMLElement>('.task-list-control-deck');
    const dataSlabs = document.querySelectorAll<HTMLElement>('.task-list-data-slab.panel--data');

    expect(controlDecks).toHaveLength(1);
    expect(dataSlabs).toHaveLength(1);

    const controlDeck = controlDecks[0] as HTMLElement;
    const dataSlab = dataSlabs[0] as HTMLElement;
    const controlGrid = controlDeck.querySelector<HTMLElement>('.task-list-control-grid');
    const tableWrapper = dataSlab.querySelector<HTMLElement>('.queue-table-wrapper[data-layout="table"]');

    expect(controlGrid).toBeNull();
    expect(tableWrapper).toBeTruthy();

    const shellPanelStyles = getComputedStyle(shellPanel as HTMLElement);
    expect(shellPanelStyles.borderTopWidth).toBe('0px');
    expect(shellPanelStyles.backgroundColor).toBe('rgba(0, 0, 0, 0)');
    expect(shellPanelStyles.boxShadow).toBe('none');
    expect(shellPanelStyles.paddingTop).toBe('0px');
    expect(shellPanelStyles.minHeight).toBe('0px');

    const dataSlabStyles = getComputedStyle(dataSlab);
    expect(dataSlabStyles.gap).toBe('0px');
    expect(dataSlabStyles.overflow).toBe('hidden');
    expect(dataSlabStyles.paddingTop).toBe('0px');

    const tableWrapperStyles = getComputedStyle(tableWrapper as HTMLElement);
    expect(tableWrapperStyles.borderTopWidth).toBe('0px');
    expect(tableWrapperStyles.borderRadius).toBe('0px');
    expect(tableWrapperStyles.backgroundColor).toBe('rgba(0, 0, 0, 0)');
  });

  it('shows clickable active column filter chips and clears filters from the control deck', async () => {
    renderWithClient(<TasksListPage payload={mockPayload} />);

    await screen.findAllByText('Example task');
    fireEvent.click(screen.getByRole('button', { name: /Filter Status\. No filter applied\./i }));
    fireEvent.change(screen.getByLabelText('Status filter value'), { target: { value: 'completed' } });
    await screen.findAllByText('Example task');
    fireEvent.click(screen.getByRole('button', { name: /Filter Repository\. No filter applied\./i }));
    fireEvent.change(screen.getByLabelText('Repository filter value'), { target: { value: 'owner/repo' } });
    await screen.findAllByText('Example task');
    fireEvent.click(screen.getByRole('button', { name: /Filter Runtime\. No filter applied\./i }));
    fireEvent.change(screen.getByLabelText('Runtime filter value'), { target: { value: 'codex_cli' } });

    await waitFor(() => {
      const activeFilterText = document.querySelector('.task-list-filter-chips')?.textContent || '';
      expect(activeFilterText).toContain('completed');
      expect(activeFilterText).toContain('owner/repo');
      expect(activeFilterText).toContain('Codex CLI');
    });

    fireEvent.click(screen.getByRole('button', { name: 'Repository filter: owner/repo' }));
    expect(screen.getByRole('dialog', { name: 'Repository filter' })).toBeTruthy();
    await waitFor(() => {
      expect(fetchSpy.mock.calls.at(-1)?.[0]).toBe(
        '/api/executions?source=temporal&pageSize=50&scope=tasks&state=completed&repo=owner%2Frepo&targetRuntime=codex_cli',
      );
    });

    fireEvent.click(screen.getByRole('button', { name: 'Clear filters' }));

    await waitFor(() => {
      fireEvent.click(screen.getByRole('button', { name: /Filter Status\. No filter applied\./i }));
      expect((screen.getByLabelText('Status filter value') as HTMLSelectElement).value).toBe('');
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
