import { useMemo, type CSSProperties } from 'react';

import { formatStepStatusLabel, isStepLedgerStatus, stepStatusPillProps } from '../status/stepStatus';
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

  return (
    <span {...pillProps} aria-label={label}>
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

export function WorkflowLifecycleStatusPill({ status, enableMotion = true }: DomainStatusPillProps) {
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

export function IntegrationProviderStatusPill({ status }: DomainStatusPillProps) {
  return (
    <StatusPill
      label={formatIntegrationStatusLabel(status, '-')}
      pillProps={integrationStatusPillProps(status)}
    />
  );
}
