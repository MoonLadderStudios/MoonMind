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
    expect(screen.getAllByRole('link', { name: 'Checkout release source workflow' })[0]?.getAttribute('href')).toBe('/workflows/mm%3Asource-1');
    expect(screen.getAllByText(/Attention/).length).toBeGreaterThan(0);
    expect(screen.getAllByText('observe_only · snapshot_then_follow').length).toBeGreaterThan(0);
    expect(window.fetch).toHaveBeenCalledWith('/api/executions/remediations', { credentials: 'same-origin' });
  });

  it('distinguishes empty and filtered-empty inventory states', async () => {
    vi.spyOn(window, 'fetch').mockResolvedValue({ ok: true, json: async () => ({ items: [] }) } as Response);
    renderPage();
    expect(await screen.findByText('No remediation workflows are visible.')).toBeTruthy();
    fireEvent.change(screen.getByLabelText('Filter remediations'), { target: { value: 'missing' } });
    expect(screen.getByText('No remediations match the current filter.')).toBeTruthy();
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

    expect(await screen.findByText('No remediation workflows are visible.')).toBeTruthy();
    expect(screen.queryByRole('link', { name: /Unauthorized repair/ })).toBeNull();
  });
});
