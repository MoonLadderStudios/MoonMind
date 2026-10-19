import { act } from 'react';
import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

import { WorkflowActionsMenu } from './WorkflowActionsMenu';

describe('WorkflowActionsMenu keyboard activation', () => {
  it.each(['Enter', ' '])('activates the focused item before its focus state commits with %j', (key) => {
    const rerun = vi.fn();
    const remediate = vi.fn();
    render(<WorkflowActionsMenu items={[
      { id: 'rerun', label: 'Rerun', onSelect: rerun },
      { id: 'remediate', label: 'Remediate', onSelect: remediate },
    ]} />);
    fireEvent.keyDown(screen.getByRole('button', { name: 'Workflow actions' }), { key: 'Enter' });
    const firstItem = screen.getByRole('menuitem', { name: 'Rerun' });
    const nextItem = screen.getByRole('menuitem', { name: 'Remediate' });
    expect(document.activeElement).toBe(firstItem);

    // Focus and activation can arrive before React commits the roving tab stop.
    // The keyboard event still belongs to the item the operator focused.
    act(() => {
      nextItem.focus();
      nextItem.dispatchEvent(new KeyboardEvent('keydown', { key, bubbles: true }));
    });

    expect(remediate).toHaveBeenCalledExactlyOnceWith();
    expect(rerun).not.toHaveBeenCalled();
    expect(screen.queryByRole('menu')).toBeNull();
  });

  it.each([
    ['ArrowDown', 'Last'],
    ['ArrowUp', 'First'],
  ])('moves %s from the focused item before its focus state commits', (key, expected) => {
    render(<WorkflowActionsMenu items={[
      { id: 'first', label: 'First' },
      { id: 'middle', label: 'Middle' },
      { id: 'last', label: 'Last' },
    ]} />);
    fireEvent.keyDown(screen.getByRole('button', { name: 'Workflow actions' }), { key: 'Enter' });
    const middle = screen.getByRole('menuitem', { name: 'Middle' });

    act(() => {
      middle.focus();
      middle.dispatchEvent(new KeyboardEvent('keydown', { key, bubbles: true }));
    });

    expect(document.activeElement).toBe(screen.getByRole('menuitem', { name: expected }));
  });

  it.each(['Enter', ' '])('does not activate a disabled focused item with %j', (key) => {
    const cancel = vi.fn();
    render(<WorkflowActionsMenu items={[
      { id: 'cancel', label: 'Cancel', section: 'unavailable', disabledReason: 'Already finished.', onSelect: cancel },
    ]} />);
    fireEvent.keyDown(screen.getByRole('button', { name: 'Workflow actions' }), { key: 'Enter' });
    fireEvent.click(screen.getByRole('menuitem', { name: 'Unavailable (1)' }));
    const disabled = screen.getByRole('menuitem', { name: 'Cancel' });

    act(() => {
      disabled.focus();
      disabled.dispatchEvent(new KeyboardEvent('keydown', { key, bubbles: true }));
    });

    expect(cancel).not.toHaveBeenCalled();
    expect(screen.getByRole('menuitem', { name: 'Cancel' })).toBe(disabled);
    expect(document.activeElement).toBe(disabled);
  });
});
