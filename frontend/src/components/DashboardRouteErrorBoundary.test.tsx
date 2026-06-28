import { afterEach, beforeEach, describe, expect, it, vi, type MockInstance } from 'vitest';
import { QueryErrorResetBoundary } from '@tanstack/react-query';

import { fireEvent, renderWithClient, screen } from '../utils/test-utils';
import { DashboardRouteErrorBoundary } from './DashboardRouteErrorBoundary';

function Boom({ message = 'lazy page exploded' }: { message?: string }): never {
  throw new Error(message);
}

describe('DashboardRouteErrorBoundary', () => {
  let consoleErrorSpy: MockInstance;

  beforeEach(() => {
    consoleErrorSpy = vi.spyOn(console, 'error').mockImplementation(() => {});
  });

  afterEach(() => {
    consoleErrorSpy.mockRestore();
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
});
