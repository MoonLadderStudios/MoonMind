import { afterEach, beforeEach, describe, expect, it, vi, type MockInstance } from 'vitest';
import { screen } from '@testing-library/react';

import type { BootPayload } from '../boot/parseBootPayload';
import { renderWithClient, waitFor } from '../utils/test-utils';
import { IndexHealthPage } from './index-health';

describe('IndexHealthPage', () => {
  const payload: BootPayload = {
    page: 'index-health',
    apiBase: '/api',
  };

  let fetchSpy: MockInstance;

  beforeEach(() => {
    window.history.pushState({}, 'Index Health', '/index-health');
    fetchSpy = vi.spyOn(window, 'fetch').mockImplementation((input: RequestInfo | URL) => {
      const url = String(input);
      if (url === '/retrieval/index-health') {
        return Promise.resolve({
          ok: true,
          json: async () => ({
            generatedAt: '2026-06-01T00:00:00+00:00',
            totalCollections: 2,
            totalPoints: 42,
            collections: [
              {
                name: 'moonmind-docs',
                status: 'green',
                pointsCount: 40,
                indexedVectorsCount: 40,
                segmentsCount: 2,
                vectorSize: 768,
                vectorDistance: 'Cosine',
                freshnessAt: '2026-05-31T23:00:00+00:00',
                freshnessSource: 'indexed_at',
                freshnessStatus: 'known',
              },
              {
                name: 'workspace-overlay',
                status: 'yellow',
                pointsCount: 2,
                indexedVectorsCount: 2,
                segmentsCount: 1,
                vectorSize: 768,
                vectorDistance: 'Cosine',
                freshnessAt: null,
                freshnessSource: null,
                freshnessStatus: 'unknown',
              },
            ],
          }),
        } as Response);
      }
      return Promise.resolve({
        ok: false,
        status: 404,
        statusText: 'Not Found',
        text: async () => 'Unhandled fetch',
      } as Response);
    });
  });

  afterEach(() => {
    fetchSpy.mockRestore();
  });

  it('shows indexed collections, document counts, and freshness metadata', async () => {
    renderWithClient(<IndexHealthPage payload={payload} />);

    expect(screen.getByRole('heading', { name: 'Index Health' })).toBeTruthy();
    expect(await screen.findByText('moonmind-docs')).toBeTruthy();
    expect(screen.getByText('workspace-overlay')).toBeTruthy();
    expect(screen.getByText('42')).toBeTruthy();
    expect(screen.getAllByText('40').length).toBeGreaterThanOrEqual(2);
    expect(screen.getByText('indexed_at')).toBeTruthy();
    expect(screen.getByText('Unknown')).toBeTruthy();
    expect(screen.getAllByText('768d Cosine').length).toBeGreaterThanOrEqual(2);
    expect(fetchSpy).toHaveBeenCalledWith(
      '/retrieval/index-health',
      expect.objectContaining({
        credentials: 'include',
        headers: { Accept: 'application/json' },
      }),
    );
  });

  it('shows an error notice when index health cannot be loaded', async () => {
    fetchSpy.mockImplementation(() =>
      Promise.resolve({
        ok: false,
        status: 503,
        statusText: 'Service Unavailable',
        text: async () => 'unavailable',
      } as Response),
    );

    renderWithClient(<IndexHealthPage payload={payload} />);

    await waitFor(() => {
      expect(screen.getByRole('alert').textContent).toContain(
        'Failed to load index health',
      );
    });
  });

  it('treats a missing collections array as an empty result', async () => {
    fetchSpy.mockImplementation(() =>
      Promise.resolve({
        ok: true,
        json: async () => ({
          generatedAt: '2026-06-01T00:00:00+00:00',
          totalCollections: 0,
          totalPoints: 0,
        }),
      } as Response),
    );

    renderWithClient(<IndexHealthPage payload={payload} />);

    expect(await screen.findByText('No indexed collections found.')).toBeTruthy();
    expect(screen.getByText('Fresh Collections').parentElement?.textContent).toContain('0');
  });
});
