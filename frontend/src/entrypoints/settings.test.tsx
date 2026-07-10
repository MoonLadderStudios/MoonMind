import { afterEach, beforeEach, describe, expect, it, vi, type MockInstance } from 'vitest';
import { fireEvent, screen } from '@testing-library/react';

import type { BootPayload } from '../boot/parseBootPayload';
import { renderWithClient } from '../utils/test-utils';
import { SettingsPage } from './settings';
import { readDashboardPreferences, updateDashboardPreferences } from '../utils/dashboardPreferences';

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
    window.localStorage.clear();
  });

  it('MM-1185 resets collection layouts and remembered identities from Settings', () => {
    window.history.replaceState({}, 'Settings', '/settings?section=user-workspace');
    updateDashboardPreferences({
      workflowListDisplayMode: 'hidden',
      lastSelectedWorkflowId: 'workflow-one',
      recurringListDisplayMode: 'hidden',
      lastSelectedDefinitionId: 'schedule-one',
    });
    renderWithClient(<SettingsPage payload={mockPayload} />);

    fireEvent.click(screen.getByRole('button', { name: 'Reset dashboard preferences' }));

    expect(readDashboardPreferences().workflowListDisplayMode).toBe('sidebar');
    expect(readDashboardPreferences().lastSelectedWorkflowId).toBe('');
    expect(readDashboardPreferences().recurringListDisplayMode).toBe('table');
    expect(readDashboardPreferences().lastSelectedDefinitionId).toBe('');
    expect(screen.getByText('Dashboard preferences reset.')).toBeTruthy();
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
