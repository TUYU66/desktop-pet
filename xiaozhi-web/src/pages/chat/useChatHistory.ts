import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { getMessageDates, getMessagePage, getMessages } from '../../api/chat';
import type { ChatMessage, ChatMessagePage } from '../../api/chat';
import { ApiError } from '../../api/request';
import { startPolling } from '../../utils/polling';
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
  const perform = useRef<(operation: Operation, signal?: AbortSignal) => Promise<boolean>>(async () => false);
  const resumePolling = useRef<() => void>(() => {});
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
    const readPage = async (params: { beforeCursor?: string; afterCursor?: string; limit?: number }, signal: AbortSignal) => {
      const result = await getMessagePage(ctx.session, { ...params, date: ctx.date || undefined }, signal);
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
    const checkOutsideDate = async (signal: AbortSignal) => {
      if (!ctx.date || Date.now() - ctx.lastOutsideCheck < 5000) return;
      ctx.lastOutsideCheck = Date.now();
      let tail = latest.current.get(ctx.session);
      if (!tail) {
        const result = await getMessagePage(ctx.session, { limit: 1 }, signal);
        const page = pageData(result.data, ctx.session);
        if (!current() || signal.aborted) return;
        tail = { cursor: page.nextAfterCursor, unread: false };
        latest.current.set(ctx.session, tail);
      }
      let more = true;
      while (more && current() && !signal.aborted && !document.hidden) {
        const cursor = tail.cursor;
        const result = await getMessagePage(ctx.session, { afterCursor: cursor, limit: 200 }, signal);
        const page = pageData(result.data, ctx.session);
        if (!current() || signal.aborted) return;
        if (page.hasMoreAfter && page.nextAfterCursor === cursor) throw new Error('新增记录游标未更新，请刷新核实');
        if (page.items.some(item => item.chatType !== '3' && chatDate(item.createDate) !== ctx.date)) tail.unread = true;
        tail.cursor = page.nextAfterCursor;
        ctx.outsideUnread = tail.unread;
        more = page.hasMoreAfter;
      }
      publish();
    };
    perform.current = async (operation, signal) => {
      if (!current() || !ctx.session || ctx.busy || document.hidden) return false;
      if (operation === 'older' && (!ctx.ready || !ctx.hasMoreBefore)) return false;
      if (!ctx.ready) operation = 'initial';
      ctx.busy = true;
      const readSignal = signal ? AbortSignal.any([ctx.controller.signal, signal]) : ctx.controller.signal;
      const reading = () => current() && !readSignal.aborted;
      publish(operation === 'older' ? { loadingOlder: true } : { loading: !ctx.ready });
      try {
        if (ctx.mode === 'legacy') {
          // Explicit compatibility mode only: refresh manually, never full-history polling.
          const result = await getMessages(ctx.session, readSignal);
          if (!validMessages(result.data) || result.data.some(item => item.sessionId !== ctx.session)) throw new Error('聊天记录响应格式异常');
          if (!reading()) return false;
          ctx.items = mergeMessages([], result.data);
          ctx.ready = true;
          publish({ loading: false, error: '', unsupported: false });
          return true;
        }
        let page = await readPage(operation === 'older' ? { beforeCursor: ctx.before }
          : operation === 'new' ? { afterCursor: ctx.after } : {}, readSignal);
        if (!reading()) return false;
        if (operation === 'older' && page.hasMoreBefore && page.nextBeforeCursor === ctx.before) throw new Error('历史记录游标未更新，请刷新核实');
        apply(page, operation);
        if (operation !== 'older') {
          while (page.hasMoreAfter && reading() && !document.hidden) {
            const cursor = ctx.after;
            page = await readPage({ afterCursor: cursor }, readSignal);
            if (!reading()) return false;
            if (page.hasMoreAfter && page.nextAfterCursor === cursor) throw new Error('新增记录游标未更新，请刷新核实');
            apply(page, 'new');
          }
          await checkOutsideDate(readSignal);
        }
        return true;
      } catch (error) {
        if (reading()) {
          ctx.unsupported = missingChatEndpoint(error);
          publish({ error: errorText(error), unsupported: ctx.unsupported });
        }
        return false;
      } finally {
        ctx.busy = false;
        publish({ loading: false, loadingOlder: false });
        if (current()) resumePolling.current();
      }
    };
    const polling = startPolling(async signal => {
      if (ctx.busy) return;
      return perform.current(ctx.ready ? 'new' : 'initial', signal);
    }, {
      interval: date ? 5000 : 2000, maxInterval: 30000,
      enabled: () => current() && !!ctx.session && !ctx.unsupported && (mode === 'paged' || !ctx.ready),
    });
    resumePolling.current = polling.resume;
    return () => { ctx.controller.abort(); polling.stop(); };

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
  const refresh = useRef<(signal?: AbortSignal) => Promise<boolean | void>>(async () => {});
  const resumePolling = useRef<() => void>(() => {});
  const [state, setState] = useState({ key: '', dates: [] as string[], ready: false, loading: false, error: '', unsupported: false });
  const legacyDates = useMemo(() => [...new Set(legacyItems.filter(item => item.chatType !== '3')
    .map(item => chatDate(item.createDate)).filter(Boolean))].sort(), [legacyItems]);

  useEffect(() => {
    const controller = new AbortController();
    let busy = false;
    let unsupported = false;
    const current = () => !controller.signal.aborted && renderKey.current === key;
    setState({ key, dates: [], ready: false, loading: !!session, error: '', unsupported: false });
    refresh.current = async signal => {
      if (!session || mode === 'legacy' || busy || document.hidden || !current()) return;
      busy = true;
      const readSignal = signal ? AbortSignal.any([controller.signal, signal]) : controller.signal;
      const reading = () => current() && !readSignal.aborted;
      setState(old => ({ ...old, loading: true }));
      try {
        const result = await getMessageDates(session, readSignal);
        const data = result.data;
        if (!data || data.timeZone !== 'Asia/Shanghai' || !Array.isArray(data.dates)
          || data.dates.some(value => typeof value !== 'string' || !/^\d{4}-\d{2}-\d{2}$/.test(value) || chatDate(value) !== value)) {
          throw new Error('日期列表响应不完整，请刷新核实');
        }
        if (reading()) {
          unsupported = false;
          setState({ key, dates: [...new Set(data.dates)].sort(), ready: true, loading: false, error: '', unsupported: false });
        }
      } catch (error) {
        if (reading()) {
          unsupported = missingChatEndpoint(error);
          setState(old => ({ ...old, ready: false, error: errorText(error), unsupported }));
        }
        return false;
      } finally {
        busy = false;
        if (current()) { setState(old => ({ ...old, loading: false })); resumePolling.current(); }
      }
    };
    const polling = startPolling(signal => refresh.current(signal), {
      interval: 15000, maxInterval: 60000,
      enabled: () => current() && !!session && mode !== 'legacy' && !unsupported,
    });
    resumePolling.current = polling.resume;
    return () => { controller.abort(); polling.stop(); };

  }, [key, session, mode]);

  const refreshDates = useCallback(() => refresh.current(), []);
  return mode === 'legacy' ? { dates: legacyDates, ready: legacyReady, loading: !legacyReady, error: '', unsupported: false,
    refresh: refreshDates } : { ...(state.key === key ? state : { dates: [], ready: false, loading: !!session, error: '', unsupported: false }),
    refresh: refreshDates };
}
