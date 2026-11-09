/** Recorded admission is evidence of permission, never proof that targets passed. */
export function AssessmentOccurrenceEvidence({ value }: { value: unknown }) {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return null;
  const record = value as Record<string, unknown>;
  if (typeof record.authorizationRef !== 'string' || typeof record.authorizationGeneration !== 'string') return null;
  const text = (key: string) => typeof record[key] === 'string' || typeof record[key] === 'number' ? String(record[key]) : 'Unverified';
  const refs = Array.isArray(record.evidenceRefs) ? record.evidenceRefs.filter((ref): ref is string => typeof ref === 'string') : [];
  const limitations = Array.isArray(record.limitations) ? record.limitations.filter((item): item is string => typeof item === 'string') : [];
  return <section className="card stack" aria-label="Recurring assessment occurrence evidence">
    <h2>Recurring assessment occurrence</h2>
    <p>Occurrence {text('ordinal')}: {record.authorizationRef} · Generation {record.authorizationGeneration}</p>
    <p>Authority admitted {text('admittedAt')}; expires {text('expiresAt')}.</p>
    <p className="small">Admission is not an observation. Retained assessment reports own actual tool, pack, feed, coverage and comparison evidence. An expired permission or missing observation is a gap, not proof of remediation.</p>
    <dl><dt>Recorded coverage</dt><dd>{text('coverage')}</dd><dt>Recorded comparison disposition</dt><dd>{text('comparisonDisposition')}</dd><dt>Admitted scope digest</dt><dd className="break-all">{text('scopeDigest')}</dd></dl>
    {refs.length ? <div><h3>Retained evidence references</h3><ul>{refs.map((ref, index) => <li className="break-all" key={index}>{ref}</li>)}</ul></div> : <p>No observation evidence recorded here. Review the run's assessment reports and gaps before drawing conclusions.</p>}
    {limitations.length ? <ul>{limitations.map((item, index) => <li key={index}>{item}</li>)}</ul> : null}
  </section>;
}
