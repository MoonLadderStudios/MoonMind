import { afterEach, beforeEach, describe, expect, it, vi, type MockInstance } from 'vitest';
import { screen } from '@testing-library/react';

import type { BootPayload } from '../boot/parseBootPayload';
import { renderWithClient } from '../utils/test-utils';
import { SettingsPage } from './settings';

describe('Settings Entrypoint', () => {
  const mockPayload: BootPayload = {
    page: 'settings',
    apiBase: '/api',
  };

  let fetchSpy: MockInstance;

  beforeEach(() => {
    window.history.pushState({}, 'Settings', '/settings?section=providers-secrets');
    fetchSpy = vi.spyOn(window, 'fetch').mockReturnValue(new Promise(() => {}) as Promise<Response>);
  });

  afterEach(() => {
    fetchSpy.mockRestore();
  });

  it('renders page-matched scoped placeholders for provider profiles and managed secrets', () => {
    renderWithClient(<SettingsPage payload={mockPayload} />);

    expect(screen.getByRole('heading', { name: 'Settings' })).toBeTruthy();
    expect(screen.getByRole('heading', { name: 'Providers & Secrets' })).toBeTruthy();
    expect(screen.getByText('Settings provider profiles loading placeholder').closest('[role="status"]')).toBeTruthy();
    expect(screen.getByText('Settings managed secrets loading placeholder').closest('[role="status"]')).toBeTruthy();
    expect(screen.getAllByTestId('loading-placeholder-table').length).toBeGreaterThanOrEqual(2);
  });
});
