import { Component, type ErrorInfo, type ReactNode } from "react";

import { DashboardErrorState } from "./DashboardErrorState";
import {
  isDynamicImportLoadError,
  reloadOnceForDynamicImportError,
} from "../lib/dynamicImportRecovery";

type Props = {
  children: ReactNode;
  buildId?: string | null;
  /**
   * Called when the user asks to retry after a route error. Used to clear any
   * React Query error state (via QueryErrorResetBoundary) before re-rendering.
   */
  onReset?: () => void;
  /**
   * Identifies what the boundary is showing, normally the route pathname. A
   * route error belongs to the page it happened on, so navigating elsewhere
   * clears it (and runs `onReset`) without the operator pressing Retry.
   */
  resetKey?: string;
};

type State = {
  error: Error | null;
};

/**
 * Route-level error boundary for lazy-loaded dashboard pages (MM-960).
 *
 * Catches render errors from a page module (including lazy import/render
 * failures) and shows a styled, recoverable dashboard error state instead of
 * an unstyled crash. The retry action, or a change of `resetKey`, resets shared
 * React Query error state and clears the boundary so the page re-renders and
 * refetches.
 */
export class DashboardRouteErrorBoundary extends Component<Props, State> {
  override state: State = { error: null };

  static getDerivedStateFromError(error: Error): State {
    return { error };
  }

  override componentDidCatch(error: Error, info: ErrorInfo): void {
    console.error("Dashboard route error:", error, info);
    if (isDynamicImportLoadError(error)) {
      reloadOnceForDynamicImportError(this.props.buildId);
    }
  }

  override componentDidUpdate(prevProps: Props, prevState: State): void {
    // Only an error that already existed before this update is reset: a page
    // that fails on the new key keeps its error instead of retrying in a loop.
    if (prevState.error && this.state.error && prevProps.resetKey !== this.props.resetKey) {
      this.handleReset();
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
