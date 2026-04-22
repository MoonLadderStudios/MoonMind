import type { CSSProperties, HTMLAttributes, ReactNode } from 'react';

export type MaskedConicBorderBeamSpeed = 'slow' | 'medium' | 'fast' | `${number}s` | `${number}ms` | number;
export type MaskedConicBorderBeamIntensity = 'subtle' | 'normal' | 'vivid';
export type MaskedConicBorderBeamTheme = 'neutral' | 'brand' | 'success' | 'custom';
export type MaskedConicBorderBeamDirection = 'clockwise' | 'counterclockwise';
export type MaskedConicBorderBeamTrail = 'none' | 'soft' | 'defined';
export type MaskedConicBorderBeamGlow = 'off' | 'low' | 'medium';
export type MaskedConicBorderBeamVariant = 'precision' | 'energized' | 'dualPhase';
export type MaskedConicBorderBeamReducedMotion = 'auto' | 'off' | 'minimal';

export interface MaskedConicBorderBeamProps extends HTMLAttributes<HTMLDivElement> {
  active?: boolean;
  borderRadius?: string | number;
  borderWidth?: string | number;
  speed?: MaskedConicBorderBeamSpeed;
  intensity?: MaskedConicBorderBeamIntensity;
  theme?: MaskedConicBorderBeamTheme;
  direction?: MaskedConicBorderBeamDirection;
  trail?: MaskedConicBorderBeamTrail;
  glow?: MaskedConicBorderBeamGlow;
  variant?: MaskedConicBorderBeamVariant;
  reducedMotion?: MaskedConicBorderBeamReducedMotion;
  children?: ReactNode;
}

export const MASKED_CONIC_BORDER_BEAM_TRACEABILITY = {
  jiraIssueKeys: ['MM-465', 'MM-466', 'MM-467'],
  designRequirements: [
    'DESIGN-REQ-001',
    'DESIGN-REQ-002',
    'DESIGN-REQ-003',
    'DESIGN-REQ-004',
    'DESIGN-REQ-005',
    'DESIGN-REQ-006',
    'DESIGN-REQ-007',
    'DESIGN-REQ-008',
    'DESIGN-REQ-009',
    'DESIGN-REQ-010',
    'DESIGN-REQ-011',
    'DESIGN-REQ-012',
    'DESIGN-REQ-016',
  ],
} as const;

function cssLength(value: string | number): string {
  if (typeof value === 'number') {
    return `${value}px`;
  }
  return value;
}

function cssSpeed(value: MaskedConicBorderBeamSpeed): string {
  if (typeof value === 'number') {
    return `${value}s`;
  }

  if (value === 'slow') return '4.8s';
  if (value === 'fast') return '2.8s';
  if (value === 'medium') return '3.6s';
  return value;
}

export function MaskedConicBorderBeam({
  active = true,
  borderRadius = '16px',
  borderWidth = '1.5px',
  speed = 'medium',
  intensity = 'normal',
  theme = 'neutral',
  direction = 'clockwise',
  trail = 'soft',
  glow = 'low',
  variant = 'precision',
  reducedMotion = 'auto',
  className = '',
  style,
  children,
  ...rest
}: MaskedConicBorderBeamProps) {
  const resolvedBorderRadius = cssLength(borderRadius);
  const resolvedBorderWidth = cssLength(borderWidth);
  const beamStyle = {
    ...style,
    '--beam-border-radius': resolvedBorderRadius,
    '--beam-border-width': resolvedBorderWidth,
    '--beam-head-arc': '12deg',
    '--beam-tail-arc': '28deg',
    '--beam-speed': cssSpeed(speed),
  } as CSSProperties;

  const classes = ['masked-conic-border-beam', className].filter(Boolean).join(' ');
  const showGlow = glow !== 'off';

  return (
    <div
      {...rest}
      className={classes}
      data-active={String(active)}
      data-intensity={intensity}
      data-theme={theme}
      data-direction={direction}
      data-trail={trail}
      data-glow={glow}
      data-variant={variant}
      data-reduced-motion={reducedMotion}
      style={beamStyle}
    >
      <span
        aria-hidden="true"
        className="masked-conic-border-beam__layer"
        data-testid="masked-conic-border-beam-layer"
      />
      {variant === 'dualPhase' ? (
        <span
          aria-hidden="true"
          className="masked-conic-border-beam__companion"
          data-testid="masked-conic-border-beam-companion"
        />
      ) : null}
      {showGlow ? (
        <span
          aria-hidden="true"
          className="masked-conic-border-beam__glow"
          data-testid="masked-conic-border-beam-glow"
        />
      ) : null}
      <div className="masked-conic-border-beam__content">{children}</div>
    </div>
  );
}
