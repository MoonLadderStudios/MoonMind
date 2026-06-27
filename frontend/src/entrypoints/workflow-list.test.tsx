import { beforeEach, describe, expect, it, vi, type MockInstance } from 'vitest';
import { fireEvent, screen, waitFor } from '@testing-library/react';

import { BootPayload } from '../boot/parseBootPayload';
import { renderWithClient } from '../utils/test-utils';
import { EXECUTING_STATUS_PILL_TRACEABILITY } from '../utils/executionStatusPillClasses';
import { WorkflowListPage } from './workflow-list';
import '../styles/dashboard.css';

describe('Workflows Entrypoint', () => {
  const mockPayload: BootPayload = {
    page: 'workflow-list',
    apiBase: '/api',
  };

  let fetchSpy: MockInstance;

  beforeEach(() => {
    window.history.pushState({}, 'Test', '/workflows');
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

  const executionListCalls = () =>
    fetchSpy.mock.calls.filter(([url]) => String(url).startsWith('/api/executions?'));

  const lastExecutionListUrl = () => executionListCalls().at(-1)?.[0];

  // The advanced filter drawer is the single surface that exposes the full
  // filter UI. These helpers open it and apply the staged draft.
  const openFilterDrawer = () => fireEvent.click(screen.getByRole('button', { name: 'Filters' }));
  const applyFilterDrawer = () => fireEvent.click(screen.getByRole('button', { name: 'Apply filters' }));

  it('shows the loading state while the workflow list request is pending', () => {
    fetchSpy.mockReturnValue(new Promise(() => {}) as Promise<Response>);

    renderWithClient(<WorkflowListPage payload={mockPayload} />);

    expect(screen.getByText('Loading workflows...')).toBeTruthy();
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

  it('moves advanced filters out of every desktop table header', async () => {
    renderWithClient(<WorkflowListPage payload={mockPayload} />);

    await screen.findAllByText('Example task');

    // No per-column filter icon button remains in the table headers.
    expect(document.querySelectorAll('.workflow-list-column-filter-button')).toHaveLength(0);
    expect(screen.queryByRole('button', { name: /No filter applied\./i })).toBeNull();
    // A single visible Filters control opens the full filter UI instead.
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
    expect(screen.getByRole('button', { name: 'Status filter: completed' })).toBeTruthy();
    expect(screen.queryByLabelText('Live updates')).toBeNull();
    expect(screen.queryByRole('button', { name: 'Clear filters' })).toBeNull();
  }, 10000);

  it('announces the current sort state on table headers', async () => {
    renderWithClient(<WorkflowListPage payload={mockPayload} />);

    await screen.findAllByText('Example task');
    expect(fetchSpy.mock.calls.at(-1)?.[0]).toBe(
      '/api/executions?source=temporal&pageSize=50',
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

  it('renders workflow table dates with two-digit years', async () => {
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
    expect(dateCells).toHaveLength(3);
    const expectedDates = [
      '2026-06-21T12:00:00Z',
      '2026-06-21T12:01:00Z',
      '2026-06-21T12:02:00Z',
    ].map((iso) =>
      new Date(iso).toLocaleString(undefined, {
        year: '2-digit',
        month: 'numeric',
        day: 'numeric',
        hour: 'numeric',
        minute: '2-digit',
        second: '2-digit',
      }),
    );
    Array.from(dateCells).forEach((cell, index) => {
      expect(cell.textContent).toBe(expectedDates[index]);
      expect(cell.textContent).not.toContain('2026');
    });
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

    const scheduledHeaderButton = await screen.findByRole('button', {
      name: /Scheduled\. Sorted descending\. Activate to sort ascending\./i,
    });

    openFilterDrawer();
    expect(screen.getByRole('dialog', { name: 'Advanced filters' })).toBeTruthy();
    expect(scheduledHeaderButton.closest('th')?.getAttribute('aria-sort')).toBe('descending');

    fireEvent.click(scheduledHeaderButton);

    await waitFor(() => {
      expect(scheduledHeaderButton.closest('th')?.getAttribute('aria-sort')).toBe('ascending');
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
    fireEvent.change(screen.getByLabelText('Runtime filter value'), {
      target: { value: 'codex_cloud' },
    });
    fireEvent.change(screen.getByLabelText('Title filter value'), {
      target: { value: 'Example' },
    });
    applyFilterDrawer();

    await waitFor(() => {
      expect(lastExecutionListUrl()).toBe(
        '/api/executions?source=temporal&pageSize=50&workflowIdContains=task-123&stateIn=completed&repoContains=owner%2Frepo&targetRuntimeIn=codex_cloud&titleContains=Example',
      );
    });
  });

  it('applies runtime and skill exclude modes from the drawer', async () => {
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
            targetSkill: 'pr-resolver',
            createdAt: '2026-03-28T00:00:00Z',
          },
        ],
      }),
    } as Response);

    renderWithClient(<WorkflowListPage payload={mockPayload} />);

    await screen.findAllByText('Example task');
    openFilterDrawer();
    fireEvent.change(screen.getByLabelText('Runtime filter mode'), { target: { value: 'exclude' } });
    fireEvent.change(screen.getByLabelText('Runtime filter value'), { target: { value: 'codex_cli' } });
    applyFilterDrawer();

    await waitFor(() => {
      expect(lastExecutionListUrl()).toContain('targetRuntimeNotIn=codex_cli');
    });
    expect(screen.getByRole('button', { name: 'Runtime filter: not Codex CLI' })).toBeTruthy();
    await screen.findAllByText('Example task');

    openFilterDrawer();
    fireEvent.change(screen.getByLabelText('Skill filter mode'), { target: { value: 'exclude' } });
    fireEvent.change(screen.getByLabelText('Skill filter value'), { target: { value: 'pr-resolver' } });
    applyFilterDrawer();

    await waitFor(() => {
      const url = lastExecutionListUrl();
      expect(url).toContain('targetRuntimeNotIn=codex_cli');
      expect(url).toContain('targetSkillNotIn=pr-resolver');
    });
    expect(screen.getByRole('button', { name: 'Skill filter: not pr-resolver' })).toBeTruthy();
  }, 10_000);

  it('offers every supported runtime identifier in the runtime filter', async () => {
    renderWithClient(<WorkflowListPage payload={mockPayload} />);

    await screen.findAllByText('Example task');
    openFilterDrawer();

    const runtimeFilter = screen.getByLabelText('Runtime filter value') as HTMLSelectElement;
    expect(runtimeFilter.multiple).toBe(false);
    // Skip the leading placeholder option that prompts the user to add a value.
    expect(
      Array.from(runtimeFilter.options)
        .map((option) => option.value)
        .filter((value) => value !== ''),
    ).toEqual([
      'codex_cli',
      'claude_code',
      'gemini_cli',
      'jules',
      'codex_cloud',
    ]);
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
    expect(screen.getByRole('button', { name: 'Runtime filter: Codex CLI +1' })).toBeTruthy();
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
    expect(screen.getByRole('button', { name: 'Runtime filter: Claude Code' })).toBeTruthy();
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
    expect(screen.getByRole('button', { name: 'Runtime filter: Codex CLI +1' })).toBeTruthy();
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
    expect(screen.getByRole('button', { name: 'Runtime filter: not (Codex CLI +1)' })).toBeTruthy();
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
    expect(Array.from(activePills).filter((pill) => pill.dataset.state === 'planning')).toHaveLength(2);
    expect(Array.from(activePills).filter((pill) => pill.dataset.state === 'executing')).toHaveLength(2);
    expect(Array.from(activePills).filter((pill) => pill.dataset.state === 'finalizing')).toHaveLength(2);
    for (const pill of activePills) {
      const label = pill.dataset.state;
      if (label !== 'planning' && label !== 'executing' && label !== 'finalizing') {
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

    const waitingPills = screen.getAllByText('AWAITING DEP');
    expect(waitingPills.length).toBeGreaterThan(0);
    for (const pill of waitingPills) {
      expect(pill.closest('span')?.dataset.effect).toBeUndefined();
    }

    const nonExecutingStatusPills = Array.from(
      document.querySelectorAll<HTMLElement>('.queue-table-cell-status span.status, .queue-card-status span.status'),
    );

    const awaitingPills = nonExecutingStatusPills.filter((pill) => pill.textContent === 'awaiting external');
    expect(awaitingPills.length).toBeGreaterThan(0);
    for (const pill of awaitingPills) {
      expect(pill.dataset.effect).toBeUndefined();
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

    const earlyLink = await screen.findByRole('link', { name: 'Early scheduled task' });
    const lateLink = await screen.findByRole('link', { name: 'Late scheduled task' });
    expect(
      lateLink.compareDocumentPosition(earlyLink) & Node.DOCUMENT_POSITION_FOLLOWING,
    ).toBeTruthy();
    expect((await screen.findAllByText('—')).length).toBeGreaterThan(0);
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

    fireEvent.click(screen.getByRole('button', { name: /Repository filter: owner\/repo/i }));
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
      'proposals',
      'awaiting_external',
      'intervention_requested',
      'finalizing',
      'completed',
      'failed',
      'canceled',
    ]);
    expect(optionLabels).toEqual([
      'scheduled',
      'initializing',
      'AWAITING DEP',
      'planning',
      'AWAITING SLOT',
      'executing',
      'proposals',
      'awaiting external',
      'intervention requested',
      'finalizing',
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
    expect(pillList.textContent).toContain('completed');
    expect(pillList.textContent).toContain('failed');

    fireEvent.click(screen.getByRole('button', { name: 'Remove completed' }));
    expect(pillList.textContent).not.toContain('completed');
    expect(pillList.textContent).toContain('failed');

    applyFilterDrawer();

    await waitFor(() => {
      expect(lastExecutionListUrl()).toBe(
        '/api/executions?source=temporal&pageSize=50&stateIn=failed',
      );
    });
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
  }, 10000);

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
    expect(selectedStatuses.textContent).toContain('completed');
    expect(selectedStatuses.textContent).toContain('failed');
    expect(selectedStatuses.textContent).not.toContain('canceled');
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

  it('renders pagination controls in the table footer', async () => {
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

    renderWithClient(<WorkflowListPage payload={mockPayload} />);

    expect(await screen.findByText('1 - 1')).toBeTruthy();
    expect(screen.getByText('21 total entries')).toBeTruthy();
    const footer = document.querySelector('.workflow-list-results-footer');
    const liveBlock = footer?.querySelector('.workflow-list-footer-live');
    const paginationBlock = footer?.querySelector('.workflow-list-footer-pagination');
    const paginationSummary = footer?.querySelector('.workflow-list-footer-page-summary');
    expect(liveBlock?.querySelector('input[type="checkbox"]')).toBeNull();
    expect(liveBlock?.textContent).toContain('Live updates enabled. Polling every 5s');
    expect(paginationBlock?.contains(screen.getByLabelText('Show'))).toBe(true);
    expect(getComputedStyle(paginationBlock as Element).flexWrap).toBe('wrap');
    expect(paginationSummary?.textContent).toContain('1 - 1');
    expect(paginationSummary?.textContent).toContain('21 total entries');
    expect(screen.getByRole('button', { name: 'Previous page' })).toBeTruthy();
    expect(screen.getByRole('button', { name: 'Next page' })).toBeTruthy();
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

  it('uses the dashboard control deck and data slab composition', async () => {
    renderWithClient(<WorkflowListPage payload={mockPayload} />);

    await screen.findAllByText('Example task');

    const controlDeck = document.querySelector<HTMLElement>('.workflow-list-control-deck');
    const dataSlab = document.querySelector<HTMLElement>('.workflow-list-data-slab.panel--data');
    const tableWrapper = dataSlab?.querySelector<HTMLElement>('.queue-table-wrapper[data-layout="table"]');
    const table = tableWrapper?.querySelector<HTMLElement>('table');
    const tableHead = tableWrapper?.querySelector<HTMLElement>('thead');
    const firstHeader = tableWrapper?.querySelector<HTMLElement>('th');

    expect(controlDeck).toBeTruthy();
    expect(controlDeck?.querySelector('form.workflow-list-control-grid')).toBeNull();
    // The advanced filter trigger lives in the control deck, not the headers.
    expect(controlDeck?.querySelector('.workflow-list-filter-trigger')).toBeTruthy();
    expect(screen.queryByRole('button', { name: /^Kind\./i })).toBeNull();
    expect(screen.queryByRole('button', { name: /^Workflow Type\./i })).toBeNull();
    expect(screen.queryByRole('button', { name: /^Entry\./i })).toBeNull();

    expect(controlDeck?.classList.contains('panel--controls')).toBe(false);
    expect(controlDeck?.querySelector('.workflow-list-utility-cluster')).toBeNull();
    expect(dataSlab?.querySelector('.workflow-list-results-footer')?.textContent).toContain(
      'Live updates enabled. Polling every 5s',
    );
    expect(screen.queryByText('Showing all task executions.')).toBeNull();
    expect(dataSlab).toBeTruthy();
    expect(dataSlab?.querySelector('.workflow-list-results-footer')).toBeTruthy();
    const pageSizeSelect = screen.getByLabelText('Show');
    const pageSizeLabel = pageSizeSelect.closest('label');
    expect(pageSizeLabel?.classList.contains('queue-page-size-selector')).toBe(true);
    expect(pageSizeLabel?.classList.contains('queue-inline-filter')).toBe(false);
    expect(tableWrapper).toBeTruthy();
    // The slab intentionally allows overflow so the row actions popover
    // can extend below the table without being clipped.
    expect(getComputedStyle(dataSlab as HTMLElement).overflow).toBe('visible');
    expect(getComputedStyle(tableWrapper as HTMLElement).overflowX).toBe('auto');
    expect(getComputedStyle(tableWrapper as HTMLElement).overflowY).toBe('visible');
    expect(getComputedStyle(tableWrapper as HTMLElement).scrollPaddingTop).not.toBe('auto');
    expect(getComputedStyle(table as HTMLElement).borderCollapse).toBe('separate');
    expect(getComputedStyle(tableHead as HTMLElement).position).toBe('sticky');
    expect(getComputedStyle(tableHead as HTMLElement).top).toBe('0px');
    expect(getComputedStyle(firstHeader as HTMLElement).position).toBe('sticky');
    expect(getComputedStyle(firstHeader as HTMLElement).top).toBe('0px');
    expect(getComputedStyle(firstHeader as HTMLElement).borderBottomWidth).toBe('0px');
    expect(Number(getComputedStyle(firstHeader as HTMLElement).zIndex)).toBeGreaterThan(1);
  });

  it('keeps the workflow list surfaces to one control deck and one data slab', async () => {
    renderWithClient(
      <section className="panel" aria-live="polite">
        <WorkflowListPage payload={mockPayload} />
      </section>,
    );

    await screen.findAllByText('Example task');

    const shellPanel = document.querySelector<HTMLElement>('.panel');
    const controlDecks = document.querySelectorAll<HTMLElement>('.workflow-list-control-deck');
    const dataSlabs = document.querySelectorAll<HTMLElement>('.workflow-list-data-slab.panel--data');

    expect(controlDecks).toHaveLength(1);
    expect(dataSlabs).toHaveLength(1);

    const controlDeck = controlDecks[0] as HTMLElement;
    const dataSlab = dataSlabs[0] as HTMLElement;
    const controlGrid = controlDeck.querySelector<HTMLElement>('.workflow-list-control-grid');
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
    // The slab intentionally allows overflow so the row actions popover
    // can extend below the table without being clipped by the data slab.
    expect(dataSlabStyles.overflow).toBe('visible');
    expect(dataSlabStyles.paddingTop).toBe('0px');

    const tableWrapperStyles = getComputedStyle(tableWrapper as HTMLElement);
    expect(tableWrapperStyles.borderTopWidth).toBe('0px');
    expect(tableWrapperStyles.borderRadius).toBe('0px');
    expect(tableWrapperStyles.backgroundColor).toBe('rgba(0, 0, 0, 0)');
    expect(tableWrapperStyles.overflowX).toBe('auto');
    expect(tableWrapperStyles.overflowY).toBe('visible');
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
    fireEvent.change(screen.getByLabelText('Runtime filter value'), { target: { value: 'codex_cli' } });
    applyFilterDrawer();

    await waitFor(() => {
      const activeFilterText = document.querySelector('.workflow-list-filter-chips')?.textContent || '';
      expect(activeFilterText).toContain('completed');
      expect(activeFilterText).toContain('owner/repo');
      expect(activeFilterText).toContain('Codex CLI');
    });

    fireEvent.click(screen.getByRole('button', { name: 'Repository filter: owner/repo' }));
    expect(screen.getByRole('dialog', { name: 'Advanced filters' })).toBeTruthy();
    await waitFor(() => {
      expect(lastExecutionListUrl()).toBe(
        '/api/executions?source=temporal&pageSize=50&stateIn=completed&repoContains=owner%2Frepo&targetRuntimeIn=codex_cli',
      );
    });
    // The drawer opens focused on the chip's field.
    await waitFor(() => {
      expect(document.activeElement).toBe(screen.getByLabelText('Repository filter value'));
    });

    fireEvent.click(screen.getByRole('button', { name: 'Remove Status filter' }));

    await waitFor(() => {
      openFilterDrawer();
      expect((screen.getByLabelText('Status filter value') as HTMLSelectElement).value).toBe('');
    });
  }, 10000);

  it('marks mobile card details links as the only full-width card action', async () => {
    renderWithClient(<WorkflowListPage payload={mockPayload} />);

    const detailsLink = await screen.findByRole('button', { name: 'View details' });

    expect(detailsLink.classList.contains('queue-card-details-action')).toBe(true);
    expect(detailsLink.closest('.queue-card-actions')).toBeTruthy();
  });

  it('keeps mobile task cards constrained to the viewport width', async () => {
    renderWithClient(<WorkflowListPage payload={mockPayload} />);

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

    renderWithClient(<WorkflowListPage payload={mockPayload} />);

    const nextButton = await screen.findByRole('button', { name: 'Next page' });
    fireEvent.click(nextButton);

    await waitFor(() => {
      expect(screen.getByText('No workflows found for the current filters.')).toBeTruthy();
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

    renderWithClient(<WorkflowListPage payload={mockPayload} />);

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

    renderWithClient(<WorkflowListPage payload={mockPayload} />);

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

    renderWithClient(<WorkflowListPage payload={mockPayload} />);

    const titleMatches = await screen.findAllByText('Long child workflow id task');
    const table = titleMatches
      .map((element) => element.closest('table'))
      .find((candidate): candidate is HTMLTableElement => Boolean(candidate));
    expect(table?.querySelectorAll('col.queue-table-column-id')).toHaveLength(1);
    expect(table?.querySelectorAll('col.queue-table-column-date')).toHaveLength(3);
    const idCell = table?.querySelector('td.queue-table-cell-id');
    expect(idCell?.textContent).toBe(longWorkflowId);
  });

  it('does not render an Actions column when workflow actions are disabled', async () => {
    renderWithClient(<WorkflowListPage payload={mockPayload} />);

    await screen.findAllByText('Example task');
    expect(screen.queryByRole('columnheader', { name: 'Actions' })).toBeNull();
    expect(screen.queryByRole('button', { name: 'Actions' })).toBeNull();
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

    renderWithClient(<WorkflowListPage payload={actionsPayload} />);

    await screen.findAllByText('Example task');
    expect(screen.getByRole('columnheader', { name: 'Actions' })).toBeTruthy();
    const triggers = screen.getAllByRole('button', { name: 'Actions' });
    expect(triggers.length).toBeGreaterThanOrEqual(1);

    // Opening the menu lazily fetches the workflow's action capabilities.
    const detailCallsBefore = fetchSpy.mock.calls.filter(([url]) =>
      String(url).endsWith('/executions/task-123'),
    );
    expect(detailCallsBefore).toHaveLength(0);
  });
});
