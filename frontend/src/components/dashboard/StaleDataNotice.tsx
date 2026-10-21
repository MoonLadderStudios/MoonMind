import { Button } from '../Button';
function formatStaleTime(updatedAt: number): string {
  const date = new Date(updatedAt);
  return date.toDateString() === new Date().toDateString()
    ? date.toLocaleTimeString()
    : date.toLocaleString();
}

/**
 * Compact notice for data that is still shown after a background refresh
 * failed. Pages keep rendering what they last loaded and show this instead of
 * replacing the content with an error, so a brief API outage (for example
 * during a managed update) never unmounts panels, log streams or chat.
 */
export function StaleDataNotice({
  updatedAt,
  error,
  onRetry,
  retrying = false,
}: {
  /** When the shown data was last loaded (epoch ms); 0 when unknown. */
  updatedAt: number;
  error: unknown;
  onRetry?: () => void;
  retrying?: boolean;
}) {
  const message = error instanceof Error && error.message ? error.message : null;
  return (
    <div className="notice pending" role="status">
      <span>
        {updatedAt > 0 ? (
          <>
            Stale since{' '}
            <time dateTime={new Date(updatedAt).toISOString()} title={new Date(updatedAt).toLocaleString()}>
              {formatStaleTime(updatedAt)}
            </time>
          </>
        ) : (
          'Stale since the last load'
        )}
        {message ? ` — refresh failed: ${message}` : ' — refresh failed.'}
      </span>{' '}
      {onRetry ? (
        <Button variant="secondary" type="button" onClick={onRetry} disabled={retrying}>
          {retrying ? 'Retrying…' : 'Retry'}
        </Button>
      ) : null}
    </div>
  );
}

/** The subset of a React Query result that {@link QueryStaleNotice} reads. */
export type StaleQueryState = {
  isRefetchError: boolean;
  dataUpdatedAt: number;
  error: unknown;
  isFetching: boolean;
  refetch: () => unknown;
};

/**
 * {@link StaleDataNotice} for a query that failed to refresh but still has
 * data. Renders nothing otherwise; a query with no data shows its own error.
 */
export function QueryStaleNotice({ query }: { query: StaleQueryState }) {
  if (!query.isRefetchError) return null;
  return (
    <StaleDataNotice
      updatedAt={query.dataUpdatedAt}
      error={query.error}
      onRetry={() => {
        void query.refetch();
      }}
      retrying={query.isFetching}
    />
  );
}
