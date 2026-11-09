import { expect } from 'vitest';
import { page, userEvent } from 'vitest/browser';
import { assessmentPresetJourneyCases } from '../utils/assessmentPresetJourneyCases';
import '../styles/dashboard.css';

assessmentPresetJourneyCases(userEvent, page, () => page.getByRole('link', { name: 'Create', exact: true }).click(), async () => {
  const summary = page.getByText('Advanced Assessment options', { exact: true });
  await expect.element(page.getByLabelText('Limit', { exact: true })).not.toBeVisible();
  await expect.element(page.getByLabelText('Target', { exact: true })).toBeVisible();
  // Native summary keeps keyboard operation without custom button semantics.
  (summary.element() as HTMLElement).focus();
  await userEvent.keyboard('{Enter}');
  await expect.element(page.getByLabelText('Limit', { exact: true })).toBeVisible();
  await userEvent.keyboard(' ');
  await expect.element(page.getByLabelText('Limit', { exact: true })).not.toBeVisible();
});
