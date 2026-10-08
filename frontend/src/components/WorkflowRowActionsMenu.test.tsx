import { afterEach, beforeEach, describe, expect, it, vi, type MockInstance } from 'vitest';
import { fireEvent, screen, waitFor, within } from '@testing-library/react';

import { renderWithClient } from '../utils/test-utils';
import { WorkflowRowActionsMenu } from './WorkflowRowActionsMenu';

describe('WorkflowRowActionsMenu', () => {
  let fetchSpy: MockInstance;

  const detailResponse = {
    workflowId: 'wf-123',
    runId: 'run-1',
    title: 'Example workflow',
    state: 'executing',
    actions: {
      canPause: true,
      canCancel: true,
      canForceCancel: true,
      canRerun: true,
    },
  };

  beforeEach(() => {
    vi.useRealTimers();
    window.history.pushState({}, 'Workflows', '/workflows?source=temporal');
    fetchSpy = vi.spyOn(window, 'fetch').mockImplementation((input: RequestInfo | URL) => {
      const url = String(input);
      if (url === '/api/executions/wf-123?source=temporal') {
        return Promise.resolve({
          ok: true,
          json: async () => detailResponse,
        } as Response);
      }
      return Promise.resolve({ ok: true, json: async () => ({}) } as Response);
    });
  });

  afterEach(() => {
    vi.useRealTimers();
    // The fetch spy is re-created per test but vi.spyOn reuses the existing
    // spy, so without a restore every test inherits the previous test's
    // recorded calls and per-test request-count assertions go order-dependent.
    vi.restoreAllMocks();
  });

  const renderMenu = (taskEditingEnabled = false) =>
    renderWithClient(
      <WorkflowRowActionsMenu
        workflowId="wf-123"
        apiBase="/api"
        actionsEnabled
        taskEditingEnabled={taskEditingEnabled}
      />,
    );

  const waitForActionAvailability = async (expectedActionName = 'Pause') => {
    await waitFor(() => {
      expect(
        fetchSpy.mock.calls.filter(
          ([url]) => String(url) === '/api/executions/wf-123?source=temporal',
        ),
      ).not.toHaveLength(0);
      expect(screen.getByRole('menuitem', { name: expectedActionName })).toBeTruthy();
      expect(screen.getByRole('menuitem', { name: 'Remediate' }).getAttribute('aria-disabled')).toBe('true');
      expect(screen.queryByText('Checking availability…')).toBeNull();
    });
  };

  it('renders an icon trigger labeled "More actions" and does not fetch on mount', () => {
    renderMenu();
    expect(screen.getByRole('button', { name: 'More actions' })).toBeTruthy();
    expect(fetchSpy).not.toHaveBeenCalled();
  });

  it('keeps Cancel, Rerun, and Remediate inside the three-dot menu', async () => {
    const { container } = renderMenu(true);
    const row = container.querySelector('.workflow-row-actions') as HTMLElement;
    const trigger = within(row).getByRole('button', { name: 'More actions' });

    expect(within(row).getAllByRole('button')).toEqual([trigger]);
    expect(trigger.querySelector('svg[aria-hidden="true"]')).not.toBeNull();
    expect(screen.queryByRole('menu')).toBeNull();
    expect(fetchSpy).not.toHaveBeenCalled();

    fireEvent.click(trigger);
    await waitForActionAvailability();
    const menu = screen.getByRole('menu', { name: 'More actions' });
    for (const label of ['Cancel', 'Rerun', 'Remediate']) {
      expect(within(menu).getByRole('menuitem', { name: label })).toBeTruthy();
      expect(within(row).queryByRole('button', { name: label })).toBeNull();
    }

    fireEvent.keyDown(menu, { key: 'Escape' });
    expect(screen.queryByRole('menu')).toBeNull();
    expect(within(row).getAllByRole('button')).toEqual([trigger]);
    expect(document.activeElement).toBe(trigger);
  });

  it('keeps the menu open on window blur but closes when focus moves outside it', async () => {
    renderMenu();
    fireEvent.click(screen.getByRole('button', { name: 'More actions' }));
    await waitForActionAvailability();
    const menu = screen.getByRole('menu', { name: 'More actions' });
    const cancel = within(menu).getByRole('menuitem', { name: 'Cancel' });
    cancel.focus();

    // Firefox emits a null-relatedTarget blur when the browser window loses
    // focus, while the document's active element remains the menu item.
    fireEvent.blur(cancel, { relatedTarget: null });
    expect(document.activeElement).toBe(cancel);
    expect(screen.getByRole('menu', { name: 'More actions' })).toBe(menu);

    const outside = document.createElement('button');
    document.body.append(outside);
    try {
      outside.focus();
      await waitFor(() => expect(screen.queryByRole('menu')).toBeNull());
    } finally {
      outside.remove();
    }
  });

  // The row detail endpoint runs a Temporal sync, so displaying a page must
  // not fan out one detail request per row.
  it('does not fetch row capabilities until the operator reaches for the row', async () => {
    const { container } = renderMenu();
    const row = container.querySelector('.workflow-row-actions') as HTMLElement;
    expect(within(row).getAllByRole('button')).toHaveLength(1);
    expect(fetchSpy).not.toHaveBeenCalled();

    fireEvent.mouseOver(row);

    await waitFor(() => {
      expect(
        fetchSpy.mock.calls.filter(
          ([url]) => String(url) === '/api/executions/wf-123?source=temporal',
        ),
      ).toHaveLength(1);
    });
    expect(within(row).getAllByRole('button')).toHaveLength(1);
    fireEvent.click(screen.getByRole('button', { name: 'More actions' }));
    await waitForActionAvailability();
    expect(screen.getByRole('menuitem', { name: 'Cancel' }).getAttribute('aria-disabled')).toBeNull();
  });

  it('lists actions immediately while lazily loading capabilities the first time the row is engaged', async () => {
    let resolveDetail: (response: Response) => void = () => {};
    const detailPromise = new Promise<Response>((resolve) => {
      resolveDetail = resolve;
    });
    fetchSpy.mockImplementation((input: RequestInfo | URL) => {
      const url = String(input);
      if (url === '/api/executions/wf-123?source=temporal') {
        return detailPromise;
      }
      return Promise.resolve({ ok: true, json: async () => ({}) } as Response);
    });

    renderMenu(true);
    fireEvent.click(screen.getByRole('button', { name: 'More actions' }));

    // While the detail request is in flight, the menu already shows the stable
    // action names and uses a disabled placeholder for workflow-specific state.
    expect(screen.getByRole('menuitem', { name: 'Pause' })).toBeTruthy();
    expect(screen.getByRole('menuitem', { name: 'Cancel' })).toBeTruthy();
    expect(screen.getByRole('menuitem', { name: 'Force cancel' })).toBeTruthy();
    expect(screen.getAllByText('Checking availability…').length).toBeGreaterThan(0);
    expect(screen.queryByText('Loading actions…')).toBeNull();
    for (const label of ['Cancel', 'Rerun', 'Remediate']) {
      const item = screen.getByRole('menuitem', { name: label });
      expect(item.getAttribute('aria-disabled')).toBe('true');
      fireEvent.click(item);
    }
    expect(screen.getByRole('menu', { name: 'More actions' })).toBeTruthy();
    expect(fetchSpy.mock.calls.some(([, init]) => (init as RequestInit | undefined)?.method === 'POST')).toBe(false);
    expect(window.location.pathname).toBe('/workflows');

    resolveDetail({
      ok: true,
      json: async () => detailResponse,
    } as Response);

    expect(await screen.findByRole('menuitem', { name: 'Pause' })).toBeTruthy();
    await waitForActionAvailability();
    expect(screen.getByRole('menuitem', { name: 'Cancel' })).toBeTruthy();
    expect(screen.getByRole('menuitem', { name: 'Force cancel' })).toBeTruthy();
    expect(
      fetchSpy.mock.calls.filter(
        ([url]) => String(url) === '/api/executions/wf-123?source=temporal',
      ),
    ).toHaveLength(1);
  });

  it('shows unavailable actions with reasons in the menu and prevents requests', async () => {
    fetchSpy.mockResolvedValue({
      ok: true,
      json: async () => ({
        ...detailResponse,
        actions: {
          canPause: true,
          canCancel: false,
          canRerun: false,
          disabledReasons: {
            canCancel: 'Workflow cannot be canceled.',
            canRerun: 'Workflow cannot be rerun.',
          },
        },
      }),
    } as Response);

    renderMenu(true);
    fireEvent.click(screen.getByRole('button', { name: 'More actions' }));
    await waitForActionAvailability();

    for (const [label, reason] of [
      ['Cancel', 'Workflow cannot be canceled.'],
      ['Rerun', 'Workflow cannot be rerun.'],
      ['Remediate', 'Available for failed, stuck, or intervention-required workflows.'],
    ] as const) {
      const item = screen.getByRole('menuitem', { name: label });
      expect(item.getAttribute('aria-disabled')).toBe('true');
      expect(within(item).getByText(reason)).toBeTruthy();
      fireEvent.click(item);
    }
    expect(screen.getByRole('menu', { name: 'More actions' })).toBeTruthy();
    expect(fetchSpy.mock.calls.some(([, init]) => (init as RequestInit | undefined)?.method === 'POST')).toBe(false);
    expect(window.location.pathname).toBe('/workflows');
  });

  it('requests the lazy detail with the Temporal source so projection reads sync', async () => {
    renderMenu();
    fireEvent.click(screen.getByRole('button', { name: 'More actions' }));

    await screen.findByRole('menuitem', { name: 'Pause' });
    await waitForActionAvailability();
    // Match only the detail request (path ends at wf-123, optionally followed by
    // a query string) and not nested action endpoints like /signal or /cancel.
    const detailUrls = fetchSpy.mock.calls
      .map(([url]) => String(url))
      .filter((url) => /\/executions\/wf-123(\?|$)/.test(url));
    expect(detailUrls.length).toBeGreaterThan(0);
    // The Workflows table reads `source=temporal`; the lazy detail request must
    // carry it too so orphaned/Temporal-only projections still resolve actions.
    expect(detailUrls.every((url) => url === '/api/executions/wf-123?source=temporal')).toBe(true);
  });

  it('invokes the signal endpoint when a lifecycle action is selected', async () => {
    renderMenu();
    fireEvent.click(screen.getByRole('button', { name: 'More actions' }));
    await waitForActionAvailability();
    fireEvent.click(await screen.findByRole('menuitem', { name: 'Pause' }));

    await waitFor(() => {
      const signalCall = fetchSpy.mock.calls.find(
        ([url]) => String(url) === '/api/executions/wf-123/signal',
      );
      expect(signalCall).toBeTruthy();
      expect(JSON.parse(String((signalCall?.[1] as RequestInit).body))).toMatchObject({
        signalName: 'Pause',
      });
    });
  });

  it('opens Create with a remediation draft instead of posting to direct remediation', async () => {
    fetchSpy.mockImplementation((input: RequestInfo | URL) => {
      const url = String(input);
      if (url === '/api/executions/wf-123?source=temporal') {
        return Promise.resolve({
          ok: true,
          json: async () => ({
            ...detailResponse,
            workflowId: 'wf-123',
            runId: 'run-1',
            state: 'failed',
            repository: 'MoonLadderStudios/MoonMind',
            actions: { canCancel: true },
          }),
        } as Response);
      }
      return Promise.resolve({ ok: true, json: async () => ({}) } as Response);
    });

    renderMenu();
    fireEvent.click(screen.getByRole('button', { name: 'More actions' }));
    const remediateItem = await screen.findByRole('menuitem', { name: 'Remediate' });
    await waitFor(() => expect(remediateItem.getAttribute('aria-disabled')).toBeNull());
    fireEvent.mouseDown(remediateItem);
    fireEvent.click(remediateItem);

    await waitFor(() => {
      expect(window.location.pathname).toBe('/workflows/new');
      expect(window.location.search).toContain('intent=remediate');
      expect(window.location.search).toContain('draftId=');
    });
    expect(
      fetchSpy.mock.calls.some(([url]) => String(url).endsWith('/remediation')),
    ).toBe(false);
  });

  it('bypasses dependencies directly without opening a dialog', async () => {
    fetchSpy.mockImplementation((input: RequestInfo | URL) => {
      const url = String(input);
      if (url === '/api/executions/wf-123?source=temporal') {
        return Promise.resolve({
          ok: true,
          json: async () => ({
            ...detailResponse,
            actions: { canBypassDependencies: true },
          }),
        } as Response);
      }
      return Promise.resolve({ ok: true, json: async () => ({}) } as Response);
    });

    renderMenu();
    fireEvent.click(screen.getByRole('button', { name: 'More actions' }));
    await waitForActionAvailability('Bypass Dependencies');
    fireEvent.click(await screen.findByRole('menuitem', { name: 'Bypass Dependencies' }));

    expect(screen.queryByRole('dialog', { name: 'Bypass dependencies' })).toBeNull();
    await waitFor(() => {
      const signalCall = fetchSpy.mock.calls.find(([url, init]) => {
        if (String(url) !== '/api/executions/wf-123/signal') return false;
        const body = (init as RequestInit | undefined)?.body;
        if (!body) return false;
        try {
          return JSON.parse(String(body)).signalName === 'BypassDependencies';
        } catch {
          return false;
        }
      });
      expect(signalCall).toBeTruthy();
      const signalInit = signalCall?.[1] as RequestInit | undefined;
      expect(signalInit?.body ? JSON.parse(String(signalInit.body)) : null).toMatchObject({
        signalName: 'BypassDependencies',
        payload: { reason: 'Dependency wait bypassed by operator from the dashboard.' },
      });
    });
  });

  it('requests a rerun from the row menu without navigating away from the workflow list', async () => {
    const { container } = renderWithClient(
      <WorkflowRowActionsMenu
        workflowId="wf-123"
        apiBase="/api"
        actionsEnabled
        taskEditingEnabled
      />,
    );
    fireEvent.click(screen.getByRole('button', { name: 'More actions' }));
    await waitForActionAvailability();
    fireEvent.click(await screen.findByRole('menuitem', { name: 'Rerun' }));

    await waitFor(() => {
      const rerunCall = fetchSpy.mock.calls.find(
        ([url]) => String(url) === '/api/executions/wf-123/update',
      );
      expect(rerunCall).toBeTruthy();
      const requestBody = (rerunCall?.[1] as RequestInit | undefined)?.body;
      expect(requestBody).toBeDefined();
      expect(JSON.parse(requestBody as string)).toMatchObject({
        updateName: 'RequestRerun',
      });
    });
    expect(window.location.pathname).toBe('/workflows');
    expect(window.location.search).toBe('?source=temporal');
    expect(
      within(container).queryByText('Rerun was requested and the latest execution view is ready.'),
    ).toBeNull();
    const toast = await screen.findByRole('status');
    expect(within(toast).getByText('Rerun requested')).toBeTruthy();
    expect(within(toast).getByText('Example workflow has been queued.')).toBeTruthy();
    const action = within(toast).getByRole('link', { name: 'View workflow' });
    expect(action.getAttribute('href')).toBe('/workflows/wf-123?source=temporal');
  });

  it('links rerun success to the returned execution when a separate workflow is created', async () => {
    fetchSpy.mockImplementation((input: RequestInfo | URL) => {
      const url = String(input);
      if (url === '/api/executions/wf-123?source=temporal') {
        return Promise.resolve({
          ok: true,
          json: async () => detailResponse,
        } as Response);
      }
      if (url === '/api/executions/wf-123/update') {
        return Promise.resolve({
          ok: true,
          json: async () => ({
            execution: {
              workflowId: 'mm:rerun-created',
              redirectPath: '/workflows/mm:rerun-created?source=temporal',
            },
          }),
        } as Response);
      }
      return Promise.resolve({ ok: true, json: async () => ({}) } as Response);
    });

    renderWithClient(
      <WorkflowRowActionsMenu
        workflowId="wf-123"
        apiBase="/api"
        actionsEnabled
        taskEditingEnabled
      />,
    );
    fireEvent.click(screen.getByRole('button', { name: 'More actions' }));
    await waitForActionAvailability();
    fireEvent.click(await screen.findByRole('menuitem', { name: 'Rerun' }));

    const toast = await screen.findByRole('status');
    const action = within(toast).getByRole('link', { name: 'View workflow' });
    expect(action.getAttribute('href')).toBe('/workflows/mm:rerun-created?source=temporal');
  });

  it('dismisses rerun success toasts manually', async () => {
    renderWithClient(
      <WorkflowRowActionsMenu
        workflowId="wf-123"
        apiBase="/api"
        actionsEnabled
        taskEditingEnabled
      />,
    );
    fireEvent.click(screen.getByRole('button', { name: 'More actions' }));
    await waitForActionAvailability();
    fireEvent.click(await screen.findByRole('menuitem', { name: 'Rerun' }));

    const toast = await screen.findByRole('status');
    fireEvent.click(within(toast).getByRole('button', { name: 'Dismiss Rerun requested' }));
    await waitFor(() => {
      expect(screen.queryByRole('status')).toBeNull();
    });
  });

  it('shows rerun request failures in an accessible toast', async () => {
    fetchSpy.mockImplementation((input: RequestInfo | URL) => {
      const url = String(input);
      if (url === '/api/executions/wf-123?source=temporal') {
        return Promise.resolve({
          ok: true,
          json: async () => detailResponse,
        } as Response);
      }
      if (url === '/api/executions/wf-123/update') {
        return Promise.resolve({
          ok: false,
          statusText: 'Conflict',
          text: async () => JSON.stringify({ detail: 'Workflow no longer accepts rerun requests.' }),
        } as Response);
      }
      return Promise.resolve({ ok: true, json: async () => ({}) } as Response);
    });

    renderWithClient(
      <WorkflowRowActionsMenu
        workflowId="wf-123"
        apiBase="/api"
        actionsEnabled
        taskEditingEnabled
      />,
    );
    fireEvent.click(screen.getByRole('button', { name: 'More actions' }));
    await waitForActionAvailability();
    fireEvent.click(await screen.findByRole('menuitem', { name: 'Rerun' }));

    const viewport = await screen.findByLabelText('Dashboard notifications');
    const toast = within(viewport).getByRole('alert');
    expect(within(toast).getByText('Workflow action failed')).toBeTruthy();
    expect(within(toast).getByText('Workflow no longer accepts rerun requests.')).toBeTruthy();
  });

  it('shows non-Error mutation failures without crashing the error toast', async () => {
    fetchSpy.mockImplementation((input: RequestInfo | URL) => {
      const url = String(input);
      if (url === '/api/executions/wf-123?source=temporal') {
        return Promise.resolve({
          ok: true,
          json: async () => detailResponse,
        } as Response);
      }
      if (url === '/api/executions/wf-123/update') {
        return Promise.reject({ message: '  Custom action failure.  ' });
      }
      return Promise.resolve({ ok: true, json: async () => ({}) } as Response);
    });

    renderWithClient(
      <WorkflowRowActionsMenu
        workflowId="wf-123"
        apiBase="/api"
        actionsEnabled
        taskEditingEnabled
      />,
    );
    fireEvent.click(screen.getByRole('button', { name: 'More actions' }));
    await waitForActionAvailability();
    fireEvent.click(await screen.findByRole('menuitem', { name: 'Rerun' }));

    const viewport = await screen.findByLabelText('Dashboard notifications');
    const toast = within(viewport).getByRole('alert');
    expect(within(toast).getByText('Workflow action failed')).toBeTruthy();
    expect(within(toast).getByText('Custom action failure.')).toBeTruthy();
  });

  it('posts a graceful cancel request directly from the row menu', async () => {
    renderMenu();
    fireEvent.click(screen.getByRole('button', { name: 'More actions' }));
    await waitForActionAvailability();
    fireEvent.click(await screen.findByRole('menuitem', { name: 'Cancel' }));
    expect(screen.queryByRole('dialog')).toBeNull();

    await waitFor(() => {
      const cancelCall = fetchSpy.mock.calls.find(
        ([url]) => String(url) === '/api/executions/wf-123/cancel',
      );
      expect(cancelCall).toBeTruthy();
      const body = JSON.parse(String((cancelCall?.[1] as RequestInit).body));
      expect(body).toMatchObject({
        action: 'cancel',
        graceful: true,
      });
      expect(body).not.toHaveProperty('reason');
    });
  });

  // A rejected cancel answers with a structured detail object. Without
  // unwrapping it the operator reads the raw JSON envelope instead of the
  // reason the cancel was refused and the force-cancel path that works.
  it('surfaces a rejected cancel reason from a structured error detail', async () => {
    fetchSpy.mockImplementation((input: RequestInfo | URL) => {
      const url = String(input);
      if (url === '/api/executions/wf-123?source=temporal') {
        return Promise.resolve({
          ok: true,
          json: async () => detailResponse,
        } as Response);
      }
      if (url === '/api/executions/wf-123/cancel') {
        return Promise.resolve({
          ok: false,
          statusText: 'Conflict',
          text: async () =>
            JSON.stringify({
              detail: {
                code: 'cancel_rejected',
                message:
                  'Graceful cancel cannot be delivered: this execution can no longer '
                  + 'process a cancellation request. Use Force cancel to terminate it.',
              },
            }),
        } as Response);
      }
      return Promise.resolve({ ok: true, json: async () => ({}) } as Response);
    });

    renderMenu();
    fireEvent.click(screen.getByRole('button', { name: 'More actions' }));
    await waitForActionAvailability();
    fireEvent.click(await screen.findByRole('menuitem', { name: 'Cancel' }));

    const viewport = await screen.findByLabelText('Dashboard notifications');
    const toast = within(viewport).getByRole('alert');
    expect(within(toast).getByText('Workflow action failed')).toBeTruthy();
    expect(
      within(toast).getByText(/Use Force cancel to terminate it\./),
    ).toBeTruthy();
    expect(within(toast).queryByText(/cancel_rejected/)).toBeNull();
  });

  it('posts a forced cancel request directly from the row menu', async () => {
    fetchSpy.mockImplementation((input: RequestInfo | URL) => Promise.resolve({
      ok: true,
      json: async () => String(input).includes('?source=temporal')
        ? { ...detailResponse, state: 'canceled', actions: { canCancel: false, canForceCancel: true } }
        : { closeStatus: 'terminated' },
    } as Response));
    renderMenu();
    fireEvent.click(screen.getByRole('button', { name: 'More actions' }));
    await waitForActionAvailability('Force cancel');
    fireEvent.click(await screen.findByRole('menuitem', { name: 'Force cancel' }));
    expect(screen.queryByRole('dialog')).toBeNull();

    await waitFor(() => {
      const cancelCall = fetchSpy.mock.calls.find(([url, init]) => {
        if (String(url) !== '/api/executions/wf-123/cancel') return false;
        const body = JSON.parse(String((init as RequestInit).body));
        return body.graceful === false;
      });
      expect(cancelCall).toBeTruthy();
      const body = JSON.parse(String((cancelCall?.[1] as RequestInit).body));
      expect(body).toMatchObject({
        action: 'cancel',
        graceful: false,
      });
      expect(body).not.toHaveProperty('reason');
    });
  });
});
