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
  ['scheduled', 'dispatching', 'awaiting_confirmation', 'delivery_unknown', 'retry_pending', 'missed', 'expired'].includes(item.status);

export const needsReminderAction = (item: Reminder) =>
  ['awaiting_confirmation', 'dispatching', 'delivery_unknown', 'retry_pending', 'missed', 'expired'].includes(item.status);

export function reminderPhase(item: Reminder, now: number) {
  if (item.status === 'retry_pending') return `等待第 ${Math.min(3, item.deliveryCount + 1)} 轮提醒 · ${Math.max(0, Math.ceil((item.nextAttemptAt-now)/1000))} 秒后`;
  if (item.status === 'expired' || item.status === 'missed') return item.deliveryCount >= 3 ? '已逾期 · 三轮提醒均未回应' : '已逾期 · 请确认或重新安排';
  if (item.status === 'delivery_unknown') return `${item.lastError?.includes('未成功送达') ? '未送达' : '送达待核实'} · 请确认或重新安排`;
  if (item.status === 'awaiting_confirmation') return `第 ${Math.max(1,item.deliveryCount)} / 3 轮 · ${Math.max(0,Math.ceil((item.ackDeadline-now)/1000))} 秒内回应`;
  return `第 ${Math.min(3,item.deliveryCount+1)} / 3 轮 · 正在准备通知`;
}

// A retry that changes dueAt is a new alert. Version changes while speech is
// sending or an ACK deadline expires belong to the same alert.
export function reconcilePopup(previous: Reminder[], page: ReminderPage, dismissed: ReadonlySet<string>) {
  const retained = new Set(previous.map(popupKey));
  return page.items.filter(item => {
    if (dismissed.has(popupKey(item)) || item.status === 'completed' || item.status === 'cancelled') return false;
    if (!Number.isFinite(item.dueAt) || item.dueAt > page.serverNow) return false;
    return retained.has(popupKey(item)) || ['scheduled', 'dispatching', 'awaiting_confirmation', 'delivery_unknown'].includes(item.status);
  }).map(item => {
    const newer = previous.find(old => popupKey(old) === popupKey(item) && old.version > item.version);
    return newer || item; // A read started before presentation must not rewind the visible round.
  }).sort((a, b) => a.dueAt - b.dueAt || a.id.localeCompare(b.id));
}
