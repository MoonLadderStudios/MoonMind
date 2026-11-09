import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { z } from 'zod';
import { Button } from '../Button';
import { responseError } from '../../lib/api/client';
import type { SecurityFindingSelection } from '../../lib/remediationCreateDraft';

const DecisionSchema = z.object({
  status: z.string(), reviewReasons: z.array(z.string()).default([]),
  decision: z.object({ disposition: z.string(), reason: z.string(), retestOutcome: z.string().nullable().optional(), reviewAt: z.string(), expiresAt: z.string().nullable().optional(), reviewConditions: z.array(z.string()), evidenceRefs: z.array(z.string()) }),
});
const NativeSecurityEvidenceSchema = z.object({
  comparisonArtifactRef: z.string(), comparisonDigest: z.string(), findingId: z.string(),
  scope: z.object({ assets: z.array(z.string()), exclusions: z.array(z.string()).default([]), repositoryExceptions: z.array(z.string()).default([]) }).optional(),
  coverageStatus: z.string(), intelligenceChanges: z.array(z.string()).default([]),
  scanRef: z.string().nullable().optional(), baselineRef: z.string().nullable().optional(),
  finding: z.object({ findingId: z.string(), state: z.string(), verification: z.string().nullable().optional(), evidenceExpiresAt: z.string(), changedIntelligence: z.array(z.string()).default([]),
    finding: z.object({ nativeId: z.string(), asset: z.string(), ruleId: z.string(), evidenceRefs: z.array(z.string()) }),
  }), decisions: z.array(DecisionSchema),
});
const ReviewSecurityEvidenceSchema = z.object({
  sourceKind: z.literal('agent_review'), reviewArtifactRef: z.string(), reviewDigest: z.string(), interpretationId: z.string(), candidateRef: z.string(),
  scope: z.object({ assets: z.array(z.string()), exclusions: z.array(z.string()).default([]), repositoryExceptions: z.array(z.string()).default([]) }),
  interpretation: z.object({ id: z.string(), hypothesis: z.string(), disposition: z.string(), evidenceStatus: z.string(), evidenceRefs: z.array(z.string()), limitations: z.array(z.string()), subject: z.object({ asset: z.string(), locator: z.string(), identity: z.string(), resource: z.string() }) }),
  threatModel: z.object({ entryPoints: z.array(z.string()), trustBoundaries: z.array(z.string()), invariants: z.array(z.string()) }), limitations: z.array(z.string()),
  retests: z.array(z.object({ hypothesisId: z.string(), originalEvidenceRefs: z.array(z.string()), currentEvidenceRefs: z.array(z.string()), changedAssets: z.array(z.string()), coveredAssets: z.array(z.string()), coverage: z.string(), limitations: z.array(z.string()) })),
});
export const SecurityEvidenceSchema = z.union([NativeSecurityEvidenceSchema, ReviewSecurityEvidenceSchema]);
export type SecurityEvidence = z.infer<typeof SecurityEvidenceSchema>;

function evidenceSelection(evidence: SecurityEvidence): SecurityFindingSelection {
  return 'reviewArtifactRef' in evidence
    ? { reviewArtifactRef: evidence.reviewArtifactRef, interpretationId: evidence.interpretationId }
    : { comparisonArtifactRef: evidence.comparisonArtifactRef, findingId: evidence.findingId };
}
function evidenceIdentity(evidence: SecurityEvidence): string {
  return 'reviewArtifactRef' in evidence ? evidence.interpretationId : evidence.findingId;
}

