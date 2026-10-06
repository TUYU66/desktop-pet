import test from 'node:test';
import assert from 'node:assert/strict';
import type { Reminder, ReminderPage } from '../src/api/reminder.ts';
import { canConfirmPopup, popupKey, reconcilePopup, validPopupPage } from '../src/layouts/reminderPopupState.ts';

const item = (id: string, status: Reminder['status'] = 'scheduled', dueAt = 1000): Reminder => ({
  id, status, dueAt, title: id, deviceId: 'device', version: 1, deliveryCount: 0,
  ackDeadline: 0, nextAttemptAt: dueAt, originalDueAt: dueAt, scheduledAt: dueAt, requestedAt: dueAt,
});
const page = (...items: Reminder[]): ReminderPage => ({ items, plans: [], serverNow: 2000, unansweredCount: 0, itemTotal: items.length });

test('group all currently due reminders and merge another that arrives while open', () => {
  const first = reconcilePopup([], page(item('one'), item('future', 'scheduled', 3000)), new Set());
  assert.deepEqual(first.map(row => row.id), ['one']);
  const next = reconcilePopup(first, page(item('one', 'awaiting_confirmation'), item('two', 'dispatching')), new Set());
  assert.deepEqual(next.map(row => row.id), ['one', 'two']);
  assert.equal(next[0].status, 'awaiting_confirmation');
  assert.equal(canConfirmPopup(next[1]), false);
});

test('closing suppresses the same due time, not a later snooze or another reminder', () => {
  const closed = item('one', 'dispatching');
  const dismissed = new Set([popupKey(closed)]);
  assert.deepEqual(reconcilePopup([], page(item('one', 'awaiting_confirmation')), dismissed), []);
  assert.deepEqual(reconcilePopup([], page(item('one', 'scheduled', 1900), item('two')), dismissed).map(row => row.id), ['two', 'one']);
  assert.deepEqual(reconcilePopup([], page(item('one', 'scheduled', 3000)), dismissed), []);
});

test('an open unanswered alert survives expiry, but completion and cancellation remove it', () => {
  const previous = [item('one', 'awaiting_confirmation'), item('two', 'awaiting_confirmation')];
  const refreshed = reconcilePopup(previous, page(item('one', 'expired'), item('two', 'completed')), new Set());
  assert.equal(refreshed.length, 1);
  assert.equal(refreshed[0].status, 'expired');
  assert.equal(canConfirmPopup(refreshed[0]), true);
  assert.deepEqual(reconcilePopup(refreshed, page(item('one', 'cancelled')), new Set()), []);
});

test('malformed read cannot replace an established popup', () => {
  assert.equal(validPopupPage(page(item('one'))), true);
  assert.equal(validPopupPage({ ...page(), items: [null] }), false);
  assert.equal(validPopupPage({ ...page(), serverNow: NaN }), false);
  assert.equal(validPopupPage(page({ ...item('one'), version: -1 })), false);
});
