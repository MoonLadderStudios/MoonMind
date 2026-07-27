import { useMemo, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useLocation, useNavigate } from 'react-router-dom';

import type { BootPayload } from '../boot/parseBootPayload';

type InventoryKind = 'agents' | 'policies';
type InventoryRow = {
  id: string;
  name: string;
  status: string;
  summary: string;
  freshness: string | null;
  formattedFreshness: string | null;
  version?: number | null;
  validation?: { valid?: boolean; diagnostics?: Array<{ code: string; message: string }> };
  document?: Record<string, unknown>;
  canManage?: boolean;
};
type PolicyVersion = {
  ref: string;
  version: number;
  state: string;
  digest: string;
  document: Record<string, unknown>;
  validation: { valid?: boolean; diagnostics?: Array<{ code: string; path?: string; message: string }> };
  compatibility?: { compatible?: boolean; diagnosticCodes?: string[] };
  rollout?: Record<string, unknown>;
  lineage?: { parentRef?: string | null; cloneSourceRef?: string | null; supersedesRef?: string | null };
  audit?: Record<string, unknown>;
  stateHistory?: Array<{ state: string; actor: string; at: string; madeDefault?: boolean }>;
};

function text(record: Record<string, unknown>, ...keys: string[]): string {
  for (const key of keys) {
    const value = record[key];
    if (typeof value === 'string' && value.trim()) return value.trim();
  }
  return '';
}

function compactRows(payload: unknown): { rows: InventoryRow[]; canWrite: boolean } {
  const items = Array.isArray(payload)
    ? payload
    : payload && typeof payload === 'object' && Array.isArray((payload as { items?: unknown }).items)
      ? (payload as { items: unknown[] }).items
      : [];
  const rows = items.flatMap((item, index) => {
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
      version: typeof row.defaultVersion === 'number' ? row.defaultVersion : null,
      validation: row.version && typeof row.version === 'object'
        ? (row.version as { validation?: InventoryRow['validation'] }).validation : undefined,
      document: row.version && typeof row.version === 'object'
        ? (row.version as { document?: Record<string, unknown> }).document : undefined,
      canManage: row.canManage === true,
    }];
  });
  const canWrite = Boolean(payload && typeof payload === 'object' && !Array.isArray(payload)
    && (payload as { canWrite?: unknown }).canWrite === true);
  return { rows, canWrite };
}

