import { afterEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';

import type { BootPayload } from '../boot/parseBootPayload';
import { renderWithClient } from '../utils/test-utils';
import Remediations from './remediations';

const payload: BootPayload = {
  page: 'remediations',
  apiBase: '/api',
  features: { remediationCollection: true },
  initialData: { uiEndpoints: { remediations: '/api/executions/remediations' } },
};

function renderPage(nextPayload: BootPayload = payload) {
  return renderWithClient(<MemoryRouter><Remediations payload={nextPayload} /></MemoryRouter>);
}

afterEach(() => vi.restoreAllMocks());

describe('Remediations', () => {
  it('renders stable remediation and source Workflow links without detail data', async () => {
    vi.spyOn(window, 'fetch').mockResolvedValue({
      ok: true,
      json: async () => ({ items: [{
        remediationWorkflowId: 'mm:repair-1', title: 'Repair checkout', status: 'executing',
        attentionRequired: true, targetWorkflowId: 'mm:source-1', targetTitle: 'Checkout release',
        authorityMode: 'observe_only', mode: 'snapshot_then_follow', latestActionSummary: 'Reviewed logs',
        createdAt: '2026-07-10T00:00:00Z', updatedAt: '2026-07-10T01:00:00Z',
      }] }),
    } as Response);
    renderPage();
    expect((await screen.findAllByRole('link', { name: 'Repair checkout remediation workflow' }))[0]?.getAttribute('href')).toBe('/workflows/mm%3Arepair-1');
    expect(screen.getByRole('heading', { level: 1, name: 'Remediation' })).toBeTruthy();
    expect(document.querySelectorAll('h1')).toHaveLength(1);
    expect(document.title).toBe('Remediation | MoonMind');
    expect(screen.getAllByRole('link', { name: 'Checkout release source workflow' })[0]?.getAttribute('href')).toBe('/workflows/mm%3Asource-1');
    expect(screen.getAllByText(/Attention/).length).toBeGreaterThan(0);
    expect(screen.getAllByText('observe_only · snapshot_then_follow').length).toBeGreaterThan(0);
    expect(window.fetch).toHaveBeenCalledWith('/api/executions/remediations', { credentials: 'same-origin' });
  });

  it('distinguishes empty and filtered-empty inventory states', async () => {
    vi.spyOn(window, 'fetch').mockResolvedValue({ ok: true, json: async () => ({ items: [] }) } as Response);
    renderPage();
    // DataTable renders the empty state in both the desktop table and the
    // mobile cards in jsdom (the browser suite proves a single representation
    // per width), so assert presence plurally like omnigent-inventory.test.tsx.
    expect((await screen.findAllByText('No remediation workflows are visible.')).length).toBeGreaterThanOrEqual(1);
    fireEvent.change(screen.getByLabelText('Filter remediations'), { target: { value: 'missing' } });
    expect(screen.getAllByText('No remediations match the current filter.').length).toBeGreaterThanOrEqual(1);
  });

  it('reports remediations that could not be loaded alongside the loaded rows', async () => {
    vi.spyOn(window, 'fetch').mockResolvedValue({
      ok: true,
      json: async () => ({
        items: [{
          remediationWorkflowId: 'mm:repair-1', title: 'Repair checkout', status: 'executing',
          attentionRequired: false, targetWorkflowId: 'mm:source-1', targetTitle: 'Checkout release',
          authorityMode: 'observe_only', mode: 'snapshot', createdAt: '2026-07-10T00:00:00Z', updatedAt: '2026-07-10T01:00:00Z',
        }],
        unavailableCount: 2,
        unavailableItems: [{
          remediationWorkflowId: 'mm:repair-broken', targetWorkflowId: 'mm:source-2',
          code: 'remediation_item_unavailable', message: 'IntegrityError while loading this remediation or its source workflow.',
        }],
      }),
    } as Response);
    renderPage();

    const notice = (await screen.findByText('2 remediations could not be loaded.')).closest('[role="status"]');
    expect(notice).toBeTruthy();
    expect(notice?.textContent).toContain('mm:repair-broken');
    expect(notice?.textContent).toContain('mm:source-2');
    expect(notice?.textContent).toContain('IntegrityError while loading');
    expect(notice?.textContent).toContain('1 more not listed.');
    expect(screen.getAllByRole('link', { name: 'Repair checkout remediation workflow' }).length).toBeGreaterThan(0);
  });

  it('shows no partial-results notice when every remediation loaded', async () => {
    vi.spyOn(window, 'fetch').mockResolvedValue({
      ok: true,
      json: async () => ({ items: [], unavailableCount: 0, unavailableItems: [] }),
    } as Response);
    renderPage();

    expect((await screen.findAllByText('No remediation workflows are visible.')).length).toBeGreaterThanOrEqual(1);
    expect(screen.queryByText(/could not be loaded/)).toBeNull();
  });

  it('includes the server error detail when the inventory request fails', async () => {
    vi.spyOn(window, 'fetch').mockResolvedValue(new Response(
      JSON.stringify({ detail: { code: 'temporal_unavailable', message: 'Temporal is not reachable.' } }),
      { status: 503, headers: { 'Content-Type': 'application/json' } },
    ));
    renderPage();

    const alert = await screen.findByRole('alert');
    expect(alert.textContent).toContain('Remediation inventory request failed (503): Temporal is not reachable.');
  });

  it('preserves the route shell and offers retry for unauthorized reads', async () => {
    vi.spyOn(window, 'fetch').mockResolvedValue({ ok: false, status: 403 } as Response);
    renderPage();
    expect(await screen.findByRole('alert')).toBeTruthy();
    expect(screen.getByRole('button', { name: 'Try again' })).toBeTruthy();
  });

  it('keeps disabled deployments inside the shell without loading collection data', () => {
    const fetchSpy = vi.spyOn(window, 'fetch');

    renderPage({ ...payload, features: { remediationCollection: false } });

    expect(screen.getByRole('alert').textContent).toContain('not enabled');
    expect(fetchSpy).not.toHaveBeenCalled();
  });

  it('keeps the collection shell pending while UI info has not provided capabilities yet', () => {
    const fetchSpy = vi.spyOn(window, 'fetch');

    renderPage({
      ...payload,
      features: {},
      initialData: {},
    });

    expect(screen.queryByRole('alert')).toBeNull();
    expect(screen.getByRole('button', { name: 'Refresh' }).hasAttribute('disabled')).toBe(true);
    expect(fetchSpy).not.toHaveBeenCalled();
  });

  it('requires the compact list endpoint to be same-origin and shell-provided', () => {
    const fetchSpy = vi.spyOn(window, 'fetch');

    renderPage({
      ...payload,
      initialData: { uiEndpoints: { remediations: 'https://example.test/remediations' } },
    });

    expect(screen.getByRole('alert').textContent).toContain('not enabled');
    expect(fetchSpy).not.toHaveBeenCalled();
  });

  it('rejects backslash-prefixed endpoint URLs before they reach fetch', () => {
    const fetchSpy = vi.spyOn(window, 'fetch');

    renderPage({
      ...payload,
      initialData: { uiEndpoints: { remediations: '/\\example.test/remediations' } },
    });

    expect(screen.getByRole('alert').textContent).toContain('not enabled');
    expect(fetchSpy).not.toHaveBeenCalled();
  });

  it('drops malformed rows instead of manufacturing unauthorized links', async () => {
    vi.spyOn(window, 'fetch').mockResolvedValue({
      ok: true,
      json: async () => ({ items: [{
        remediationWorkflowId: '../outside',
        title: 'Unauthorized repair',
        status: 'executing',
        attentionRequired: false,
        targetWorkflowId: 'mm:source-1',
        targetTitle: 'Source',
        authorityMode: 'observe_only',
        mode: 'snapshot_then_follow',
        createdAt: '2026-07-10T00:00:00Z',
        updatedAt: '2026-07-10T01:00:00Z',
      }] }),
    } as Response);

    renderPage();

    expect((await screen.findAllByText('No remediation workflows are visible.')).length).toBeGreaterThanOrEqual(1);
    expect(screen.queryByRole('link', { name: /Unauthorized repair/ })).toBeNull();
  });
});
