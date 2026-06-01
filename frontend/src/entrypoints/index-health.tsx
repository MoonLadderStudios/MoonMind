import { useQuery } from '@tanstack/react-query';

import type { BootPayload } from '../boot/parseBootPayload';

interface IndexCollectionHealth {
  name: string;
  status: string;
  pointsCount: number | null;
  indexedVectorsCount: number | null;
  segmentsCount: number | null;
  vectorSize: number | null;
  vectorDistance: string | null;
  freshnessAt: string | null;
  freshnessSource: string | null;
  freshnessStatus: string;
}

interface IndexHealthResponse {
  generatedAt: string;
  totalCollections: number;
  totalPoints: number;
  collections: IndexCollectionHealth[];
}

function formatNumber(value: number | null | undefined): string {
  if (typeof value !== 'number' || !Number.isFinite(value)) {
    return 'Unknown';
  }
  return new Intl.NumberFormat().format(value);
}

function formatTimestamp(value: string | null | undefined): string {
  if (!value) {
    return 'Unknown';
  }
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) {
    return value;
  }
  return parsed.toLocaleString(undefined, {
    dateStyle: 'medium',
    timeStyle: 'short',
  });
}

function formatFreshness(collection: IndexCollectionHealth): string {
  if (collection.freshnessStatus === 'empty') {
    return 'Empty';
  }
  if (collection.freshnessAt) {
    return formatTimestamp(collection.freshnessAt);
  }
  return 'Unknown';
}

function formatVector(collection: IndexCollectionHealth): string {
  const size = collection.vectorSize ? `${collection.vectorSize}d` : 'Unknown size';
  return collection.vectorDistance ? `${size} ${collection.vectorDistance}` : size;
}

async function fetchIndexHealth(): Promise<IndexHealthResponse> {
  const response = await fetch('/retrieval/index-health', {
    credentials: 'include',
    headers: {
      Accept: 'application/json',
    },
  });
  if (!response.ok) {
    throw new Error(`Failed to load index health: ${response.statusText || response.status}`);
  }
  return response.json();
}

export function IndexHealthPage({ payload: _payload }: { payload: BootPayload }) {
  const { data, isLoading, isError, error } = useQuery<IndexHealthResponse>({
    queryKey: ['index-health'],
    queryFn: fetchIndexHealth,
  });

  return (
    <div className="index-health-page stack">
      <header className="index-health-header">
        <div>
          <p className="eyebrow">RAG & Document Retrieval</p>
          <h2 className="page-title">Index Health</h2>
        </div>
        <div className="index-health-generated">
          <span>Refreshed</span>
          <strong>{data ? formatTimestamp(data.generatedAt) : 'Loading...'}</strong>
        </div>
      </header>

      {isError ? (
        <div className="notice error" role="alert">
          {(error as Error).message}
        </div>
      ) : null}

      <section className="index-health-summary" aria-label="Index health summary">
        <article className="index-health-metric">
          <span>Collections</span>
          <strong>{isLoading ? '...' : formatNumber(data?.totalCollections)}</strong>
        </article>
        <article className="index-health-metric">
          <span>Document Count</span>
          <strong>{isLoading ? '...' : formatNumber(data?.totalPoints)}</strong>
        </article>
        <article className="index-health-metric">
          <span>Fresh Collections</span>
          <strong>
            {isLoading
              ? '...'
              : formatNumber(
                  data?.collections.filter((collection) => collection.freshnessAt).length,
                )}
          </strong>
        </article>
      </section>

      <section className="panel panel--data index-health-table-panel" aria-labelledby="index-health-table-title">
        <div className="index-health-table-heading">
          <h3 id="index-health-table-title">Indexed Collections</h3>
          <p className="small">
            Counts are read from the vector index; freshness is derived from indexed timestamp metadata when present.
          </p>
        </div>

        {isLoading ? (
          <p className="loading">Loading index health...</p>
        ) : data && data.collections.length === 0 ? (
          <p className="small index-health-empty">No indexed collections found.</p>
        ) : data ? (
          <div className="index-health-table-wrap">
            <table className="index-health-table">
              <thead>
                <tr>
                  <th scope="col">Collection</th>
                  <th scope="col">Status</th>
                  <th scope="col">Document Count</th>
                  <th scope="col">Indexed Vectors</th>
                  <th scope="col">Freshness</th>
                  <th scope="col">Vector</th>
                  <th scope="col">Segments</th>
                </tr>
              </thead>
              <tbody>
                {data.collections.map((collection) => (
                  <tr key={collection.name}>
                    <td>
                      <code>{collection.name}</code>
                    </td>
                    <td>
                      <span className={`index-health-status index-health-status--${collection.status.toLowerCase()}`}>
                        {collection.status}
                      </span>
                    </td>
                    <td>{formatNumber(collection.pointsCount)}</td>
                    <td>{formatNumber(collection.indexedVectorsCount)}</td>
                    <td>
                      <span>{formatFreshness(collection)}</span>
                      {collection.freshnessSource ? (
                        <small>{collection.freshnessSource}</small>
                      ) : null}
                    </td>
                    <td>{formatVector(collection)}</td>
                    <td>{formatNumber(collection.segmentsCount)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : null}
      </section>
    </div>
  );
}

export default IndexHealthPage;
