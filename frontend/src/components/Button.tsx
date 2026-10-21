import { useId, type ComponentPropsWithRef } from 'react';

export type ButtonVariant = 'primary' | 'secondary' | 'ghost' | 'danger' | 'icon';
export type ButtonProps = ComponentPropsWithRef<'button'> & {
  variant?: ButtonVariant;
  size?: 'sm' | 'md' | 'lg';
  disabledReason?: string | null | undefined;
};

/** Native button semantics with an explicit, shared visual treatment. */
export function Button({ variant = 'secondary', size = 'md', disabledReason, className, type = 'button', onClick, ...props }: ButtonProps) {
  const reasonId = useId();
  const unavailable = props.disabled || props['aria-disabled'] === true || props['aria-disabled'] === 'true';
  const reason = unavailable ? disabledReason : null;
  return <><button
    {...props}
    type={type}
    title={props.title ?? reason ?? undefined}
    className={['mm-button', `mm-button--${variant}`, `mm-button--${size}`, className].filter(Boolean).join(' ')}
    aria-describedby={[props['aria-describedby'], reason ? reasonId : null].filter(Boolean).join(' ') || undefined}
    onClick={(event) => {
      if (unavailable) {
        event.preventDefault();
        return;
      }
      onClick?.(event);
    }}
  />{reason ? <span className={`mm-button-reason${variant === 'icon' ? ' sr-only' : ''}`} id={reasonId}>{reason}</span> : null}</>;
}
