import { useCallback, useState } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { Button } from '../Button';
import { responseError } from '../../lib/api/client';
import { ASSESSMENT_PROJECTS_KEY, createAssessmentEnvironment, createAssessmentProject, readAssessmentProjects, type AssessmentEnvironment, type AssessmentProject, type AssessmentProjects } from '../../lib/assessmentProjects';
import { fetchCatalog, fetchSettingAudit, SettingAuditHistory } from './GeneratedSettingsSection';
import { useSettingsDraftRegistration } from './SettingsDraftGuard';

const fieldClass = 'mt-1 w-full rounded-xl border border-slate-300 bg-white px-3 py-2 text-sm text-slate-900 dark:border-slate-700 dark:bg-slate-950 dark:text-white';
function Field({ label, value, onChange, type = 'text' }: { label: string; value: string | number; onChange: (value: string) => void; type?: string }) {
  return <label className="block text-sm">{label}<input className={fieldClass} type={type} value={value} onChange={event => onChange(event.target.value)} /></label>;
}
function ListField({ label, values, onChange }: { label: string; values: string[]; onChange: (value: string[]) => void }) {
  return <label className="block text-sm">{label}<textarea className={fieldClass} rows={2} value={values.join('\n')} onChange={event => onChange(event.target.value.split('\n'))} onBlur={event => onChange(event.target.value.split('\n').map(value => value.trim()).filter(Boolean))} /></label>;
}
export function AssessmentEnvironmentFields({ environment: env, onChange, disabled = false, allowIdentityEdit = true }: { environment: AssessmentEnvironment; onChange: (value: AssessmentEnvironment) => void; disabled?: boolean; allowIdentityEdit?: boolean }) {
  const update = (patch: Partial<AssessmentEnvironment>) => onChange({ ...env, ...patch });
  return <fieldset disabled={disabled} className="space-y-3">
    <div className="grid gap-3 sm:grid-cols-2">
      {allowIdentityEdit ? <Field label="Environment ID" value={env.id} onChange={id => update({ id })} /> : null}
      <Field label="Environment name" value={env.name} onChange={name => update({ name })} />
    </div>
    <p className="text-xs text-slate-500">Enter one item per line in list fields. Save reference IDs only, never passwords, keys, or tokens.</p>
    <div className="grid gap-3 sm:grid-cols-2">
      <ListField label="Assets" values={env.assets} onChange={assets => update({ assets })} />
      <ListField label="Exclusions" values={env.exclusions} onChange={exclusions => update({ exclusions })} />
      <ListField label="Retained artifact references" values={env.artifactRefs} onChange={artifactRefs => update({ artifactRefs })} />
      <ListField label="Candidate web origins" values={env.originCandidates} onChange={originCandidates => update({ originCandidates })} />
      <ListField label="Connection references" values={env.connectionRefs} onChange={connectionRefs => update({ connectionRefs })} />
      <ListField label="Credential references (db://…)" values={env.credentialRefs} onChange={credentialRefs => update({ credentialRefs })} />
      <ListField label="Jurisdictions" values={env.jurisdictions} onChange={jurisdictions => update({ jurisdictions })} />
      <label className="block text-sm">Data classification<select className={fieldClass} value={env.dataClassification} onChange={event => update({ dataClassification: event.target.value as AssessmentEnvironment['dataClassification'] })}>{['public', 'internal', 'confidential', 'restricted'].map(value => <option key={value}>{value}</option>)}</select></label>
    </div>
    <fieldset className="space-y-2"><legend className="font-medium">Evidence processing</legend>
      <label className="block text-sm">Evidence model delivery<select className={fieldClass} value={env.evidenceProcessing?.modelDelivery ?? 'allowed'} onChange={event => update({ evidenceProcessing: { modelDelivery: event.target.value as 'allowed' | 'disabled' | 'local_only', allowedProfileRefs: env.evidenceProcessing?.allowedProfileRefs ?? [] } })}>
        <option value="allowed">Allow an admitted model recipient</option>
        <option value="disabled">Do not send evidence to models</option>
        <option value="local_only">Local models only</option>
      </select></label>
      <ListField label="Permitted Provider Profile references (optional)" values={env.evidenceProcessing?.allowedProfileRefs ?? []} onChange={allowedProfileRefs => update({ evidenceProcessing: { modelDelivery: env.evidenceProcessing?.modelDelivery ?? 'allowed', allowedProfileRefs } })} />
      <p className="text-xs text-slate-500">A blank recipient list adds no profile restriction. Delivery still requires normal admission and authorized evidence. Local-only model delivery is currently unavailable and is blocked rather than sent to an external model. These limits do not authorize publication.</p>
    </fieldset>
    <fieldset className="space-y-2"><legend className="font-medium">Repositories</legend>
      {env.repositories.map((repo, i) => <div key={i} className="grid gap-2 sm:grid-cols-3">
        <Field label="Repository" value={repo.repository} onChange={repository => update({ repositories: env.repositories.map((item, index) => index === i ? { ...item, repository } : item) })} />
        <Field label="Repository connection reference" value={repo.connectionRef ?? ''} onChange={connectionRef => update({ repositories: env.repositories.map((item, index) => index === i ? { ...item, connectionRef: connectionRef || null } : item) })} />
        <Button variant="ghost" onClick={() => update({ repositories: env.repositories.filter((_, index) => index !== i) })}>Remove repository</Button>
      </div>)}
      <Button variant="ghost" onClick={() => update({ repositories: [...env.repositories, { repository: '' }] })}>Add repository</Button>
    </fieldset>
    <fieldset className="space-y-2"><legend className="font-medium">Candidate AWS accounts</legend>
      {env.cloudAccountCandidates.map((account, i) => <div key={i} className="grid gap-2 sm:grid-cols-2">
        <Field label="AWS account ID" value={account.account} onChange={value => update({ cloudAccountCandidates: env.cloudAccountCandidates.map((item, index) => index === i ? { ...item, account: value } : item) })} />
        <ListField label="AWS regions" values={account.regions} onChange={regions => update({ cloudAccountCandidates: env.cloudAccountCandidates.map((item, index) => index === i ? { ...item, regions } : item) })} />
        <Field label="AWS credential reference" value={account.credentialRef ?? ''} onChange={credentialRef => update({ cloudAccountCandidates: env.cloudAccountCandidates.map((item, index) => index === i ? { ...item, credentialRef: credentialRef || null } : item) })} />
        <Button variant="ghost" onClick={() => update({ cloudAccountCandidates: env.cloudAccountCandidates.filter((_, index) => index !== i) })}>Remove AWS account</Button>
      </div>)}
      <Button variant="ghost" onClick={() => update({ cloudAccountCandidates: [...env.cloudAccountCandidates, { provider: 'aws', account: '', regions: [] }] })}>Add AWS account</Button>
    </fieldset>
    <fieldset className="space-y-2"><legend className="font-medium">Frameworks</legend>
      {env.frameworks.map((framework, i) => <div key={i} className="grid gap-2 sm:grid-cols-3">
        <Field label="Framework ID" value={framework.id} onChange={id => update({ frameworks: env.frameworks.map((item, index) => index === i ? { ...item, id } : item) })} />
        <Field label="Framework edition" value={framework.edition} onChange={edition => update({ frameworks: env.frameworks.map((item, index) => index === i ? { ...item, edition } : item) })} />
        <Button variant="ghost" onClick={() => update({ frameworks: env.frameworks.filter((_, index) => index !== i) })}>Remove framework</Button>
      </div>)}
      <Button variant="ghost" onClick={() => update({ frameworks: [...env.frameworks, { id: '', edition: '' }] })}>Add framework</Button>
    </fieldset>
    <div className="grid gap-3 sm:grid-cols-2">
      <Field label="Evidence period start (YYYY-MM-DD)" type="date" value={env.period?.start ?? ''} onChange={start => update({ period: { start, end: env.period?.end ?? '' } })} />
      <Field label="Evidence period end (YYYY-MM-DD)" type="date" value={env.period?.end ?? ''} onChange={end => update({ period: { start: env.period?.start ?? '', end } })} />
      <Button variant="ghost" onClick={() => update({ period: null })}>Clear evidence period</Button>
      <Field label="Retention days (optional)" type="number" value={env.retentionDays ?? ''} onChange={value => update({ retentionDays: value === '' ? null : Number(value) })} />
    </div>
    <fieldset><legend className="font-medium">Comparison budget (seconds)</legend><p className="text-xs text-slate-500">Use 0 &lt; stall ≤ base ≤ maximum ≤ 300 seconds.</p><div className="grid gap-3 sm:grid-cols-3">
      <Field label="Base seconds" type="number" value={env.budget.baseSeconds} onChange={value => update({ budget: { ...env.budget, baseSeconds: Number(value) } })} />
      <Field label="Maximum seconds" type="number" value={env.budget.maxSeconds} onChange={value => update({ budget: { ...env.budget, maxSeconds: Number(value) } })} />
      <Field label="Progress stall seconds" type="number" value={env.budget.progressStallSeconds} onChange={value => update({ budget: { ...env.budget, progressStallSeconds: Number(value) } })} />
    </div></fieldset>
  </fieldset>;
}

