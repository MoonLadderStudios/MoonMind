import { useMemo, type CSSProperties } from 'react';

import {
  formatStepExecutionStatusLabel,
  formatStepStatusLabel,
  isStepLedgerStatus,
  stepExecutionStatusPillProps,
  stepStatusPillProps,
} from '../status/stepStatus';
import {
  formatWorkflowStatusLabel,
  isWorkflowLifecycleStatus,
  workflowStatusPillProps,
} from '../status/workflowStatus';
import {
  formatIntegrationStatusLabel,
  integrationStatusPillProps,
  isIntegrationStatus,
} from '../status/integrationStatus';
import type { WorkflowStatusPillOptions } from '../status/workflowStatus';
import {
  CHECKPOINT_BRANCH_STATUS,
  CHECKPOINT_BRANCH_TURN_STATUS,
  DEPENDENCY_OUTCOME_STATUS,
  type StatusVocabulary,
} from '../status/statusVocabulary';

type GlyphStyle = CSSProperties & {
  '--mm-letter-count'?: number;
  '--mm-letter-index'?: number;
};

type ExecutionStatusPillClassProps = Readonly<{
  className: string;
  'data-state'?: string;
  'data-effect'?: 'shimmer-sweep';
  'data-shimmer-label'?: string;
}>;

type SegmenterLike = new (
  locales?: string | string[],
  options?: { granularity?: 'grapheme' | 'word' | 'sentence' },
) => {
  segment(input: string): Iterable<{ segment: string }>;
};

function splitGraphemes(value: string): string[] {
  const maybeIntl = Intl as typeof Intl & { Segmenter?: SegmenterLike };

  if (typeof maybeIntl.Segmenter === 'function') {
    const segmenter = new maybeIntl.Segmenter(undefined, {
      granularity: 'grapheme',
    });

    return Array.from(segmenter.segment(value), (part) => part.segment);
  }

  return Array.from(value);
}

function visibleStatusLabel(status: string | null | undefined): string {
  if (isWorkflowLifecycleStatus(status)) {
    return formatWorkflowStatusLabel(status, '-');
  }
  if (isStepLedgerStatus(status)) {
    return formatStepStatusLabel(status, '-');
  }
  if (isIntegrationStatus(status)) {
    return formatIntegrationStatusLabel(status, '-');
  }
  return formatWorkflowStatusLabel(status, '-');
}

function executionStatusPillProps(
  status: string | null | undefined,
  options: { enableMotion?: boolean } = {},
): ExecutionStatusPillClassProps {
  if (isWorkflowLifecycleStatus(status)) {
    return workflowStatusPillProps(status, options);
  }
  if (isStepLedgerStatus(status)) {
    return stepStatusPillProps(status);
  }
  if (isIntegrationStatus(status)) {
    return integrationStatusPillProps(status);
  }
  return workflowStatusPillProps(status, options);
}

function StatusPill({ label, pillProps }: { label: string; pillProps: ExecutionStatusPillClassProps }) {
  const hasShimmerSweep = pillProps['data-effect'] === 'shimmer-sweep';
  const glyphs = useMemo(() => splitGraphemes(label), [label]);

  if (!hasShimmerSweep) {
    return <span {...pillProps}>{label}</span>;
  }

  const count = Math.max(glyphs.length, 1);

  // The per-letter wave is decorative and hidden from assistive technology.
  // The real label is ordinary text so screen readers announce it (an
  // aria-label on a generic span is often ignored) and copying the pill
  // yields the word rather than one letter per line.
  return (
    <span {...pillProps}>
      <span className="sr-only">{label}</span>
      <span className="status-letter-wave" aria-hidden="true" data-label={label}>
        {glyphs.map((glyph, index) => {
          return (
            <span
              key={`${index}-${glyph}`}
              className="status-letter-wave__glyph"
              style={{ '--mm-letter-count': count, '--mm-letter-index': index } as GlyphStyle}
            >
              {glyph === ' ' ? '\u00A0' : glyph}
            </span>
          );
        })}
      </span>
    </span>
  );
}

export function ExecutionStatusPill({
  status,
  enableMotion = true,
}: {
  status: string | null | undefined;
  enableMotion?: boolean;
}) {
  return (
    <StatusPill
      label={visibleStatusLabel(status)}
      pillProps={executionStatusPillProps(status, { enableMotion })}
    />
  );
}

type DomainStatusPillProps = WorkflowStatusPillOptions & {
  status: string | null | undefined;
};

export function WorkflowLifecycleStatusPill({
  status,
  enableMotion = true,
  completionDisposition,
  objectiveOutcome,
}: DomainStatusPillProps & {
  completionDisposition?: string | null | undefined;
  objectiveOutcome?: string | null | undefined;
}) {
  if (status === 'completed' && completionDisposition === 'gated_continuation') {
    return <StatusPill label="Handed off" pillProps={{ className: 'status status-neutral' }} />;
  }
  if (status === 'completed') {
    switch (objectiveOutcome) {
      case 'idle':
        return <StatusPill label="Idle" pillProps={{ className: 'status status-neutral' }} />;
      case 'failed':
        return <StatusPill label="Failed" pillProps={{ className: 'status status-failed' }} />;
      case 'verification_blocked':
        return <StatusPill label="Verification blocked" pillProps={{ className: 'status status-failed' }} />;
      case 'cancelled':
        return <StatusPill label="Cancelled" pillProps={{ className: 'status status-canceled' }} />;
    }
  }
  return (
    <StatusPill
      label={formatWorkflowStatusLabel(status, '-')}
      pillProps={workflowStatusPillProps(status, { enableMotion })}
    />
  );
}

export function StepLedgerStatusPill({ status }: DomainStatusPillProps) {
  return <StatusPill label={formatStepStatusLabel(status, '-')} pillProps={stepStatusPillProps(status)} />;
}

export function StepExecutionStatusPill({ status }: DomainStatusPillProps) {
  return (
    <StatusPill
      label={formatStepExecutionStatusLabel(status, '-')}
      pillProps={stepExecutionStatusPillProps(status)}
    />
  );
}

export function IntegrationProviderStatusPill({ status }: DomainStatusPillProps) {
  return (
    <StatusPill
      label={formatIntegrationStatusLabel(status, '-')}
      pillProps={integrationStatusPillProps(status)}
    />
  );
}

function VocabularyStatusPill({ vocabulary, status }: { vocabulary: StatusVocabulary; status: string | null | undefined }) {
  return <StatusPill label={vocabulary.label(status)} pillProps={vocabulary.pillProps(status)} />;
}

export function CheckpointBranchStatusPill({ status }: { status: string | null | undefined }) {
  return <VocabularyStatusPill vocabulary={CHECKPOINT_BRANCH_STATUS} status={status} />;
}

export function CheckpointBranchTurnStatusPill({ status }: { status: string | null | undefined }) {
  return <VocabularyStatusPill vocabulary={CHECKPOINT_BRANCH_TURN_STATUS} status={status} />;
}

/**
 * A prerequisite or dependent row: the other workflow's lifecycle state, or the
 * dependency gate's outcome for it (terminated, timed out, bypassed, waiting
 * for a successful rerun).
 */
export function DependencyStatusPill({ status }: { status: string | null | undefined }) {
  if (isWorkflowLifecycleStatus(status)) {
    return <WorkflowLifecycleStatusPill status={status} />;
  }
  return <VocabularyStatusPill vocabulary={DEPENDENCY_OUTCOME_STATUS} status={status} />;
}