import { afterEach, describe, expect, it, vi } from 'vitest';
import { act, fireEvent, render, screen } from '@testing-library/react';

import { DashboardToastProvider, useDashboardToast } from './DashboardToast';

function ToastHarness() {
  const toast = useDashboardToast();
  return (
    <div>
      <button
        type="button"
        onClick={() =>
          toast.success({
            title: 'Rerun requested',
            message: 'Example workflow has been queued.',
            action: { label: 'View workflow', href: '/workflows/wf-123?source=temporal' },
          })
        }
      >
        Show success
      </button>
      <button
        type="button"
        onClick={() =>
          toast.error({
            title: 'Workflow action failed',
            message: 'Request failed.',
          })
        }
      >
        Show error
      </button>
    </div>
  );
}

function renderToastHarness() {
  render(
    <DashboardToastProvider>
      <ToastHarness />
    </DashboardToastProvider>,
  );
}

afterEach(() => {
  vi.useRealTimers();
});

describe('DashboardToast (MM-1033)', () => {
  it('renders a success toast with action and manual dismiss', () => {
    renderToastHarness();
    fireEvent.click(screen.getByRole('button', { name: 'Show success' }));

    const toast = screen.getByRole('status');
    expect(toast.className).toContain('dashboard-toast--success');
    expect(screen.getByText('Example workflow has been queued.')).toBeTruthy();
    const action = screen.getByRole('link', { name: 'View workflow' });
    expect(action.getAttribute('href')).toBe('/workflows/wf-123?source=temporal');

    fireEvent.click(screen.getByRole('button', { name: 'Dismiss Rerun requested' }));
    expect(screen.queryByRole('status')).toBeNull();
  });

  it('auto-dismisses success toasts after a short delay', () => {
    vi.useFakeTimers();
    renderToastHarness();
    fireEvent.click(screen.getByRole('button', { name: 'Show success' }));
    expect(screen.getByRole('status')).toBeTruthy();

    act(() => {
      vi.advanceTimersByTime(5000);
    });

    expect(screen.queryByRole('status')).toBeNull();
  });

  it('pauses auto-dismiss while hovered or focused', () => {
    vi.useFakeTimers();
    renderToastHarness();
    fireEvent.click(screen.getByRole('button', { name: 'Show success' }));
    const toast = screen.getByRole('status');

    fireEvent.mouseEnter(toast);
    act(() => {
      vi.advanceTimersByTime(6000);
    });
    expect(screen.getByRole('status')).toBeTruthy();

    fireEvent.mouseLeave(toast);
    act(() => {
      vi.advanceTimersByTime(5000);
    });
    expect(screen.queryByRole('status')).toBeNull();

    fireEvent.click(screen.getByRole('button', { name: 'Show success' }));
    const focusedToast = screen.getByRole('status');
    fireEvent.focus(screen.getByRole('link', { name: 'View workflow' }));
    act(() => {
      vi.advanceTimersByTime(6000);
    });
    expect(screen.getByRole('status')).toBe(focusedToast);

    fireEvent.blur(screen.getByRole('link', { name: 'View workflow' }), {
      relatedTarget: document.body,
    });
    act(() => {
      vi.advanceTimersByTime(5000);
    });
    expect(screen.queryByRole('status')).toBeNull();
  });

  it('uses alert semantics for error toasts', () => {
    renderToastHarness();
    fireEvent.click(screen.getByRole('button', { name: 'Show error' }));

    const alert = screen.getByRole('alert');
    expect(alert.className).toContain('dashboard-toast--error');
    expect(screen.getByText('Request failed.')).toBeTruthy();
  });
});
