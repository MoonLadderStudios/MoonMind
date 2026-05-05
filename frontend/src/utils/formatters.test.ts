import { describe, expect, it } from 'vitest';

import { formatStatusLabel } from './formatters';

describe('formatStatusLabel', () => {
  it('uses compact user-facing labels for dependency and slot waits', () => {
    expect(formatStatusLabel('waiting_on_dependencies')).toBe('AWAITING TASK');
    expect(formatStatusLabel('WAITING ON DEPENDENCIES')).toBe('AWAITING TASK');
    expect(formatStatusLabel('awaiting_slot')).toBe('AWAITING SLOT');
  });

  it('replaces status underscores with spaces without changing raw filter values', () => {
    expect(formatStatusLabel('awaiting_external')).toBe('awaiting external');
    expect(formatStatusLabel('validating_token')).toBe('validating token');
    expect(formatStatusLabel(null)).toBe('—');
  });
});
