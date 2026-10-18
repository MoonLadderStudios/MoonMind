import { describe, expect, it } from 'vitest';
import { MemoryRouter } from 'react-router-dom';

import { fireEvent, renderWithClient, screen, within } from '../utils/test-utils';
import { DASHBOARD_DESTINATIONS } from '../lib/dashboardRoutes';
import { DashboardSystemMenu } from './DashboardSystemMenu';

const SYSTEM_LABELS = DASHBOARD_DESTINATIONS
  .filter(({ navigationGroup }) => navigationGroup !== 'primary')
  .map(({ label }) => label);

describe('DashboardSystemMenu without UI info', () => {
  it('stays usable from the client registry when /api/ui/info failed', () => {
    renderWithClient(
      <MemoryRouter initialEntries={['/workflows']}>
        <DashboardSystemMenu uiInfo={null} availabilityUnknown mobileDrawerOpen={false} />
      </MemoryRouter>,
    );

    fireEvent.click(screen.getByRole('button', { name: 'System' }));
    const menu = screen.getByRole('menu', { name: 'System' });
    const items = within(menu).getAllByRole('menuitem');
    expect(items.map((item) => item.textContent?.replace(/\s*\(availability unknown\)$/, '').trim()))
      .toEqual(SYSTEM_LABELS);
    // Capability is unknown, not confirmed: say so instead of implying access.
    const noteId = menu.getAttribute('aria-describedby');
    expect(noteId).toBeTruthy();
    expect(document.getElementById(noteId as string)?.textContent).toMatch(/couldn.t check which pages are available/i);
    for (const item of items) {
      expect(item.getAttribute('href')).toBeTruthy();
      expect(item.textContent).toContain('availability unknown');
    }
  });

  it('offers the same fallback in the mobile drawer', () => {
    renderWithClient(
      <MemoryRouter initialEntries={['/workflows']}>
        <DashboardSystemMenu uiInfo={null} availabilityUnknown mobileDrawerOpen />
      </MemoryRouter>,
    );

    const inline = screen.getByLabelText('System destinations');
    expect(within(inline).getByRole('link', { name: /Recurring/ })).toBeTruthy();
    expect(within(inline).getByRole('note')).toBeTruthy();
  });

  it('stays hidden while UI info is still loading', () => {
    renderWithClient(
      <MemoryRouter initialEntries={['/workflows']}>
        <DashboardSystemMenu uiInfo={null} mobileDrawerOpen={false} />
      </MemoryRouter>,
    );

    expect(screen.queryByRole('button', { name: 'System' })).toBeNull();
  });
});
