import { useEffect, useRef, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { Button } from '../Button';
import { AssessmentEnvironmentFields } from '../settings/AssessmentProjectsSettings';
import { fetchCatalog } from '../settings/GeneratedSettingsSection';
import {
  ASSESSMENT_PROJECTS_KEY, readAssessmentProjects, resolveAssessmentProject,
  type AssessmentEnvironment, type AssessmentProjectSnapshot, type AssessmentReferenceGap,
} from '../../lib/assessmentProjects';

type Selection = { projectId: string; environmentId: string; version: number; configuration: AssessmentEnvironment };
const PENDING = 'Load the selected assessment context before starting the workflow.';
export function AssessmentProjectPicker({ value, onChange, onStatusChange }: {
  value: AssessmentProjectSnapshot | null;
  onChange: (value: AssessmentProjectSnapshot | null) => void;
  onStatusChange: (problem: string | null) => void;
}) {
  const catalog = useQuery({ queryKey: ['settings-catalog'], queryFn: fetchCatalog });
  const descriptor = Object.values(catalog.data?.categories ?? {}).flat().find(item => item.key === ASSESSMENT_PROJECTS_KEY);
  const projects = readAssessmentProjects(descriptor?.effective_value).projects;
  const [projectId, setProjectId] = useState(value?.projectId ?? '');
  const [environmentId, setEnvironmentId] = useState(value?.environmentId ?? '');
  const [selection, setSelection] = useState<Selection | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [gaps, setGaps] = useState<AssessmentReferenceGap[]>([]);
  const request = useRef(0);
  const selectedProject = projects.find(item => item.id === projectId);
  useEffect(() => {
    onStatusChange(null);
    return () => { request.current += 1; };
  }, [onStatusChange]);
  // Async reconstruction may supply a historical snapshot after first render.
  // Catalog refreshes are deliberately absent: they cannot alter this draft.
  useEffect(() => {
    if (value) { setProjectId(value.projectId); setEnvironmentId(value.environmentId); }
  }, [value]);
  function invalidate() {
    request.current += 1; setLoading(false); setError(''); setGaps([]);
    onStatusChange(PENDING);
  }
  function chooseProject(id: string) {
    invalidate(); setProjectId(id); setEnvironmentId(''); setSelection(null);
    // Clearing a selection is an explicit removal, never a quiet fallback.
    if (!id) { onChange(null); onStatusChange(null); }
  }
  function chooseEnvironment(id: string) {
    invalidate(); setEnvironmentId(id);
    const environment = selectedProject?.environments.find(item => item.id === id);
    setSelection(environment && descriptor ? {
      projectId, environmentId: id, version: descriptor.value_version,
      configuration: structuredClone(environment),
    } : null);
  }
  async function load() {
    if (!selection || loading) return;
    const serial = ++request.current;
    setLoading(true); setError(''); onStatusChange('Assessment context is loading.');
    try {
      const result = await resolveAssessmentProject({
        projectId: selection.projectId, environmentId: selection.environmentId,
        expectedVersion: selection.version, overrides: selection.configuration,
      });
      if (request.current !== serial) return;
      setGaps(result.gaps);
      onChange(result.snapshot);
      onStatusChange(result.gaps.length ? 'Resolve the assessment reference gaps before starting the workflow.' : null);
      setSelection(null);
    } catch (cause) {
      if (request.current !== serial) return;
      const message = cause instanceof Error ? cause.message : 'Assessment context could not be loaded.';
      setError(message); onStatusChange(message);
    } finally { if (request.current === serial) setLoading(false); }
  }
  function remove() {
    request.current += 1; setLoading(false); setError(''); setGaps([]);
    setProjectId(''); setEnvironmentId(''); setSelection(null); onChange(null); onStatusChange(null);
  }
  return <section className="card stack" aria-label="Assessment context">
    <div><h2>Assessment context</h2><p className="small">Reuse a saved system and environment, or continue without one. Saved context grants no credentials, permissions, or network access. <a href="/settings/instance">Manage saved context in Settings</a>.</p></div>
    {value ? <div className="stack" aria-label="Frozen assessment context">
      <p><strong>Frozen assessment context:</strong> {value.projectName} / {value.environmentName} (version {value.sourceVersion})</p>
      <p className="small">{value.purpose} · Boundaries: {value.boundaries}</p>
      <p className="small">Assets: {value.configuration.assets.join(', ') || 'None'} · Exclusions: {value.configuration.exclusions.join(', ') || 'None'}</p>
      <p className="small">Budget: {value.configuration.budget.baseSeconds}/{value.configuration.budget.maxSeconds} seconds, stall {value.configuration.budget.progressStallSeconds} seconds. Retained evidence: {value.configuration.artifactRefs.join(', ') || 'None'}.</p>
      <p className="small">Changes in Settings do not change this draft. Review tool inputs; explicit input values take priority.</p>
      <details><summary>Review all frozen context</summary><pre className="whitespace-pre-wrap break-words text-xs">{JSON.stringify(value, null, 2)}</pre></details>
    </div> : null}
    {catalog.isLoading ? <p role="status">Loading saved assessment choices…</p> : null}
    {catalog.isError ? <p role="alert">Saved assessment choices could not be loaded. {value ? 'Your frozen context is retained.' : 'You can still continue without saved context.'}</p> : null}
    <div className="grid-2">
      <label>Assessment system<select value={projectId} onChange={event => chooseProject(event.target.value)}>
        <option value="">No saved context</option>
        {projectId && !selectedProject ? <option value={projectId}>{value?.projectName ?? projectId} (historical)</option> : null}
        {projects.map(project => <option key={project.id} value={project.id}>{project.name}</option>)}
      </select></label>
      <label>Assessment environment<select value={environmentId} disabled={!selectedProject} onChange={event => chooseEnvironment(event.target.value)}>
        <option value="">Choose environment</option>
        {environmentId && !selectedProject?.environments.some(item => item.id === environmentId) ? <option value={environmentId}>{value?.environmentName ?? environmentId} (historical)</option> : null}
        {selectedProject?.environments.map(environment => <option key={environment.id} value={environment.id}>{environment.name}</option>)}
      </select></label>
    </div>
    {selection ? <details><summary>Override context for this run</summary>
      <p className="small">These changes apply only to this draft. They do not update saved Settings.</p>
      <AssessmentEnvironmentFields environment={selection.configuration} allowIdentityEdit={false} onChange={configuration => {
        invalidate(); setSelection({ ...selection, configuration });
      }} />
    </details> : null}
    {error ? <p role="alert" className="notice error">{error} Your draft changes are retained.</p> : null}
    {gaps.length ? <div role="alert"><strong>Assessment reference gaps</strong><ul>{gaps.map((gap, index) => <li key={`${gap.field}-${index}`}>{gap.field}: {gap.message}</li>)}</ul><p>Repair these references in Settings, or review the current saved environment and override them for this run. No replacement reference is selected automatically.</p></div> : null}
    <div className="actions">
      <Button type="button" variant="secondary" disabled={!selection || loading} onClick={() => void load()}>{loading ? 'Loading assessment context…' : 'Load assessment context'}</Button>
      {value && !selection && selectedProject?.environments.some(item => item.id === environmentId) ? <Button type="button" variant="secondary" disabled={catalog.isFetching} onClick={() => chooseEnvironment(environmentId)}>Review current saved environment</Button> : null}
      {value || projectId ? <Button type="button" variant="secondary" onClick={remove}>Remove assessment context</Button> : null}
      <Button type="button" variant="ghost" disabled={catalog.isFetching} onClick={() => void catalog.refetch()}>Refresh saved choices</Button>
    </div>
    {selection && descriptor && selection.version !== descriptor.value_version ? <p className="notice">Saved context changed. Your run overrides are retained; select the environment again to review the new saved version before loading.</p> : null}
  </section>;
}
