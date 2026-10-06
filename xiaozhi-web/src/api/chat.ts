import request from './request';

export function getSessions(roleId?: string) {
  const params = roleId ? { roleId } : {};
  return request.get('/chat/sessions', { params });
}

export function getMessages(sessionId: string, signal?: AbortSignal) {
  return request.get<unknown, ChatResult<ChatMessage[]>>(`/chat/sessions/${encodeURIComponent(sessionId)}/messages`, { signal });
}

export interface ChatMessage {
  id: number | string;
  sessionId: string;
  chatType: string | null;
  content: string;
  createDate: string;
}

export interface ChatMessagePage {
  items: ChatMessage[];
  nextBeforeCursor: string;
  nextAfterCursor: string;
  hasMoreBefore: boolean;
  hasMoreAfter: boolean;
}

export type ChatRequestStatus = 'accepted' | 'processing' | 'responding' | 'waiting_action' | 'server_done' | 'failed' | 'unknown';

export interface ChatRequestReceipt {
  requestId: string;
  status: ChatRequestStatus;
  message?: string;
  updatedAt?: number;
}

interface ChatResult<T> { data: T; msg?: string }

export function getMessagePage(sessionId: string, params: {
  beforeCursor?: string; afterCursor?: string; date?: string; limit?: number;
}, signal?: AbortSignal) {
  return request.get<unknown, ChatResult<ChatMessagePage>>(
    `/chat/sessions/${encodeURIComponent(sessionId)}/message-page`, { params, signal },
  );
}

export function getMessageDates(sessionId: string, signal?: AbortSignal) {
  return request.get<unknown, ChatResult<{ dates: string[]; timeZone: string }>>(
    `/chat/sessions/${encodeURIComponent(sessionId)}/message-dates`, { signal },
  );
}

export function getChatRequestStatus(requestId: string, signal?: AbortSignal) {
  return request.get<unknown, ChatResult<ChatRequestReceipt>>(
    `/chat/requests/${encodeURIComponent(requestId)}`, { signal },
  );
}

export function deleteSession(sessionId: string, date?: string) {
  return request.delete(`/chat/sessions/${sessionId}`, { params: date ? { date } : undefined });
}

export function createSession(roleId: string, roleName: string) {
  return request.post('/chat/new', { roleId, roleName });
}

export function getSessionByRole(roleId: string, roleName: string) {
  return request.get(`/chat/session-by-role/${roleId}`, { params: { roleName } });
}

export function sendMessage(text: string, deviceId?: string, sessionId?: string, requestId?: string) {
  return request.post<unknown, ChatResult<ChatRequestReceipt | null>>('/chat/send', { text, deviceId, sessionId, requestId });
}

export function clearMemory(roleId: string) {
  return request.delete(`/chat/memory/${roleId}`);
}

export interface MemoryRecord {
  id: number;
  roleId: string;
  category: string;
  content: string;
  sourceType: 'manual' | 'conversation';
  sourceSessionId?: string;
  createDate?: string;
  updateDate?: string;
  version?: number;
  factJson?: string | null;
}

export function getMemories(roleId: string) {
  return request.get('/memories', { params: { roleId } });
}

export function createMemory(roleId: string, category: string, content: string, key?: string) {
  return request.post('/memories', { roleId, category, content, key });
}

export function updateMemory(id: number, category: string, content: string, version?: number, key?: string) {
  return request.put(`/memories/${id}`, { category, content, version, key });
}

export interface MemoryHistoryRecord {
  id: number;
  oldJson?: string | null;
  newJson?: string | null;
  reason: string;
  source: string;
  version: number;
  changedAt?: string;
}

export function getMemoryHistory(id: number, roleId: string) {
  return request.get<unknown, { data: MemoryHistoryRecord[] }>(`/memories/semantic/${id}/history`, { params: { roleId } });
}

export function deleteMemory(id: number) {
  return request.delete(`/memories/${id}`);
}

export function clearMemories(roleId: string) {
  return request.delete('/memories', { params: { roleId } });
}
