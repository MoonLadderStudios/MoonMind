import { useEffect, useState } from 'react';
import { useQueries, useQuery } from '@tanstack/react-query';
import { Link, useLocation } from 'react-router-dom';
import { z } from 'zod';
import type { BootPayload } from '../boot/parseBootPayload';
import { Button } from '../components/Button';
import { useDocumentTitle } from '../components/dashboard/PageHeader';
import { AssessmentProjectsSettings } from '../components/settings/AssessmentProjectsSettings';
import { AssessmentAuthorizationsSettings } from '../components/settings/AssessmentAuthorizationsSettings';
import { SettingsDraftGuardProvider } from '../components/settings/SettingsDraftGuard';
import { ApiError, responseError } from '../lib/api/client';
import { copyAssessmentSnapshot } from '../lib/assessmentProjects';
import { buildSavedResultPreviewHref, canDownloadSavedResultRaw, buildSavedResultDownloadHref, type SavedResultArtifactLike } from './saved-results';

const Presets = z.object({ items: z.array(z.object({ slug: z.string(), title: z.string().optional(), tags: z.array(z.string()).optional() })) });
const Runs = z.object({ items: z.array(z.object({ workflowId: z.string().optional(), taskId: z.string().optional(), title: z.string(), status: z.string(), createdAt: z.string() })), nextPageToken: z.string().nullable().optional(), degradedCount: z.boolean().optional() });
const Detail = z.object({ namespace: z.string(), runId: z.string().optional(), temporalRunId: z.string().optional(), inputParameters: z.record(z.string(), z.unknown()).default({}) });
type Run = z.infer<typeof Runs>['items'][number];
function runId(run: Run) { return run.workflowId || run.taskId || ''; }
function workflowHref(id: string) { return `/workflows/${encodeURIComponent(id)}`; }
function linkType(link: { linkType?: unknown; link_type?: unknown }) { return String(link.linkType || link.link_type || ''); }
// Create derives the plan's jobs and conclusion; the operator still selects an
// authorization and reviews the rules of engagement before anything runs.
function runPlanHref(planRef: string) { return `/workflows/new?preset=security-plan-run&presetInputs=${encodeURIComponent(JSON.stringify({ plan_ref: planRef }))}`; }
async function read(apiBase: string, path: string, signal?: AbortSignal) {
  const response = await fetch(`${apiBase}${path}`, { credentials: 'same-origin', ...(signal ? { signal } : {}) });
  if (!response.ok) throw await responseError(response, 'Security workspace request failed');
  return response.json() as Promise<unknown>;
}

