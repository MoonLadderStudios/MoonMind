import { useMemo } from 'react';
import { useQuery } from '@tanstack/react-query';
import { Link, useLocation, useNavigate } from 'react-router-dom';

import type { BootPayload } from '../boot/parseBootPayload';

type Category = 'artifacts' | 'reports' | 'observability';
type CollectionRow = {
  artifact_id: string;
  created_at: string;
  content_type?: string | null;
  size_bytes?: number | null;
  status: string;
  retention_class: string;
  link_type?: string | null;
  label?: string | null;
  workflow_id?: string | null;
  run_id?: string | null;
  view_url: string;
  download_url: string;
};
type CollectionResponse = {
  category: Category;
  items: CollectionRow[];
  total: number;
  offset: number;
  limit: number;
  refreshed_at: string;
};

const CATEGORIES: Array<{ value: Category; label: string; description: string }> = [
  { value: 'artifacts', label: 'Artifacts', description: 'Generated files and durable execution evidence.' },
  { value: 'reports', label: 'Reports', description: 'Published summaries, findings, and exports.' },
  { value: 'observability', label: 'Observability', description: 'Logs, diagnostics, and trace evidence.' },
];

function formatBytes(value?: number | null): string {
  if (value == null) return 'Unknown size';
  if (value < 1024) return `${value} B`;
  if (value < 1024 * 1024) return `${(value / 1024).toFixed(1)} KB`;
  return `${(value / (1024 * 1024)).toFixed(1)} MB`;
}

function EvidenceSection({ category, apiBase }: { category: Category; apiBase: string }) {
  const location = useLocation();
  const navigate = useNavigate();
  const params = useMemo(() => new URLSearchParams(location.search), [location.search]);
  const query = params.get('q') ?? '';
  const page = Math.max(1, Number(params.get('page') || '1') || 1);
  const limit = 25;
  const offset = (page - 1) * limit;
  const result = useQuery({
    queryKey: ['artifact-collection', category, query, offset],
    queryFn: async (): Promise<CollectionResponse> => {
      const search = new URLSearchParams({ category, offset: String(offset), limit: String(limit) });
      if (query) search.set('q', query);
      const response = await fetch(`${apiBase}/artifacts/collection?${search}`, { credentials: 'same-origin' });
      if (!response.ok) throw new Error(`Collection request failed (${response.status})`);
      return response.json() as Promise<CollectionResponse>;
    },
  });
  const setSearch = (next: string) => {
    const search = new URLSearchParams(location.search);
    if (next) search.set('q', next); else search.delete('q');
    search.delete('page');
    navigate({ pathname: location.pathname, search: search.toString() }, { replace: true });
  };
  const setPage = (next: number) => {
    const search = new URLSearchParams(location.search);
    if (next > 1) search.set('page', String(next)); else search.delete('page');
    navigate({ pathname: location.pathname, search: search.toString() });
  };
  const definition = CATEGORIES.find((item) => item.value === category)!;

  return (
    <section className="evidence-collection" aria-labelledby={`${category}-heading`}>
      <header className="evidence-collection__header">
        <div><h2 id={`${category}-heading`}>{definition.label}</h2><p>{definition.description}</p></div>
        <button type="button" onClick={() => void result.refetch()} disabled={result.isFetching}>Refresh</button>
      </header>
      <label className="evidence-collection__filter">
        <span>Filter {definition.label.toLowerCase()}</span>
        <input value={query} onChange={(event) => setSearch(event.target.value)} type="search" />
      </label>
      {result.isPending ? <p role="status">Loading {definition.label.toLowerCase()}…</p> : null}
      {result.isError ? <div role="alert"><p>{result.error.message}</p><button type="button" onClick={() => void result.refetch()}>Try again</button></div> : null}
      {result.data && result.data.items.length === 0 ? <p>{query ? `No ${definition.label.toLowerCase()} match this filter.` : `No authorized ${definition.label.toLowerCase()} are available.`}</p> : null}
      {result.data?.items.length ? (
        <div className="evidence-collection__table-wrap"><table><thead><tr><th>Evidence</th><th>Provenance</th><th>Freshness</th><th>Size</th><th>Actions</th></tr></thead>
          <tbody>{result.data.items.map((row) => <tr key={row.artifact_id}>
            <td><code>{row.artifact_id}</code><small>{row.content_type || 'Unknown type'} · {row.status}</small></td>
            <td>{row.workflow_id ? <Link to={`/workflows/${encodeURIComponent(row.workflow_id)}`}>{row.label || row.link_type || 'Workflow'}</Link> : (row.label || row.link_type || 'Unlinked')}</td>
            <td><time dateTime={row.created_at}>{new Date(row.created_at).toLocaleString()}</time></td>
            <td>{formatBytes(row.size_bytes)}</td>
            <td><a href={row.view_url}>View metadata</a> <a href={row.download_url}>Download</a></td>
          </tr>)}</tbody></table></div>
      ) : null}
      {result.data ? <footer className="evidence-collection__pagination"><span>{result.data.total} total · refreshed <time dateTime={result.data.refreshed_at}>{new Date(result.data.refreshed_at).toLocaleTimeString()}</time></span><div><button type="button" disabled={page === 1} onClick={() => setPage(page - 1)}>Previous</button><button type="button" disabled={offset + limit >= result.data.total} onClick={() => setPage(page + 1)}>Next</button></div></footer> : null}
    </section>
  );
}

export default function ArtifactsPage({ payload }: { payload: BootPayload }) {
  const location = useLocation();
  const selected: Category = location.pathname === '/observability' ? 'observability' : 'artifacts';
  const enabled = payload.features?.artifacts !== false;
  if (!enabled) return <div role="alert">Artifacts and observability are not enabled for this deployment.</div>;
  return <div className="evidence-workspace"><header><p className="eyebrow">Evidence</p><h1>Artifacts &amp; Observability</h1><p>Authorized evidence from MoonMind-owned, same-origin services.</p><nav aria-label="Evidence collections">{CATEGORIES.map((item) => <a key={item.value} href={`#${item.value}-heading`}>{item.label}</a>)}</nav></header><EvidenceSection category={selected} apiBase={payload.apiBase || '/api'} />{selected === 'artifacts' ? <><EvidenceSection category="reports" apiBase={payload.apiBase || '/api'} /><EvidenceSection category="observability" apiBase={payload.apiBase || '/api'} /></> : null}</div>;
}
