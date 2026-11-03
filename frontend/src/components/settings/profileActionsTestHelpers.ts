import { fireEvent, screen, within } from '@testing-library/react';

// Provider Profile row actions live in each row's ellipsis menu. These helpers
// open that menu (for the named profile, or the only profile shown) and return
// the requested action so tests exercise the same path as an operator.

function profileActionsTrigger(profileId?: string): HTMLElement {
  const triggers = screen.getAllByRole('button', {
    name: profileId ? `Actions for ${profileId}` : /^Actions for /,
  });
  if (!profileId && triggers.length !== 1) {
    throw new Error(`Expected one Provider Profile actions menu, found ${triggers.length}.`);
  }
  return triggers[0]!;
}

export function openProfileActions(profileId?: string): HTMLElement {
  const trigger = profileActionsTrigger(profileId);
  if (trigger.getAttribute('aria-expanded') !== 'true') fireEvent.click(trigger);
  return screen.getByRole('menu', {
    name: `${profileId ?? trigger.getAttribute('aria-label')!.replace(/^Actions for /, '')} actions`,
  });
}

/** The enabled action named `label`, from the row's opened actions menu. */
export function profileAction(label: string | RegExp, profileId?: string): HTMLElement {
  return within(openProfileActions(profileId)).getByRole('menuitem', { name: label });
}

export function queryProfileAction(label: string | RegExp, profileId?: string): HTMLElement | null {
  const menu = openProfileActions(profileId);
  const toggle = within(menu).queryByRole('menuitem', { name: /^Unavailable \(/ });
  if (toggle && toggle.getAttribute('aria-expanded') !== 'true') fireEvent.click(toggle);
  return within(menu).queryByRole('menuitem', { name: label });
}

export async function findProfileAction(label: string | RegExp, profileId?: string): Promise<HTMLElement> {
  await screen.findAllByRole('button', {
    name: profileId ? `Actions for ${profileId}` : /^Actions for /,
  });
  const trigger = profileActionsTrigger(profileId);
  if (trigger.getAttribute('aria-expanded') === 'true') fireEvent.click(trigger);
  fireEvent.click(trigger);
  const menu = await screen.findByRole('menu', {
    name: `${profileId ?? trigger.getAttribute('aria-label')!.replace(/^Actions for /, '')} actions`,
  });
  return within(menu).findByRole('menuitem', { name: label });
}

/** Close any open profile actions menu without choosing an action. */
export function closeProfileActions(): void {
  const menu = screen.queryByRole('menu', { name: / actions$/ });
  if (menu) fireEvent.keyDown(menu, { key: 'Escape' });
}
