import { useState } from 'react';
import { describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen } from '@testing-library/react';

import { DashboardActionDialog } from './DashboardActionDialog';

describe('DashboardActionDialog', () => {
  it('closes on Escape, restores focus, and does not confirm on cancel', async () => {
    const onCancel = vi.fn();
    const onConfirm = vi.fn();
    function Harness() {
      const [open, setOpen] = useState(false);
      return (
        <>
          <button type="button" onClick={() => setOpen(true)}>Trigger action</button>
          <DashboardActionDialog
            open={open}
            title="Rename workflow"
            subject="Workflow title"
            compactId="wf-1"
            consequence="Rename this workflow."
            valueLabel="Workflow title"
            confirmLabel="Rename workflow"
            onCancel={() => {
              onCancel();
              setOpen(false);
            }}
            onConfirm={onConfirm}
          />
        </>
      );
    }

    render(<Harness />);
    const trigger = screen.getByRole('button', { name: 'Trigger action' });
    trigger.focus();
    fireEvent.click(trigger);
    fireEvent.keyDown(screen.getByRole('dialog', { name: 'Rename workflow' }), { key: 'Escape' });

    expect(onCancel).toHaveBeenCalledTimes(1);
    expect(onConfirm).not.toHaveBeenCalled();
    await new Promise((resolve) => window.setTimeout(resolve, 0));
    expect(document.activeElement).toBe(trigger);
  });

  it('traps tab focus inside the dialog and requires typed destructive confirmation', () => {
    const onConfirm = vi.fn();
    render(
      <DashboardActionDialog
        open
        title="Delete schedule"
        subject="Nightly maintenance"
        compactId="schedule-1"
        consequence="Delete this recurring schedule."
        confirmLabel="Delete schedule"
        destructive
        confirmationText="DELETE"
        onCancel={vi.fn()}
        onConfirm={onConfirm}
      />,
    );

    const dialog = screen.getByRole('dialog', { name: 'Delete schedule' });
    const closeButton = screen.getByRole('button', { name: 'Close Delete schedule' });
    const confirmButton = screen.getByRole('button', { name: 'Delete schedule' }) as HTMLButtonElement;
    expect(confirmButton.disabled).toBe(true);

    closeButton.focus();
    fireEvent.keyDown(dialog, { key: 'Tab', shiftKey: true });
    expect(document.activeElement).toBe(screen.getByRole('button', { name: 'Cancel' }));

    fireEvent.change(screen.getByLabelText('Type DELETE to confirm'), {
      target: { value: 'DELETE' },
    });
    expect(confirmButton.disabled).toBe(false);
    confirmButton.focus();
    fireEvent.keyDown(dialog, { key: 'Tab' });
    expect(document.activeElement).toBe(closeButton);
    fireEvent.click(confirmButton);
    expect(onConfirm).toHaveBeenCalledWith('');
  });

  it('disables confirmation while the action is pending', () => {
    const onConfirm = vi.fn();
    render(
      <DashboardActionDialog
        open
        title="Archive report"
        subject="Daily report"
        compactId="report-1"
        consequence="Archive this report."
        confirmLabel="Archiving"
        confirmPending
        onCancel={vi.fn()}
        onConfirm={onConfirm}
      />,
    );

    const confirmButton = screen.getByRole('button', { name: 'Archiving' }) as HTMLButtonElement;
    expect(confirmButton.disabled).toBe(true);
    expect(confirmButton.getAttribute('aria-busy')).toBe('true');

    fireEvent.click(confirmButton);
    fireEvent.submit(confirmButton.closest('form') as HTMLFormElement);
    expect(onConfirm).not.toHaveBeenCalled();
  });
});
