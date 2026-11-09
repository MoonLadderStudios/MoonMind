import { useCallback, useState } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { Button } from '../Button';
import { SchemaCapabilityFields, schemaProperties } from '../SchemaCapabilityFields';
import { responseError } from '../../lib/api/client';
import { ASSESSMENT_AUTHORIZATIONS_KEY, createAssessmentAuthorization, readAssessmentAuthorizations, type AssessmentAuthorization } from '../../lib/assessmentEngagement';
import { fetchCatalog, fetchSettingAudit, SettingAuditHistory } from './GeneratedSettingsSection';
import { useSettingsDraftRegistration } from './SettingsDraftGuard';

const fieldClass = 'mt-1 w-full rounded-xl border border-slate-300 bg-white px-3 py-2 text-sm text-slate-900 dark:border-slate-700 dark:bg-slate-950 dark:text-white';
type Draft = { records: AssessmentAuthorization[]; scopeTexts: string[]; version: number };
function Field({ label, value, onChange, type = 'text' }: { label: string; value: string | number; onChange: (value: string) => void; type?: string }) {
  return <label className="block text-sm">{label}<input className={fieldClass} type={type} value={value} onChange={event => onChange(event.target.value)} /></label>;
}
function parseScope(text: string, name: string): Record<string, unknown> {
  let scope: Record<string, unknown>;
  try { scope = JSON.parse(text) as Record<string, unknown>; }
  catch { throw new Error(`${name}: enter valid JSON for the authorized scope.`); }
  if (!scope || typeof scope !== 'object' || Array.isArray(scope)) throw new Error(`${name}: authorized scope must be a JSON object.`);
  if ('engagement' in scope) throw new Error(`${name}: engagement metadata is assigned by the server, not authored in Settings.`);
  if (!Array.isArray(scope.targets) || !scope.targets.length || !Array.isArray(scope.profileRefs) || !scope.profileRefs.length || !Array.isArray(scope.actionClasses) || !scope.actionClasses.length || !scope.assessmentRef || !scope.evidenceIntent || !(Number(scope.maxJobs) > 0) || !(Number(scope.maxJobSeconds) > 0)) throw new Error(`${name}: specify targets, profileRefs, actionClasses, assessmentRef, evidenceIntent, maxJobs, and maxJobSeconds. The server validates the complete scope before saving.`);
  return scope;
}
function editableScope(text: string): Record<string, unknown> | null {
  try {
    const scope: unknown = JSON.parse(text);
    return scope && typeof scope === 'object' && !Array.isArray(scope) ? scope as Record<string, unknown> : null;
  } catch { return null; }
}

function AuthorizedScopeFields({ text, schema, disabled, onChange }: {
  text: string;
  schema?: Record<string, unknown> | null;
  disabled: boolean;
  onChange: (text: string) => void;
}) {
  const scope = editableScope(text);
  const properties = schemaProperties(schema ?? undefined);
  return <div className="space-y-3">
    {scope && Object.keys(properties).length ? <SchemaCapabilityFields
      fields={Object.entries(properties)} detail={{ inputSchema: schema! }} values={scope} errors={{}}
      disabled={disabled} repositoryOptions={[]} branchOptions={[]}
      onChange={(name, value) => {
        // Preserve unknown and advanced fields. The raw draft is the single
        // source of truth and the server remains the authority for admission.
        const next = { ...scope, [name]: value };
        if (value === undefined) delete next[name];
        onChange(JSON.stringify(next, null, 2));
      }} /> : !scope ? <p className="text-sm">The scope JSON is incomplete or invalid. Correct it below before using structured fields; your draft is retained.</p> : <p className="text-sm">Structured scope fields are unavailable. Existing scope JSON is preserved below.</p>}
    <details open={!scope ? true : undefined}>
      <summary>Advanced scope JSON</summary>
      <p className="text-xs">Structured edits preserve unrecognized fields. Review the full scope here; changing the text does not save or grant access.</p>
      <label className="block text-sm">Authorized scope JSON<textarea className={`${fieldClass} font-mono`} rows={12} value={text} disabled={disabled} onChange={event => onChange(event.target.value)} /></label>
    </details>
  </div>;
}

