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
export const SecurityEvidenceSchema = z.object({
  comparisonArtifactRef: z.string(), comparisonDigest: z.string(), findingId: z.string(),
  scope: z.object({ assets: z.array(z.string()), exclusions: z.array(z.string()).default([]), repositoryExceptions: z.array(z.string()).default([]) }).optional(),
  coverageStatus: z.string(), intelligenceChanges: z.array(z.string()).default([]),
  scanRef: z.string().nullable().optional(), baselineRef: z.string().nullable().optional(),
  finding: z.object({ findingId: z.string(), state: z.string(), verification: z.string().nullable().optional(), evidenceExpiresAt: z.string(), changedIntelligence: z.array(z.string()).default([]),
    finding: z.object({ nativeId: z.string(), asset: z.string(), ruleId: z.string(), evidenceRefs: z.array(z.string()) }),
  }), decisions: z.array(DecisionSchema),
});
export type SecurityEvidence = z.infer<typeof SecurityEvidenceSchema>;

export function SecurityEvidenceSummary({ evidence }: { evidence: SecurityEvidence }) {
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
  const comparisons = artifacts.filter((item) => item.links?.some((link) => link.linkType === 'security.comparison'));
  const selectedAvailable = comparisons.some((item) => item.artifactId === selected);
  const query = useQuery({
    queryKey: ['security-findings', workflowId, runId, selected],
    enabled: Boolean(selectedAvailable && runId), retry: false,
    queryFn: async ({ signal }) => {
      const params = new URLSearchParams({ comparisonArtifactRef: selected, runId });
      const response = await fetch(`${apiBase}/executions/${encodeURIComponent(workflowId)}/remediation-security-findings?${params}`, { credentials: 'same-origin', signal });
      if (!response.ok) throw await responseError(response, 'Security evidence could not be verified.');
      const result = z.object({ comparisonArtifactRef: z.string(), findings: z.array(SecurityEvidenceSchema) }).parse(await response.json());
      if (result.comparisonArtifactRef !== selected || result.findings.some((item) => item.comparisonArtifactRef !== selected || item.findingId !== item.finding.findingId)) throw new Error('Security evidence selection changed. Reload the comparison.');
      return result;
    },
  });
  if (!comparisons.length) return null;
  return <section className="stack" aria-label="Security findings">
    <h3>Security findings and retests</h3>
    <p>Investigation carries the original evidence into Remediation. Fixes and publication require separate authority. Successful CI alone does not verify a security fix; direct infrastructure mutation is unsupported.</p>
    <label>Security comparison<select value={selectedAvailable ? selected : ''} onChange={(event) => setSelected(event.target.value)}><option value="">Select a comparison</option>{comparisons.map((item) => <option key={item.artifactId} value={item.artifactId}>{item.artifactId}</option>)}</select></label>
    {query.isFetching ? <p role="status">Verifying security evidence…</p> : null}
    {selectedAvailable && query.isError ? <p role="alert">{query.error.message}</p> : null}
    {selectedAvailable && !query.isError && query.data?.findings.map((evidence) => <article className="stack" key={evidence.findingId}>
      <SecurityEvidenceSummary evidence={evidence} />
      {onSelect ? <Button variant="secondary" disabled={query.isFetching} onClick={() => onSelect({ comparisonArtifactRef: selected, findingId: evidence.findingId })}>Investigate {evidence.finding.finding.nativeId}</Button> : null}
    </article>)}
    {selectedAvailable && query.data?.findings.length === 0 ? <p>No findings in this comparison. Absence alone is not a verified fix.</p> : null}
  </section>;
}