function AssessmentHistory({ apiBase, reports }: { apiBase: string; reports: boolean }) {
  const [tokens, setTokens] = useState<string[]>(['']);
  const [target, setTarget] = useState('');
  const [selected, setSelected] = useState('');
  const presets = useQuery({ queryKey: ['security-presets', apiBase], queryFn: async ({ signal }) => Presets.parse(await read(apiBase, '/presets?includeInactive=true', signal)) });
  const catalogDisabled = presets.error instanceof ApiError && presets.error.status === 404 && presets.error.code === 'preset_catalog_disabled';
  const assessmentPresets = catalogDisabled ? [] : presets.data?.items.filter(item => item.tags?.includes('security') && item.tags?.includes('assessment')) ?? [];
  // The execution API projects preset slugs as targetSkill. Include direct
  // portable assessment/tool work as well; no title-based classification.
  const skills = ['security-review', 'security.assess', ...assessmentPresets.map(item => item.slug)];
  const params = new URLSearchParams({ source: 'temporal', pageSize: '10', targetSkillIn: skills.join(',') });
  const token = tokens[tokens.length - 1];
  if (token) params.set('nextPageToken', token);
  const runs = useQuery({ queryKey: ['security-runs', apiBase, params.toString()], enabled: presets.isSuccess || catalogDisabled,
    queryFn: async ({ signal }) => Runs.parse(await read(apiBase, `/executions?${params}`, signal)) });
  const items = runs.data?.items ?? [];
  const details = useQueries({ queries: items.map(run => ({ queryKey: ['security-run-context', apiBase, runId(run)],
    queryFn: async ({ signal }: { signal: AbortSignal }) => Detail.parse(await read(apiBase, `/executions/${encodeURIComponent(runId(run))}?source=temporal`, signal)), staleTime: 15_000 })) });
  const rows = items.map((run, i) => ({ run, detail: details[i], target: copyAssessmentSnapshot(details[i]?.data?.inputParameters.assessmentProject) }));
  const targetKey = (row: typeof rows[number]) => row.target ? `${row.target.projectId}/${row.target.environmentId}` : '';
  const visible = rows.filter(row => !target || targetKey(row) === target);
  const chosen = rows.find(row => runId(row.run) === selected);
  const context = chosen?.detail?.data;
  const recordedRunId = context?.temporalRunId || context?.runId;
  const evidence = useQuery({ queryKey: ['security-reports', apiBase, selected, context?.namespace, recordedRunId],
    enabled: reports && Boolean(selected && context?.namespace && recordedRunId),
    queryFn: async ({ signal }) => {
      const value = await read(apiBase, `/executions/${encodeURIComponent(context!.namespace)}/${encodeURIComponent(selected)}/${encodeURIComponent(recordedRunId!)}/artifacts`, signal);
      const list = z.object({ artifacts: z.array(z.record(z.string(), z.unknown())) }).parse(value);
      return list.artifacts.map(item => ({ ...item, artifactId: String(item.artifactId || item.artifact_id || '') } as SavedResultArtifactLike)).filter(item => item.artifactId && item.links?.some(link => { const type = linkType(link); return type.startsWith('report.') || type === 'security.plan'; }));
    },
  });
  const changePage = (next: string[] ) => { setTokens(next); setTarget(''); setSelected(''); };
  return <section className="space-y-4" aria-label={reports ? 'Assessment reports' : 'Recent assessments'}>
    <h2>{reports ? 'Reports' : 'Recent assessments'}</h2>
    <p>History for cataloged security assessment presets and direct security assessment work. Filters below apply to this page of 10 workflows, not all history. Custom workflows without assessment identity may only appear in <Link to="/workflows">Workflows</Link>.</p>
    {presets.isPending || runs.isLoading ? <p role="status">Loading assessments…</p> : null}
    {catalogDisabled ? <p role="status">Preset catalog is disabled. Showing direct security assessment work only; preset-based history is unavailable.</p> : null}
    {(presets.isError && !catalogDisabled) || runs.isError ? <div role="alert"><p>{(!catalogDisabled && presets.error?.message) || runs.error?.message}</p><Button onClick={() => { void presets.refetch(); void runs.refetch(); }}>Try again</Button></div> : null}
    {runs.data?.degradedCount ? <p role="alert">The workflow service reports degraded filtering or counts. This page may be incomplete; check Workflows and Operations.</p> : null}
    <label className="block">Target / environment (current page)<select className="ml-2" value={target} onChange={event => { setTarget(event.target.value); setSelected(''); }}><option value="">All targets</option>{Array.from(new Map(rows.filter(row => row.target).map(row => [targetKey(row), `${row.target!.projectName} / ${row.target!.environmentName}`])).entries()).map(([key, label]) => <option key={key} value={key}>{label}</option>)}</select></label>
    {reports ? <label className="block">Assessment<select className="ml-2" value={selected} onChange={event => setSelected(event.target.value)}><option value="">Select an assessment</option>{visible.map(({ run }) => <option key={runId(run)} value={runId(run)}>{run.title}</option>)}</select></label> : null}
    <div className="stacked-records-wrap"><table className="stacked-records"><thead><tr><th>Assessment</th><th>Target</th><th>Status</th><th>Created</th><th>Actions</th></tr></thead><tbody>{visible.map(({ run, detail, target: snapshot }) => <tr key={runId(run)}>
      <td data-label="Assessment">{run.title}</td><td data-label="Target">{snapshot ? `${snapshot.projectName} / ${snapshot.environmentName}` : detail?.isPending ? 'Loading target…' : detail?.isError ? 'Target unavailable' : 'No saved target recorded'}</td><td data-label="Status">{run.status}</td><td data-label="Created"><time dateTime={run.createdAt}>{new Date(run.createdAt).toLocaleString()}</time></td><td data-label="Actions"><Link to={workflowHref(runId(run))}>Open / resume</Link>{' · '}<Link to={`${workflowHref(runId(run))}/details`}>Recorded scope</Link>{' · '}<Link to={`${workflowHref(runId(run))}/evidence`}>Results</Link></td>
    </tr>)}</tbody></table></div>
    {runs.isSuccess && !visible.length ? <p>No assessments match on this page.</p> : null}
    <div className="flex gap-3"><Button disabled={tokens.length === 1} onClick={() => changePage(tokens.slice(0, -1))}>Previous assessments</Button><Button disabled={!runs.data?.nextPageToken} onClick={() => changePage([...tokens, runs.data!.nextPageToken!])}>Next assessments</Button><Button onClick={() => { void runs.refetch(); details.forEach(detail => { void detail.refetch(); }); if (selected) void evidence.refetch(); }}>Refresh</Button></div>
    {reports && !selected ? <p>Select an assessment to see its retained report links. Reports stay in the original workflow artifact store.</p> : null}
    {reports && selected ? <section aria-label="Selected assessment reports" className="space-y-3"><h3>Reports for {chosen?.run.title}</h3><Link to={`${workflowHref(selected)}/evidence`}>Review evidence</Link><p>Recorded run: {recordedRunId || 'Not available'}. The workflow evidence view provides full run history, scope, and any available continuation or retry actions.</p>
      {chosen?.detail?.isError ? <p role="alert">Assessment context could not be loaded. Open the workflow to inspect its current state.</p> : null}
      {evidence.isFetching ? <p role="status">Loading report links…</p> : null}
      {evidence.isError ? <p role="alert">{evidence.error.message}</p> : null}
      {evidence.isSuccess && evidence.data.length === 0 ? <p>No report-linked artifacts are retained for this run. Other evidence may still be available in the workflow.</p> : null}
      <ul>{evidence.data?.map(artifact => { const plan = artifact.links?.some(link => linkType(link) === 'security.plan'); return <li key={artifact.artifactId}><span>{plan ? 'Retained security plan' : artifact.links?.find(link => linkType(link).startsWith('report.'))?.label || artifact.artifactId}</span>{' · '}<a href={buildSavedResultPreviewHref(apiBase, artifact)}>Preview</a>{canDownloadSavedResultRaw(artifact) ? <> · <a href={buildSavedResultDownloadHref(apiBase, artifact)}>Download</a></> : null}{plan ? <>{' · '}<Link to={runPlanHref(artifact.artifactId)}>Run this plan</Link></> : null}</li>; })}</ul>
    </section> : null}
  </section>;
}

