import { forwardRef, type ButtonHTMLAttributes } from 'react';

export type ButtonVariant = 'primary' | 'secondary' | 'ghost' | 'danger' | 'icon';
export type ButtonSize = 'sm' | 'md';

export type ButtonStyleOptions = {
  variant?: ButtonVariant | undefined;
  size?: ButtonSize | undefined;
  className?: string | undefined;
};

/**
 * Class names for the dashboard button variants. Links and other elements that
 * must look like a button use this so they share the Button styles.
 *
 * Danger is for irreversible or high-consequence actions only; reversible
 * actions such as pause or drain stay secondary.
 */
export function buttonClassName({ variant = 'primary', size = 'md', className }: ButtonStyleOptions = {}): string {
  const classes = ['button'];
  if (variant !== 'primary') classes.push(`button--${variant}`);
  if (size === 'sm') classes.push('button--sm');
  if (className) classes.push(className);
  return classes.join(' ');
}

export type ButtonProps = ButtonHTMLAttributes<HTMLButtonElement> & {
  variant?: ButtonVariant | undefined;
  size?: ButtonSize | undefined;
};

/** The dashboard's shared button. Defaults to `type="button"` so it never submits a form by accident. */
export const Button = forwardRef<HTMLButtonElement, ButtonProps>(function Button(
  { variant, size, className, type = 'button', ...props },
  ref,
) {
  return <button ref={ref} type={type} className={buttonClassName({ variant, size, className })} {...props} />;
});
