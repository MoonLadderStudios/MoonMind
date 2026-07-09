import { Component, type ErrorInfo, type ReactNode } from 'react';

import { DashboardErrorState } from './DashboardErrorState';
import {
  isDynamicImportLoadError,
  reloadOnceForDynamicImportError,
} from '../lib/dynamicImportRecovery';

type Props = {
  children: ReactNode;
  buildId?: string | null;
  /**
   * Called when the user asks to retry after a route error. Used to clear any
   * React Query error state (via QueryErrorResetBoundary) before re-rendering.
   */
  onReset?: () => void;
};

type State = {
  error: Error | null;
};

/**
 * Route-level error boundary for lazy-loaded dashboard pages (MM-960).
 *
 * Catches render errors from a page module (including lazy import/render
 * failures) and shows a styled, recoverable dashboard error state instead of
 * an unstyled crash. The retry action resets shared React Query error state and
 * clears the boundary so the page re-renders and refetches.
 */
export class DashboardRouteErrorBoundary extends Component<Props, State> {
  override state: State = { error: null };

  static getDerivedStateFromError(error: Error): State {
    return { error };
  }

  override componentDidCatch(error: Error, info: ErrorInfo): void {
    console.error('Dashboard route error:', error, info);
    if (isDynamicImportLoadError(error)) {
      reloadOnceForDynamicImportError(this.props.buildId);
    }
  }

  handleReset = (): void => {
    this.props.onReset?.();
    this.setState({ error: null });
  };

  override render(): ReactNode {
    const { error } = this.state;
    if (error) {
      return (
        <DashboardErrorState
          title="This page failed to load"
          description="Something went wrong while rendering this dashboard page. You can retry, or reload if the problem persists."
          detail={error.message}
          onRetry={this.handleReset}
          retryLabel="Retry"
        />
      );
    }
    return this.props.children;
  }
}

export default DashboardRouteErrorBoundary;
