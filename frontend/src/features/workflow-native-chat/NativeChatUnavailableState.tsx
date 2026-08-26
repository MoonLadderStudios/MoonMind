// Explicit, actionable state panel for every non-mounted native chat status
// (MoonLadderStudios/MoonMind#3639). It never renders a composer or falls back
// to the legacy custom chat implementation — it states the stable reason and
// offers bounded next steps: retry, the authorized full-page native surface, and
// links to captured evidence / diagnostics.
import type { MouseEvent } from 'react';

import {
  nativeChatStateCopy,
  type NativeChatStatus,
} from './chatBindingModel';

interface NativeChatUnavailableStateProps {
  status: NativeChatStatus;
  unavailableReason?: string | null;
  onRetry?: (() => void) | null;
  /** Full-page MoonMind-scoped native surface (never an upstream URL). */
  fullPageHref?: string | null;
  /** MoonMind-scoped evidence subroute href. */
  evidenceHref?: string | null;
  /** MoonMind-scoped diagnostics (Debug) subroute href. */
  diagnosticsHref?: string | null;
  /** SPA navigation for internal (evidence/diagnostics) links. */
  onNavigate?: ((href: string) => void) | null;
}

function isPlainClick(event: MouseEvent<HTMLAnchorElement>): boolean {
  return (
    event.button === 0 &&
    !event.metaKey &&
    !event.ctrlKey &&
    !event.shiftKey &&
    !event.altKey
  );
}

export function NativeChatUnavailableState({
  status,
  unavailableReason = null,
  onRetry = null,
  fullPageHref = null,
  evidenceHref = null,
  diagnosticsHref = null,
  onNavigate = null,
}: NativeChatUnavailableStateProps) {
  const copy = nativeChatStateCopy(status, unavailableReason);

  const handleInternal = (href: string) => (event: MouseEvent<HTMLAnchorElement>) => {
    if (!onNavigate || !isPlainClick(event)) {
      return;
    }
    event.preventDefault();
    onNavigate(href);
  };

  return (
    <div
      className="wf-native-chat__state"
      data-status={status}
      role={copy.role}
      aria-live={copy.role === 'status' ? 'polite' : 'assertive'}
    >
      {copy.role === 'status' ? (
        <span className="wf-native-chat__spinner" aria-hidden="true" />
      ) : null}
      <h3 className="wf-native-chat__state-title">{copy.title}</h3>
      <p className="small wf-native-chat__state-desc">{copy.description}</p>
      <div className="wf-native-chat__state-actions">
        {copy.canRetry && onRetry ? (
          <button type="button" className="button secondary small" onClick={onRetry}>
            Retry
          </button>
        ) : null}
        {fullPageHref ? (
          <a className="button secondary small" href={fullPageHref}>
            Open in Omnigent
          </a>
        ) : null}
        {evidenceHref ? (
          <a
            className="button secondary small"
            href={evidenceHref}
            onClick={handleInternal(evidenceHref)}
          >
            View captured evidence
          </a>
        ) : null}
        {diagnosticsHref ? (
          <a
            className="button secondary small"
            href={diagnosticsHref}
            onClick={handleInternal(diagnosticsHref)}
          >
            Open diagnostics
          </a>
        ) : null}
      </div>
    </div>
  );
}
