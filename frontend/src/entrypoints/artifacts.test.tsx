import { afterEach, describe, expect, it, vi } from 'vitest';
import { MemoryRouter } from 'react-router-dom';
import { fireEvent, screen } from '@testing-library/react';

import { renderWithClient } from '../utils/test-utils';
import ArtifactsPage from './artifacts';
import { DashboardApp } from './dashboard-app';

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
          has_more: false,
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

  it('pages with the server has_more flag instead of an exact total', async () => {
    const row = (id: string) => ({
      artifact_id: id,
      created_at: '2026-07-10T12:00:00Z',
      content_type: 'text/plain',
      size_bytes: 12,
      status: 'complete',
      retention_class: 'standard',
      link_type: 'output.primary',
      label: `Output ${id}`,
      workflow_id: null,
      run_id: null,
      download_url: `/api/artifacts/${id}/download`,
    });
    const fetchSpy = vi.spyOn(window, 'fetch').mockImplementation(async (input) => {
      const url = new URL(String(input), window.location.origin);
      const offset = Number(url.searchParams.get('offset'));
      return {
        ok: true,
        json: async () => ({
          category: 'observability',
          items: offset === 0
            ? Array.from({ length: 25 }, (_value, index) => row(`art_P1_${index}`))
            : [row('art_P2_0')],
          has_more: offset === 0,
          offset,
          limit: 25,
          refreshed_at: '2026-07-10T12:01:00Z',
        }),
      } as Response;
    });

    renderWithClient(
      <MemoryRouter initialEntries={['/observability']}>
        <ArtifactsPage payload={{ page: 'artifacts', apiBase: '/api', features: { artifacts: true } }} />
      </MemoryRouter>,
    );

    expect(await screen.findByText('art_P1_0')).toBeTruthy();
    expect(screen.getByText(/Showing 1–25/)).toBeTruthy();
    const next = screen.getByRole('button', { name: 'Next' }) as HTMLButtonElement;
    expect(next.disabled).toBe(false);
    fireEvent.click(next);

    expect(await screen.findByText('art_P2_0')).toBeTruthy();
    expect(screen.getByText(/Showing 26–26/)).toBeTruthy();
    expect((screen.getByRole('button', { name: 'Next' }) as HTMLButtonElement).disabled).toBe(true);
    expect(fetchSpy.mock.calls.map(([input]) => new URL(String(input), window.location.origin).searchParams.get('offset')))
      .toEqual(['0', '25']);
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
          has_more: false,
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

  it('keeps the filter field mounted and focused inside the dashboard shell (MoonMind#4559)', async () => {
    window.history.replaceState({}, '', '/artifacts');
    vi.spyOn(window, 'fetch').mockImplementation(async (input) => {
      const url = new URL(String(input), window.location.origin);
      const json = (value: unknown) => new Response(JSON.stringify(value), {
        headers: { 'Content-Type': 'application/json' },
      });
      if (url.pathname === '/api/ui/info') {
        return json({ app: 'moonmind', apiBase: '/api', features: { artifacts: true }, endpoints: {} });
      }
      if (url.pathname.startsWith('/api/v1/provider-profiles')) return json([]);
      const category = url.searchParams.get('category') || 'artifacts';
      return json({ category, items: [], has_more: false, offset: 0, limit: 25, refreshed_at: '2026-07-10T12:01:00Z' });
    });
    renderWithClient(<DashboardApp payload={{ page: 'dashboard', apiBase: '/api' }} />);
    const filter = await screen.findByLabelText('Filter artifacts');
    filter.focus();

    fireEvent.change(filter, { target: { value: 'final' } });

    expect(screen.getByLabelText('Filter artifacts')).toBe(filter);
    expect(document.activeElement).toBe(filter);
    expect((filter as HTMLInputElement).value).toBe('final');
    expect(window.location.search).toContain('artifacts_q=final');
    expect(await screen.findByText('No artifacts match this filter.')).toBeTruthy();
    expect(screen.getByLabelText('Filter artifacts')).toBe(filter);
    window.history.replaceState({}, '', '/');
  });
});
