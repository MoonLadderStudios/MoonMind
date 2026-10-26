// Thin MoonMind context bar rendered above the native Omnigent chat application
// (MoonLadderStudios/MoonMind#3639). It must stay visually subordinate to the
// native application and must not duplicate the native session header or
// control cluster (composer, queue, approvals, tools, file rail, terminals).
// The page header already shows the workflow title and status, and the detail
// tabs own navigation, so the bar carries only chat-specific context: which
// step's session is open, its session state, and the full-page escape hatch.

interface WorkflowChatContextBarProps {
  /** Human-readable label of the step whose session is bound. */
  stepLabel?: string | null;
  /** Bounded read-only/unavailable status label, or null when writable. */
  statusLabel?: string | null;
  /** Full-page MoonMind-scoped native surface; null hides the action. */
  fullPageHref?: string | null;
}

export function WorkflowChatContextBar({
  stepLabel = null,
  statusLabel = null,
  fullPageHref = null,
}: WorkflowChatContextBarProps) {
  if (!stepLabel && !statusLabel && !fullPageHref) {
    return null;
  }
  return (
    <header className="wf-native-chat__context-bar" aria-label="Workflow chat context">
      <span className="wf-native-chat__meta">
        {stepLabel ? <span className="wf-native-chat__step">{stepLabel}</span> : null}
        {statusLabel ? (
          <span className="wf-native-chat__status-label">{statusLabel}</span>
        ) : null}
      </span>
      {fullPageHref ? (
        <a className="mm-button mm-button--secondary mm-button--sm" href={fullPageHref}>
          Open in Omnigent
        </a>
      ) : null}
    </header>
  );
}
