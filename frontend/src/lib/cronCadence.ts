const WEEKDAY_NAMES = ['Sunday', 'Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday'];

function parseNumber(raw: string, min: number, max: number): number | null {
  if (!/^\d+$/.test(raw)) {
    return null;
  }
  const value = Number(raw);
  return value >= min && value <= max ? value : null;
}

function parseList(raw: string, min: number, max: number): number[] | null {
  const values: number[] = [];
  for (const part of raw.split(',')) {
    const value = parseNumber(part, min, max);
    if (value === null) {
      return null;
    }
    values.push(value);
  }
  return values;
}

function parseStep(raw: string, max: number): number | null {
  const match = /^\*\/(\d+)$/.exec(raw);
  if (!match) {
    return null;
  }
  const step = Number(match[1]);
  return step >= 1 && step <= max ? step : null;
}

function joinWords(words: string[]): string {
  if (words.length <= 1) {
    return words.join('');
  }
  return `${words.slice(0, -1).join(', ')} and ${words[words.length - 1]}`;
}

function clock(hour: number, minute: number): string {
  return `${String(hour).padStart(2, '0')}:${String(minute).padStart(2, '0')}`;
}

/** Weekday names for a cron weekday field (0 and 7 are Sunday), or null. */
function weekdayPhrase(raw: string): string | null {
  if (raw === '1-5') {
    return 'Weekdays';
  }
  if (raw === '0,6' || raw === '6,0' || raw === '6,7') {
    return 'Weekends';
  }
  const range = /^(\d)-(\d)$/.exec(raw);
  if (range) {
    const start = parseNumber(range[1] || '', 0, 7);
    const end = parseNumber(range[2] || '', 0, 7);
    if (start === null || end === null || start >= end) {
      return null;
    }
    return `Every ${WEEKDAY_NAMES[start % 7]} to ${WEEKDAY_NAMES[end % 7]}`;
  }
  const days = parseList(raw, 0, 7);
  if (!days) {
    return null;
  }
  return `Every ${joinWords(days.map((day) => WEEKDAY_NAMES[day % 7] || ''))}`;
}

/**
 * A plain-language description of a common five-field cron expression, or
 * null when the expression is not one of the recognised shapes. Callers show
 * the raw expression in that case.
 */
export function describeCron(cron: string): string | null {
  const fields = cron.trim().split(/\s+/);
  if (fields.length !== 5) {
    return null;
  }
  const [minuteField = '', hourField = '', dayField = '', monthField = '', weekdayField = ''] = fields;
  if (monthField !== '*') {
    return null;
  }

  if (hourField === '*' && dayField === '*' && weekdayField === '*') {
    if (minuteField === '*') {
      return 'Every minute';
    }
    const step = parseStep(minuteField, 59);
    if (step !== null) {
      return step === 1 ? 'Every minute' : `Every ${step} minutes`;
    }
  }

  const minute = parseNumber(minuteField, 0, 59);
  if (minute === null) {
    return null;
  }
  const minuteLabel = `:${String(minute).padStart(2, '0')}`;

  if (dayField === '*' && weekdayField === '*') {
    if (hourField === '*') {
      return `Hourly at ${minuteLabel}`;
    }
    const hourStep = parseStep(hourField, 23);
    if (hourStep !== null) {
      return hourStep === 1 ? `Hourly at ${minuteLabel}` : `Every ${hourStep} hours at ${minuteLabel}`;
    }
  }

  const hours = parseList(hourField, 0, 23);
  if (!hours) {
    return null;
  }
  const times = joinWords(hours.map((hour) => clock(hour, minute)));

  if (dayField === '*' && weekdayField === '*') {
    return `Daily at ${times}`;
  }
  if (dayField === '*') {
    const days = weekdayPhrase(weekdayField);
    return days ? `${days} at ${times}` : null;
  }
  if (weekdayField === '*') {
    const day = parseNumber(dayField, 1, 31);
    return day === null ? null : `Monthly on day ${day} at ${times}`;
  }
  return null;
}

/** Readable cadence with its timezone, falling back to the raw expression. */
export function describeCadence(cron: string, timezone: string | null | undefined): string {
  const description = describeCron(cron) ?? cron.trim();
  const zone = timezone?.trim();
  return zone ? `${description} (${zone})` : description;
}
