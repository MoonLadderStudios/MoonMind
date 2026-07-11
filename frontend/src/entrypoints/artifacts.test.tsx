import { afterEach, describe, expect, it, vi } from 'vitest';
import { MemoryRouter } from 'react-router-dom';
import { screen } from '@testing-library/react';

import { renderWithClient } from '../utils/test-utils';
import ArtifactsPage from './artifacts';

describe('ArtifactsPage', () => {
  afterEach(() => vi.restoreAllMocks());

  it('renders independently loaded compact evidence collections and owning workflow links', async () => {
    vi.spyOn(window, 'fetch').mockImplementation(async (input) => {
      const url = new URL(String(input), window.location.origin);
      const category = url.searchParams.get('category') || 'artifacts';
      return {
        ok: true,
        json: async () => ({
          category,
          items: category === 'artifacts' ? [{
            artifact_id: 'art_SAFE',
            created_at: '2026-07-10T12:00:00Z',
            content_type: 'text/plain',
            size_bytes: 12,
            status: 'complete',
            retention_class: 'standard',
            link_type: 'output.primary',
            label: 'Final output',
            workflow_id: 'mm:workflow-1',
            run_id: 'run-1',
            download_url: '/api/artifacts/art_SAFE/download',
          }] : [],
          total: category === 'artifacts' ? 1 : 0,
          offset: 0,
          limit: 25,
          refreshed_at: '2026-07-10T12:01:00Z',
        }),
      } as Response;
    });

    renderWithClient(
      <MemoryRouter initialEntries={['/artifacts']}>
        <ArtifactsPage payload={{ page: 'artifacts', apiBase: '/api', features: { artifacts: true } }} />
      </MemoryRouter>,
    );

    expect(screen.getByRole('heading', { name: 'Artifacts & Observability' })).toBeTruthy();
    expect(await screen.findByText('art_SAFE')).toBeTruthy();
    expect(screen.getByRole('link', { name: 'Final output' }).getAttribute('href')).toBe('/workflows/mm%3Aworkflow-1');
    expect(window.fetch).toHaveBeenCalledTimes(3);
  });

  it('hides collection data when the capability is disabled', () => {
    const fetchSpy = vi.spyOn(window, 'fetch');
    renderWithClient(
      <MemoryRouter initialEntries={['/artifacts']}>
        <ArtifactsPage payload={{ page: 'artifacts', apiBase: '/api', features: { artifacts: false } }} />
      </MemoryRouter>,
    );
    expect(screen.getByRole('alert').textContent).toContain('not enabled');
    expect(fetchSpy).not.toHaveBeenCalled();
  });

  it('keeps observability deep links on the observability collection', async () => {
    const fetchSpy = vi.spyOn(window, 'fetch').mockImplementation(async (input) => {
      const url = new URL(String(input), window.location.origin);
      return {
        ok: true,
        json: async () => ({
          category: url.searchParams.get('category'),
          items: [],
          total: 0,
          offset: 0,
          limit: 25,
          refreshed_at: '2026-07-10T12:01:00Z',
        }),
      } as Response;
    });

    renderWithClient(
      <MemoryRouter initialEntries={['/observability/runs/today']}>
        <ArtifactsPage payload={{ page: 'artifacts', apiBase: '/api', features: { artifacts: true } }} />
      </MemoryRouter>,
    );

    await screen.findByText('No authorized observability are available.');
    expect(fetchSpy.mock.calls.map(([input]) => new URL(String(input), window.location.origin).searchParams.get('category')))
      .toContain('observability');
  });
});
