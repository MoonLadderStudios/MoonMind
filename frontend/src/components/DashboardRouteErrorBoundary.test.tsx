import { afterEach, beforeEach, describe, expect, it, vi, type MockInstance } from 'vitest';
import { QueryErrorResetBoundary } from '@tanstack/react-query';

import { fireEvent, renderWithClient, screen } from '../utils/test-utils';
import { DashboardRouteErrorBoundary } from './DashboardRouteErrorBoundary';

function Boom({ message = 'lazy page exploded' }: { message?: string }): never {
  throw new Error(message);
}

describe('DashboardRouteErrorBoundary', () => {
  let consoleErrorSpy: MockInstance;
  const reload = vi.fn();

  beforeEach(() => {
    consoleErrorSpy = vi.spyOn(console, 'error').mockImplementation(() => {});
    window.sessionStorage.clear();
    reload.mockClear();
    Object.defineProperty(window, 'location', {
      configurable: true,
      value: {
        ...window.location,
        reload,
      },
    });
  });

  afterEach(() => {
    consoleErrorSpy.mockRestore();
    window.sessionStorage.clear();
  });

  it('renders a styled dashboard error state when a page render throws', () => {
    renderWithClient(
      <DashboardRouteErrorBoundary>
        <Boom message="render blew up" />
      </DashboardRouteErrorBoundary>,
    );

    expect(screen.getByRole('alert')).toBeTruthy();
    expect(screen.getByText('This page failed to load')).toBeTruthy();
    expect(screen.getByText('render blew up')).toBeTruthy();
  });

  it('resets query error state and re-renders the page after retry', () => {
    let shouldThrow = true;
    const reset = vi.fn(() => {
      shouldThrow = false;
    });

    function MaybeBoom() {
      if (shouldThrow) {
        throw new Error('transient render failure');
      }
      return <div>Recovered page</div>;
    }

    renderWithClient(
      <QueryErrorResetBoundary>
        {() => (
          <DashboardRouteErrorBoundary onReset={reset}>
            <MaybeBoom />
          </DashboardRouteErrorBoundary>
        )}
      </QueryErrorResetBoundary>,
    );

    expect(screen.getByText('This page failed to load')).toBeTruthy();

    fireEvent.click(screen.getByRole('button', { name: 'Retry' }));

    expect(reset).toHaveBeenCalledTimes(1);
    expect(screen.getByText('Recovered page')).toBeTruthy();
    expect(screen.queryByText('This page failed to load')).toBeNull();
  });

  it('hard reloads once when a lazy route import has a stale chunk failure', () => {
    renderWithClient(
      <DashboardRouteErrorBoundary buildId="build-123">
        <Boom message="Failed to fetch dynamically imported module: /static/workflow_console/dist/assets/schedules-old.js" />
      </DashboardRouteErrorBoundary>,
    );

    expect(reload).toHaveBeenCalledTimes(1);
    expect(
      window.sessionStorage.getItem(
        'moonmind.dashboard.dynamic-import-reload:build-123',
      ),
    ).toBe('1');

    renderWithClient(
      <DashboardRouteErrorBoundary buildId="build-123">
        <Boom message="Failed to fetch dynamically imported module: /static/workflow_console/dist/assets/schedules-old.js" />
      </DashboardRouteErrorBoundary>,
    );

    expect(reload).toHaveBeenCalledTimes(1);
    expect(screen.getAllByText('This page failed to load')).toHaveLength(2);
  });

  it('does not hard reload for ordinary render errors', () => {
    renderWithClient(
      <DashboardRouteErrorBoundary buildId="build-123">
        <Boom message="ordinary render failure" />
      </DashboardRouteErrorBoundary>,
    );

    expect(reload).not.toHaveBeenCalled();
    expect(screen.getByText('ordinary render failure')).toBeTruthy();
  });
});
