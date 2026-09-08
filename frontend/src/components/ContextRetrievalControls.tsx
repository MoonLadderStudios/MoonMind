import { JSX } from 'react';

import {
  ContextRetrievalAuthoring,
  RetrievalCeilings,
} from '../lib/contextRetrievalAuthoring';

interface ContextRetrievalControlsProps {
  value: ContextRetrievalAuthoring;
  onChange: (next: ContextRetrievalAuthoring) => void;
  ceilings?: RetrievalCeilings;
  /** Optional heading/context copy tailored to the hosting surface. */
  description?: string;
  disabled?: boolean;
  /**
   * Retained for caller compatibility; retired surfaces render no
   * initial-injection controls.
   */
  showInitialControls?: boolean;
  idPrefix?: string;
}

/**
 * Retired built-in vector retrieval authoring
 * (MoonLadderStudios/MoonMind#4105).
 *
 * Renders no editable inputs, hidden fields, or cached form state: new writes
 * must not advertise or accept a built-in vector capability. Historical
 * payloads remain readable in detail views through
 * `parseContextRetrievalParameters`; this surface only explains the retirement
 * and points at supported alternatives.
 */
export function ContextRetrievalControls({
  description,
}: ContextRetrievalControlsProps): JSX.Element {
  return (
    <div className="context-retrieval-controls stack" data-testid="context-retrieval-controls">
      {description ? <p className="small">{description}</p> : null}
      <div className="notice warning" role="note">
        <p className="small">
          Built-in vector retrieval has been retired
          (MoonLadderStudios/MoonMind#4105). New runs use explicit attachments,
          artifact refs, or scoped workspace access instead. Historical details
          remain readable; resubmit without retrieval settings to start new work.
        </p>
      </div>
    </div>
  );
}
