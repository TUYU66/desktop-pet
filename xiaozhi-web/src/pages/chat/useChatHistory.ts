import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { getMessageDates, getMessagePage, getMessages } from '../../api/chat';
import type { ChatMessage, ChatMessagePage } from '../../api/chat';
import { ApiError } from '../../api/request';
import { chatDate, chatTime } from './chatTime';

export type HistoryMode = 'paged' | 'legacy';
type Operation = 'initial' | 'older' | 'new';
type View = {
  key: string; items: ChatMessage[]; ready: boolean; loading: boolean; loadingOlder: boolean;
  error: string; unsupported: boolean; hasMoreBefore: boolean; outsideUnread: boolean;
};
type Context = {
  key: string; session: string; date: string; mode: HistoryMode; controller: AbortController;
  busy: boolean; ready: boolean; items: ChatMessage[]; before: string; after: string;
  hasMoreBefore: boolean; outsideUnread: boolean; lastOutsideCheck: number; unsupported: boolean;
};
const emptyView = (key: string, loading = false): View => ({
  key, items: [], ready: false, loading, loadingOlder: false, error: '', unsupported: false,
  hasMoreBefore: false, outsideUnread: false,
});

// Business 404/410 identifies a request/record; it is not a missing HTTP route.
export const missingChatEndpoint = (error: unknown) => error instanceof ApiError
  && error.status === 404 && error.code === undefined;
const errorText = (error: unknown) => error instanceof Error ? error.message : '聊天记录暂时无法更新，请重试';
const validMessages = (items: unknown): items is ChatMessage[] => Array.isArray(items) && items.every(item =>
  item && (typeof item.id === 'string' || typeof item.id === 'number') && typeof item.sessionId === 'string'
  && (typeof item.chatType === 'string' || item.chatType === null) && typeof item.content === 'string' && typeof item.createDate === 'string');

function pageData(value: unknown, session: string): ChatMessagePage {
  const page = value as ChatMessagePage | null;
  if (!page || !validMessages(page.items) || page.items.some(item => item.sessionId !== session)
    || typeof page.nextBeforeCursor !== 'string' || !page.nextBeforeCursor
    || typeof page.nextAfterCursor !== 'string' || !page.nextAfterCursor
    || typeof page.hasMoreBefore !== 'boolean' || typeof page.hasMoreAfter !== 'boolean') {
    throw new Error('聊天记录响应不完整，请刷新核实');
  }
  return page;
}

function compareIds(a: ChatMessage['id'], b: ChatMessage['id']) {
  const left = String(a), right = String(b);
  if (/^\d+$/.test(left) && /^\d+$/.test(right)) return left.length - right.length || left.localeCompare(right);
  return left.localeCompare(right);
}

function mergeMessages(previous: ChatMessage[], incoming: ChatMessage[]) {
  const byId = new Map(previous.map(item => [String(item.id), item]));
  incoming.forEach(item => byId.set(String(item.id), item));
  const merged = [...byId.values()].sort((a, b) => (chatTime(a.createDate) || 0) - (chatTime(b.createDate) || 0) || compareIds(a.id, b.id));
  return previous.length === merged.length && previous.every((item, index) => {
    const next = merged[index];
    return String(item.id) === String(next.id) && item.content === next.content
      && item.chatType === next.chatType && item.createDate === next.createDate;
  }) ? previous : merged;
}

