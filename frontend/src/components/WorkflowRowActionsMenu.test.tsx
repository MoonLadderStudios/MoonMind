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
    },
  };

  beforeEach(() => {
    fetchSpy = vi.spyOn(window, 'fetch').mockImplementation((input: RequestInfo | URL) => {
      const url = String(input);
      if (url.endsWith('/executions/wf-123')) {
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
    expect(
      fetchSpy.mock.calls.filter(([url]) => String(url) === '/api/executions/wf-123'),
    ).toHaveLength(1);
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

  it('confirms before cancelling and posts to the cancel endpoint', async () => {
    const confirmSpy = vi.spyOn(window, 'confirm').mockReturnValue(true);
    renderMenu();
    fireEvent.click(screen.getByRole('button', { name: 'Actions' }));
    fireEvent.click(await screen.findByRole('menuitem', { name: 'Cancel' }));

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
    confirmSpy.mockRestore();
  });
});
