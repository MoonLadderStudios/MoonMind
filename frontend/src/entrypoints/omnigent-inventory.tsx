import { useMemo, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useLocation, useNavigate } from 'react-router-dom';

import type { BootPayload } from '../boot/parseBootPayload';
import { ContextRetrievalControls } from '../components/ContextRetrievalControls';
import {
  type ContextRetrievalAuthoring,
  defaultContextRetrievalAuthoring,
  retrievalCeilingsFromRuntimeConfig,
} from '../lib/contextRetrievalAuthoring';

type InventoryKind = 'agents' | 'policies';
type InventoryRow = {
  id: string;
  name: string;
  status: string;
  summary: string;
  freshness: string | null;
  formattedFreshness: string | null;
  version?: number | null;
  validation?: { valid?: boolean; diagnostics?: Array<{ code: string; path?: string; message: string }> } | undefined;
  document?: Record<string, unknown> | undefined;
};
type PolicyVersion = {
  policyId: string;
  version: number;
  ref: string;
  state: string;
  digest: string;
  document: Record<string, unknown>;
  validation: { valid?: boolean; diagnostics?: Array<{ code: string; path?: string; message: string }> };
};
type AuditEvent = { eventId: string; version: number | null; type: string; actor: string; createdAt: string };
type ProfileVersion = {
  version: number;
  digest: string;
  validationResult?: { ready?: boolean } | null;
};
type AgentProfile = {
  profileId: string;
  displayName: string;
  description?: string | null;
  state: string;
  activeVersion?: number | null;
  defaultForRuntime?: boolean;
  versions: ProfileVersion[];
};

/**
 * Assisted RAG editor for a policy document (MoonMind#3514). The policy `rag`
 * block (RagPolicy) is the deployment authority that feeds the per-run
 * follow-up retrieval budget: `collectionRefs` is the allowed collection set and
 * `tokenBudget` / `latencyBudgetMs` become the compiled budget ceilings. This
 * maps only the RagPolicy-valid fields so the edited document stays valid; the
 * remaining controls preview the per-run authoring experience.
 */
function readPolicyContextRetrieval(
  documentJson: string,
): { value: ContextRetrievalAuthoring; parsed: Record<string, unknown> } | null {
  try {
    const parsed = JSON.parse(documentJson);
    if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) return null;
    const document = parsed as Record<string, unknown>;
    const rag =
      document.rag && typeof document.rag === 'object' && !Array.isArray(document.rag)
        ? (document.rag as Record<string, unknown>)
        : null;
    if (!rag) return null;
    const collections = Array.isArray(rag.collectionRefs)
      ? rag.collectionRefs.map((item) => String(item)).filter(Boolean)
      : [];
    const value = defaultContextRetrievalAuthoring();
    value.initial.collections = collections;
    value.followUp.enabled = true;
    value.followUp.collections = collections;
    value.followUp.budgetPreset = 'custom';
    if (typeof rag.tokenBudget === 'number') {
      value.followUp.maxContextTokens = rag.tokenBudget;
    }
    if (typeof rag.latencyBudgetMs === 'number') {
      value.followUp.latencyMs = rag.latencyBudgetMs;
    }
    value.followUp.fallbackAllowed = rag.fallback === 'empty';
    return { value, parsed: document };
  } catch {
    return null;
  }
}

function writePolicyContextRetrieval(
  document: Record<string, unknown>,
  value: ContextRetrievalAuthoring,
): string {
  const rag: Record<string, unknown> = {
    ...(document.rag && typeof document.rag === 'object' && !Array.isArray(document.rag)
      ? (document.rag as Record<string, unknown>)
      : {}),
  };
  const collections = Array.from(
    new Set([...value.followUp.collections, ...value.initial.collections]),
  );
  if (collections.length > 0) {
    rag.collectionRefs = collections;
  }
  rag.tokenBudget = value.followUp.maxContextTokens;
  rag.latencyBudgetMs = value.followUp.latencyMs;
  rag.fallback = value.followUp.fallbackAllowed ? 'empty' : 'deny';
  return JSON.stringify({ ...document, rag }, null, 2);
}

