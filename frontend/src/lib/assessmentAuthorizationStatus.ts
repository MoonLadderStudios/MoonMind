import type { AssessmentAuthorization } from './assessmentEngagement';

/** Display-only validity; server admission still checks scope and remaining budgets. */
export function authorizationStatus(record: Pick<AssessmentAuthorization, 'enabled' | 'validFrom' | 'validUntil'>, now = Date.now()): string {
  if (!record.enabled) return 'Disabled';
  const start = Date.parse(record.validFrom);
  const end = Date.parse(record.validUntil);
  if (!Number.isFinite(start) || !Number.isFinite(end) || end <= start) return 'Invalid validity window';
  if (now >= end) return 'Expired';
  if (now < start) return 'Not yet valid';
  return 'Current';
}