export function useChatHistory(session: string | null, date: string, mode: HistoryMode, revision: number) {
  const key = JSON.stringify([session, date, mode, revision]);
  const renderKey = useRef(key);
  renderKey.current = key;
  const context = useRef<Context | null>(null);
  const perform = useRef<(operation: Operation) => Promise<boolean>>(async () => false);
  const latest = useRef(new Map<string, { cursor: string; unread: boolean }>());
  const [view, setView] = useState<View>(() => emptyView(key, !!session));

  useEffect(() => {
    const ctx: Context = {
      key, session: session || '', date, mode, controller: new AbortController(), busy: false,
      ready: false, items: [], before: '', after: '', hasMoreBefore: false,
      outsideUnread: date ? latest.current.get(session || '')?.unread || false : false, lastOutsideCheck: 0, unsupported: false,
    };
    context.current = ctx;
    if (!date && session) {
      const tail = latest.current.get(session);
      if (tail) tail.unread = false;
    }
    setView(emptyView(key, !!session));
    const current = () => context.current === ctx && renderKey.current === ctx.key && !ctx.controller.signal.aborted;
    const publish = (extra: Partial<View> = {}) => {
      if (current()) setView(old => ({ ...old, key, items: ctx.items, ready: ctx.ready,
        hasMoreBefore: ctx.hasMoreBefore, outsideUnread: ctx.outsideUnread, ...extra }));
    };
    const readPage = async (params: { beforeCursor?: string; afterCursor?: string; limit?: number }) => {
      const result = await getMessagePage(ctx.session, { ...params, date: ctx.date || undefined }, ctx.controller.signal);
      return pageData(result.data, ctx.session);
    };
    const apply = (page: ChatMessagePage, operation: Operation) => {
      ctx.items = mergeMessages(operation === 'initial' ? [] : ctx.items, page.items);
      if (operation !== 'new') { ctx.before = page.nextBeforeCursor; ctx.hasMoreBefore = page.hasMoreBefore; }
      if (operation !== 'older') ctx.after = page.nextAfterCursor;
      ctx.ready = true;
      ctx.unsupported = false;
      if (!ctx.date) latest.current.set(ctx.session, { cursor: ctx.after, unread: false });
      publish({ loading: false, error: '', unsupported: false });
    };
    const checkOutsideDate = async () => {
      if (!ctx.date || Date.now() - ctx.lastOutsideCheck < 5000) return;
      ctx.lastOutsideCheck = Date.now();
      let tail = latest.current.get(ctx.session);
      if (!tail) {
        const result = await getMessagePage(ctx.session, { limit: 1 }, ctx.controller.signal);
        const page = pageData(result.data, ctx.session);
        if (!current()) return;
        tail = { cursor: page.nextAfterCursor, unread: false };
        latest.current.set(ctx.session, tail);
      }
      let more = true;
      while (more && current() && !document.hidden) {
        const cursor = tail.cursor;
        const result = await getMessagePage(ctx.session, { afterCursor: cursor, limit: 200 }, ctx.controller.signal);
        const page = pageData(result.data, ctx.session);
        if (!current()) return;
        if (page.hasMoreAfter && page.nextAfterCursor === cursor) throw new Error('新增记录游标未更新，请刷新核实');
        if (page.items.some(item => item.chatType !== '3' && chatDate(item.createDate) !== ctx.date)) tail.unread = true;
        tail.cursor = page.nextAfterCursor;
        ctx.outsideUnread = tail.unread;
        more = page.hasMoreAfter;
      }
      publish();
    };
    perform.current = async operation => {
      if (!current() || !ctx.session || ctx.busy || document.hidden) return false;
      if (operation === 'older' && (!ctx.ready || !ctx.hasMoreBefore)) return false;
      if (!ctx.ready) operation = 'initial';
      ctx.busy = true;
      publish(operation === 'older' ? { loadingOlder: true } : { loading: !ctx.ready });
      try {
        if (ctx.mode === 'legacy') {
          // Explicit compatibility mode only: refresh manually, never full-history polling.
          const result = await getMessages(ctx.session, ctx.controller.signal);
          if (!validMessages(result.data) || result.data.some(item => item.sessionId !== ctx.session)) throw new Error('聊天记录响应格式异常');
          if (!current()) return false;
          ctx.items = mergeMessages([], result.data);
          ctx.ready = true;
          publish({ loading: false, error: '', unsupported: false });
          return true;
        }
        let page = await readPage(operation === 'older' ? { beforeCursor: ctx.before }
          : operation === 'new' ? { afterCursor: ctx.after } : {});
        if (!current()) return false;
        if (operation === 'older' && page.hasMoreBefore && page.nextBeforeCursor === ctx.before) throw new Error('历史记录游标未更新，请刷新核实');
        apply(page, operation);
        if (operation !== 'older') {
          while (page.hasMoreAfter && current() && !document.hidden) {
            const cursor = ctx.after;
            page = await readPage({ afterCursor: cursor });
            if (!current()) return false;
            if (page.hasMoreAfter && page.nextAfterCursor === cursor) throw new Error('新增记录游标未更新，请刷新核实');
            apply(page, 'new');
          }
          await checkOutsideDate();
        }
        return true;
      } catch (error) {
        if (current()) {
          ctx.unsupported = missingChatEndpoint(error);
          publish({ error: errorText(error), unsupported: ctx.unsupported });
        }
        return false;
      } finally {
        ctx.busy = false;
        publish({ loading: false, loadingOlder: false });
      }
    };
    const poll = () => { if (current() && mode === 'paged' && !ctx.unsupported) void perform.current(ctx.ready ? 'new' : 'initial'); };
    const visible = () => { if (current() && !document.hidden && !ctx.unsupported && (mode === 'paged' || !ctx.ready)) void perform.current(ctx.ready ? 'new' : 'initial'); };
    void perform.current('initial');
    const timer = window.setInterval(poll, 2000);
    document.addEventListener('visibilitychange', visible);
    return () => { ctx.controller.abort(); window.clearInterval(timer); document.removeEventListener('visibilitychange', visible); };
  }, [key, session, date, mode]);

  // Explicit refresh re-reads the current page, including deletions. Failed reads keep old items.
  const refresh = useCallback(() => perform.current('initial'), []);
  const loadOlder = useCallback(() => perform.current('older'), []);
  const refreshNew = useCallback(() => perform.current('new'), []);
  return { ...(view.key === key ? view : emptyView(key, !!session)), key, refresh, loadOlder, refreshNew };
}