function text(record: Record<string, unknown>, ...keys: string[]): string {
  for (const key of keys) {
    const value = record[key];
    if (typeof value === 'string' && value.trim()) return value.trim();
  }
  return '';
}

function compactRows(payload: unknown): InventoryRow[] {
  const items = Array.isArray(payload)
    ? payload
    : payload && typeof payload === 'object' && Array.isArray((payload as { items?: unknown }).items)
      ? (payload as { items: unknown[] }).items
      : [];
  return items.flatMap((item, index) => {
    if (!item || typeof item !== 'object' || Array.isArray(item)) return [];
    const row = item as Record<string, unknown>;
    const id = text(row, 'id', 'agentId', 'agent_id', 'slug', 'name') || `agent-${index + 1}`;
    const freshness = text(row, 'updatedAt', 'updated_at', 'lastSeenAt', 'last_seen_at') || null;
    return [{
      id,
      name: text(row, 'displayName', 'display_name', 'name', 'label') || id,
      status: text(row, 'status', 'state', 'health') || 'Available',
      summary: text(row, 'description', 'summary', 'scope') || 'No summary provided.',
      freshness,
      formattedFreshness: freshness ? new Date(freshness).toLocaleString() : null,
      version: typeof row.defaultVersion === 'number'
        ? row.defaultVersion
        : row.version && typeof row.version === 'object' && typeof (row.version as { version?: unknown }).version === 'number'
          ? (row.version as { version: number }).version
          : null,
      validation: row.version && typeof row.version === 'object'
        ? (row.version as { validation?: InventoryRow['validation'] }).validation : undefined,
      document: row.version && typeof row.version === 'object'
        ? (row.version as { document?: Record<string, unknown> }).document : undefined,
    }];
  });
}

