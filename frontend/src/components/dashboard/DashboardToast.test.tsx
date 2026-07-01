import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { act, fireEvent, render, screen } from '@testing-library/react';

import { DashboardToastProvider, useDashboardToast } from './DashboardToast';

function ToastHarness() {
  const toast = useDashboardToast();
  return (
    <button
      type="button"
      onClick={() => {
        toast.success({
          title: 'Rerun requested',
          message: 'Example workflow has been queued.',
          action: { label: 'View workflow', href: '/workflows/wf-123?source=temporal' },
          durationMs: 1000,
        });
      }}
    >
      Show toast
    </button>
  );
}

function renderToastHarness() {
  render(
    <DashboardToastProvider>
      <ToastHarness />
    </DashboardToastProvider>,
  );
  fireEvent.click(screen.getByRole('button', { name: 'Show toast' }));
}

describe('DashboardToast (MM-1033)', () => {
  beforeEach(() => {
    vi.useFakeTimers();
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it('auto-dismisses success toasts after their duration', () => {
    renderToastHarness();
    expect(screen.getByRole('status')).toBeTruthy();

    act(() => {
      vi.advanceTimersByTime(1000);
    });

    expect(screen.queryByRole('status')).toBeNull();
  });

  it('pauses auto-dismiss while hovered', () => {
    renderToastHarness();
    const toast = screen.getByRole('status');

    fireEvent.mouseEnter(toast);
    act(() => {
      vi.advanceTimersByTime(1500);
    });
    expect(screen.getByRole('status')).toBeTruthy();

    fireEvent.mouseLeave(toast);
    act(() => {
      vi.advanceTimersByTime(1000);
    });
    expect(screen.queryByRole('status')).toBeNull();
  });

  it('pauses auto-dismiss while focused', () => {
    renderToastHarness();
    const closeButton = screen.getByRole('button', { name: 'Dismiss Rerun requested' });

    fireEvent.focus(closeButton);
    act(() => {
      vi.advanceTimersByTime(1500);
    });
    expect(screen.getByRole('status')).toBeTruthy();

    fireEvent.blur(closeButton);
    act(() => {
      vi.advanceTimersByTime(1000);
    });
    expect(screen.queryByRole('status')).toBeNull();
  });
});
