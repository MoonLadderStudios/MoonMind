import { beforeEach, describe, expect, it } from 'vitest';

import {
  readRecurringScheduleFocusRequest,
  requestRecurringScheduleFocus,
} from './recurringScheduleFocus';

describe('recurring schedule focus request storage', () => {
  beforeEach(() => {
    window.sessionStorage.clear();
  });

  it('returns null for a stored null payload', () => {
    window.sessionStorage.setItem('moonmind:recurringScheduleFocusRequest', 'null');

    expect(readRecurringScheduleFocusRequest()).toBeNull();
  });

  it('round-trips a valid focus request', () => {
    requestRecurringScheduleFocus({ target: 'table-row', definitionId: 'schedule-1' });

    expect(readRecurringScheduleFocusRequest()).toEqual({
      target: 'table-row',
      definitionId: 'schedule-1',
    });
  });
});
