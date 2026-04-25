import { afterEach, beforeEach, describe, expect, it, vi, type MockInstance } from 'vitest';
import { fireEvent, screen, waitFor } from '@testing-library/react';

import type { BootPayload } from '../boot/parseBootPayload';
import { renderWithClient } from '../utils/test-utils';
import { ManifestsPage } from './manifests';

// The default fixture seeds two manifest executions: one completed
// `nightly-docs` run and one in-flight `registry-refresh` run with a `fetch`
// stage. Tests that exercise the empty state or specific manifests override
// `fetchSpy` locally with their own implementation.
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
                title: 'Nightly Docs Manifest',
                manifestName: 'nightly-docs',
                action: 'run',
                status: 'completed',
                startedAt: '2026-04-21T12:00:00Z',
                durationSeconds: 42,
                detailHref: '/tasks/mm:existing-manifest?source=temporal',
              },
              {
                taskId: 'mm:running-manifest',
                source: 'temporal',
                sourceLabel: 'Temporal',
                title: 'Registry Refresh',
                manifestName: 'registry-refresh',
                action: 'plan',
                status: 'running',
                phase: 'fetch',
                startedAt: '2026-04-21T12:05:00Z',
                detailHref: '/tasks/mm:running-manifest?source=temporal',
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

  it('shows manifest run details, stage-aware status, timing, and accessible row actions', async () => {
    renderWithClient(<ManifestsPage payload={mockPayload} />);

    await waitFor(() => {
      expect(screen.getByRole('link', { name: 'Open run mm:existing-manifest' })).toBeTruthy();
      expect(screen.getByRole('link', { name: 'View details for mm:running-manifest' })).toBeTruthy();
    });

    expect(screen.getByText('nightly-docs')).toBeTruthy();
    expect(screen.getByText('registry-refresh')).toBeTruthy();
    expect(screen.getAllByText('run').length).toBeGreaterThanOrEqual(1);
    expect(screen.getAllByText('plan').length).toBeGreaterThanOrEqual(1);
    expect(screen.getByText('Running · fetch')).toBeTruthy();
    expect(screen.getByText('42s')).toBeTruthy();
  });

  it('filters recent manifest runs by status, manifest name, and free-text search', async () => {
    renderWithClient(<ManifestsPage payload={mockPayload} />);

    await waitFor(() => {
      expect(screen.getByText('nightly-docs')).toBeTruthy();
      expect(screen.getByText('registry-refresh')).toBeTruthy();
    });

    fireEvent.change(screen.getByLabelText('Filter by status'), {
      target: { value: 'running' },
    });
    expect(screen.queryByText('nightly-docs')).toBeNull();
    expect(screen.getByText('registry-refresh')).toBeTruthy();

    fireEvent.change(screen.getByLabelText('Filter by status'), {
      target: { value: 'all' },
    });
    fireEvent.change(screen.getByLabelText('Filter by manifest'), {
      target: { value: 'nightly' },
    });
    expect(screen.getByText('nightly-docs')).toBeTruthy();
    expect(screen.queryByText('registry-refresh')).toBeNull();

    fireEvent.change(screen.getByLabelText('Filter by manifest'), {
      target: { value: '' },
    });
    fireEvent.change(screen.getByLabelText('Search recent runs'), {
      target: { value: 'missing-run' },
    });
    expect(screen.getByText('No manifest runs exist yet. Run a registry manifest or submit inline YAML above.')).toBeTruthy();
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
    expect(screen.getByRole('link', { name: 'Open run' }).getAttribute('href')).toBe('/tasks/mm:manifest-123?source=temporal');
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

  it('uses the selected registry manifest as the run title after editing inline fields', async () => {
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
            execution: {
              workflowId: 'mm:manifest-123',
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

    fireEvent.change(screen.getByLabelText('Source Kind'), {
      target: { value: 'inline' },
    });
    fireEvent.change(screen.getByLabelText('Manifest Name'), {
      target: { value: 'stale-inline-title' },
    });
    fireEvent.change(screen.getByLabelText('Source Kind'), {
      target: { value: 'registry' },
    });
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
    const runCall = fetchSpy.mock.calls.find(([url]) => url === '/api/manifests/docs-registry/runs');
    const request = JSON.parse(String(runCall?.[1]?.body));
    expect(request).toMatchObject({
      title: 'docs-registry',
    });
  });

  it('ignores inactive inline secrets when running a registry manifest', async () => {
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
            execution: {
              workflowId: 'mm:manifest-123',
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

    fireEvent.change(screen.getByLabelText('Source Kind'), {
      target: { value: 'inline' },
    });
    fireEvent.change(screen.getByLabelText('Manifest Name'), {
      target: { value: 'inline-draft' },
    });
    fireEvent.change(screen.getByLabelText('Inline YAML'), {
      target: { value: 'kind: docs\nauth:\n  token=stale-draft\n' },
    });
    fireEvent.change(screen.getByLabelText('Source Kind'), {
      target: { value: 'registry' },
    });
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
    expect(screen.queryByText('Raw secret-like values are not allowed. Use env or Vault references instead.')).toBeNull();
  });

  it('rejects invalid max docs before calling manifest APIs', async () => {
    renderWithClient(<ManifestsPage payload={mockPayload} />);

    fireEvent.change(screen.getByLabelText('Registry Manifest Name'), {
      target: { value: 'docs-registry' },
    });
    fireEvent.click(screen.getByText('Advanced options'));
    fireEvent.change(screen.getByLabelText('Max Docs'), {
      target: { value: '1.5' },
    });

    fireEvent.click(screen.getByRole('button', { name: 'Run Manifest' }));

    await waitFor(() => {
      expect(screen.getByText('Max Docs must be a positive whole number.')).toBeTruthy();
    });
    expect(fetchSpy.mock.calls.some(([url]) => String(url).includes('/api/manifests/docs-registry'))).toBe(false);
  });

  it('rejects overflowed max docs before building a request body', async () => {
    renderWithClient(<ManifestsPage payload={mockPayload} />);

    fireEvent.change(screen.getByLabelText('Registry Manifest Name'), {
      target: { value: 'docs-registry' },
    });
    fireEvent.click(screen.getByText('Advanced options'));
    fireEvent.change(screen.getByLabelText('Max Docs'), {
      target: { value: '9'.repeat(400) },
    });

    fireEvent.click(screen.getByRole('button', { name: 'Run Manifest' }));

    await waitFor(() => {
      expect(screen.getByText('Max Docs must be a positive whole number.')).toBeTruthy();
    });
    expect(fetchSpy.mock.calls.some(([url]) => String(url).includes('/api/manifests/docs-registry'))).toBe(false);
  });

  it('rejects raw secret-shaped values before calling manifest APIs', async () => {
    renderWithClient(<ManifestsPage payload={mockPayload} />);

    fireEvent.change(screen.getByLabelText('Source Kind'), {
      target: { value: 'inline' },
    });
    fireEvent.change(screen.getByLabelText('Manifest Name'), {
      target: { value: 'nightly-docs' },
    });
    fireEvent.change(screen.getByLabelText('Inline YAML'), {
      target: { value: 'kind: docs\nauth:\n  token=example\n' },
    });

    fireEvent.click(screen.getByRole('button', { name: 'Run Manifest' }));

    await waitFor(() => {
      expect(screen.getByText('Raw secret-like values are not allowed. Use env or Vault references instead.')).toBeTruthy();
    });
    expect(fetchSpy.mock.calls.some(([url]) => String(url).includes('/api/manifests/nightly-docs'))).toBe(false);
  });

  it('allows env-style secret references in inline YAML', async () => {
    renderWithClient(<ManifestsPage payload={mockPayload} />);

    fireEvent.change(screen.getByLabelText('Source Kind'), {
      target: { value: 'inline' },
    });
    fireEvent.change(screen.getByLabelText('Manifest Name'), {
      target: { value: 'nightly-docs' },
    });
    fireEvent.change(screen.getByLabelText('Inline YAML'), {
      target: { value: 'kind: docs\nauth:\n  token: ${GITHUB_TOKEN}\n' },
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
  });

  it('styles submit failures as errors', async () => {
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
          ok: false,
          status: 500,
          text: async () => 'Server error',
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

    const errorMessage = await screen.findByText('Failed to create manifest run.');
    expect(errorMessage.className).toContain('error');
  });
});
