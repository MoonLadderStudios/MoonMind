import { userEvent } from 'vitest/browser';
import { expect } from 'vitest';
import { waitFor, within } from '../utils/test-utils';

// On phones the Create submit bar hides while a form field has focus and
// collapses Repository, Branch and Publish Mode behind a summary
// (MoonMind#5471). Both helpers do nothing on wider screens.

function submitBar(): HTMLElement {
  // A hidden bar has no accessible name, so find it by its label attribute.
  const bar = document.querySelector<HTMLElement>('[aria-label="Workflow submission controls"]');
  if (!bar) throw new Error('The Create submit bar is not rendered.');
  return bar;
}

/** Closes the software keyboard (blurs the field) so the bar returns. */
export async function revealSubmitBar(): Promise<void> {
  const bar = submitBar();
  if (bar.dataset.placement !== 'hidden') return;
  (document.activeElement as HTMLElement | null)?.blur();
  await waitFor(() => expect(bar.dataset.placement).not.toBe('hidden'));
}

/** Expands a collapsed phone bar to its Repository, Branch and Publish Mode controls. */
export async function openSubmitControls(): Promise<void> {
  await revealSubmitBar();
  const toggle = within(submitBar()).queryByRole('button', { name: /^Repository and publish mode:/ });
  if (!toggle || toggle.getAttribute('aria-expanded') === 'true') return;
  await userEvent.click(toggle);
  await waitFor(() => expect(toggle.getAttribute('aria-expanded')).toBe('true'));
}
