import { afterEach, beforeEach, describe, expect, it, vi, type MockInstance } from 'vitest';
import { fireEvent, screen, waitFor } from '@testing-library/react';
import { BrowserRouter } from 'react-router-dom';

import type { BootPayload } from '../boot/parseBootPayload';
import { renderWithClient } from '../utils/test-utils';
import { OperationsSettingsPage } from './settings';

// MoonLadderStudios/MoonMind#4353: the Operations configuration view replaces
// User / Workspace naming and human-scope selectors. It renders without any
// account/session/profile bootstrap request and without user identity.
describe('Operations settings page (account-free)', () => {
  const mockPayload: BootPayload = {
    page: 'settings-operations',
    apiBase: '/api',
    initialData: {},
  };

  let fetchSpy: MockInstance;

  beforeEach(() => {
    window.history.pushState({}, 'Settings', '/settings/operations');
    fetchSpy = vi.spyOn(window, 'fetch').mockReturnValue(new Promise(() => {}) as Promise<Response>);
  });

  afterEach(() => {
    fetchSpy.mockRestore();
    window.localStorage.clear();
  });

  it('renders the Operations view with no human-scope selector or signed-in user', async () => {
    renderWithClient(
      <BrowserRouter><OperationsSettingsPage payload={mockPayload} /></BrowserRouter>,
    );

    expect(await screen.findByRole('heading', { name: 'Operations' })).toBeTruthy();
    expect(screen.queryByRole('heading', { name: 'User / Workspace' })).toBeNull();
    expect(screen.queryByText('Signed-in user')).toBeNull();
    expect(screen.queryByLabelText('Settings scope')).toBeNull();
  });

  it('renders the page title as the single h1 with plain-language scope copy', async () => {
    renderWithClient(
      <BrowserRouter><OperationsSettingsPage payload={mockPayload} /></BrowserRouter>,
    );

    expect(await screen.findByRole('heading', { level: 1, name: 'Operations' })).toBeTruthy();
    expect(document.querySelectorAll('h1')).toHaveLength(1);
    expect(document.title).toBe('Operations | MoonMind');
    expect(document.body.textContent).not.toMatch(/MoonMind#\d+/);
    expect(screen.getByText('Advanced')).toBeTruthy();
  });

  it('sends no account, session, or profile bootstrap request', async () => {
    renderWithClient(
      <BrowserRouter><OperationsSettingsPage payload={mockPayload} /></BrowserRouter>,
    );

    await screen.findByRole('heading', { name: 'Operations' });
    fireEvent.click(screen.getByText('Advanced'));
    await waitFor(() => expect(fetchSpy.mock.calls.some(([url]) =>
      String(url).startsWith('/api/v1/settings/catalog'),
    )).toBe(true));
    const urls = fetchSpy.mock.calls.map(([url]) => String(url));
    expect(urls.some((url) => url === '/me' || url.includes('/api/me/profile'))).toBe(false);
    expect(urls.some((url) => /[?&]scope=(personal|user|global)\b/.test(url))).toBe(false);
  });
});
