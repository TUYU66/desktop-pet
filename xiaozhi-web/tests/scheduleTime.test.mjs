import assert from 'node:assert/strict';
import { nextTrigger } from '../src/pages/reminders/scheduleTime.ts';
const at = text => new Date(`${text}+08:00`).getTime();
const now = at('2026-09-14T17:00:00'); // Monday
assert.equal(nextTrigger({ recurrence: 'daily', time: '18:00' }, now), at('2026-09-14T18:00:00'));
assert.equal(nextTrigger({ recurrence: 'daily', time: '17:00' }, now), at('2026-09-15T17:00:00'));
assert.equal(nextTrigger({ recurrence: 'weekly', weekday: 1, time: '16:00' }, now), at('2026-09-21T16:00:00'));
assert.equal(nextTrigger({ recurrence: 'weekly', weekday: 0, time: '08:00' }, now), at('2026-09-20T08:00:00'));
assert.equal(nextTrigger({ recurrence: 'weekdays', time: '09:00' }, at('2026-09-18T10:00:00')), at('2026-09-21T09:00:00'));
assert.equal(nextTrigger({ recurrence: 'daily', time: '00:01' }, at('2026-12-31T23:59:00')), at('2027-01-01T00:01:00'));
assert.equal(nextTrigger({ recurrence: 'once', date: '2026-09-15T08:00' }, now), at('2026-09-15T08:00:00'));
assert.ok(Number.isNaN(nextTrigger({ recurrence: 'weekly', time: '12:00' }, now)));
assert.ok(Number.isNaN(nextTrigger({ recurrence: 'daily', time: '25:00' }, now)));
console.log('9 schedule time checks passed');
