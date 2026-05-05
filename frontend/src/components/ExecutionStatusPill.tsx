import { useMemo, type CSSProperties } from 'react';

import { executionStatusPillProps } from '../utils/executionStatusPillClasses';
import { formatStatusLabel } from '../utils/formatters';

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

function visibleStatusLabel(status: string | null | undefined): string {
  return formatStatusLabel(status);
}

export function ExecutionStatusPill({ status }: { status: string | null | undefined }) {
  const label = visibleStatusLabel(status);
  const pillProps = executionStatusPillProps(status);
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
