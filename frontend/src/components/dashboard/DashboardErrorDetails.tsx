import { useId, useState, type ReactNode } from 'react';
import { copyTextToClipboard } from '../../utils/clipboard';

/**
 * Friendly error surface with collapsible technical disclosure (MM-959).
 *
 * Surfaces a human-readable message in the foreground and tucks raw API /
 * network diagnostics (endpoint, status, request id, raw error) behind a
 * disclosure with a copy-diagnostics affordance. Pages should use this instead
 * of dumping endpoint URLs and stack traces directly into the main error text.
 */
export interface DashboardErrorDetailsProps {
  /** Human-readable, non-technical summary of what went wrong. */
  message: ReactNode;
  endpoint?: string | null | undefined;
  status?: number | string | null | undefined;
  requestId?: string | null | undefined;
  /** Raw error / diagnostics text shown inside the disclosure. */
  rawError?: string | null | undefined;
  detailsLabel?: string;
  copyLabel?: string;
  className?: string;
  actions?: ReactNode;
}

function buildDiagnosticsText({
  message,
  endpoint,
  status,
  requestId,
  rawError,
}: {
  message: ReactNode;
  endpoint?: string | null | undefined;
  status?: number | string | null | undefined;
  requestId?: string | null | undefined;
  rawError?: string | null | undefined;
}): string {
  const lines: string[] = [];
  if (typeof message === 'string') {
    lines.push(`Message: ${message}`);
  }
  if (endpoint) {
    lines.push(`Endpoint: ${endpoint}`);
  }
  if (status !== null && status !== undefined && status !== '') {
    lines.push(`Status: ${status}`);
  }
  if (requestId) {
    lines.push(`Request ID: ${requestId}`);
  }
  if (rawError) {
    lines.push(`Raw error: ${rawError}`);
  }
  return lines.join('\n');
}

export function DashboardErrorDetails({
  message,
  endpoint,
  status,
  requestId,
  rawError,
  detailsLabel = 'Technical details',
  copyLabel = 'Copy diagnostics',
  className,
  actions,
}: DashboardErrorDetailsProps) {
  const [open, setOpen] = useState(false);
  const [copied, setCopied] = useState(false);
  const detailsId = useId();

  const hasTechnicalDetail = Boolean(
    endpoint ||
      (status !== null && status !== undefined && status !== '') ||
      requestId ||
      rawError,
  );

  const diagnosticsText = buildDiagnosticsText({
    message,
    endpoint,
    status,
    requestId,
    rawError,
  });

  const handleCopy = async () => {
    const ok = await copyTextToClipboard(diagnosticsText);
    if (ok) {
      setCopied(true);
    }
  };

  const classes = ['dashboard-error-details'];
  if (className) {
    classes.push(className);
  }

  return (
    <div className={classes.join(' ')} role="alert" aria-live="assertive">
      <p className="dashboard-error-details__message">{message}</p>
      {hasTechnicalDetail ? (
        <details
          className="dashboard-error-details__disclosure"
          open={open}
          onToggle={(event) =>
            setOpen((event.target as HTMLDetailsElement).open)
          }
        >
          <summary className="dashboard-error-details__summary">
            {detailsLabel}
          </summary>
          <div className="dashboard-error-details__body" id={detailsId}>
            <dl className="dashboard-error-details__meta">
              {endpoint ? (
                <>
                  <dt>Endpoint</dt>
                  <dd>
                    <code>{endpoint}</code>
                  </dd>
                </>
              ) : null}
              {status !== null && status !== undefined && status !== '' ? (
                <>
                  <dt>Status</dt>
                  <dd>
                    <code>{status}</code>
                  </dd>
                </>
              ) : null}
              {requestId ? (
                <>
                  <dt>Request ID</dt>
                  <dd>
                    <code>{requestId}</code>
                  </dd>
                </>
              ) : null}
            </dl>
            {rawError ? (
              <pre className="dashboard-error-details__raw">{rawError}</pre>
            ) : null}
            <div className="dashboard-error-details__actions">
              <button
                type="button"
                className="secondary small"
                onClick={handleCopy}
              >
                {copied ? 'Copied' : copyLabel}
              </button>
            </div>
          </div>
        </details>
      ) : null}
      {actions ? (
        <div className="dashboard-error-details__page-actions">{actions}</div>
      ) : null}
    </div>
  );
}

export default DashboardErrorDetails;
