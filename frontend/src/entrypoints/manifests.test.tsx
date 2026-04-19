import { afterEach, beforeEach, describe, expect, it, vi, type MockInstance } from 'vitest';
import { fireEvent, screen, waitFor } from '@testing-library/react';

import type { BootPayload } from '../boot/parseBootPayload';
import { renderWithClient } from '../utils/test-utils';
import { ManifestsPage } from './manifests';

describe('Manifests Entrypoint', () => {
  const mockPayload: BootPayload = {
    page: 'manifests',
    apiBase: '/api',
  };

  let fetchSpy: MockInstance;

  beforeEach(() => {
    window.history.pushState({}, 'Manifests', '/tasks/manifests');
    fetchSpy = vi.spyOn(window, 'fetch').mockImplementation((input: RequestInfo | URL) => {
      const url = String(input);
      if (url === '/api/executions?entry=manifest&limit=200') {
        return Promise.resolve({
          ok: true,
          json: async () => ({
            items: [
              {
                taskId: 'mm:existing-manifest',
                source: 'temporal',
                sourceLabel: 'Temporal',
                status: 'completed',
              },
            ],
          }),
        } as Response);
      }
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

  it('renders manifest submission and recent runs on the same page', async () => {
    renderWithClient(<ManifestsPage payload={mockPayload} />);

    expect(screen.getByRole('heading', { name: 'Manifests' })).toBeTruthy();
    expect(screen.getByRole('heading', { name: 'Run Manifest' })).toBeTruthy();
    expect(screen.getByRole('heading', { name: 'Recent Runs' })).toBeTruthy();
    expect(screen.getByRole('button', { name: 'Run Manifest' })).toBeTruthy();

    await waitFor(() => {
      expect(screen.getByText('mm:existing-manifest')).toBeTruthy();
    });
  });

  it('upserts inline manifests through the supported manifest API and refreshes recent runs in place', async () => {
    renderWithClient(<ManifestsPage payload={mockPayload} />);

    await waitFor(() => {
      expect(screen.getByText('mm:existing-manifest')).toBeTruthy();
    });

    fireEvent.change(screen.getByLabelText('Source Kind'), {
      target: { value: 'inline' },
    });

    fireEvent.change(screen.getByLabelText('Manifest Name'), {
      target: { value: 'nightly-docs' },
    });
    fireEvent.change(screen.getByLabelText('Inline YAML'), {
      target: { value: 'kind: docs\nversion: v1\n' },
    });
    fireEvent.change(screen.getByLabelText('Action'), {
      target: { value: 'plan' },
    });

    fireEvent.click(screen.getByRole('button', { name: 'Run Manifest' }));

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

    const upsertCall = fetchSpy.mock.calls.find(([url]) => url === '/api/manifests/nightly-docs');
    const upsertRequest = JSON.parse(String(upsertCall?.[1]?.body));
    expect(upsertRequest).toEqual({
      content: 'kind: docs\nversion: v1\n',
    });

    const runCall = fetchSpy.mock.calls.find(([url]) => url === '/api/manifests/nightly-docs/runs');
    const request = JSON.parse(String(runCall?.[1]?.body));
    expect(request).toMatchObject({
      action: 'plan',
      title: 'nightly-docs',
    });

    await waitFor(() => {
      expect(screen.getByText('Manifest run started: mm:manifest-123')).toBeTruthy();
    });
    expect(fetchSpy.mock.calls.filter(([url]) => url === '/api/executions?entry=manifest&limit=200').length).toBeGreaterThanOrEqual(2);
  });

  it('runs registry manifests without re-uploading the manifest body', async () => {
    fetchSpy.mockImplementation((input: RequestInfo | URL) => {
      const url = String(input);
      if (url === '/api/executions?entry=manifest&limit=200') {
        return Promise.resolve({
          ok: true,
          json: async () => ({ items: [] }),
        } as Response);
      }
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

    renderWithClient(<ManifestsPage payload={mockPayload} />);

    fireEvent.change(screen.getByLabelText('Registry Manifest Name'), {
      target: { value: 'docs-registry' },
    });

    fireEvent.click(screen.getByRole('button', { name: 'Run Manifest' }));

    await waitFor(() => {
      expect(fetchSpy).toHaveBeenCalledWith(
        '/api/manifests/docs-registry/runs',
        expect.objectContaining({
          method: 'POST',
        }),
      );
    });
    expect(fetchSpy).not.toHaveBeenCalledWith(
      '/api/manifests/docs-registry',
      expect.objectContaining({ method: 'PUT' }),
    );
  });
});
