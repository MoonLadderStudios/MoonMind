import { useMemo, type CSSProperties } from 'react';

import {
  integrationProviderStatusPillView,
  stepLedgerStatusPillView,
  workflowLifecycleStatusPillView,
  type ExecutionStatusPillOptions,
  type StatusPillView,
} from '../utils/executionStatusPillClasses';

type GlyphStyle = CSSProperties & {
  '--mm-letter-count'?: number;
  '--mm-letter-index'?: number;
};

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

function StatusPill({ view }: { view: StatusPillView }) {
  const { label, pillProps } = view;
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

type DomainStatusPillProps = ExecutionStatusPillOptions & {
  status: string | null | undefined;
};

export function WorkflowLifecycleStatusPill({ status, enableMotion = true }: DomainStatusPillProps) {
  return <StatusPill view={workflowLifecycleStatusPillView(status, { enableMotion })} />;
}

export function StepLedgerStatusPill({ status, enableMotion = true }: DomainStatusPillProps) {
  return <StatusPill view={stepLedgerStatusPillView(status, { enableMotion })} />;
}

export function IntegrationProviderStatusPill({ status, enableMotion = true }: DomainStatusPillProps) {
  return <StatusPill view={integrationProviderStatusPillView(status, { enableMotion })} />;
}
