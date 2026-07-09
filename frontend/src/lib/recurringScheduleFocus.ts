export type RecurringScheduleFocusTarget =
  | 'detail-heading'
  | 'sidebar-row'
  | 'table-row'
  | 'table-title';

export type RecurringScheduleFocusRequest = {
  target: RecurringScheduleFocusTarget;
  definitionId?: string | undefined;
};

const STORAGE_KEY = 'moonmind:recurringScheduleFocusRequest';

export function requestRecurringScheduleFocus(request: RecurringScheduleFocusRequest): void {
  try {
    window.sessionStorage.setItem(STORAGE_KEY, JSON.stringify(request));
  } catch {
    // Focus restoration is progressive; navigation should not depend on storage.
  }
}

export function readRecurringScheduleFocusRequest(): RecurringScheduleFocusRequest | null {
  try {
    const raw = window.sessionStorage.getItem(STORAGE_KEY);
    if (!raw) {
      return null;
    }
    const parsed = JSON.parse(raw) as Partial<RecurringScheduleFocusRequest> | null;
    if (
      !parsed ||
      typeof parsed !== 'object' ||
      parsed.target !== 'detail-heading' &&
      parsed.target !== 'sidebar-row' &&
      parsed.target !== 'table-row' &&
      parsed.target !== 'table-title'
    ) {
      return null;
    }
    return {
      target: parsed.target,
      definitionId: typeof parsed.definitionId === 'string' ? parsed.definitionId : undefined,
    };
  } catch {
    return null;
  }
}

export function clearRecurringScheduleFocusRequest(): void {
  try {
    window.sessionStorage.removeItem(STORAGE_KEY);
  } catch {
    // Ignore unavailable session storage.
  }
}
