import { describe, expect, it } from 'vitest';

import { describeCadence, describeCron } from './cronCadence';

describe('describeCron', () => {
  it.each([
    ['* * * * *', 'Every minute'],
    ['*/15 * * * *', 'Every 15 minutes'],
    ['0 * * * *', 'Hourly at :00'],
    ['15 * * * *', 'Hourly at :15'],
    ['30 */6 * * *', 'Every 6 hours at :30'],
    ['0 2 * * *', 'Daily at 02:00'],
    ['5 9,17 * * *', 'Daily at 09:05 and 17:05'],
    ['0 9 * * 1-5', 'Weekdays at 09:00'],
    ['0 10 * * 0,6', 'Weekends at 10:00'],
    ['30 4 * * 1', 'Every Monday at 04:30'],
    ['0 8 * * 1,3,5', 'Every Monday, Wednesday and Friday at 08:00'],
    ['0 8 * * 7', 'Every Sunday at 08:00'],
    ['0 8 * * 2-4', 'Every Tuesday to Thursday at 08:00'],
    ['0 0 1 * *', 'Monthly on day 1 at 00:00'],
    ['45 23 15 * *', 'Monthly on day 15 at 23:45'],
  ])('describes %s as %s', (cron, expected) => {
    expect(describeCron(cron)).toBe(expected);
  });

  it.each([
    'bad cron',
    '0 2 * *',
    '0 2 1 6 *',
    '0 2 1 * 1',
    '0-30 2 * * *',
    '0 2/3 * * *',
    '61 2 * * *',
    '0 25 * * *',
    '',
  ])('returns null for unrecognised expression %j', (cron) => {
    expect(describeCron(cron)).toBeNull();
  });
});

describe('describeCadence', () => {
  it('adds the timezone to a recognised cadence', () => {
    expect(describeCadence('0 2 * * *', 'UTC')).toBe('Daily at 02:00 (UTC)');
  });

  it('falls back to the raw expression when it is not recognised', () => {
    expect(describeCadence('0 2 1 6 *', 'America/New_York')).toBe('0 2 1 6 * (America/New_York)');
  });

  it('omits a blank timezone', () => {
    expect(describeCadence('*/5 * * * *', '  ')).toBe('Every 5 minutes');
  });
});
