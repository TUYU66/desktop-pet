import { useState, useEffect, useRef, useCallback } from 'react';
import { Alert, Button, Spin, Tag } from 'antd';
import { ReloadOutlined } from '@ant-design/icons';
import { Link } from 'react-router-dom';
import { getBotConfig } from '../../api/user';
import { getSessionByRole } from '../../api/chat';
import request, { ApiError } from '../../api/request';
import dayjs from 'dayjs';
import SpotlightPanel from '../../components/SpotlightPanel';
import './dashboard-effects.css';
import DevicePanel from './DevicePanel';

type PreviewMessage = { id: number; content: string; chatType: string; createDate: string };
type PreviewPage = { items: unknown[]; nextBeforeCursor?: string | null; hasMoreBefore: boolean };
const previewPageSize = 20;
const previewCount = 3;

class PreviewPagingUnavailable extends Error {
  constructor() {
    super('当前服务未提供聊天分页查询，请更新 Java 服务后点击刷新。');
    this.name = 'PreviewPagingUnavailable';
  }
}

function visiblePreview(value: unknown): PreviewMessage | null {
  if (!value || typeof value !== 'object') return null;
  const item = value as Record<string, unknown>;
  const chatType = typeof item.chatType === 'number' ? String(item.chatType) : item.chatType;
  if (typeof item.id !== 'number' || !Number.isFinite(item.id) ||
    typeof chatType !== 'string' || chatType === '3' || chatType === 'tool' ||
    typeof item.content !== 'string' || !item.content.replace(/^🔔 /, '').trim() ||
    typeof item.createDate !== 'string' || !dayjs(item.createDate).isValid()) return null;
  return { id: item.id, content: item.content, chatType, createDate: item.createDate };
}

async function recentPreview(sessionId: string, current: () => boolean, signal: AbortSignal): Promise<PreviewMessage[] | null> {
  const messages = new Map<number, PreviewMessage>();
  const cursors = new Set<string>();
  let beforeCursor: string | undefined;
  while (current()) {
    let data: unknown;
    try {
      const response = await request.get<unknown, { data: unknown }>(`/chat/sessions/${encodeURIComponent(sessionId)}/message-page`, {
        params: { limit: previewPageSize, ...(beforeCursor ? { beforeCursor } : {}) }, signal,
      });
      data = response.data;
    } catch (error) {
      if (error instanceof ApiError && [404, 405, 501].includes(Number(error.status === 200 ? error.code : error.status))) {
        throw new PreviewPagingUnavailable();
      }
      throw error;
    }
    if (!current()) return null;
    if (!data || typeof data !== 'object' || !Array.isArray((data as PreviewPage).items) ||
      typeof (data as PreviewPage).hasMoreBefore !== 'boolean') throw new Error('聊天分页响应不完整，请刷新核实。');
    const page = data as PreviewPage;
    if (page.hasMoreBefore && page.items.length === 0) throw new Error('聊天分页响应缺少记录，请刷新核实。');
    for (const item of page.items) {
      const message = visiblePreview(item);
      if (message && !messages.has(message.id)) messages.set(message.id, message);
    }
    if (messages.size >= previewCount || !page.hasMoreBefore) {
      return [...messages.values()]
        .sort((a, b) => dayjs(b.createDate).valueOf() - dayjs(a.createDate).valueOf() || b.id - a.id)
        .slice(0, previewCount);
    }
    const cursor = page.nextBeforeCursor;
    if (typeof cursor !== 'string' || !cursor || cursors.has(cursor)) throw new Error('聊天分页游标未推进，请刷新核实。');
    cursors.add(cursor);
    beforeCursor = cursor;
  }
  return null;
}

