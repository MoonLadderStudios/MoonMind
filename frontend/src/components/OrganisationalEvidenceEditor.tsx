import { useRef, useState } from 'react';
import { Button } from './Button';

type ObjectValue = Record<string, unknown>;
const object = (value: unknown): ObjectValue => value && typeof value === 'object' && !Array.isArray(value) ? value as ObjectValue : {};

/** Edits the existing Tool contract. Uploads, access, retention and execution keep their owners. */
export function OrganisationalEvidenceEditor({ values, onChange, onImport, disabled = false }: { values: ObjectValue; onChange: (name: string, value: unknown) => void; onImport?: (file: File) => Promise<string>; disabled?: boolean }) {
  const control = object(values.controlAssessment ?? values.control_assessment);
  const pack = object(values.frameworkPack ?? values.framework_pack);
  const requirements = Array.isArray(control.requirements) ? control.requirements.map(object) : [];
  const choices = object(pack.requirementChoices ?? pack.requirement_choices);
  const rows = requirements.length ? requirements.map((row, index) => ({ key: String(index), label: String(row.id), row })) : Object.entries(choices).map(([key, row]) => ({ key, label: key, row: object(row) }));
  const [selection, setSelection] = useState('');
  const [file, setFile] = useState<File | null>(null);
  const [importing, setImporting] = useState(false);
  const importingRef = useRef(false);
  const [imported, setImported] = useState('');
  const [error, setError] = useState('');
  const importEvidence = async () => {
    if (!file || !onImport || importingRef.current) return;
    if (!file.size || file.size > 4 * 1024 * 1024) { setError('Supply a non-empty file no larger than 4 MiB.'); return; }
    importingRef.current = true; setImporting(true); setError('');
    try { setImported(await onImport(file)); }
    catch { setError('Evidence import did not complete. The selected file is preserved; retry or use an existing authorized artifact reference.'); }
    finally { importingRef.current = false; setImporting(false); }
  };
  const selected = rows.find(row => row.key === selection);
  const persisted = selected?.row.organisationalEvidence ?? selected?.row.organisational_evidence ?? [];
  const conflictingAliases = (Object.hasOwn(values, 'controlAssessment') && Object.hasOwn(values, 'control_assessment')) || (Object.hasOwn(values, 'frameworkPack') && Object.hasOwn(values, 'framework_pack')) || (Object.hasOwn(pack, 'requirementChoices') && Object.hasOwn(pack, 'requirement_choices')) || (selected && Object.hasOwn(selected.row, 'organisationalEvidence') && Object.hasOwn(selected.row, 'organisational_evidence'));
  const addRequest = () => {
    if (disabled || conflictingAliases || !selected || !Array.isArray(persisted) || persisted.length >= 32) return;
    const ids = new Set(persisted.map(item => object(item).id));
    let index = persisted.length + 1;
    while (ids.has(`evidence-${index}`)) index += 1;
    const row: ObjectValue = { ...selected.row, organisationalEvidence: [...persisted, { id: `evidence-${index}`, category: 'policy', purpose: 'design', description: 'Supply the current policy and adoption record', owner: 'Unassigned', revisions: [] }] };
    delete row.organisational_evidence;
    if (requirements.length) onChange(Object.hasOwn(values, 'controlAssessment') ? 'controlAssessment' : 'control_assessment', { ...control, requirements: requirements.map((item, index) => String(index) === selection ? row : item) });
    else {
      const next: ObjectValue = { ...pack, requirementChoices: { ...choices, [selection]: row } };
      delete next.requirement_choices;
      onChange(Object.hasOwn(values, 'frameworkPack') ? 'frameworkPack' : 'framework_pack', next);
    }
  };
  return <details className="stack">
    <summary>Organisational evidence requests and review</summary>
    <p className="small">Track policies, risk registers, training, audits and operational records alongside technical checks. Import documents below through the existing artifact upload service, then copy their art_ references into revisions. Do not paste document contents or secrets here.</p>
    <p className="small">Select a declared requirement below. The same artifact may be linked to several requirements, each with its own purpose, period, population and review. Missing requests remain report gaps. Assertions and generated drafts cannot establish adoption or real occurrence.</p>
    {disabled || conflictingAliases ? <p role="alert">Correct the Tool input JSON before editing evidence. Existing inputs are preserved.</p> : null}
    <fieldset disabled={disabled || Boolean(conflictingAliases)} className="stack">
    {onImport ? <fieldset disabled={importing}>
      <label>Import organisational evidence<input type="file" onChange={event => { setFile(event.target.files?.[0] ?? null); setImported(''); setError(''); }} /></label>
      <Button type="button" disabled={!file || importing} onClick={() => void importEvidence()}>{importing ? 'Importing evidence…' : 'Upload evidence'}</Button>
      {imported ? <p role="status">Imported artifact reference: <code>{imported}</code>. Copy it into the relevant revision. Import alone does not establish sufficiency.</p> : null}
      {error ? <p role="alert">{error}</p> : null}
    </fieldset> : null}
    {!rows.length ? <p role="status">First declare controlAssessment requirements or frameworkPack requirementChoices in the Tool inputs. Then return here to track evidence.</p> : <>
      <label>Evidence requirement<select value={selection} onChange={event => setSelection(event.target.value)}><option value="">Select requirement</option>{rows.map(row => <option value={row.key} key={row.key}>{row.label}</option>)}</select></label>
      {selected ? <>
        <p className="small">{Array.isArray(persisted) ? persisted.length : 0} requests linked. Add a missing request, then edit its metadata and revisions in the existing controlAssessment or frameworkPack input below (or Edit JSON). Changes use the same workflow draft and validation as other Tool inputs.</p>
        <Button type="button" variant="secondary" disabled={!Array.isArray(persisted) || persisted.length >= 32} onClick={addRequest}>Add evidence request</Button>
        <p className="small">Supply a revision with artifactRef, kind (document, operational_record, management_assertion or generated_draft), source, collectedAt, expiresAt, period (start/end), population (asset list) and sensitivity. A review records outcome (accepted or insufficient), purpose (design, adoption or occurrence), reviewer, reason, reviewedAt, period and population. Acceptance applies only to that purpose and covered period. All dates use ISO format.</p>
        <p className="small">To replace evidence, append a revision with a new artifact reference. Keep earlier revisions and reviews: they are superseded, retained history, not current evidence. Re-run the assessment after supplying evidence or restoring access; failed reads stay explicit and retries do not repeat completed scans.</p>
      </> : null}
    </>}
    </fieldset>
  </details>;
}
