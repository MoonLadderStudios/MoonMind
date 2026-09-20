import { afterEach, beforeEach, describe, expect, it, vi, type MockInstance } from 'vitest';
import { screen } from '@testing-library/react';
import { BrowserRouter } from 'react-router-dom';

import type { BootPayload } from '../boot/parseBootPayload';
import { renderWithClient } from '../utils/test-utils';
import { InstanceSettingsPage } from './settings';

// MoonLadderStudios/MoonMind#4353: the Instance configuration view replaces
// User / Workspace naming and human-scope selectors. It renders without any
// account/session/profile bootstrap request and without user identity.
describe('Instance settings page (account-free)', () => {
  const mockPayload: BootPayload = {
    page: 'settings-instance',
    apiBase: '/api',
    initialData: {
      settingsPermissions: ['settings.catalog.read', 'settings.audit.read'],
    },
  };

  let fetchSpy: MockInstance;

  beforeEach(() => {
    window.history.pushState({}, 'Settings', '/settings/instance');
    fetchSpy = vi.spyOn(window, 'fetch').mockReturnValue(new Promise(() => {}) as Promise<Response>);
  });

  afterEach(() => {
    fetchSpy.mockRestore();
    window.localStorage.clear();
  });

  it('renders the Instance view with no human-scope selector or signed-in user', async () => {
    renderWithClient(
      <BrowserRouter><InstanceSettingsPage payload={mockPayload} /></BrowserRouter>,
    );

    expect(await screen.findByRole('heading', { name: 'Instance' })).toBeTruthy();
    expect(screen.queryByRole('heading', { name: 'User / Workspace' })).toBeNull();
    expect(screen.queryByText('Signed-in user')).toBeNull();
    expect(screen.queryByLabelText('Settings scope')).toBeNull();
  });

  it('sends no account, session, or profile bootstrap request', async () => {
    renderWithClient(
      <BrowserRouter><InstanceSettingsPage payload={mockPayload} /></BrowserRouter>,
    );

    await screen.findByRole('heading', { name: 'Instance' });
    const urls = fetchSpy.mock.calls.map(([url]) => String(url));
    expect(urls.some((url) => url === '/me' || url.includes('/api/me/profile'))).toBe(false);
    expect(urls.some((url) => /[?&]scope=(personal|user|global)\b/.test(url))).toBe(false);
  });
});