export default function Dashboard() {
  const [role, setRole] = useState<Record<string, string>>({});
  const [messages, setMessages] = useState<PreviewMessage[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(false);
  const [chatError, setChatError] = useState('');
  const [refreshing, setRefreshing] = useState(false);
  const fetching = useRef(false);
  const mounted = useRef(false);
  const generation = useRef(0);
  const refreshQueued = useRef(false);
  const controller = useRef<AbortController | null>(null);
  const pagingUnavailable = useRef(false);
  const [online, setOnline] = useState<boolean | null>(null);

  const load = useCallback(async (refreshRequested = false) => {
    if (!mounted.current || document.hidden) return;
    if (fetching.current) { if (refreshRequested) refreshQueued.current = true;return; }
    if (refreshRequested) pagingUnavailable.current = false;
    fetching.current = true;
    setRefreshing(true);
    const token = generation.current;
    const isCurrent = () => mounted.current && generation.current === token && !document.hidden;
    const abort = new AbortController();
    controller.current = abort;
    const statusTask = request.get('/chat/active-sessions', { signal: abort.signal }).then(
      response => {
        if (!isCurrent()) return;
        if (!Array.isArray(response.data)) { setOnline(null);setError(true);return; }
        setOnline(response.data.length > 0);
        setError(false);
      },
      () => { if (isCurrent()) { setOnline(null);setError(true); } },
    );
    try {
      if (pagingUnavailable.current) throw new PreviewPagingUnavailable();
      const c = await getBotConfig();
      if (!isCurrent()) return;
      const config = c.data;
      if (!config || typeof config !== 'object') throw new Error('角色配置未完整返回');
      const roles = config.roles ? JSON.parse(config.roles) : [];
      if (!Array.isArray(roles) || roles.some(r => !r || typeof r !== 'object')) throw new Error('角色配置格式错误');
      const currentRole = roles.find((r: { id: string }) => r.id === config.activeRole) || roles[0] || config;
      const name = typeof currentRole.name === 'string' && currentRole.name.trim() ? currentRole.name.trim() : '桌面宠物';
      const session = await getSessionByRole(currentRole.id || config.activeRole || 'default', name);
      if (!isCurrent()) return;
      if (typeof session.data?.sessionId !== 'string' || !session.data.sessionId.trim()) throw new Error('无法读取当前对话');
      const records = await recentPreview(session.data.sessionId, isCurrent, abort.signal);
      if (!isCurrent() || records === null) return;
      setRole({ ...currentRole, name });
      setMessages(records);
      setChatError('');
    } catch (error) {
      if (isCurrent()) {
        if (error instanceof PreviewPagingUnavailable) pagingUnavailable.current = true;
        setChatError(error instanceof Error ? error.message : '聊天记录读取失败，请检查服务连接后刷新。');
      }
    } finally {
      if (isCurrent()) setLoading(false);
      await statusTask;
      fetching.current = false;
      if (controller.current === abort) controller.current = null;
      if (isCurrent()) setRefreshing(false);
      if (refreshQueued.current && mounted.current && !document.hidden) {
        refreshQueued.current = false;
        void load(true);
      }
    }
  }, []);

  useEffect(() => {
    mounted.current = true;
    ++generation.current;
    void load(true);
    const timer = window.setInterval(() => { if (!document.hidden) void load(); }, 15000);
    const visible = () => {
      ++generation.current;
      controller.current?.abort();
      setOnline(null);
      if (!document.hidden) void load(true);
    };
    document.addEventListener('visibilitychange', visible);
    return () => {
      mounted.current = false;
      ++generation.current;
      refreshQueued.current = false;
      controller.current?.abort();
      window.clearInterval(timer);
      document.removeEventListener('visibilitychange', visible);
    };
  }, [load]);
  if (loading) return <Spin />;

  return <div className="overview page-width tech-overview effects-on">
    <div className="page-heading"><div><h1>桌面宠物</h1><p>查看连接状态，接着上次的话题聊。</p></div><div className="overview-actions"><Button icon={<ReloadOutlined />} loading={refreshing} onClick={() => void load(true)}>刷新</Button></div></div>
    {error && <Alert type="warning" showIcon title="设备状态暂时无法更新" description="连接状态可能不是最新状态，聊天记录独立加载。" />}
    <SpotlightPanel enabled>
      <div className="tech-copy"><Tag color={online ? 'cyan' : 'default'}>{online === null ? '连接状态未知' : online ? '设备已连接' : '设备未连接'}</Tag><h2>{role.name || '桌面宠物'}<span>你的桌面伙伴</span></h2><p>把文字留在这里，让声音陪在身边。</p><div className="tech-links"><Link className="primary-link" to="/chat">开始对话</Link><Link className="secondary-link" to="/settings">调整角色</Link></div><div className="hero-details"><span>语音与文字对话</span><span>历史记录随时回看</span></div></div>
      <div className="robot-stage" aria-hidden="true"><div className="stage-orbit orbit-back" /><div className="stage-orbit orbit-front" /><div className="robot-frame"><div className="border-tracer" /><div className="robot-shell"><div className="robot-display"><i /><i /><span /></div><div className="robot-chin"><b /><b /><b /><b /><b /></div></div></div><div className="stage-plinth" /></div>
    </SpotlightPanel>
    <DevicePanel />
    <section className="surface recent-conversations"><div className="section-heading"><h2>最近对话</h2><Link to="/chat">查看全部记录</Link></div>{chatError && <Alert type="warning" showIcon title="聊天记录更新失败" description={`${chatError}${messages.length ? ' 下方保留上次读取的预览，尚未更新。' : ' 暂时无法确认当前聊天记录。'}`} />}{messages.length ? <div className="recent-message-list">{messages.map(m => <article className="recent-message" key={m.id}><span className="preview-speaker">{m.chatType === '1' || m.chatType === 'user' ? '我' : role.name || '桌面宠物'}</span><p className="preview-message">{m.content.replace(/^🔔 /, '')}</p><time dateTime={m.createDate}>{dayjs(m.createDate).format('M月D日 HH:mm')}</time></article>)}</div> : <p className="empty-copy">{chatError ? '暂时无法读取聊天记录。' : `还没有对话记录。和${role.name || '桌面宠物'}聊几句后，再来这里回看。`}</p>}</section>
  </div>;
}