export function useChatDates(session: string | null, mode: HistoryMode, revision: number,
  legacyItems: ChatMessage[], legacyReady: boolean) {
  const key = JSON.stringify([session, mode, revision]);
  const renderKey = useRef(key);
  renderKey.current = key;
  const refresh = useRef<() => Promise<void>>(async () => {});
  const [state, setState] = useState({ key: '', dates: [] as string[], ready: false, loading: false, error: '', unsupported: false });
  const legacyDates = useMemo(() => [...new Set(legacyItems.filter(item => item.chatType !== '3')
    .map(item => chatDate(item.createDate)).filter(Boolean))].sort(), [legacyItems]);

  useEffect(() => {
    const controller = new AbortController();
    let busy = false;
    let unsupported = false;
    const current = () => !controller.signal.aborted && renderKey.current === key;
    setState({ key, dates: [], ready: false, loading: !!session, error: '', unsupported: false });
    refresh.current = async () => {
      if (!session || mode === 'legacy' || busy || document.hidden || !current()) return;
      busy = true;
      setState(old => ({ ...old, loading: true }));
      try {
        const result = await getMessageDates(session, controller.signal);
        const data = result.data;
        if (!data || data.timeZone !== 'Asia/Shanghai' || !Array.isArray(data.dates)
          || data.dates.some(value => typeof value !== 'string' || !/^\d{4}-\d{2}-\d{2}$/.test(value) || chatDate(value) !== value)) {
          throw new Error('日期列表响应不完整，请刷新核实');
        }
        if (current()) {
          unsupported = false;
          setState({ key, dates: [...new Set(data.dates)].sort(), ready: true, loading: false, error: '', unsupported: false });
        }
      } catch (error) {
        if (current()) {
          unsupported = missingChatEndpoint(error);
          setState(old => ({ ...old, ready: false, error: errorText(error), unsupported }));
        }
      } finally {
        busy = false;
        if (current()) setState(old => ({ ...old, loading: false }));
      }
    };
    const visible = () => { if (current() && !document.hidden && !unsupported) void refresh.current(); };
    void refresh.current();
    const timer = window.setInterval(() => { if (current() && !unsupported) void refresh.current(); }, 10000);
    document.addEventListener('visibilitychange', visible);
    return () => { controller.abort(); window.clearInterval(timer); document.removeEventListener('visibilitychange', visible); };
  }, [key, session, mode]);

  const refreshDates = useCallback(() => refresh.current(), []);
  return mode === 'legacy' ? { dates: legacyDates, ready: legacyReady, loading: !legacyReady, error: '', unsupported: false,
    refresh: refreshDates } : { ...(state.key === key ? state : { dates: [], ready: false, loading: !!session, error: '', unsupported: false }),
    refresh: refreshDates };
}
