import { afterEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';

import type { BootPayload } from '../boot/parseBootPayload';
import { renderWithClient } from '../utils/test-utils';
import Remediations from './remediations';

const payload: BootPayload = { page: 'remediations', apiBase: '/api' };

function renderPage() {
  return renderWithClient(<MemoryRouter><Remediations payload={payload} /></MemoryRouter>);
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
    expect((await screen.findAllByRole('link', { name: 'Repair checkout' }))[0]?.getAttribute('href')).toBe('/workflows/mm%3Arepair-1');
    expect(screen.getAllByRole('link', { name: 'Checkout release' })[0]?.getAttribute('href')).toBe('/workflows/mm%3Asource-1');
    expect(screen.getAllByText(/Attention/).length).toBeGreaterThan(0);
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
});
