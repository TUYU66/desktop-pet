// Retired todo API: retained for historical reference; no product page imports it.
import request from './request';
import type { Reminder, ReminderPlan } from './reminder';

export type TaskStatus = 'open' | 'completed' | 'cancelled' | 'expired';
export type TaskScope = 'all' | 'today' | 'tomorrow' | 'upcoming' | 'overdue' | 'undated' | 'completed' | 'cancelled' | 'expired';
export type TaskRecurrence = 'once' | 'daily' | 'weekly' | 'weekdays';
export type TaskReminder = Reminder & { taskId: string | null; originalDueAt: number };
export type TaskPlan = ReminderPlan & {
  taskEnabled: boolean;
  taskDateEnabled: boolean;
  deadlineOffset: number | null;
  remindOffset: number | null;
};
export interface Task {
  id: string;
  title: string;
  status: TaskStatus;
  version: number;
  taskDate: string | null;
  deadlineAt: number | null;
  remindAt: number | null;
  deviceId: string | null;
  reminderId: string | null;
  reminderEnabled: boolean;
  reminder: TaskReminder | null;
  planId: string | null;
  occurrenceAt: number | null;
  virtual: boolean;
  createdAt: number;
  updatedAt: number;
}
export interface TaskPage {
  items: Task[];
  total: number;
  offset: number;
  limit: number;
  hasMore: boolean;
  serverNow: number;
  timeZone: 'Asia/Shanghai';
  legacyReminders: TaskReminder[];
  plans: TaskPlan[];
  unansweredCount: number;
}
export interface TaskFields {
  title: string;
  taskDate: string | null;
  deadlineAt: number | null;
  reminderEnabled: boolean;
  remindAt: number | null;
  deviceId: string | null;
}
export type CreateTaskRequest = TaskFields & { requestId: string; recurrence: TaskRecurrence };
export type TaskAction = 'edit' | 'complete' | 'restore' | 'cancel' | 'delete' | 'disable_reminder' | 'confirm' | 'snooze';
export type TaskActionRequest = Partial<TaskFields> & {
  requestId: string;
  version: number;
  action: TaskAction;
  reminderVersion?: number;
  planId?: string;
  occurrenceAt?: number;
};
export type TaskPlanAction = 'pause' | 'resume' | 'cancel';
export type TaskPlanActionRequest = { requestId: string; version: number; action: TaskPlanAction };
export interface TaskReceipt { item: Task | null; plan: TaskPlan | null; remindersStopped: boolean }

export function getTasks(params: { scope?: TaskScope; offset?: number; limit?: number; date?: string } = {}, signal?: AbortSignal) {
  return request.get<unknown, { data: TaskPage }>('/tasks', { params, signal });
}
export function createTask(data: CreateTaskRequest) {
  return request.post<unknown, { data: TaskReceipt }>('/tasks', data);
}
export function taskAction(id: string, data: TaskActionRequest) {
  return request.post<unknown, { data: TaskReceipt }>(`/tasks/${encodeURIComponent(id)}/actions`, data);
}
export function taskPlanAction(id: string, data: TaskPlanActionRequest) {
  return request.post<unknown, { data: TaskReceipt }>(`/tasks/plans/${encodeURIComponent(id)}/actions`, data);
}
