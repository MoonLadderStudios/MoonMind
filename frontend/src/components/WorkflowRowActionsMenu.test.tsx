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
  });

  const renderMenu = () =>
    renderWithClient(
      <WorkflowRowActionsMenu
        workflowId="wf-123"
        apiBase="/api"
        actionsEnabled
        taskEditingEnabled={false}
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
      expect(screen.queryByRole('menuitem', { name: 'Remediate' })).toBeNull();
      expect(screen.queryByText('Checking availability…')).toBeNull();
    });
  };

  it('renders an icon trigger labeled "Actions" and does not fetch until opened', () => {
    renderMenu();
    expect(screen.getByRole('button', { name: 'Actions' })).toBeTruthy();
    expect(fetchSpy).not.toHaveBeenCalled();
  });

  it('lists actions immediately while lazily loading capabilities the first time the menu opens', async () => {
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

    renderMenu();
    fireEvent.click(screen.getByRole('button', { name: 'Actions' }));

    // While the detail request is in flight, the menu already shows the stable
    // action names and uses a disabled placeholder for workflow-specific state.
    expect(screen.getByRole('menuitem', { name: 'Pause' })).toBeTruthy();
    expect(screen.getByRole('menuitem', { name: 'Cancel' })).toBeTruthy();
    expect(screen.getByRole('menuitem', { name: 'Force cancel' })).toBeTruthy();
    expect(screen.getAllByText('Checking availability…').length).toBeGreaterThan(0);
    expect(screen.queryByText('Loading actions…')).toBeNull();

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

  it('requests the lazy detail with the Temporal source so projection reads sync', async () => {
    renderMenu();
    fireEvent.click(screen.getByRole('button', { name: 'Actions' }));

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
    fireEvent.click(screen.getByRole('button', { name: 'Actions' }));
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
    fireEvent.click(screen.getByRole('button', { name: 'Actions' }));
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
    fireEvent.click(screen.getByRole('button', { name: 'Actions' }));
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
    fireEvent.click(screen.getByRole('button', { name: 'Actions' }));
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
    fireEvent.click(screen.getByRole('button', { name: 'Actions' }));
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
    fireEvent.click(screen.getByRole('button', { name: 'Actions' }));
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
    fireEvent.click(screen.getByRole('button', { name: 'Actions' }));
    await waitForActionAvailability();
    fireEvent.click(await screen.findByRole('menuitem', { name: 'Rerun' }));

    const viewport = await screen.findByLabelText('Dashboard notifications');
    const toast = within(viewport).getByRole('alert');
    expect(within(toast).getByText('Workflow action failed')).toBeTruthy();
    expect(within(toast).getByText('Custom action failure.')).toBeTruthy();
  });

  it('posts a graceful cancel request directly from the row menu', async () => {
    renderMenu();
    fireEvent.click(screen.getByRole('button', { name: 'Actions' }));
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

  it('posts a forced cancel request directly from the row menu', async () => {
    renderMenu();
    fireEvent.click(screen.getByRole('button', { name: 'Actions' }));
    await waitForActionAvailability();
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
