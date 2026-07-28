import { useMemo } from 'react';
import { useMutation, useQuery } from '@tanstack/react-query';
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
};
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
    }];
  });
}

export default function OmnigentInventoryPage({ payload }: { payload: BootPayload }) {
  const location = useLocation();
  const navigate = useNavigate();
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
          body: action === 'validate'
            ? JSON.stringify({ version: profile.versions[0]?.version ?? null })
            : undefined,
        },
      );
      if (!response.ok) throw new Error(`${action} failed (${response.status})`);
    },
    onSuccess: () => void profiles.refetch(),
  });
  const rows = (result.data ?? []).filter((row) =>
    `${row.name} ${row.status} ${row.summary}`.toLowerCase().includes(filter.toLowerCase()),
  );
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
      <div className="omnigent-inventory__toolbar"><h2 id="omnigent-inventory-heading">{label} inventory</h2><button type="button" onClick={() => void result.refetch()} disabled={result.isFetching}>Refresh</button></div>
      <label><span>Filter {label.toLowerCase()}</span><input type="search" value={filter} onChange={(event) => setFilter(event.target.value)} /></label>
      {result.isPending ? <p role="status">Loading {label.toLowerCase()}…</p> : null}
      {result.isError ? <div role="alert"><p>{result.error.message}</p><button type="button" onClick={() => void result.refetch()}>Try again</button></div> : null}
      {result.data && rows.length === 0 ? <p>{filter ? `No ${label.toLowerCase()} match this filter.` : `No authorized ${label.toLowerCase()} are available.`}</p> : null}
      {rows.length ? <div className="omnigent-inventory__table-wrap"><table><thead><tr><th>Identity</th><th>Status</th><th>Summary</th><th>Freshness</th></tr></thead><tbody>{rows.map((row) => <tr key={row.id}><td><strong>{row.name}</strong><small>{row.id}</small></td><td>{row.status}</td><td>{row.summary}</td><td>{row.freshness ? <time dateTime={row.freshness}>{row.formattedFreshness}</time> : 'Not reported'}</td></tr>)}</tbody></table></div> : null}
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
              {ready && profile.state !== 'active' ? <button type="button" disabled={profileAction.isPending} onClick={() => profileAction.mutate({ profile, action: 'activate' })}>Activate {profile.displayName}</button> : null}
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
