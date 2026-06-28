import { describe, expect, it } from 'vitest';
import { fireEvent, render, screen } from '@testing-library/react';
import { DashboardNotice, type DashboardNoticeVariant } from './DashboardNotice';

const VARIANTS: DashboardNoticeVariant[] = [
  'info',
  'success',
  'warning',
  'error',
  'pending',
];

describe('DashboardNotice (MM-959)', () => {
  it.each(VARIANTS)('renders the %s variant class', (variant) => {
    render(
      <DashboardNotice variant={variant} title={`title-${variant}`}>
        body
      </DashboardNotice>,
    );
    const notice = screen.getByText(`title-${variant}`).closest('.dashboard-notice');
    expect(notice).toBeTruthy();
    expect(notice?.classList.contains(`dashboard-notice--${variant}`)).toBe(true);
  });

  it('uses an alert role for the error variant and status otherwise', () => {
    const { rerender } = render(<DashboardNotice variant="error">boom</DashboardNotice>);
    expect(screen.getByRole('alert')).toBeTruthy();
    rerender(<DashboardNotice variant="info">fyi</DashboardNotice>);
    expect(screen.getByRole('status')).toBeTruthy();
  });

  it('exposes a collapsible technical details disclosure', () => {
    render(
      <DashboardNotice variant="warning" details={<span>raw detail</span>}>
        body
      </DashboardNotice>,
    );
    const summary = screen.getByText('Technical details');
    expect(summary).toBeTruthy();
    expect(screen.getByText('raw detail')).toBeTruthy();
    // Toggling the disclosure should not throw.
    fireEvent.click(summary);
  });
});
