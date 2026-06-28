import { beforeEach, describe, expect, it, vi, type MockInstance } from 'vitest';
import { fireEvent, screen, waitFor } from '@testing-library/react';

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

  const renderMenu = () =>
    renderWithClient(
      <WorkflowRowActionsMenu
        workflowId="wf-123"
        apiBase="/api"
        actionsEnabled
        taskEditingEnabled={false}
      />,
    );
  it('renders an icon trigger labeled "Actions" and does not fetch until opened', () => {
    renderMenu();
    expect(screen.getByRole('button', { name: 'Actions' })).toBeTruthy();
    expect(fetchSpy).not.toHaveBeenCalled();
  });

  it('lazily loads action capabilities the first time the menu opens', async () => {
    renderMenu();
    fireEvent.click(screen.getByRole('button', { name: 'Actions' }));

    // While the detail request is in flight, a loading message is shown.
    expect(screen.getByText('Loading actions…')).toBeTruthy();

    expect(await screen.findByRole('menuitem', { name: 'Pause' })).toBeTruthy();
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

  it('requests a rerun from the row menu without navigating away from the workflow list', async () => {
    renderWithClient(
      <WorkflowRowActionsMenu
        workflowId="wf-123"
        apiBase="/api"
        actionsEnabled
        taskEditingEnabled
      />,
    );
    fireEvent.click(screen.getByRole('button', { name: 'Actions' }));
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
      await screen.findByText('Rerun was requested and the latest execution view is ready.'),
    ).toBeTruthy();
    expect(screen.getByRole('status').className).toContain('notice');
    expect(screen.getByRole('status').className).toContain('ok');
  });

  it('opens a cancel dialog and posts to the cancel endpoint after confirmation', async () => {
    renderMenu();
    fireEvent.click(screen.getByRole('button', { name: 'Actions' }));
    fireEvent.click(await screen.findByRole('menuitem', { name: 'Cancel' }));
    expect(screen.getByRole('dialog', { name: 'Cancel workflow' })).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: 'Cancel workflow' }));

    await waitFor(() => {
      const cancelCall = fetchSpy.mock.calls.find(
        ([url]) => String(url) === '/api/executions/wf-123/cancel',
      );
      expect(cancelCall).toBeTruthy();
      expect(JSON.parse(String((cancelCall?.[1] as RequestInit).body))).toMatchObject({
        action: 'cancel',
        graceful: true,
      });
    });
  });

  it('requires typed confirmation before posting a forced cancel request', async () => {
    renderMenu();
    fireEvent.click(screen.getByRole('button', { name: 'Actions' }));
    fireEvent.click(await screen.findByRole('menuitem', { name: 'Force cancel' }));
    const confirmButton = screen.getByRole('button', { name: 'Force cancel workflow' }) as HTMLButtonElement;
    expect(confirmButton.disabled).toBe(true);
    fireEvent.change(screen.getByLabelText('Type FORCE CANCEL to confirm'), {
      target: { value: 'FORCE CANCEL' },
    });
    expect(confirmButton.disabled).toBe(false);
    fireEvent.click(confirmButton);

    await waitFor(() => {
      const cancelCall = fetchSpy.mock.calls.find(([url, init]) => {
        if (String(url) !== '/api/executions/wf-123/cancel') return false;
        const body = JSON.parse(String((init as RequestInit).body));
        return body.graceful === false;
      });
      expect(cancelCall).toBeTruthy();
      expect(JSON.parse(String((cancelCall?.[1] as RequestInit).body))).toMatchObject({
        action: 'cancel',
        graceful: false,
        reason: 'Force canceled by operator from the dashboard.',
      });
    });
  });
});
