import { useQuery } from '@tanstack/react-query';
import { fetchCatalog } from '../settings/GeneratedSettingsSection';
import { ASSESSMENT_AUTHORIZATIONS_KEY, readAssessmentAuthorizations, type AssessmentEngagementReview as Review } from '../../lib/assessmentEngagement';

export function AssessmentAuthorizationPicker({ value, onChange }: { value: string; onChange: (value: string) => void }) {
  const catalog = useQuery({ queryKey: ['settings-catalog'], queryFn: fetchCatalog });
  const descriptor = Object.values(catalog.data?.categories ?? {}).flat().find(item => item.key === ASSESSMENT_AUTHORIZATIONS_KEY);
  const records = readAssessmentAuthorizations(descriptor?.effective_value);
  return <section className="card stack" aria-label="Assessment authorization selection">
    <h2>Assessment authorization</h2>
    <p className="small">Choose a saved authorization to review exact rules of engagement before starting. Selecting a record or loading assessment context does not start work or grant access. <a href="/settings/instance#assessment-authorizations">Manage authorizations in Settings</a>.</p>
    <label>Assessment authorization<select value={value} onChange={event => onChange(event.target.value)}>
      <option value="">No assessment authorization</option>
      {value && !records.some(record => record.ref === value) ? <option value={value}>{value} (historical or unavailable)</option> : null}
      {records.map(record => <option key={record.ref} value={record.ref} disabled={!record.enabled}>{record.name}{record.enabled ? '' : ' (disabled)'}</option>)}
    </select></label>
    {!catalog.isLoading && !catalog.isError && records.length === 0 ? <p className="small">No saved authorizations. Create an explicitly scoped authorization in Settings before starting a cloud or web assessment.</p> : null}
    {catalog.isError ? <p role="alert">Saved authorizations could not be loaded. Existing selections are retained; the server must validate them before launch.</p> : null}
  </section>;
}

export function AssessmentEngagementReview({ review, historical = false }: { review: Review; historical?: boolean }) {
  return <section className="card stack" aria-label={historical ? 'Recorded rules of engagement' : 'Rules of engagement'}>
    <h2>{historical ? 'Recorded rules of engagement' : 'Rules of engagement'}</h2>
    <p><strong>Authorization:</strong> {review.authorizationRef} · Generation {review.authorizationGeneration}</p>
    {historical ? <p className="small">This is the scope recorded for this run, not a renewed authorization. Reruns remain subject to the original validity, budget, and current authorization status.</p> : <p className="small">Review the server-resolved scope below. Starting submits this exact proposal for server validation; this display is not itself permission to run jobs.</p>}
    {review.sections.map((section, index) => <section key={`${section.label}-${index}`}><h3>{section.label}</h3><ul>{section.items.map((item, i) => <li className="whitespace-pre-wrap break-words" key={i}>{item}</li>)}</ul></section>)}
    {review.limitations.length ? <div><h3>Limitations</h3><ul>{review.limitations.map((item, i) => <li key={i}>{item}</li>)}</ul></div> : null}
    <p className="small">Cancellation and revocation stop new work. Retained evidence remains available. Use the workflow's existing Cancel action to stop an active run.</p>
    <details><summary>Review scope and proposal identifiers</summary><p className="small break-all">Scope: {review.scopeDigest}</p><p className="small break-all">Proposal: {review.proposalDigest}</p><pre className="whitespace-pre-wrap break-words text-xs">{JSON.stringify(review.scope, null, 2)}</pre></details>
  </section>;
}
