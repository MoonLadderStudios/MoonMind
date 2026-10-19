import {
  useEffect,
  useId,
  useRef,
  useState,
  type FormEvent,
  type KeyboardEvent,
  type ReactNode,
} from 'react';

import { focusableElementsWithin, trapTabWithin } from '../lib/focusTrap';
import { Button } from './dashboard/Button';

type DashboardActionDialogProps = {
  open: boolean;
  title: string;
  subject: string;
  compactId?: string | null;
  consequence: ReactNode;
  confirmLabel: string;
  confirmPending?: boolean;
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

export type ActionConfirmationRequest = {
  title: string;
  subject: string;
  compactId?: string | null | undefined;
  /** One line stating what happens. */
  consequence: ReactNode;
  confirmLabel: string;
  danger?: boolean | undefined;
  onConfirm: () => void;
};

/**
 * Holds one pending confirmation and renders its dialog. A guarded control
 * calls `requestConfirmation`; its action runs only from the dialog's confirm
 * button. Render `confirmationDialog` anywhere in the component.
 */
export function useActionConfirmation(): {
  requestConfirmation: (request: ActionConfirmationRequest) => void;
  confirmationDialog: ReactNode;
} {
  const [pending, setPending] = useState<ActionConfirmationRequest | null>(null);
  const confirmationDialog = pending ? (
    <DashboardActionDialog
      open
      title={pending.title}
      subject={pending.subject}
      compactId={pending.compactId ?? null}
      consequence={pending.consequence}
      confirmLabel={pending.confirmLabel}
      cancelLabel="Go back"
      danger={pending.danger ?? false}
      onCancel={() => setPending(null)}
      onConfirm={() => {
        setPending(null);
        pending.onConfirm();
      }}
    />
  ) : null;
  return { requestConfirmation: setPending, confirmationDialog };
}

export function DashboardActionDialog({
  open,
  title,
  subject,
  compactId,
  consequence,
  confirmLabel,
  confirmPending = false,
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
  const disabledReasonId = useId();
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
      const [firstFocusable] = focusableElementsWithin(dialogRef.current);
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
  const confirmDisabled = Boolean(confirmPending || disabledReason || valueMissing || confirmationMissing);

  const onKeyDown = (event: KeyboardEvent<HTMLDivElement>) => {
    if (event.key === 'Escape' && (!destructive || nonDestructiveEscapeClose)) {
      event.preventDefault();
      onCancel();
      return;
    }
    trapTabWithin(dialogRef.current, event);
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
            <Button
              variant="secondary"
              className="dashboard-dialog-close"
              aria-label={`Close ${title}`}
              onClick={onCancel}
            >
              x
            </Button>
          </div>
          <div id={descriptionId} className="dashboard-dialog-consequence">
            {consequence}
          </div>
          {disabledReason ? (
            <div id={disabledReasonId} className="notice pending" role="note">
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
              <Button variant="secondary" size="sm" onClick={() => void copyDiagnostics()}>
                {copyLabel}
              </Button>
            </div>
          ) : null}
          <div className="dashboard-dialog-actions">
            <Button variant="secondary" onClick={onCancel}>
              {cancelLabel}
            </Button>
            <Button
              type="submit"
              variant={danger ? 'danger' : 'primary'}
              disabled={confirmDisabled}
              aria-busy={confirmPending}
              aria-describedby={disabledReason ? disabledReasonId : undefined}
            >
              {confirmLabel}
            </Button>
          </div>
        </form>
      </div>
    </div>
  );
}
