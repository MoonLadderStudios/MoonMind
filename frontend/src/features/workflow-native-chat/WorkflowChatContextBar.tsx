// Thin MoonMind workflow context bar rendered above the native Omnigent chat
// application (MoonLadderStudios/MoonMind#3639). It owns workflow context and
// bounded navigation only — it must stay visually subordinate to the native
// application and must not duplicate the native session header or control
// cluster (composer, queue, approvals, tools, file rail, terminals). The
// Workflow Detail page header already shows the title and status, so the bar
// carries only chat-specific context.
import type { MouseEvent } from 'react';

interface WorkflowChatContextBarProps {
  stepLabel?: string | null;
  runtimeLabel?: string | null;
  /** Bounded read-only/unavailable status label, or null when writable. */
  statusLabel?: string | null;
  overviewHref: string;
  evidenceHref: string;
  /** Full-page MoonMind-scoped native surface; null hides the action. */
  fullPageHref?: string | null;
  /** SPA navigation for internal overview/evidence links. */
  onNavigate: (href: string) => void;
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

export function WorkflowChatContextBar({
  stepLabel = null,
  runtimeLabel = null,
  statusLabel = null,
  overviewHref,
  evidenceHref,
  fullPageHref = null,
  onNavigate,
}: WorkflowChatContextBarProps) {
  const handleInternal = (href: string) => (event: MouseEvent<HTMLAnchorElement>) => {
    if (!isPlainClick(event)) {
      return;
    }
    event.preventDefault();
    onNavigate(href);
  };

  return (
    <header className="wf-native-chat__context-bar" aria-label="Workflow chat context">
      <div className="wf-native-chat__identity">
        <span className="wf-native-chat__meta">
          {statusLabel ? (
            <span className="wf-native-chat__status-label">{statusLabel}</span>
          ) : null}
          {stepLabel ? (
            <span className="wf-native-chat__step">{stepLabel}</span>
          ) : null}
          {runtimeLabel ? (
            <span className="wf-native-chat__runtime">{runtimeLabel}</span>
          ) : null}
        </span>
      </div>
      <nav className="wf-native-chat__actions" aria-label="Workflow chat actions">
        <a
          className="mm-button mm-button--secondary mm-button--sm"
          href={overviewHref}
          onClick={handleInternal(overviewHref)}
        >
          Back to Overview
        </a>
        <a
          className="mm-button mm-button--secondary mm-button--sm"
          href={evidenceHref}
          onClick={handleInternal(evidenceHref)}
        >
          View captured evidence
        </a>
        {fullPageHref ? (
          <a className="mm-button mm-button--secondary mm-button--sm" href={fullPageHref}>
            Open in Omnigent
          </a>
        ) : null}
      </nav>
    </header>
  );
}
