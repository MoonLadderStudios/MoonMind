import {
  useEffect,
  useId,
  useRef,
  useState,
  type FormEvent,
  type KeyboardEvent,
  type ReactNode,
} from 'react';

type DashboardActionDialogProps = {
  open: boolean;
  title: string;
  subject: string;
  compactId?: string | null;
  consequence: ReactNode;
  confirmLabel: string;
  cancelLabel?: string;
  danger?: boolean;
  destructive?: boolean;
  confirmationText?: string;
  disabledReason?: string | null | undefined;
  error?: string | null | undefined;
  initialValue?: string;
  valueLabel?: string;
  valuePlaceholder?: string;
  valueRequired?: boolean;
  valueMultiline?: boolean;
  nonDestructiveEscapeClose?: boolean;
  onCancel: () => void;
  onConfirm: (value: string) => void;
};

const FOCUSABLE_SELECTOR = [
  'a[href]',
  'button:not([disabled])',
  'textarea:not([disabled])',
  'input:not([disabled])',
  'select:not([disabled])',
  '[tabindex]:not([tabindex="-1"])',
].join(',');

export function DashboardActionDialog({
  open,
  title,
  subject,
  compactId,
  consequence,
  confirmLabel,
  cancelLabel = 'Cancel',
  danger = false,
  destructive = false,
  confirmationText,
  disabledReason,
  error,
  initialValue = '',
  valueLabel,
  valuePlaceholder,
  valueRequired = false,
  valueMultiline = false,
  nonDestructiveEscapeClose = true,
  onCancel,
  onConfirm,
}: DashboardActionDialogProps) {
  const titleId = useId();
  const descriptionId = useId();
  const previousFocusRef = useRef<HTMLElement | null>(null);
  const dialogRef = useRef<HTMLDivElement | null>(null);
  const [value, setValue] = useState(initialValue);
  const [typedConfirmation, setTypedConfirmation] = useState('');
  const [copyLabel, setCopyLabel] = useState('Copy diagnostics');

  useEffect(() => {
    if (!open) return undefined;
    previousFocusRef.current = document.activeElement instanceof HTMLElement
      ? document.activeElement
      : null;
    setValue(initialValue);
    setTypedConfirmation('');
    setCopyLabel('Copy diagnostics');
    const focusTimer = window.setTimeout(() => {
      const firstFocusable = dialogRef.current?.querySelector<HTMLElement>(FOCUSABLE_SELECTOR);
      firstFocusable?.focus();
    }, 0);
    return () => {
      window.clearTimeout(focusTimer);
      previousFocusRef.current?.focus();
    };
  }, [initialValue, open]);

  if (!open) return null;

  const expectedConfirmation = confirmationText?.trim() || '';
  const valueMissing = valueRequired && !value.trim();
  const confirmationMissing = destructive && expectedConfirmation
    ? typedConfirmation.trim() !== expectedConfirmation
    : false;
  const confirmDisabled = Boolean(disabledReason || valueMissing || confirmationMissing);

  const focusableElements = () => Array.from(
    dialogRef.current?.querySelectorAll<HTMLElement>(FOCUSABLE_SELECTOR) ?? [],
  ).filter((element) => !element.hasAttribute('disabled'));

  const onKeyDown = (event: KeyboardEvent<HTMLDivElement>) => {
    if (event.key === 'Escape' && (!destructive || nonDestructiveEscapeClose)) {
      event.preventDefault();
      onCancel();
      return;
    }
    if (event.key !== 'Tab') return;
    const elements = focusableElements();
    if (elements.length === 0) return;
    const first = elements[0];
    const last = elements[elements.length - 1];
    if (!first || !last) return;
    if (event.shiftKey && document.activeElement === first) {
      event.preventDefault();
      last.focus();
    } else if (!event.shiftKey && document.activeElement === last) {
      event.preventDefault();
      first.focus();
    }
  };

  const submit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (confirmDisabled) return;
    onConfirm(value.trim());
  };

  const copyDiagnostics = async () => {
    if (!error) return;
    try {
      await navigator.clipboard?.writeText(error);
      setCopyLabel('Copied');
    } catch {
      setCopyLabel('Copy unavailable');
    }
  };

  return (
    <div className="dashboard-dialog-backdrop">
      <div
        ref={dialogRef}
        className="dashboard-dialog"
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        aria-describedby={descriptionId}
        onKeyDown={onKeyDown}
      >
        <form onSubmit={submit}>
          <div className="dashboard-dialog-header">
            <div>
              <h3 id={titleId}>{title}</h3>
              <p className="dashboard-dialog-subject">
                <span>{subject}</span>
                {compactId ? <code>{compactId}</code> : null}
              </p>
            </div>
            <button
              type="button"
              className="secondary dashboard-dialog-close"
              aria-label={`Close ${title}`}
              onClick={onCancel}
            >
              x
            </button>
          </div>
          <div id={descriptionId} className="dashboard-dialog-consequence">
            {consequence}
          </div>
          {disabledReason ? (
            <div className="notice pending" role="note">
              {disabledReason}
            </div>
          ) : null}
          {valueLabel ? (
            <label className="dashboard-dialog-field">
              <span>{valueLabel}</span>
              {valueMultiline ? (
                <textarea
                  value={value}
                  placeholder={valuePlaceholder}
                  onChange={(event) => setValue(event.currentTarget.value)}
                  rows={4}
                  required={valueRequired}
                />
              ) : (
                <input
                  value={value}
                  placeholder={valuePlaceholder}
                  onChange={(event) => setValue(event.currentTarget.value)}
                  required={valueRequired}
                />
              )}
            </label>
          ) : null}
          {destructive && expectedConfirmation ? (
            <label className="dashboard-dialog-field">
              <span>Type {expectedConfirmation} to confirm</span>
              <input
                value={typedConfirmation}
                onChange={(event) => setTypedConfirmation(event.currentTarget.value)}
                aria-label={`Type ${expectedConfirmation} to confirm`}
              />
            </label>
          ) : null}
          {error ? (
            <div className="notice error dashboard-dialog-error" role="alert">
              <span>{error}</span>
              <button type="button" className="secondary" onClick={() => void copyDiagnostics()}>
                {copyLabel}
              </button>
            </div>
          ) : null}
          <div className="dashboard-dialog-actions">
            <button type="button" className="secondary" onClick={onCancel}>
              {cancelLabel}
            </button>
            <button
              type="submit"
              className={danger ? 'button dashboard-dialog-danger' : 'button'}
              disabled={confirmDisabled}
            >
              {confirmLabel}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}