export default function OmnigentInventoryPage({ payload }: { payload: BootPayload }) {
  const location = useLocation();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const [selected, setSelected] = useState<InventoryRow | null>(null);
  const [notice, setNotice] = useState('');
  const [editor, setEditor] = useState<{ mode: 'create' | 'clone' | 'version'; id: string; name: string; document: string } | null>(null);
  const [compare, setCompare] = useState<[number, number] | null>(null);
  const kind: InventoryKind = location.pathname === '/omnigent/policies'
    || location.pathname.startsWith('/omnigent/policies/')
    ? 'policies'
    : 'agents';
  const featureKey = kind === 'agents' ? 'omnigentAgents' : 'omnigentPolicies';
  const label = kind === 'agents' ? 'Agents' : 'Policies';
  const queryKey = kind === 'agents' ? 'omnigent_agents_q' : 'omnigent_policies_q';
  const params = useMemo(() => new URLSearchParams(location.search), [location.search]);
  const filter = params.get(queryKey) ?? '';
  const initialData = payload.initialData as { uiEndpoints?: Record<string, unknown> } | undefined;
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
  const versions = useQuery({
    queryKey: ['omnigent-policy-versions', selected?.id],
    enabled: kind === 'policies' && Boolean(endpoint && selected),
    queryFn: async () => {
      const response = await fetch(`${endpoint}/${encodeURIComponent(selected!.id)}/versions`, { credentials: 'same-origin' });
      if (!response.ok) throw new Error(`Policy versions request failed (${response.status})`);
      return (await response.json()) as { items: PolicyVersion[] };
    },
  });
  const diff = useQuery({
    queryKey: ['omnigent-policy-diff', selected?.id, compare],
    enabled: Boolean(endpoint && selected && compare),
    queryFn: async () => {
      const [fromVersion, toVersion] = compare!;
      const response = await fetch(`${endpoint}/${encodeURIComponent(selected!.id)}/diff?from_version=${fromVersion}&to_version=${toVersion}`, { credentials: 'same-origin' });
      if (!response.ok) throw new Error(`Policy diff request failed (${response.status})`);
      return (await response.json()) as { fromRef: string; toRef: string; diff: string };
    },
  });
  const rows = (result.data?.rows ?? []).filter((row) =>
    `${row.name} ${row.status} ${row.summary}`.toLowerCase().includes(filter.toLowerCase()),
  );
  const transition = useMutation({
    mutationFn: async ({ row, version = row.version, state, makeDefault = false }: { row: InventoryRow; version?: number | null; state: string; makeDefault?: boolean }) => {
      const response = await fetch(`${endpoint}/${encodeURIComponent(row.id)}/versions/${version}/transition`, {
        method: 'POST', credentials: 'same-origin', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ state, makeDefault }),
      });
      if (!response.ok) throw new Error(`Policy action failed (${response.status})`);
      return response.json();
    },
    onSuccess: async (_, variables) => {
      setNotice(`${variables.row.name} is now ${variables.state}.`);
      await queryClient.invalidateQueries({ queryKey: ['omnigent-inventory', kind] });
      await queryClient.invalidateQueries({ queryKey: ['omnigent-policy-versions', variables.row.id] });
    },
  });
  const savePolicy = useMutation({
    mutationFn: async (draft: NonNullable<typeof editor>) => {
      const isVersion = draft.mode === 'version';
      const url = isVersion ? `${endpoint}/${encodeURIComponent(draft.id)}/versions` : endpoint!;
      const body = isVersion
        ? { expectedParentRef: `${draft.id}@${selected?.version}`, document: JSON.parse(draft.document) }
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
      <div className="omnigent-inventory__toolbar"><h2 id="omnigent-inventory-heading">{label} inventory</h2>{kind === 'policies' && result.data?.canWrite ? <button type="button" onClick={() => setEditor({ mode: 'create', id: '', name: '', document: '{}' })}>Create policy</button> : null}<button type="button" onClick={() => void result.refetch()} disabled={result.isFetching}>Refresh</button></div>
      <label><span>Filter {label.toLowerCase()}</span><input type="search" value={filter} onChange={(event) => setFilter(event.target.value)} /></label>
      {result.isPending ? <p role="status">Loading {label.toLowerCase()}…</p> : null}
      {result.isError ? <div role="alert"><p>{result.error.message}</p><button type="button" onClick={() => void result.refetch()}>Try again</button></div> : null}
      {result.data && rows.length === 0 ? <p>{filter ? `No ${label.toLowerCase()} match this filter.` : `No authorized ${label.toLowerCase()} are available.`}</p> : null}
      {notice ? <p role="status">{notice}</p> : null}
      {rows.length ? <div className="omnigent-inventory__table-wrap"><table><thead><tr><th>Identity</th><th>Status</th><th>Summary</th><th>Freshness</th>{kind === 'policies' ? <th>Actions</th> : null}</tr></thead><tbody>{rows.map((row) => <tr key={row.id}><td><strong>{row.name}</strong><small>{row.id}{row.version ? `@${row.version}` : ''}</small></td><td>{row.status}</td><td>{row.summary}</td><td>{row.freshness ? <time dateTime={row.freshness}>{row.formattedFreshness}</time> : 'Not reported'}</td>{kind === 'policies' ? <td><button type="button" onClick={() => setSelected(row)}>Inspect</button>{row.version && row.canManage ? <><button type="button" onClick={() => transition.mutate({ row, state: 'active', makeDefault: true })}>Activate / rollback</button><button type="button" onClick={() => transition.mutate({ row, state: 'disabled' })}>Disable</button><button type="button" onClick={() => transition.mutate({ row, state: 'deprecated' })}>Deprecate</button></> : null}</td> : null}</tr>)}</tbody></table></div> : null}
      {kind === 'policies' ? <p>Creating, cloning, and editing always produces an immutable new version through the policy editor API; active historical versions are never changed.</p> : null}
      {selected ? <section className="omnigent-policy-detail" aria-label="Immutable policy version">
        <div className="omnigent-inventory__toolbar"><h2>{selected.name} immutable version</h2><button type="button" onClick={() => setSelected(null)}>Close</button></div>
        <p>Validation: {selected.validation?.valid ? 'Valid' : 'Needs attention'}</p>
        {selected.validation?.diagnostics?.map((diagnostic) => <p role="alert" key={diagnostic.code}>{diagnostic.code}: {diagnostic.message}</p>)}
        <h3>Host, resources, workspace, network, capture, controls, checkpoints, remediation, RAG, approvals, and retention</h3>
        <pre>{JSON.stringify(selected.document, null, 2)}</pre>
        {selected.canManage ? <><button type="button" onClick={() => setEditor({ mode: 'version', id: selected.id, name: selected.name, document: JSON.stringify(selected.document, null, 2) })}>Edit as new version</button>
        <button type="button" onClick={() => setEditor({ mode: 'clone', id: `${selected.id}-clone`, name: `${selected.name} clone`, document: JSON.stringify(selected.document, null, 2) })}>Clone</button></> : <p>Read-only access: lifecycle actions require settings write permission.</p>}
        <h3>Immutable version history and audit</h3>
        {versions.isPending ? <p role="status">Loading immutable version history…</p> : null}
        {versions.isError ? <p role="alert">{versions.error.message}</p> : null}
        {versions.data?.items.map((version) => <article key={version.ref} aria-label={`Policy version ${version.version}`}>
          <h4>{version.ref} — {version.state}</h4>
          <p>Digest: <code>{version.digest}</code></p>
          <p>Compatibility: {version.compatibility?.compatible ? 'Compatible' : 'Incompatible'}</p>
          {selected.canManage ? <p><button type="button" aria-label={`Activate ${version.ref} as default`} onClick={() => transition.mutate({ row: selected, version: version.version, state: 'active', makeDefault: true })}>Activate as default</button><button type="button" aria-label={`Disable ${version.ref}`} onClick={() => transition.mutate({ row: selected, version: version.version, state: 'disabled' })}>Disable</button><button type="button" aria-label={`Deprecate ${version.ref}`} onClick={() => transition.mutate({ row: selected, version: version.version, state: 'deprecated' })}>Deprecate</button></p> : null}
          {version.lineage?.parentRef ? <p>Parent: {version.lineage.parentRef}</p> : null}
          {version.lineage?.supersedesRef ? <p>Supersedes: {version.lineage.supersedesRef}</p> : null}
          {version.stateHistory?.map((event) => <p key={`${event.at}-${event.state}`}>{event.at}: {event.actor} set {event.state}{event.madeDefault ? ' and selected it as default' : ''}.</p>)}
        </article>)}
        {versions.data && versions.data.items.length > 1 ? <button type="button" onClick={() => setCompare([
          versions.data.items[1].version, versions.data.items[0].version,
        ])}>Compare latest versions</button> : null}
        {diff.data ? <section aria-label="Normalized policy diff"><h3>{diff.data.fromRef} to {diff.data.toRef}</h3><pre>{diff.data.diff || 'No normalized differences.'}</pre></section> : null}
        <h3>Activation impact</h3>
        <p>{selected.validation?.valid ? 'Validation passed. Review dependent workflow and profile usage before changing the default.' : 'Activation is blocked until all capability diagnostics are resolved.'}</p>
      </section> : null}
      {editor ? <form className="omnigent-policy-editor" onSubmit={(event) => { event.preventDefault(); savePolicy.mutate(editor); }}>
        <h2>{editor.mode === 'version' ? 'Edit as immutable new version' : editor.mode === 'clone' ? 'Clone policy' : 'Create policy'}</h2>
        <label><span>Policy id</span><input value={editor.id} disabled={editor.mode === 'version'} onChange={(event) => setEditor({ ...editor, id: event.target.value })} required /></label>
        <label><span>Name</span><input value={editor.name} onChange={(event) => setEditor({ ...editor, name: event.target.value })} required /></label>
        <label><span>Complete policy document (JSON)</span><textarea rows={18} value={editor.document} onChange={(event) => setEditor({ ...editor, document: event.target.value })} required /></label>
        {savePolicy.isError ? <p role="alert">{savePolicy.error.message}</p> : null}
        <button type="submit" disabled={savePolicy.isPending}>Validate and save draft</button>
        <button type="button" onClick={() => setEditor(null)}>Cancel</button>
      </form> : null}
    </section>
  </div>;
}
