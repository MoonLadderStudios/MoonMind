import { afterEach, beforeEach, describe, expect, it, vi, type MockInstance } from 'vitest';
import { fireEvent, screen, waitFor } from '@testing-library/react';

import type { BootPayload } from '../boot/parseBootPayload';
import { renderWithClient } from '../utils/test-utils';
import { ManifestSubmitPage } from './manifest-submit';

describe('Manifest Submit Entrypoint', () => {
  const mockPayload: BootPayload = {
    page: 'manifest-submit',
    apiBase: '/api',
  };

  let fetchSpy: MockInstance;

  beforeEach(() => {
    window.history.pushState({}, 'Manifest Submit', '/tasks/manifests/new');
    fetchSpy = vi.spyOn(window, 'fetch').mockResolvedValue({
      ok: true,
      json: async () => ({
        workflowId: 'mm:manifest-123',
        redirectPath: '/tasks/mm:manifest-123?source=temporal',
      }),
    } as Response);
  });

  afterEach(() => {
    fetchSpy.mockRestore();
  });

  it('submits a manifest-shaped execution payload and redirects to the created run', async () => {
    renderWithClient(<ManifestSubmitPage payload={mockPayload} />);

    fireEvent.change(screen.getByLabelText('Manifest Name'), {
      target: { value: 'nightly-docs' },
    });
    fireEvent.change(screen.getByLabelText('Manifest Content'), {
      target: { value: 'kind: docs\nversion: v1\n' },
    });
    fireEvent.change(screen.getByLabelText('Action'), {
      target: { value: 'plan' },
    });

    fireEvent.click(screen.getByRole('button', { name: 'Submit Manifest' }));

    await waitFor(() => {
      expect(fetchSpy).toHaveBeenCalledWith(
        '/api/executions',
        expect.objectContaining({
          method: 'POST',
        }),
      );
    });

    const executionCall = fetchSpy.mock.calls[0];
    const request = JSON.parse(String(executionCall?.[1]?.body));
    expect(request).toMatchObject({
      type: 'manifest',
      payload: {
        manifest: {
          name: 'nightly-docs',
          action: 'plan',
          content: 'kind: docs\nversion: v1\n',
        },
      },
    });

    await waitFor(() => {
      expect(window.location.pathname).toBe('/tasks/mm:manifest-123');
      expect(window.location.search).toBe('?source=temporal');
    });
  });
});
