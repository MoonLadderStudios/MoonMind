import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { DashboardErrorDetails } from './DashboardErrorDetails';

describe('DashboardErrorDetails (MM-959)', () => {
  let writeText: ReturnType<typeof vi.fn>;

  beforeEach(() => {
    writeText = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(navigator, 'clipboard', {
      value: { writeText },
      configurable: true,
    });
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it('surfaces the friendly message in the foreground', () => {
    render(<DashboardErrorDetails message="Couldn't reach the service." endpoint="https://api/start" />);
    const alert = screen.getByRole('alert');
    expect(alert.textContent).toContain("Couldn't reach the service.");
  });

  it('keeps endpoint/status/request id behind a technical disclosure', () => {
    render(
      <DashboardErrorDetails
        message="Couldn't reach the service."
        endpoint="https://api/start"
        status={503}
        requestId="req-42"
        rawError="TypeError: Failed to fetch"
      />,
    );
    const summary = screen.getByText('Technical details');
    expect(summary).toBeTruthy();
    // The endpoint is not part of the main message text.
    const message = screen.getByText("Couldn't reach the service.");
    expect(message.textContent).not.toContain('https://api/start');
    // Disclosure body exposes the technical fields.
    expect(screen.getByText('https://api/start')).toBeTruthy();
    expect(screen.getByText('req-42')).toBeTruthy();
    expect(screen.getByText('503')).toBeTruthy();
  });

  it('copies aggregated diagnostics on demand', async () => {
    render(
      <DashboardErrorDetails
        message="Couldn't reach the service."
        endpoint="https://api/start"
        rawError="TypeError: Failed to fetch"
      />,
    );
    fireEvent.click(screen.getByRole('button', { name: 'Copy diagnostics' }));
    await waitFor(() => expect(writeText).toHaveBeenCalledTimes(1));
    const copied = writeText.mock.calls[0]![0] as string;
    expect(copied).toContain('Endpoint: https://api/start');
    expect(copied).toContain('Raw error: TypeError: Failed to fetch');
    await waitFor(() => expect(screen.getByRole('button', { name: 'Copied' })).toBeTruthy());
  });

  it('omits the disclosure when there is no technical detail', () => {
    render(<DashboardErrorDetails message="Something went wrong." />);
    expect(screen.queryByText('Technical details')).toBeNull();
  });
});
