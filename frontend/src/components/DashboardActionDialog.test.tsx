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
        title="Force cancel workflow"
        subject="Workflow title"
        compactId="wf-1"
        consequence="Force cancel this workflow."
        valueLabel="Reason"
        confirmLabel="Force cancel workflow"
        destructive
        confirmationText="FORCE CANCEL"
        onCancel={vi.fn()}
        onConfirm={onConfirm}
      />,
    );

    const dialog = screen.getByRole('dialog', { name: 'Force cancel workflow' });
    const closeButton = screen.getByRole('button', { name: 'Close Force cancel workflow' });
    const confirmButton = screen.getByRole('button', { name: 'Force cancel workflow' }) as HTMLButtonElement;
    expect(confirmButton.disabled).toBe(true);

    closeButton.focus();
    fireEvent.keyDown(dialog, { key: 'Tab', shiftKey: true });
    expect(document.activeElement).toBe(screen.getByRole('button', { name: 'Cancel' }));

    fireEvent.change(screen.getByLabelText('Type FORCE CANCEL to confirm'), {
      target: { value: 'FORCE CANCEL' },
    });
    expect(confirmButton.disabled).toBe(false);
    confirmButton.focus();
    fireEvent.keyDown(dialog, { key: 'Tab' });
    expect(document.activeElement).toBe(closeButton);
    fireEvent.click(confirmButton);
    expect(onConfirm).toHaveBeenCalledWith('');
  });
});
