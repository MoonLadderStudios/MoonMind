import { describe, expect, it } from 'vitest';

import { formatDurationMs, formatStatusLabel } from './formatters';

describe('formatStatusLabel', () => {
  it('replaces underscores with spaces without applying domain-specific mappings', () => {
    expect(formatStatusLabel('waiting_on_dependencies')).toBe('waiting on dependencies');
    expect(formatStatusLabel('awaiting_slot')).toBe('awaiting slot');
    expect(formatStatusLabel('no_commit')).toBe('no commit');
    expect(formatStatusLabel('awaiting_external')).toBe('awaiting external');
    expect(formatStatusLabel('validating_token')).toBe('validating token');
    expect(formatStatusLabel('failed_step_execution')).toBe('Failed step execution');
    expect(formatStatusLabel('constructor')).toBe('constructor');
    expect(formatStatusLabel(null)).toBe('—');
  });
});

describe('formatDurationMs', () => {
  it('formats long durations as hours and minutes', () => {
    expect(formatDurationMs(64 * 60 * 1000)).toBe('1h 04m');
    expect(formatDurationMs(null)).toBe('—');
  });
});