export default function SecurityPage({ payload }: { payload: BootPayload }) {
  const location = useLocation();
  const tab = location.pathname.endsWith('/targets') ? 'targets' : location.pathname.endsWith('/reports') ? 'reports' : 'assessments';
  useDocumentTitle('Security');
  useEffect(() => {
    if (location.hash) document.getElementById(location.hash.slice(1))?.scrollIntoView?.();
  }, [location.hash, tab]);
  const params = new URLSearchParams(location.search);
  const authorizationRef = params.get('authorizationRef') || undefined;
  return <SettingsDraftGuardProvider><main className="evidence-workspace space-y-6"><header><h1>Security</h1><p>Assessments, reusable target context, and retained reports. Saving a target does not authorize access or start work.</p><nav aria-label="Security sections" className="flex gap-4">{['assessments', 'targets', 'reports'].map(value => <Link key={value} to={`/security/${value}`} aria-current={tab === value ? 'page' : undefined}>{value[0]!.toUpperCase() + value.slice(1)}</Link>)}</nav></header>
    {tab === 'targets' ? <><h2>Targets</h2><p>Saved systems and environments are context only. Select and review a separate, current authorization when launching an assessment.</p><AssessmentProjectsSettings /><AssessmentAuthorizationsSettings {...(authorizationRef ? { authorizationRef } : {})} /></> : <>{tab === 'assessments' ? <section><h2>Start or continue an assessment</h2><p>Start with a security plan: choose what you are securing, a saved environment and the standards to meet. The plan runs no scanner or network job. To run it, open the plan's Reports and choose Run this plan; Create prefills its jobs and conclusion for your authorization and review. Existing runs resume through their workflow controls; opening a run does not restart it.</p><Link to="/workflows/new?preset=security-plan">Start a security plan</Link>{' · '}<Link to="/workflows/new">Start another assessment</Link>{' · '}<Link to="/security/targets">Manage targets and authorizations</Link></section> : null}<AssessmentHistory apiBase={payload.apiBase || '/api'} reports={tab === 'reports'} /></>}
  </main></SettingsDraftGuardProvider>;
}