export function AssessmentProjectsSettings() {
  const client = useQueryClient();
  const catalog = useQuery({ queryKey: ['settings-catalog'], queryFn: fetchCatalog });
  const descriptor = Object.values(catalog.data?.categories ?? {}).flat().find(item => item.key === ASSESSMENT_PROJECTS_KEY);
  const [draft, setDraft] = useState<{ value: AssessmentProjects; version: number } | null>(null);
  const [saving, setSaving] = useState(false);
  const [notice, setNotice] = useState('');
  const [error, setError] = useState('');
  const [auditOpen, setAuditOpen] = useState(false);
  const audit = useQuery({ queryKey: ['settings-audit', ASSESSMENT_PROJECTS_KEY], queryFn: () => fetchSettingAudit(ASSESSMENT_PROJECTS_KEY), enabled: auditOpen });
  const discard = useCallback(() => { setDraft(null); setError(''); }, []);
  useSettingsDraftRegistration('assessment-projects', draft !== null, discard);
  if (catalog.isLoading) return <p role="status">Loading assessment context…</p>;
  if (catalog.isError) return <p role="alert">Assessment context could not be loaded.</p>;
  if (!descriptor) return null;
  const value = draft?.value ?? readAssessmentProjects(descriptor.effective_value);
  const update = (next: AssessmentProjects) => { setDraft({ value: next, version: draft?.version ?? descriptor.value_version }); setNotice(''); };
  const updateProject = (index: number, project: AssessmentProject) => update({ projects: value.projects.map((item, i) => i === index ? project : item) });
  const save = async () => {
    if (!draft || saving || descriptor.read_only) return;
    if (new TextEncoder().encode(JSON.stringify(draft.value)).length > 16384) { setError('Assessment context exceeds the 16 KiB setting limit. Reduce the saved context.'); return; }
    setSaving(true); setError('');
    try {
      const response = await fetch('/api/v1/settings', { method: 'PATCH', headers: { Accept: 'application/json', 'Content-Type': 'application/json' }, body: JSON.stringify({ changes: { [ASSESSMENT_PROJECTS_KEY]: draft.value }, expected_versions: { [ASSESSMENT_PROJECTS_KEY]: draft.version }, reason: 'Updated reusable assessment context from Settings.' }) });
      if (!response.ok) throw await responseError(response, `Assessment context save failed (${response.status}).`);
      await client.invalidateQueries({ queryKey: ['settings-catalog'] });
      await client.invalidateQueries({ queryKey: ['settings-audit', ASSESSMENT_PROJECTS_KEY] });
      setDraft(null); setNotice('Assessment context saved.');
    } catch (cause) { setError(`${cause instanceof Error ? cause.message : 'Save failed.'} Your draft is retained. For a concurrent change, review the saved context before retrying.`); }
    finally { setSaving(false); }
  };
  return <section aria-label="Reusable assessment context" className="space-y-4 rounded-3xl border border-mm-border/80 p-6">
    <h3 className="text-lg font-semibold">Reusable assessment context</h3>
    <p className="text-sm">Save systems and environments for future assessments. Candidate origins, accounts, and reference IDs are context only: they grant no credentials, collection, or network access.</p>
    {descriptor.read_only ? <p>{descriptor.read_only_reason ?? 'This setting is read-only.'}</p> : null}
    {error ? <p role="alert">{error}</p> : null}{notice ? <p role="status">{notice}</p> : null}
    <fieldset disabled={descriptor.read_only || saving} className="space-y-4">
      {value.projects.map((project, p) => <fieldset key={p} className="space-y-4 rounded-xl border border-mm-border p-4"><legend className="px-2 font-semibold">System {p + 1}: {project.name || 'New system'}</legend>
        <div className="grid gap-3 sm:grid-cols-2">
          <Field label="System ID" value={project.id} onChange={id => updateProject(p, { ...project, id })} />
          <Field label="System name" value={project.name} onChange={name => updateProject(p, { ...project, name })} />
          <Field label="Purpose" value={project.purpose} onChange={purpose => updateProject(p, { ...project, purpose })} />
          <Field label="Boundaries" value={project.boundaries} onChange={boundaries => updateProject(p, { ...project, boundaries })} />
        </div>
        <Button variant="ghost" onClick={() => update({ projects: [...value.projects, { ...structuredClone(project), id: createAssessmentProject().id, name: `${project.name} copy` }] })}>Copy system</Button>
        <Button variant="ghost" onClick={() => update({ projects: value.projects.filter((_, i) => i !== p) })}>Remove system</Button>
        {project.environments.map((environment, e) => <fieldset key={e} className="space-y-3 rounded-xl border border-mm-border p-4"><legend className="px-2 font-medium">Environment {e + 1}</legend>
          <AssessmentEnvironmentFields environment={environment} onChange={next => updateProject(p, { ...project, environments: project.environments.map((item, i) => i === e ? next : item) })} />
          <Button variant="ghost" onClick={() => updateProject(p, { ...project, environments: [...project.environments, { ...structuredClone(environment), id: createAssessmentEnvironment().id, name: `${environment.name} copy` }] })}>Copy environment</Button>
          <Button variant="ghost" disabled={project.environments.length === 1} onClick={() => updateProject(p, { ...project, environments: project.environments.filter((_, i) => i !== e) })}>Remove environment</Button>
        </fieldset>)}
        <Button variant="ghost" onClick={() => updateProject(p, { ...project, environments: [...project.environments, createAssessmentEnvironment()] })}>Add environment</Button>
      </fieldset>)}
      <Button variant="ghost" onClick={() => update({ projects: [...value.projects, createAssessmentProject()] })}>Add system</Button>
      <div className="flex gap-3"><Button disabled={!draft} onClick={() => void save()}>Save assessment context</Button><Button variant="ghost" disabled={!draft} onClick={discard}>Discard assessment changes</Button></div>
    </fieldset>
    <Button variant="ghost" onClick={() => setAuditOpen(!auditOpen)} aria-expanded={auditOpen}>View assessment audit</Button>
    {auditOpen ? <SettingAuditHistory descriptor={descriptor} query={audit} /> : null}
  </section>;
}
