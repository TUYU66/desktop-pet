import type { Reminder, ReminderPage } from '../api/reminder';

export function validPopupPage(value: unknown): value is ReminderPage {
  if (!value || typeof value !== 'object') return false;
  const page = value as ReminderPage;
  return Number.isFinite(page.serverNow) && Number.isInteger(page.unansweredCount) && page.unansweredCount >= 0
    && Array.isArray(page.items) && page.items.every(item => item && typeof item.id === 'string'
      && typeof item.title === 'string' && Number.isFinite(item.dueAt) && item.dueAt > 0
      && Number.isInteger(item.version) && item.version >= 0
      && ['scheduled', 'dispatching', 'awaiting_confirmation', 'delivery_unknown', 'completed',
        'cancelled', 'expired', 'retry_pending', 'missed'].includes(item.status));
}

export const popupKey = (item: Reminder) => `${item.id}:${item.dueAt}`;
export const canConfirmPopup = (item: Reminder) =>
  ['awaiting_confirmation', 'delivery_unknown', 'retry_pending', 'missed', 'expired'].includes(item.status);

// A retry that changes dueAt is a new alert. Version changes while speech is
// sending or an ACK deadline expires belong to the same alert.
export function reconcilePopup(previous: Reminder[], page: ReminderPage, dismissed: ReadonlySet<string>) {
  const retained = new Set(previous.map(popupKey));
  return page.items.filter(item => {
    if (dismissed.has(popupKey(item)) || item.status === 'completed' || item.status === 'cancelled') return false;
    if (!Number.isFinite(item.dueAt) || item.dueAt > page.serverNow) return false;
    return retained.has(popupKey(item)) || ['scheduled', 'dispatching', 'awaiting_confirmation', 'delivery_unknown'].includes(item.status);
  }).sort((a, b) => a.dueAt - b.dueAt || a.id.localeCompare(b.id));
}
