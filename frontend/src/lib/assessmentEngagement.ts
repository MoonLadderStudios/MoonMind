import { responseError } from './api/client';
import { newRequestId } from './requestId';

export const ASSESSMENT_AUTHORIZATIONS_KEY = 'security.assessment_authorizations';
export interface AssessmentAuthorization {
  ref: string;
  name: string;
  enabled: boolean;
  generation?: string;
  authorityKind: 'self_hosted' | 'client_engagement';
  authoritySource: string;
  validFrom: string;
  validUntil: string;
  maxTotalJobSeconds: number;
  scope: Record<string, unknown>;
}
export interface AssessmentEngagementReview {
  schemaVersion: 'assessment-engagement/v1';
  authorizationRef: string;
  authorizationGeneration: string;
  scopeDigest: string;
  proposalDigest: string;
  scope: Record<string, unknown>;
  sections: Array<{ label: string; items: string[] }>;
  limitations: string[];
}
export function readAssessmentAuthorizations(value: unknown): AssessmentAuthorization[] {
  if (!value || typeof value !== 'object') return [];
  const records = (value as { authorizations?: unknown }).authorizations;
  return Array.isArray(records) ? records as AssessmentAuthorization[] : [];
}
export function copyAssessmentReview(value: unknown): AssessmentEngagementReview | null {
  if (!value || typeof value !== 'object') return null;
  const review = value as AssessmentEngagementReview;
  return review.schemaVersion === 'assessment-engagement/v1'
    && typeof review.authorizationRef === 'string' && typeof review.authorizationGeneration === 'string'
    && typeof review.scopeDigest === 'string' && typeof review.proposalDigest === 'string'
    && review.scope !== null && typeof review.scope === 'object' && !Array.isArray(review.scope)
    && Array.isArray(review.sections) && review.sections.every(section => section && typeof section.label === 'string' && Array.isArray(section.items) && section.items.every(item => typeof item === 'string'))
    && Array.isArray(review.limitations) && review.limitations.every(item => typeof item === 'string')
    ? structuredClone(review) : null;
}
export function createAssessmentAuthorization(): AssessmentAuthorization {
  return {
    ref: `assessment-${newRequestId()}`, name: 'New authorization', enabled: false,
    authorityKind: 'self_hosted', authoritySource: '', validFrom: '', validUntil: '', maxTotalJobSeconds: 60,
    scope: { assessmentRef: '', profileRefs: [], actionClasses: [], targets: [], exclusions: [], configurationExceptions: [], networkRefs: [], maxJobs: 1, maxJobSeconds: 60, maxCpuMillis: 1000, maxMemoryMiB: 1024, maxRequestsPerSecond: 10, evidenceIntent: '', grants: [] },
  };
}
export async function reviewAssessmentProposal(payload: Record<string, unknown>, authorizationRef: string, signal?: AbortSignal, executionId?: string): Promise<AssessmentEngagementReview> {
  const response = await fetch('/api/executions/assessment-proposal', {
    method: 'POST', headers: { 'Content-Type': 'application/json', Accept: 'application/json' },
    body: JSON.stringify({ payload, authorizationRef, ...(executionId ? { executionId } : {}) }), ...(signal ? { signal } : {}),
  });
  if (!response.ok) throw await responseError(response, 'Could not prepare rules of engagement');
  const review = copyAssessmentReview(await response.json());
  if (!review || review.authorizationRef !== authorizationRef) throw new Error('The rules of engagement response did not match the selected authorization. Review again.');
  return review;
}
