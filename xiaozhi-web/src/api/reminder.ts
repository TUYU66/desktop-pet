import request from './request';

export interface Reminder {
  id: string;
  deviceId: string;
  title: string;
  status: 'scheduled' | 'dispatching' | 'awaiting_confirmation' | 'delivery_unknown' | 'completed' | 'cancelled' | 'expired' | 'retry_pending' | 'missed';
  dueAt: number;
  version: number;
  lastError?: string;
  planId?: string;
  deliveryCount: number;
  ackDeadline: number;
  nextAttemptAt: number;
  originalDueAt: number;
  scheduledAt: number;
  requestedAt: number;
}
export interface ReminderPlan { id: string; deviceId: string; title: string; recurrence: string; initialAt: number; nextAt: number; status: string; version: number }
export interface ReminderPage { items: Reminder[]; plans: ReminderPlan[]; serverNow: number; unansweredCount: number; itemTotal: number }
export interface CreationReceipt {
  id: string;
  creation: { requestId: string; deviceId: string; title: string; triggerAt: number | null; recurrence: string; seconds: number | null };
  current: { status: string; title?: string; scheduledAt?: number; nextAt?: number };
}
export const getReminders = (signal?: AbortSignal) => request.get<unknown, { data: ReminderPage }>('/reminders', { signal });
export const deleteReminderHistory = (item: Reminder) => request.delete(`/reminders/${item.id}`, { params: { version: item.version } });
export const getReminderDevices = () => request.get('/reminders/devices');
export const getReminderSettings = () => request.get('/reminders/settings');
export const saveReminderMusicVolume = (musicVolume: number) => request.put('/reminders/settings', { musicVolume });
export const saveReminderMusicSettings = (settings: { musicVolume?: number; musicTrackId?: string | null }) => request.put('/reminders/settings', settings);
export const createReminder = (data: { requestId: string; deviceId: string; title: string; seconds?: number; triggerAt?: number; recurrence?: string }) => request.post<unknown, { data: CreationReceipt }>('/reminders', data);
export const reminderAction = (item: Reminder, action: 'confirm' | 'snooze' | 'cancel', seconds = 300) =>
  request.post(`/reminders/${item.id}/actions`, { version: item.version, action, seconds });
export const changeReminder = (item: Reminder, title: string, triggerAt: number) => request.post(`/reminders/${item.id}/actions`, { version: item.version, action: 'edit', title, triggerAt });
export const planAction = (item: ReminderPlan, action: string, values?: { title: string; triggerAt: number; recurrence: string }) => request.post(`/reminders/plans/${item.id}/actions`, { version: item.version, action, ...values });