export function AssessmentAuthorizationsSettings({ authorizationRef }: { authorizationRef?: string } = {}) {
  const client = useQueryClient();
  const catalog = useQuery({ queryKey: ['settings-catalog'], queryFn: fetchCatalog });
  const descriptor = Object.values(catalog.data?.categories ?? {}).flat().find(item => item.key === ASSESSMENT_AUTHORIZATIONS_KEY);
  const [draft, setDraft] = useState<Draft | null>(null);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [auditOpen, setAuditOpen] = useState(false);
  const audit = useQuery({ queryKey: ['settings-audit', ASSESSMENT_AUTHORIZATIONS_KEY], queryFn: () => fetchSettingAudit(ASSESSMENT_AUTHORIZATIONS_KEY), enabled: auditOpen });
  const discard = useCallback(() => { setDraft(null); setError(''); }, []);
  useSettingsDraftRegistration('assessment-authorizations', draft !== null, discard);
  if (catalog.isLoading) return <p role="status">Loading assessment authorizations…</p>;
  if (catalog.isError) return <p role="alert">Assessment authorizations could not be loaded.</p>;
  if (!descriptor) return null;
  const records = draft?.records ?? readAssessmentAuthorizations(descriptor.effective_value);
  const current: Draft = draft ?? { records, scopeTexts: records.map(record => JSON.stringify(record.scope, null, 2)), version: descriptor.value_version };
  const update = (next: Draft) => { setDraft(next); setNotice(''); setError(''); };
  const updateRecord = (index: number, patch: Partial<AssessmentAuthorization>) => update({ ...current, records: records.map((record, i) => i === index ? { ...record, ...patch } : record) });
  async function save() {
    if (!draft || saving || !descriptor || descriptor.read_only) return;
    setError('');
    let authorizations: AssessmentAuthorization[];
    try {
      authorizations = draft.records.map((record, i) => {
        if (!record.ref.trim() || !record.name.trim() || !record.authoritySource.trim()) throw new Error('Each authorization needs a reference, name, and authority source.');
        const from = Date.parse(record.validFrom); const until = Date.parse(record.validUntil);
        if (![record.validFrom, record.validUntil].every(value => /T.*(?:Z|[+-]\d{2}:\d{2})$/i.test(value)) || !Number.isFinite(from) || !Number.isFinite(until) || until <= from) throw new Error(`${record.name}: specify a validity start and later end, including timezones.`);
        if (!Number.isInteger(record.maxTotalJobSeconds) || record.maxTotalJobSeconds <= 0) throw new Error(`${record.name}: total job seconds must be a positive integer.`);
        if (record.recurring && Object.entries(record.recurring).some(([key, value]) => !Number.isInteger(value) || value < (key === 'minIntervalSeconds' || key === 'maxRunSeconds' ? 60 : 1) || (key === 'maxRunSeconds' && value > 86400))) throw new Error(`${record.name}: recurring limits must be positive integers; interval and run duration must be at least 60 seconds and run duration at most 86400 seconds.`);
        return { ...record, scope: parseScope(draft.scopeTexts[i] ?? '', record.name) };
      });
      if (new TextEncoder().encode(JSON.stringify({ authorizations })).length > 16384) throw new Error('Assessment authorizations exceed the 16 KiB setting limit. Reduce the saved records.');
    } catch (cause) { setError(cause instanceof Error ? cause.message : 'Invalid authorization.'); return; }
    setSaving(true);
    try {
      const response = await fetch('/api/v1/settings', { method: 'PATCH', headers: { Accept: 'application/json', 'Content-Type': 'application/json' }, body: JSON.stringify({ changes: { [ASSESSMENT_AUTHORIZATIONS_KEY]: { authorizations } }, expected_versions: { [ASSESSMENT_AUTHORIZATIONS_KEY]: draft.version }, reason: 'Updated assessment authorizations from Settings.' }) });
      if (!response.ok) throw await responseError(response, 'Assessment authorization save failed.');
      await client.invalidateQueries({ queryKey: ['settings-catalog'] });
      await client.invalidateQueries({ queryKey: ['settings-audit', ASSESSMENT_AUTHORIZATIONS_KEY] });
      setDraft(null); setNotice('Assessment authorizations saved.');
    } catch (cause) { setError(`${cause instanceof Error ? cause.message : 'Save failed.'} Your draft is retained. Review concurrent changes before retrying.`); }
    finally { setSaving(false); }
  }
  return <section id="assessment-authorizations" className="space-y-4 rounded-3xl border border-mm-border/80 p-6" aria-label="Reusable assessment authorizations">
    <h3 className="text-lg font-semibold">Reusable assessment authorizations</h3>
    <p className="text-sm">Record the authority, exact allowed scope, validity, and budgets for self-hosted or client engagements. These records are separate from non-authoritative assessment context. Save reference IDs only, never passwords, keys, or tokens.</p>
    <p className="text-sm">Disabling, removing, or resetting an authorization blocks new work under that grant. Retained evidence remains available. Use the workflow's Cancel action to stop an active run.</p>
    {authorizationRef && !records.some(record => record.ref === authorizationRef) ? <p role="alert">This authorization was removed or is unavailable. Select and review a current authorization before starting more work.</p> : null}
    {descriptor.read_only ? <p>{descriptor.read_only_reason ?? 'This setting is read-only.'}</p> : null}
    {error ? <p role="alert">{error}</p> : null}{notice ? <p role="status">{notice}</p> : null}
    <fieldset disabled={saving || descriptor.read_only} className="space-y-4">
      {records.map((record, index) => authorizationRef && record.ref !== authorizationRef ? null : <fieldset key={index} className="space-y-3 rounded-xl border border-mm-border p-4"><legend className="px-2 font-semibold">{record.name || `Authorization ${index + 1}`}</legend>
        <div className="grid gap-3 sm:grid-cols-2">
          <Field label="Authorization reference" value={record.ref} onChange={ref => updateRecord(index, { ref })} />
          <Field label="Authorization name" value={record.name} onChange={name => updateRecord(index, { name })} />
          <label className="block text-sm">Authority kind<select className={fieldClass} value={record.authorityKind} onChange={event => updateRecord(index, { authorityKind: event.target.value as AssessmentAuthorization['authorityKind'] })}><option value="self_hosted">Self-hosted system</option><option value="client_engagement">Client engagement</option></select></label>
          <Field label="Authority source" value={record.authoritySource} onChange={authoritySource => updateRecord(index, { authoritySource })} />
          <Field label="Valid from (ISO time with timezone)" value={record.validFrom} onChange={validFrom => updateRecord(index, { validFrom })} />
          <Field label="Valid until (ISO time with timezone)" value={record.validUntil} onChange={validUntil => updateRecord(index, { validUntil })} />
          <Field label="Total job seconds" type="number" value={record.maxTotalJobSeconds} onChange={value => updateRecord(index, { maxTotalJobSeconds: Number(value) })} />
        </div>
        <label className="flex gap-2"><input type="checkbox" checked={Boolean(record.recurring)} onChange={event => updateRecord(index, { recurring: event.target.checked ? { minIntervalSeconds: 86400, maxOccurrences: 1, maxJobsPerOccurrence: 1, maxJobSecondsPerOccurrence: 60, maxRunSeconds: 3600 } : undefined })} />Bound recurring assessments</label>
        {record.recurring ? <div className="grid gap-3 sm:grid-cols-2">
          {([['minIntervalSeconds', 'Minimum interval (seconds)'], ['maxOccurrences', 'Maximum occurrences'], ['maxJobsPerOccurrence', 'Jobs per occurrence'], ['maxJobSecondsPerOccurrence', 'Job seconds per occurrence'], ['maxRunSeconds', 'Run authority lifetime (seconds)']] as const).map(([key, label]) => <Field key={key} label={label} type="number" value={record.recurring![key]} onChange={value => updateRecord(index, { recurring: { ...record.recurring!, [key]: Number(value) } })} />)}
        </div> : null}
        <p className="text-xs">Renew by editing the validity window and limits, then saving. Material changes require a fresh rules-of-engagement review before future runs. Changing a schedule or renewing authority does not refund previously reserved budgets. Equivalent authorized occurrences do not need another confirmation.</p>
        <label className="flex gap-2"><input type="checkbox" checked={record.enabled} onChange={event => updateRecord(index, { enabled: event.target.checked })} />Authorization enabled</label>
        <p className="text-xs">Server generation: {record.generation ?? 'Assigned on save'}. Enabling this record alone does not launch work.</p>
        <AuthorizedScopeFields text={current.scopeTexts[index] ?? ''} schema={descriptor.input_schema ?? null} disabled={saving || descriptor.read_only}
          onChange={value => update({ ...current, scopeTexts: current.scopeTexts.map((text, i) => i === index ? value : text) })} />
        <p className="text-xs">Scope includes assessmentRef, profileRefs, actionClasses, targets, exclusions, configurationExceptions, networkRefs, maxJobs, maxJobSeconds, maxCpuMillis, maxMemoryMiB, maxRequestsPerSecond, evidenceIntent, and optional grants. CPU and memory ceilings must be positive; web and TLS assessments also need an explicit request rate of 1–100 per second. Credential grants use reference IDs, expiry, and purpose. The server validates targets and resource limits; the launch review presents the complete rules in readable sections.</p>
        <Button type="button" variant="ghost" disabled={!record.enabled} onClick={() => updateRecord(index, { enabled: false })}>Revoke authorization</Button>
        <Button type="button" variant="ghost" onClick={() => update({ ...current, records: records.filter((_, i) => i !== index), scopeTexts: current.scopeTexts.filter((_, i) => i !== index) })}>Remove authorization</Button>
      </fieldset>)}
      {!authorizationRef ? <Button type="button" variant="ghost" onClick={() => { const record = createAssessmentAuthorization(); update({ ...current, records: [...records, record], scopeTexts: [...current.scopeTexts, JSON.stringify(record.scope, null, 2)] }); }}>Add authorization</Button> : null}
      <div className="flex gap-3"><Button type="button" disabled={!draft} onClick={() => void save()}>Save assessment authorizations</Button><Button type="button" variant="ghost" disabled={!draft} onClick={discard}>Discard authorization changes</Button></div>
    </fieldset>
    <Button type="button" variant="ghost" onClick={() => setAuditOpen(!auditOpen)} aria-expanded={auditOpen}>View authorization audit</Button>
    {auditOpen ? <SettingAuditHistory descriptor={descriptor} query={audit} /> : null}
  </section>;
}
