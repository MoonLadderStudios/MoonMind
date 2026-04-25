import { useMemo, type CSSProperties } from 'react';

import { executionStatusPillProps } from '../utils/executionStatusPillClasses';

const SHIMMER_DURATION_MS = 1650;
const SHIMMER_EDGE_PADDING_CHARS = 3;
const SWEEP_DIRECTION: 'ltr' | 'rtl' = 'rtl';

type GlyphStyle = CSSProperties & {
  '--mm-letter-delay'?: string;
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
  return String(status || '—').trim().replace(/\s+/g, ' ') || '—';
}

export function ExecutionStatusPill({ status }: { status: string | null | undefined }) {
  const label = visibleStatusLabel(status);
  const pillProps = executionStatusPillProps(status);
  const isExecuting = pillProps['data-state'] === 'executing';
  const glyphs = useMemo(() => splitGraphemes(label), [label]);

  if (!isExecuting) {
    return <span {...pillProps}>{label}</span>;
  }

  const count = Math.max(glyphs.length, 1);
  const totalSweepCells = count + SHIMMER_EDGE_PADDING_CHARS * 2;

  return (
    <span {...pillProps} aria-label={label}>
      <span className="status-letter-wave" aria-hidden="true">
        {glyphs.map((glyph, index) => {
          const phaseIndex = SWEEP_DIRECTION === 'ltr' ? index : count - 1 - index;
          const delayMs =
            ((phaseIndex + SHIMMER_EDGE_PADDING_CHARS) / totalSweepCells) * SHIMMER_DURATION_MS;

          return (
            <span
              key={`${index}-${glyph}`}
              className="status-letter-wave__glyph"
              style={{ '--mm-letter-delay': `${Math.round(delayMs)}ms` } as GlyphStyle}
            >
              {glyph === ' ' ? '\u00A0' : glyph}
            </span>
          );
        })}
      </span>
    </span>
  );
}