export default function OmnigentInventoryPage({ payload }: { payload: BootPayload }) {
  const location = useLocation();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const [selected, setSelected] = useState<InventoryRow | null>(null);
  const [selectedVersion, setSelectedVersion] = useState<PolicyVersion | null>(null);
  const [notice, setNotice] = useState('');
  const [editor, setEditor] = useState<{ mode: 'create' | 'clone' | 'version'; id: string; name: string; document: string } | null>(null);
  const kind: InventoryKind = location.pathname === '/omnigent/policies'
    || location.pathname.startsWith('/omnigent/policies/')
    ? 'policies'
    : 'agents';
  const featureKey = kind === 'agents' ? 'omnigentAgents' : 'omnigentPolicies';
  const label = kind === 'agents' ? 'Agents' : 'Policies';
  const queryKey = kind === 'agents' ? 'omnigent_agents_q' : 'omnigent_policies_q';
  const params = useMemo(() => new URLSearchParams(location.search), [location.search]);
  const filter = params.get(queryKey) ?? '';
  const initialData = payload.initialData as { uiEndpoints?: Record<string, unknown>; dashboardConfig?: { system?: { retrievalAuthoring?: Record<string, unknown> } } } | undefined;
  const retrievalCeilings = retrievalCeilingsFromRuntimeConfig(
    initialData?.dashboardConfig?.system?.retrievalAuthoring,
  );
  const endpoints = initialData?.uiEndpoints;
  const enabled = payload.features?.[featureKey] === true;
  const discoveredEndpoint = endpoints?.[kind === 'agents' ? 'omnigentAgents' : 'omnigentPolicies'];
  const endpoint = typeof discoveredEndpoint === 'string' ? discoveredEndpoint : null;
  const result = useQuery({
    queryKey: ['omnigent-inventory', kind],
    enabled: enabled && Boolean(endpoint),
    staleTime: Number.POSITIVE_INFINITY,
    queryFn: async () => {
      const response = await fetch(endpoint!, { credentials: 'same-origin' });
      if (!response.ok) throw new Error(`${label} request failed (${response.status})`);
      return compactRows(await response.json());
    },
  });
  const profiles = useQuery({
    queryKey: ['omnigent-agent-profiles'],
    enabled: enabled && kind === 'agents',
    queryFn: async (): Promise<AgentProfile[]> => {
      const response = await fetch('/api/omnigent/agent-profiles', { credentials: 'same-origin' });
      if (!response.ok) throw new Error(`Agent profiles request failed (${response.status})`);
      const data: unknown = await response.json();
      return Array.isArray(data) ? data as AgentProfile[] : [];
    },
  });
  const profileAction = useMutation({
    mutationFn: async ({ profile, action }: { profile: AgentProfile; action: string }) => {
      const suffix = action === 'validate'
        ? 'validate'
        : action === 'activate'
          ? `activate/${profile.versions[0]?.version ?? 1}`
          : action;
      const response = await fetch(
        `/api/omnigent/agent-profiles/${encodeURIComponent(profile.profileId)}/${suffix}`,
        {
          method: 'POST',
          credentials: 'same-origin',
          headers: { 'Content-Type': 'application/json' },
          ...(action === 'validate'
            ? { body: JSON.stringify({ version: profile.versions[0]?.version ?? null }) }
            : {}),
        },
      );
      if (!response.ok) throw new Error(`${action} failed (${response.status})`);
    },
    onSuccess: () => void profiles.refetch(),
  });
  const versions = useQuery({
    queryKey: ['omnigent-policy-versions', selected?.id],
    enabled: kind === 'policies' && Boolean(endpoint && selected),
    queryFn: async () => {
      const response = await fetch(`${endpoint}/${encodeURIComponent(selected!.id)}/versions`, { credentials: 'same-origin' });
      if (!response.ok) throw new Error(`Version history request failed (${response.status})`);
      return (await response.json() as { items: PolicyVersion[] }).items;
    },
  });
  const audit = useQuery({
    queryKey: ['omnigent-policy-audit', selected?.id],
    enabled: kind === 'policies' && Boolean(endpoint && selected),
    queryFn: async () => {
      const response = await fetch(`${endpoint}/${encodeURIComponent(selected!.id)}/audit`, { credentials: 'same-origin' });
      if (!response.ok) throw new Error(`Audit history request failed (${response.status})`);
      return (await response.json() as { items: AuditEvent[] }).items;
    },
  });
  const visibleVersion = selectedVersion ?? versions.data?.[0] ?? null;
  const versionDiff = useQuery({
    queryKey: ['omnigent-policy-diff', selected?.id, visibleVersion?.version, selected?.version],
    enabled: Boolean(endpoint && selected && visibleVersion && selected.version && visibleVersion.version !== selected.version),
    queryFn: async () => {
      const response = await fetch(`${endpoint}/${encodeURIComponent(selected!.id)}/diff?from_version=${visibleVersion!.version}&to_version=${selected!.version}`, { credentials: 'same-origin' });
      if (!response.ok) throw new Error(`Version diff request failed (${response.status})`);
      return response.json() as Promise<{ diff: string }>;
    },
  });
  const rows = (result.data ?? []).filter((row) =>
    `${row.name} ${row.status} ${row.summary}`.toLowerCase().includes(filter.toLowerCase()),
  );
  const transition = useMutation({
    mutationFn: async ({ row, state, makeDefault = false }: { row: InventoryRow; state: string; makeDefault?: boolean }) => {
      const response = await fetch(`${endpoint}/${encodeURIComponent(row.id)}/versions/${row.version}/transition`, {
        method: 'POST', credentials: 'same-origin', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ state, makeDefault }),
      });
      if (!response.ok) throw new Error(`Policy action failed (${response.status})`);
      return response.json();
    },
    onSuccess: async (_, variables) => {
      setNotice(`${variables.row.name} is now ${variables.state}.`);
      await queryClient.invalidateQueries({ queryKey: ['omnigent-inventory', kind] });
    },
  });
  const validate = useMutation({
    mutationFn: async (version: PolicyVersion) => {
      const response = await fetch(`${endpoint}/${encodeURIComponent(version.policyId)}/versions/${version.version}/validate`, {
        method: 'POST', credentials: 'same-origin',
      });
      if (!response.ok) throw new Error(`Policy validation failed (${response.status})`);
      return response.json() as Promise<PolicyVersion>;
    },
    onSuccess: async (version) => {
      setSelectedVersion(version);
      setNotice(version.validation.valid ? `${version.ref} is compatible.` : `${version.ref} needs attention.`);
      await queryClient.invalidateQueries({ queryKey: ['omnigent-policy-versions', version.policyId] });
    },
  });
  const savePolicy = useMutation({
    mutationFn: async (draft: NonNullable<typeof editor>) => {
      const isVersion = draft.mode === 'version';
      const url = isVersion ? `${endpoint}/${encodeURIComponent(draft.id)}/versions` : endpoint!;
      const body = isVersion
        ? { expectedParentRef: `${draft.id}@${versions.data?.[0]?.version}`, document: JSON.parse(draft.document) }
        : { policyId: draft.id, name: draft.name, visibility: 'deployment',
            cloneSourceRef: draft.mode === 'clone' ? `${selected?.id}@${selected?.version}` : undefined,
            document: JSON.parse(draft.document) };
      const response = await fetch(url, { method: 'POST', credentials: 'same-origin',
        headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
      if (!response.ok) throw new Error(`Policy save failed (${response.status})`);
      return response.json();
    },
    onSuccess: async () => {
      setNotice('Immutable policy version saved. Validate diagnostics before activation.');
      setEditor(null);
      await queryClient.invalidateQueries({ queryKey: ['omnigent-inventory', kind] });
    },
  });
  const setFilter = (value: string) => {
    const next = new URLSearchParams(location.search);
    if (value) next.set(queryKey, value); else next.delete(queryKey);
    navigate({ pathname: location.pathname, search: next.toString() }, { replace: true });
  };

  if (!enabled || !endpoint) {
    return <div className="omnigent-inventory" role="alert"><h1>Omnigent {label}</h1><p>This inventory is not available for this deployment.</p></div>;
  }
  return <div className="omnigent-inventory">
    <header><p className="eyebrow">Omnigent</p><h1>{label}</h1><p>{kind === 'agents' ? 'Available agent identities and runtime status.' : 'Authorized policy scopes and status.'}</p></header>
    <section aria-labelledby="omnigent-inventory-heading">
      <div className="omnigent-inventory__toolbar"><h2 id="omnigent-inventory-heading">{label} inventory</h2>{kind === 'policies' ? <button type="button" onClick={() => setEditor({ mode: 'create', id: '', name: '', document: '{}' })}>Create policy</button> : null}<button type="button" onClick={() => void result.refetch()} disabled={result.isFetching}>Refresh</button></div>
      <label><span>Filter {label.toLowerCase()}</span><input type="search" value={filter} onChange={(event) => setFilter(event.target.value)} /></label>
      {result.isPending ? <p role="status">Loading {label.toLowerCase()}…</p> : null}
      {result.isError ? <div role="alert"><p>{result.error.message}</p><button type="button" onClick={() => void result.refetch()}>Try again</button></div> : null}
      {result.data && rows.length === 0 ? <p>{filter ? `No ${label.toLowerCase()} match this filter.` : `No authorized ${label.toLowerCase()} are available.`}</p> : null}
      {notice ? <p role="status">{notice}</p> : null}
      {transition.isError ? <p role="alert">{transition.error.message}</p> : null}
      {rows.length ? <div className="omnigent-inventory__table-wrap"><table><thead><tr><th>Identity</th><th>Status</th><th>Summary</th><th>Freshness</th>{kind === 'policies' ? <th>Actions</th> : null}</tr></thead><tbody>{rows.map((row) => <tr key={row.id}><td><strong>{row.name}</strong><small>{row.id}{row.version ? `@${row.version}` : ''}</small></td><td>{row.status}</td><td>{row.summary}</td><td>{row.freshness ? <time dateTime={row.freshness}>{row.formattedFreshness}</time> : 'Not reported'}</td>{kind === 'policies' ? <td><button type="button" onClick={() => { setSelected(row); setSelectedVersion(null); }}>Inspect</button>{row.version ? <button type="button" onClick={() => transition.mutate({ row, state: 'active', makeDefault: true })}>Activate / rollback</button> : null}</td> : null}</tr>)}</tbody></table></div> : null}
      {kind === 'policies' ? <p>Creating, cloning, and editing always produces an immutable new version through the policy editor API; active historical versions are never changed.</p> : null}
      {selected ? <section className="omnigent-policy-detail" aria-label="Immutable policy version">
        <div className="omnigent-inventory__toolbar"><h2>{selected.name} immutable version</h2><button type="button" onClick={() => { setSelected(null); setSelectedVersion(null); }}>Close</button></div>
        {versions.isError || audit.isError ? <p role="alert">{versions.error?.message ?? audit.error?.message}</p> : null}
        {versions.data ? <div><h3>Version history</h3>{versions.data.map((version) =>
          <button type="button" key={version.ref} aria-pressed={visibleVersion?.ref === version.ref}
            onClick={() => setSelectedVersion(version)}>{version.ref} · {version.state}</button>)}</div> : <p role="status">Loading immutable history…</p>}
        <p>Validation: {(visibleVersion?.validation ?? selected.validation)?.valid ? 'Valid' : 'Needs attention'}</p>
        {(visibleVersion?.validation ?? selected.validation)?.diagnostics?.map((diagnostic) => <p role="alert" key={`${diagnostic.code}-${diagnostic.path}`}>{diagnostic.path ? `${diagnostic.path}: ` : ''}{diagnostic.code}: {diagnostic.message}</p>)}
        <h3>Host, resources, workspace, network, capture, controls, checkpoints, remediation, RAG, approvals, and retention</h3>
        <pre>{JSON.stringify(visibleVersion?.document ?? selected.document, null, 2)}</pre>
        {visibleVersion ? <button type="button" onClick={() => validate.mutate(visibleVersion)}>Validate against deployment</button> : null}
        {visibleVersion && visibleVersion.version !== selected.version ? <button type="button" onClick={() => transition.mutate({ row: { ...selected, version: visibleVersion.version }, state: 'active', makeDefault: true })}>Roll back default to {visibleVersion.ref}</button> : null}
        <button type="button" onClick={() => setEditor({ mode: 'version', id: selected.id, name: selected.name, document: JSON.stringify(visibleVersion?.document ?? selected.document, null, 2) })}>Edit as new version</button>
        <button type="button" onClick={() => setEditor({ mode: 'clone', id: `${selected.id}-clone`, name: `${selected.name} clone`, document: JSON.stringify(visibleVersion?.document ?? selected.document, null, 2) })}>Clone</button>
        {versionDiff.data ? <><h3>Normalized diff to current default</h3><pre>{versionDiff.data.diff || 'No document differences.'}</pre></> : null}
        <h3>Audit history</h3>
        {audit.data?.length ? <ol>{audit.data.map((event) => <li key={event.eventId}>{event.type} · version {event.version ?? 'identity'} · {event.actor}</li>)}</ol> : <p>No lifecycle events recorded.</p>}
        <p>Activation is blocked when deployment compatibility diagnostics fail. Policy documents render references only; secret values and host paths are rejected by the API.</p>
      </section> : null}
      {editor ? <form className="omnigent-policy-editor" onSubmit={(event) => { event.preventDefault(); savePolicy.mutate(editor); }}>
        <h2>{editor.mode === 'version' ? 'Edit as immutable new version' : editor.mode === 'clone' ? 'Clone policy' : 'Create policy'}</h2>
        <label><span>Policy id</span><input value={editor.id} disabled={editor.mode === 'version'} onChange={(event) => setEditor({ ...editor, id: event.target.value })} required /></label>
        <label><span>Name</span><input value={editor.name} onChange={(event) => setEditor({ ...editor, name: event.target.value })} required /></label>
        <label><span>Complete policy document (JSON)</span><textarea rows={18} value={editor.document} onChange={(event) => setEditor({ ...editor, document: event.target.value })} required /></label>
        {(() => {
          const retrieval = readPolicyContextRetrieval(editor.document);
          if (!retrieval) {
            return (
              <p className="small">
                Add a <code>rag</code> block to the document above to configure
                context retrieval defaults with assisted controls.
              </p>
            );
          }
          return (
            <details className="omnigent-policy-context-retrieval">
              <summary>Context retrieval (RAG) defaults</summary>
              <ContextRetrievalControls
                value={retrieval.value}
                ceilings={retrievalCeilings}
                onChange={(next) =>
                  setEditor({
                    ...editor,
                    document: writePolicyContextRetrieval(retrieval.parsed, next),
                  })
                }
                description="Policy defaults set the allowed collections and the budget ceilings that per-run and workflow authoring narrow within. Only collections and budget ceilings persist to the policy document."
              />
            </details>
          );
        })()}
        {savePolicy.isError ? <p role="alert">{savePolicy.error.message}</p> : null}
        <button type="submit" disabled={savePolicy.isPending}>Validate and save draft</button>
        <button type="button" onClick={() => setEditor(null)}>Cancel</button>
      </form> : null}
    </section>
    {kind === 'agents' ? <section aria-labelledby="omnigent-profiles-heading">
      <div className="omnigent-inventory__toolbar">
        <div><p className="eyebrow">Reusable configuration</p><h2 id="omnigent-profiles-heading">Agent profiles</h2></div>
        <button type="button" onClick={() => void profiles.refetch()} disabled={profiles.isFetching}>Refresh profiles</button>
      </div>
      <p>Immutable, validated selections used by workflows and continuations.</p>
      {profiles.isPending ? <p role="status">Loading agent profiles…</p> : null}
      {profiles.isError ? <p role="alert">{profiles.error.message}</p> : null}
      {profileAction.isError ? <p role="alert">{profileAction.error.message}</p> : null}
      {profiles.data?.length === 0 ? <p>No persistent agent profiles are available.</p> : null}
      {profiles.data?.length ? <div className="omnigent-inventory__table-wrap"><table>
        <thead><tr><th>Profile</th><th>Lifecycle</th><th>Version history</th><th>Readiness</th><th>Actions</th></tr></thead>
        <tbody>{profiles.data.map((profile) => {
          const latest = profile.versions[0];
          const ready = latest?.validationResult?.ready === true;
          return <tr key={profile.profileId}>
            <td><strong>{profile.displayName}</strong><small>{profile.profileId}</small>{profile.description ? <small>{profile.description}</small> : null}</td>
            <td>{profile.state}{profile.defaultForRuntime ? ' · Default' : ''}</td>
            <td>{latest ? <><span>Version {latest.version}</span><small title={latest.digest}>{latest.digest.slice(0, 18)}…</small><small>{profile.versions.length} immutable version{profile.versions.length === 1 ? '' : 's'}</small></> : 'No versions'}</td>
            <td>{ready ? 'Ready' : 'Validation required'}</td>
            <td>
              <button type="button" disabled={profileAction.isPending} onClick={() => profileAction.mutate({ profile, action: 'validate' })}>Validate {profile.displayName}</button>
              {ready && (profile.state !== 'active' || latest.version !== profile.activeVersion) ? <button type="button" disabled={profileAction.isPending} onClick={() => profileAction.mutate({ profile, action: 'activate' })}>Activate {profile.displayName}</button> : null}
              {profile.state === 'active' && !profile.defaultForRuntime ? <button type="button" disabled={profileAction.isPending} onClick={() => profileAction.mutate({ profile, action: 'default' })}>Make {profile.displayName} default</button> : null}
              {profile.state === 'active' ? <button type="button" disabled={profileAction.isPending} onClick={() => profileAction.mutate({ profile, action: 'disable' })}>Disable {profile.displayName}</button> : null}
              {profile.state !== 'deprecated' ? <button type="button" disabled={profileAction.isPending} onClick={() => profileAction.mutate({ profile, action: 'deprecate' })}>Deprecate {profile.displayName}</button> : null}
            </td>
          </tr>;
        })}</tbody>
      </table></div> : null}
    </section> : null}
  </div>;
}