export function SecurityEvidenceSummary({ evidence }: { evidence: SecurityEvidence }) {
  if ('reviewArtifactRef' in evidence) return <div className="stack">
    <p><strong>{evidence.interpretationId}</strong> · Agent interpretation: {evidence.interpretation.disposition} · Evidence: {evidence.interpretation.evidenceStatus}</p>
    <p>{evidence.interpretation.hypothesis}</p>
    <p>Examined subject: {evidence.interpretation.subject.asset} · {evidence.interpretation.subject.locator} · Identity: {evidence.interpretation.subject.identity} · Resource: {evidence.interpretation.subject.resource}</p>
    <p>Original candidate: <code>{evidence.candidateRef}</code></p>
    <p>Scope: {evidence.scope.assets.join(', ')}. Exclusions: {evidence.scope.exclusions.join(', ') || 'none'}.</p>
    <p>Expected behavior: {evidence.threatModel.invariants.join('; ')}</p>
    <p>Trust boundaries: {evidence.threatModel.trustBoundaries.join('; ')}</p>
    <p>Original evidence: {evidence.interpretation.evidenceRefs.join(', ')}</p>
    <p>Limitations: {[...evidence.limitations, ...evidence.interpretation.limitations].join('; ')}</p>
    {evidence.retests.map((retest, index) => <div key={index} className="stack">
      <p>Retest coverage: {retest.coverage} · Changed: {retest.changedAssets.join(', ')} · Covered: {retest.coveredAssets.join(', ') || 'none'}</p>
      <p>Before: {retest.originalEvidenceRefs.join(', ')} · After: {retest.currentEvidenceRefs.join(', ')}</p>
      <p>{retest.limitations.join('; ')}</p>
    </div>)}
    <p>Coverage and agent interpretation do not establish a verified fix. Actual job outcomes and comparable retest evidence remain authoritative.</p>
    <p>Review: <code>{evidence.reviewArtifactRef}</code></p>
  </div>;
  return <div className="stack">
    <p><strong>{evidence.finding.finding.nativeId}</strong> · {evidence.finding.finding.asset} · {evidence.finding.finding.ruleId}</p>
    <p>Comparison state: <strong>{evidence.finding.state}</strong>{evidence.finding.verification ? ` (${evidence.finding.verification})` : ''}</p>
    {evidence.scope ? <p>Original scope: {evidence.scope.assets.join(', ')}. Exclusions: {evidence.scope.exclusions.join(', ') || 'none'}. Repository exceptions: {evidence.scope.repositoryExceptions.join(', ') || 'none'}.</p> : null}
    <p>Coverage: {evidence.coverageStatus}. Evidence expires: {evidence.finding.evidenceExpiresAt}</p>
    {evidence.intelligenceChanges.length || evidence.finding.changedIntelligence.length ? <p>Changed intelligence: {[...evidence.intelligenceChanges, ...evidence.finding.changedIntelligence].join(', ')}</p> : null}
    {evidence.decisions.map(({ decision, status, reviewReasons }, index) => <div key={index} className="stack">
      <p>Disposition: {decision.disposition} · {status}{decision.retestOutcome ? ` · Retest: ${decision.retestOutcome}` : ''}</p>
      <p>{decision.reason}</p>
      <p>Review: {decision.reviewAt}{decision.expiresAt ? ` · Expires: ${decision.expiresAt}` : ''}</p>
      <p>Review conditions: {decision.reviewConditions.join('; ')}</p>
      {reviewReasons.length ? <p>Review reasons: {reviewReasons.join(', ')}</p> : null}
      <p>Decision evidence: {decision.evidenceRefs.join(', ')}</p>
    </div>)}
    {!evidence.decisions.length ? <p>No reviewed disposition recorded.</p> : null}
    <p>Original evidence: {evidence.finding.finding.evidenceRefs.join(', ')}</p>
    <p>Comparison: <code>{evidence.comparisonArtifactRef}</code>{evidence.scanRef ? ` · Scan: ${evidence.scanRef}` : ''}{evidence.baselineRef ? ` · Baseline: ${evidence.baselineRef}` : ''}</p>
  </div>;
}

export function SecurityFindingsPanel({ workflowId, runId, apiBase, artifacts, onSelect }: {
  workflowId: string; runId: string; apiBase: string;
  artifacts: Array<{ artifactId: string; links?: Array<{ linkType: string }> }>;
  onSelect?: ((selection: SecurityFindingSelection) => void) | undefined;
}) {
  const [selected, setSelected] = useState('');
  const comparisons = artifacts.filter((item) => item.links?.some((link) => ['security.comparison', 'security.review'].includes(link.linkType)));
  const isReview = comparisons.find((item) => item.artifactId === selected)?.links?.some((link) => link.linkType === 'security.review');
  const selectedAvailable = comparisons.some((item) => item.artifactId === selected);
  const query = useQuery({
    queryKey: ['security-findings', workflowId, runId, selected],
    enabled: Boolean(selectedAvailable && runId), retry: false,
    queryFn: async ({ signal }) => {
      const params = new URLSearchParams({ [isReview ? 'reviewArtifactRef' : 'comparisonArtifactRef']: selected, runId });
      const response = await fetch(`${apiBase}/executions/${encodeURIComponent(workflowId)}/remediation-security-findings?${params}`, { credentials: 'same-origin', signal });
      if (!response.ok) throw await responseError(response, 'Security evidence could not be verified.');
      const result = z.object({ comparisonArtifactRef: z.string().optional(), reviewArtifactRef: z.string().optional(), findings: z.array(SecurityEvidenceSchema) }).parse(await response.json());
      if ((isReview ? result.reviewArtifactRef : result.comparisonArtifactRef) !== selected || result.findings.some((item) => 'reviewArtifactRef' in item ? item.reviewArtifactRef !== selected || item.interpretationId !== item.interpretation.id : item.comparisonArtifactRef !== selected || item.findingId !== item.finding.findingId)) throw new Error('Security evidence selection changed. Reload the comparison.');
      return result;
    },
  });
  if (!comparisons.length) return null;
  return <section className="stack" aria-label="Security findings">
    <h3>Security findings and retests</h3>
    <p>Investigation carries the original evidence into Remediation. Fixes and publication require separate authority. Successful CI alone does not verify a security fix; direct infrastructure mutation is unsupported.</p>
    <label>Security evidence<select value={selectedAvailable ? selected : ''} onChange={(event) => setSelected(event.target.value)}><option value="">Select a comparison or review</option>{comparisons.map((item) => <option key={item.artifactId} value={item.artifactId}>{item.artifactId}</option>)}</select></label>
    {query.isFetching ? <p role="status">Verifying security evidence…</p> : null}
    {selectedAvailable && query.isError ? <p role="alert">{query.error.message}</p> : null}
    {selectedAvailable && !query.isError && query.data?.findings.map((evidence) => <article className="stack" key={evidenceIdentity(evidence)}>
      <SecurityEvidenceSummary evidence={evidence} />
      {onSelect ? <Button variant="secondary" disabled={query.isFetching} onClick={() => onSelect(evidenceSelection(evidence))}>Investigate {'reviewArtifactRef' in evidence ? evidence.interpretationId : evidence.finding.finding.nativeId}</Button> : null}
    </article>)}
    {selectedAvailable && query.data?.findings.length === 0 ? <p>No findings in this comparison. Absence alone is not a verified fix.</p> : null}
  </section>;
}
