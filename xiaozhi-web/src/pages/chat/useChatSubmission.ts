import { useCallback, useEffect, useRef, useState } from 'react';
import { getChatRequestStatus, sendMessage } from '../../api/chat';
import type { ChatRequestReceipt, ChatRequestStatus } from '../../api/chat';
import { ApiError } from '../../api/request';
import { missingChatEndpoint } from './useChatHistory';
import { isChatControl, submissionBlocks } from './chatControl';
export { submissionBlocks } from './chatControl';

export const requestLabels: Record<ChatRequestStatus, string> = {
  accepted: '已提交', processing: '正在处理', responding: '正在回复',
  waiting_action: '等待动作确认',
  server_done: '回复处理已结束', failed: '处理失败', unknown: '结果待核实',
};
export interface Submission extends ChatRequestReceipt {
  session: string; text: string; posting: boolean; accepted: boolean;
  tracking: boolean; stopPolling: boolean; started: number;
}
type Context = { value: Submission; onAccepted: () => void; notified: boolean };
const knownStatuses = new Set(Object.keys(requestLabels));
const terminal = new Set<ChatRequestStatus>(['server_done', 'failed', 'unknown']);

function receipt(value: unknown, requestId: string): ChatRequestReceipt {
  const data = value as ChatRequestReceipt | null;
  if (!data || data.requestId !== requestId || !knownStatuses.has(data.status)
    || (data.message !== undefined && typeof data.message !== 'string')) throw new Error('提交回执不完整，结果待核实');
  return data;
}

function createRequestId() {
  if (typeof crypto.randomUUID === 'function') return crypto.randomUUID();
  // getRandomValues also works on LAN HTTP pages where randomUUID may be absent.
  return Array.from(crypto.getRandomValues(new Uint8Array(16)), byte => byte.toString(16).padStart(2, '0')).join('');
}

export function useChatSubmission(wakeWord = '') {
  const [record, setRecord] = useState<Submission | null>(null);
  const [interrupted, setInterrupted] = useState<Submission | null>(null);
  const context = useRef<Context | null>(null);
  const alive = useRef(false);
  const postingLock = useRef(false);
  const check = useRef<() => Promise<void>>(async () => {});

  useEffect(() => { alive.current = true; return () => { alive.current = false; }; }, []);
  const publish = useCallback((ctx: Context, changes: Partial<Submission>) => {
    if (!alive.current || context.current !== ctx) return;
    ctx.value = { ...ctx.value, ...changes };
    setRecord(ctx.value);
  }, []);
  const applyReceipt = useCallback((ctx: Context, data: ChatRequestReceipt) => {
    if (!alive.current || context.current !== ctx) return;
    publish(ctx, { ...data, accepted: true, tracking: true, stopPolling: terminal.has(data.status) });
    if (!ctx.notified && data.status !== 'unknown' && data.status !== 'failed') {
      ctx.notified = true;
      ctx.onAccepted();
    }
  }, [publish]);

  const submit = useCallback(async (text: string, device: string, session: string, onAccepted: () => void) => {
    const previous = context.current?.value || null;
    if (postingLock.current || submissionBlocks(previous, session, text, wakeWord)) return;
    postingLock.current = true;
    if (previous?.session === session && (previous.status === 'unknown'
      || (isChatControl(text, wakeWord) && submissionBlocks(previous, session)
        && !isChatControl(previous.text, wakeWord)))) {
      // Replacing the polling context must not hide that an earlier tool or
      // physical action may already have executed. Late receipts cannot publish.
      setInterrupted({ ...previous });
    }
    const ctx: Context = { value: {
      requestId: createRequestId(), session, text, status: 'accepted', message: '正在提交消息…',
      accepted: false, posting: true, tracking: false, stopPolling: false, started: Date.now(),
    }, onAccepted, notified: false };
    context.current = ctx;
    publish(ctx, {});
    try {
      const result = await sendMessage(text, device || undefined, session, ctx.value.requestId);
      if (!alive.current || context.current !== ctx) return;
      if (result.data == null) {
        // A successful legacy receipt confirms submission only, with no status endpoint.
        publish(ctx, { accepted: true, status: 'accepted', tracking: false, stopPolling: true,
          message: result.msg && !['消息已发送', '消息已提交'].includes(result.msg) ? result.msg : '消息已提交，当前服务暂未提供后续处理状态。' });
        ctx.notified = true;
        ctx.onAccepted();
      } else {
        applyReceipt(ctx, receipt(result.data, ctx.value.requestId));
      }
    } catch (error) {
      if (!alive.current || context.current !== ctx) return;
      const code = error instanceof ApiError ? Number(error.code ?? error.status) : NaN;
      const rejected = [400, 401, 403, 404, 409].includes(code);
      publish(ctx, { status: rejected ? 'failed' : 'unknown', tracking: !rejected,
        stopPolling: rejected || code === 410,
        message: error instanceof Error ? error.message : '提交结果未确认，请核实；不会自动重发。' });
    } finally {
      postingLock.current = false;
      publish(ctx, { posting: false });
    }
  }, [applyReceipt, publish, wakeWord]);

  useEffect(() => {
    const ctx = context.current;
    if (!ctx || ctx.value.requestId !== record?.requestId) { check.current = async () => {}; return; }
    const controller = new AbortController();
    let busy = false;
    const current = () => alive.current && context.current === ctx && !controller.signal.aborted;
    check.current = async () => {
      if (!current() || busy || ctx.value.posting || !ctx.value.tracking || document.hidden) return;
      busy = true;
      try {
        const result = await getChatRequestStatus(ctx.value.requestId, controller.signal);
        if (current()) applyReceipt(ctx, receipt(result.data, ctx.value.requestId));
      } catch (error) {
        if (!current()) return;
        const code = error instanceof ApiError ? Number(error.code) : NaN;
        const absent = code === 404 || code === 410;
        publish(ctx, { status: 'unknown', stopPolling: absent || missingChatEndpoint(error),
          message: missingChatEndpoint(error) ? '当前服务尚未提供回执查询，结果待核实；不会自动重发。'
            : error instanceof Error ? error.message : '处理状态查询失败，结果待核实；不会自动重发。' });
      } finally { busy = false; }
    };
    const poll = () => {
      if (ctx.value.stopPolling || !ctx.value.tracking || ctx.value.posting || document.hidden) return;
      if (Date.now() - ctx.value.started >= 180000) {
        publish(ctx, { status: 'unknown', stopPolling: true, message: '跟踪已超时，请核实机器人和聊天记录；不会自动重发。' });
      } else { void check.current(); }
    };
    const visible = () => { if (!document.hidden) poll(); };
    poll();
    const timer = window.setInterval(poll, 2000);
    document.addEventListener('visibilitychange', visible);
    return () => { controller.abort(); window.clearInterval(timer); document.removeEventListener('visibilitychange', visible); };
  }, [record?.requestId, record?.posting, record?.tracking, record?.stopPolling, applyReceipt, publish]);

  const checkStatus = useCallback(() => check.current(), []);
  const finishChecking = useCallback(() => {
    if (context.current?.value.posting || context.current?.value.status !== 'unknown') return;
    context.current = null;
    setRecord(null);
  }, []);
  const dismissInterrupted = useCallback(() => setInterrupted(null), []);
  return { record, interrupted, submit, checkStatus, finishChecking, dismissInterrupted };
}
