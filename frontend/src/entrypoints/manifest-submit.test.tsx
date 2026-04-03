import { afterEach, beforeEach, describe, expect, it, vi, type MockInstance } from 'vitest';
import { fireEvent, screen, waitFor } from '@testing-library/react';

import type { BootPayload } from '../boot/parseBootPayload';
import { navigateTo } from '../lib/navigation';
import { renderWithClient } from '../utils/test-utils';
import { ManifestSubmitPage } from './manifest-submit';

vi.mock('../lib/navigation', () => ({
  navigateTo: vi.fn(),
}));

describe('Manifest Submit Entrypoint', () => {
  const mockPayload: BootPayload = {
    page: 'manifest-submit',
    apiBase: '/api',
  };

  let fetchSpy: MockInstance;

  beforeEach(() => {
    window.history.pushState({}, 'Manifest Submit', '/tasks/manifests/new');
    vi.mocked(navigateTo).mockReset();
    fetchSpy = vi.spyOn(window, 'fetch').mockImplementation((input: RequestInfo | URL) => {
      const url = String(input);
      if (url === '/api/manifests/nightly-docs') {
        return Promise.resolve({
          ok: true,
          json: async () => ({
            name: 'nightly-docs',
          }),
        } as Response);
      }
      if (url === '/api/manifests/nightly-docs/runs') {
        return Promise.resolve({
          ok: true,
          json: async () => ({
            source: 'temporal',
            execution: {
              workflowId: 'mm:manifest-123',
              link: '/tasks/mm:manifest-123?source=temporal',
            },
          }),
        } as Response);
      }
      return Promise.resolve({
        ok: false,
        status: 404,
        text: async () => 'Unhandled fetch',
      } as Response);
    });
  });

  afterEach(() => {
    fetchSpy.mockRestore();
  });

  it('upserts inline manifests through the supported manifest API and redirects to the created run', async () => {
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
        '/api/manifests/nightly-docs',
        expect.objectContaining({
          method: 'PUT',
        }),
      );
      expect(fetchSpy).toHaveBeenCalledWith(
        '/api/manifests/nightly-docs/runs',
        expect.objectContaining({
          method: 'POST',
        }),
      );
    });

    const upsertCall = fetchSpy.mock.calls[0];
    const upsertRequest = JSON.parse(String(upsertCall?.[1]?.body));
    expect(upsertRequest).toEqual({
      content: 'kind: docs\nversion: v1\n',
    });

    const runCall = fetchSpy.mock.calls[1];
    const request = JSON.parse(String(runCall?.[1]?.body));
    expect(request).toMatchObject({
      action: 'plan',
      title: 'nightly-docs',
    });

    await waitFor(() => {
      expect(navigateTo).toHaveBeenCalledWith('/tasks/mm:manifest-123?source=temporal');
    });
  });

  it('runs registry manifests without re-uploading the manifest body', async () => {
    fetchSpy.mockImplementation((input: RequestInfo | URL) => {
      const url = String(input);
      if (url === '/api/manifests/docs-registry/runs') {
        return Promise.resolve({
          ok: true,
          json: async () => ({
            source: 'temporal',
            execution: {
              workflowId: 'mm:manifest-123',
              link: '/tasks/mm:manifest-123?source=temporal',
            },
          }),
        } as Response);
      }
      return Promise.resolve({
        ok: false,
        status: 404,
        text: async () => 'Unhandled fetch',
      } as Response);
    });

    renderWithClient(<ManifestSubmitPage payload={mockPayload} />);

    fireEvent.change(screen.getByLabelText('Manifest Name'), {
      target: { value: 'nightly-docs' },
    });
    fireEvent.change(screen.getByLabelText('Source Kind'), {
      target: { value: 'registry' },
    });
    fireEvent.change(screen.getByLabelText('Registry Name'), {
      target: { value: 'docs-registry' },
    });

    fireEvent.click(screen.getByRole('button', { name: 'Submit Manifest' }));

    await waitFor(() => {
      expect(fetchSpy).toHaveBeenCalledTimes(1);
      expect(fetchSpy).toHaveBeenCalledWith(
        '/api/manifests/docs-registry/runs',
        expect.objectContaining({
          method: 'POST',
        }),
      );
    });
  });
});
