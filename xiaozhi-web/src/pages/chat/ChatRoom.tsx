import { Fragment, useCallback, useEffect, useId, useLayoutEffect, useMemo, useRef, useState } from 'react';
import type { KeyboardEvent } from 'react';
import { Alert, App, Button, DatePicker, Input, Modal, Space, Spin, Tag } from 'antd';
import { CloseOutlined, DeleteOutlined, ReloadOutlined, RobotOutlined, SendOutlined, SettingOutlined, WifiOutlined } from '@ant-design/icons';
import { Link } from 'react-router-dom';
import dayjs from 'dayjs';
import { deleteSession, getSessionByRole } from '../../api/chat';
import { getBotConfig } from '../../api/user';
import request from '../../api/request';
import useUnsavedChanges from '../../hooks/useUnsavedChanges';
import { ChatAvatar } from '../../components/ChatAvatar';
import { chatDate, chatDateLabel, chatTime, chatTimeLabel } from './chatTime';
import { useChatDates, useChatHistory } from './useChatHistory';
import type { HistoryMode } from './useChatHistory';
import { requestLabels, submissionBlocks, useChatSubmission } from './useChatSubmission';
import './chat-room.css';

const { TextArea } = Input;
interface RoleItem { id: string; name?: string }
interface Conversation { session: string | null; name: string; userAvatar: string; botAvatar: string }
const errorText = (error: unknown, fallback: string) => error instanceof Error ? error.message : fallback;

export default function ChatRoom() {
  const { message } = App.useApp();
  const [conversation, setConversation] = useState<Conversation>({ session: null, name: '桌面宠物', userAvatar: '', botAvatar: '' });
  const [configError, setConfigError] = useState('');
  const [configLoading, setConfigLoading] = useState(false);
  const [deviceId, setDeviceId] = useState('');
  const [wakeWord, setWakeWord] = useState('');
  const [activeSessions, setActiveSessions] = useState<Set<string> | null>(null);
  const [connectionError, setConnectionError] = useState('');
  const [selectedDate, setSelectedDate] = useState('');
  const [mode, setMode] = useState<HistoryMode>('paged');
  const [revision, setRevision] = useState(0);
  const [inputText, setInputText] = useState('');
  const [deleting, setDeleting] = useState(false);
  const [deleteError, setDeleteError] = useState('');
  const [unread, setUnread] = useState(false);
  useUnsavedChanges(Boolean(inputText.trim()));

  const alive = useRef(false);
  const currentSession = useRef(conversation.session);
  currentSession.current = conversation.session;
  const configSequence = useRef(0);
  const configPromise = useRef<Promise<void> | null>(null);
  const draftRevision = useRef(0);
  const draftText = useRef(inputText);
  draftText.current = inputText;
  const requestToastKey = useId();
  const deleteLock = useRef(false);
  const streamRef = useRef<HTMLDivElement>(null);
  const followMessages = useRef(true);
  const rendered = useRef({ key: '', ready: false, lastId: '', count: 0 });
  const prepend = useRef<{ key: string; height: number; top: number } | null>(null);
  const history = useChatHistory(conversation.session, selectedDate, mode, revision);
  const dates = useChatDates(conversation.session, mode, revision, history.items, history.ready);
  const submission = useChatSubmission(wakeWord);
  const activeRequest = submission.record?.session === conversation.session ? submission.record : null;
  const posting = !!submission.record?.posting;
  const blocked = posting || submissionBlocks(submission.record, conversation.session);
  const sendBlocked = posting || submissionBlocks(submission.record, conversation.session, inputText, wakeWord);

  useEffect(() => { alive.current = true; return () => { alive.current = false; }; }, []);
  const restoreDraft = useCallback((text: string, session: string) => {
    if (currentSession.current !== session) return;
    if (draftText.current.trim()) {
      message.info('输入框已有内容，请先保留当前草稿。');
      return;
    }
    ++draftRevision.current;
    setInputText(text);
  }, [message]);

  useEffect(() => {
    if (!activeRequest) { message.destroy(requestToastKey); return; }
    const pending = activeRequest.posting || activeRequest.tracking && !activeRequest.stopPolling
      && activeRequest.status !== 'unknown' && activeRequest.status !== 'failed';
    const label = activeRequest.posting ? '正在提交…'
      : activeRequest.status === 'waiting_action' ? activeRequest.message || requestLabels.waiting_action
      : requestLabels[activeRequest.status];
    const detail = (activeRequest.status === 'unknown' || activeRequest.status === 'failed'
      || !activeRequest.tracking && !activeRequest.posting) ? activeRequest.message : '';
    void message.open({
      key: requestToastKey,
      type: pending ? 'loading' : activeRequest.status === 'failed' ? 'error'
        : activeRequest.status === 'unknown' ? 'warning' : activeRequest.status === 'server_done' ? 'success' : 'info',
      duration: pending || activeRequest.status === 'unknown' || activeRequest.status === 'failed' ? 0 : 3,
      className: 'chat-request-message',
      content: <div className='chat-request-toast'>
        <span title={activeRequest.status === 'server_done' ? '语音播放和底盘动作请以设备实际反馈为准。' : undefined}>{label}</span>
        {activeRequest.status === 'failed' && <Button size='small' type='text' icon={<CloseOutlined />}
          aria-label='关闭处理失败提示' onClick={() => message.destroy(requestToastKey)} />}
        {detail && detail !== label && <span className='chat-request-toast-detail' title={detail}>
          {detail.slice(0, 160)}{detail.length > 160 ? '…' : ''}</span>}
        {activeRequest.status === 'unknown' && <Space className='chat-request-toast-actions' wrap>
          {activeRequest.tracking && <Button size='small' disabled={activeRequest.posting}
            onClick={() => void submission.checkStatus()}>核实状态</Button>}
          <Button size='small' disabled={activeRequest.posting} onClick={submission.finishChecking}>已核实，继续聊天</Button>
        </Space>}
        {activeRequest.status === 'failed' && <Button size='small' type='link'
          onClick={() => restoreDraft(activeRequest.text, activeRequest.session)}>放回输入框</Button>}
      </div>,
    });
  }, [activeRequest, message, requestToastKey, restoreDraft, submission.checkStatus, submission.finishChecking]);
  useEffect(() => () => message.destroy(requestToastKey), [message, requestToastKey]);

  const refreshConfig = useCallback(async (force = false) => {
    if (configPromise.current) {
      if (!force) return configPromise.current;
      ++configSequence.current;
      await configPromise.current;
    }
    const sequence = ++configSequence.current;
    const current = () => alive.current && sequence === configSequence.current;
    if (current()) setConfigLoading(true);
    const pending = (async () => {
      try {
        const result = await getBotConfig();
        const cfg = result.data || {};
        const parsed = typeof cfg.roles === 'string' ? JSON.parse(cfg.roles) : cfg.roles;
        const roles: RoleItem[] = Array.isArray(parsed) ? parsed : [];
        const role = roles.find(item => item.id === cfg.activeRole) || roles[0];
        const name = role?.name?.trim() || '桌面宠物';
        const sessionResult = await getSessionByRole(role?.id || 'default', name);
        const session = sessionResult.data?.sessionId;
        if (typeof session !== 'string' || !session) throw new Error('无法读取当前对话');
        if (!current()) return;
        if (currentSession.current !== session) setSelectedDate('');
        setConversation({ session, name, userAvatar: cfg.userAvatar || '', botAvatar: cfg.botAvatar || '' });
        setWakeWord(typeof cfg.customWakeWord === 'string' ? cfg.customWakeWord : '');
        setConfigError('');
      } catch (error) {
        if (current()) setConfigError(errorText(error, '无法加载对话，请检查管理服务'));
      } finally { if (current()) setConfigLoading(false); }
    })();
    configPromise.current = pending;
    await pending;
    if (configPromise.current === pending) configPromise.current = null;
  }, []);

  useEffect(() => {
    void refreshConfig();
    const timer = window.setInterval(() => { if (!document.hidden) void refreshConfig(); }, 30000);
    const visible = () => { if (!document.hidden) void refreshConfig(); };
    document.addEventListener('visibilitychange', visible);
    return () => { window.clearInterval(timer); document.removeEventListener('visibilitychange', visible); };
  }, [refreshConfig]);

  useEffect(() => {
    const controller = new AbortController();
    let busy = false;
    const refresh = async () => {
      if (busy || document.hidden || controller.signal.aborted) return;
      busy = true;
      try {
        const [device, sessions] = await Promise.allSettled([
          request.get<unknown, { data?: { macAddress?: string } }>('/devices/info', { signal: controller.signal }),
          request.get<unknown, { data?: string[] }>('/chat/active-sessions', { signal: controller.signal }),
        ]);
        if (controller.signal.aborted) return;
        if (device.status === 'fulfilled') setDeviceId(typeof device.value.data?.macAddress === 'string' ? device.value.data.macAddress : '');
        if (sessions.status === 'fulfilled' && Array.isArray(sessions.value.data)
          && sessions.value.data.every(value => typeof value === 'string')) {
          setActiveSessions(new Set(sessions.value.data));
          setConnectionError('');
        } else {
          setActiveSessions(null);
          setConnectionError('连接状态暂时无法核实，发送时由设备服务确认。');
        }
      } finally { busy = false; }
    };
    const visible = () => { if (!document.hidden) void refresh(); };
    void refresh();
    const timer = window.setInterval(() => { void refresh(); }, 5000);
    document.addEventListener('visibilitychange', visible);
    return () => { controller.abort(); window.clearInterval(timer); document.removeEventListener('visibilitychange', visible); };
  }, []);

  const dateSet = useMemo(() => new Set(dates.dates), [dates.dates]);
  const visibleMessages = useMemo(() => history.items.filter(item => item.chatType !== '3'
    && (!selectedDate || chatDate(item.createDate) === selectedDate)), [history.items, selectedDate]);

  useEffect(() => {
    if (selectedDate && dates.ready && !dateSet.has(selectedDate)) {
      setSelectedDate('');
      message.info('所选日期已没有聊天记录，已返回全部日期。');
    }
  }, [selectedDate, dates.ready, dateSet, message]);

  useLayoutEffect(() => {
    const element = streamRef.current;
    const lastId = visibleMessages.length ? String(visibleMessages[visibleMessages.length - 1].id) : '';
    const previous = rendered.current;
    if (element) {
      if (previous.key !== history.key || !previous.ready && history.ready) {
        followMessages.current = !selectedDate;
        prepend.current = null;
        element.scrollTop = selectedDate ? 0 : element.scrollHeight;
        setUnread(false);
      } else if (prepend.current?.key === history.key) {
        element.scrollTop = prepend.current.top + element.scrollHeight - prepend.current.height;
        prepend.current = null;
      } else if (lastId !== previous.lastId && visibleMessages.length >= previous.count && history.ready) {
        if (followMessages.current) element.scrollTop = element.scrollHeight;
        else setUnread(true);
      }
    }
    rendered.current = { key: history.key, ready: history.ready, lastId, count: visibleMessages.length };
  }, [history.key, history.ready, visibleMessages, selectedDate]);

  useEffect(() => {
    if (activeRequest?.status === 'server_done' || activeRequest?.status === 'failed') {
      void history.refreshNew();
      void dates.refresh();
    }
  }, [activeRequest?.requestId, activeRequest?.status, history.refreshNew, dates.refresh]);

  const refreshRecords = () => { void history.refresh(); void dates.refresh(); };
  const loadOlder = async () => {
    if (history.loadingOlder || !history.hasMoreBefore) return;
    const element = streamRef.current;
    if (element) prepend.current = { key: history.key, top: element.scrollTop, height: element.scrollHeight };
    const success = await history.loadOlder();
    if (!success && prepend.current?.key === history.key) prepend.current = null;
  };
  const handleSend = async () => {
    const text = inputText.trim();
    const session = conversation.session;
    if (!text || !session || sendBlocked || deleting) return;
    const draft = draftRevision.current;
    await submission.submit(text, deviceId, session, () => {
      if (!alive.current || currentSession.current !== session) return;
      if (draftRevision.current === draft) setInputText('');
      followMessages.current = true;
      setUnread(false);
      setSelectedDate('');
      void history.refreshNew();
    });
  };
  const handleKeyDown = (event: KeyboardEvent<HTMLTextAreaElement>) => {
    if (event.key === 'Enter' && !event.shiftKey && !event.nativeEvent.isComposing) {
      event.preventDefault(); void handleSend();
    }
  };
  const handleDelete = () => {
    const session = conversation.session;
    const date = selectedDate;
    if (!session || deleting || blocked) return;
    Modal.confirm({
      title: date ? '删除 ' + date + ' 的对话记录？' : '清空全部日期的对话记录？',
      content: date ? '只删除北京时间这一天的记录，其他日期和长期记忆保留。' : '清空当前会话全部日期的记录，不会删除长期记忆。',
      okText: '清空记录', cancelText: '保留', okButtonProps: { danger: true },
      onOk: async () => {
        if (deleteLock.current || currentSession.current !== session) { message.info('对话已变化，请重新选择删除范围。'); return; }
        deleteLock.current = true;
        setDeleting(true);
        try {
          await deleteSession(session, date || undefined);
          if (!alive.current || currentSession.current !== session) return;
          setDeleteError('');
          setSelectedDate('');
          setRevision(value => value + 1);
          if (!date) {
            ++configSequence.current;
            setConversation(old => ({ ...old, session: null }));
            await refreshConfig(true);
          }
          message.success(date ? '当天记录已删除' : '对话记录已清空');
        } catch (error) {
          if (alive.current && currentSession.current === session) {
            setDeleteError(errorText(error, '删除结果未确认，请刷新核实'));
            void history.refresh();
            void dates.refresh();
          }
        } finally { deleteLock.current = false; if (alive.current) setDeleting(false); }
      },
    });
  };

  const connectionLabel = conversation.session && activeSessions?.has(conversation.session) ? '设备已连接'
    : activeSessions === null ? '连接状态待核实' : activeSessions.size ? '设备连接其他对话' : '尚未确认在线设备';
  const messageRows = useMemo(() => visibleMessages.map((item, index) => {
    const user = item.chatType === '1' || item.chatType === 'user';
    const showDate = index === 0 || chatDate(item.createDate) !== chatDate(visibleMessages[index - 1].createDate);
    return <Fragment key={String(item.id)}>
      {showDate && <div className='chat-date-separator'><span>{chatDateLabel(item.createDate)}</span></div>}
      <article className={'message-row' + (user ? ' from-user' : '')}>
        <ChatAvatar value={user ? conversation.userAvatar : conversation.botAvatar} robot={!user} />
        <div className='message-body'><div className='message-meta'><span>{user ? '我' : conversation.name}</span>
          <time dateTime={Number.isFinite(chatTime(item.createDate)) ? new Date(chatTime(item.createDate)).toISOString() : undefined}>{chatTimeLabel(item.createDate)}</time></div>
          <div className='message-bubble'>{item.content.replace(/^🔔 /, '')}</div>
        </div>
      </article>
    </Fragment>;
  }), [visibleMessages, conversation.name, conversation.userAvatar, conversation.botAvatar]);

  return <div className='chat-workspace qq-chat single-chat'>
    <section className='chat-panel'>
      <div className='chat-heading'><ChatAvatar value={conversation.botAvatar} robot size={36} />
        <div className='chat-persona'><h2 title={conversation.name}>{conversation.name}</h2><p>在网页输入，由桌面宠物出声回复</p></div>
        <Tag color={activeSessions?.has(conversation.session || '') ? 'success' : 'default'} icon={<WifiOutlined />}>{connectionLabel}</Tag>
        <Link className='chat-avatar-link' to='/settings' aria-label='设置对话头像'><SettingOutlined /> 头像设置</Link>
      </div>
      <div className='chat-date-toolbar'>
        <DatePicker className='chat-date-picker' aria-label='按北京时间查询聊天记录' inputReadOnly allowClear format='YYYY-MM-DD'
          value={selectedDate ? dayjs(selectedDate) : null} placeholder='全部日期'
          defaultPickerValue={dayjs(selectedDate || dates.dates.at(-1) || chatDate(new Date().toISOString()))}
          disabled={!dates.ready || deleting} disabledDate={value => !dateSet.has(value.format('YYYY-MM-DD'))}
          onChange={value => setSelectedDate(value?.format('YYYY-MM-DD') || '')} />
        {selectedDate && <Button type='link' onClick={() => setSelectedDate('')}>全部日期</Button>}
        <span className='chat-date-hint'>{dates.loading ? '正在核对记录日期…' : dates.ready ? '北京时间 · 灰色日期没有记录' : '记录日期待核实'}</span>
        <Space className='chat-record-actions' wrap><Button size='small' icon={<ReloadOutlined />} onClick={refreshRecords}
          title='刷新聊天记录' aria-label='刷新聊天记录'
          disabled={!conversation.session || deleting} loading={history.loading}><span className='chat-action-label'>刷新记录</span></Button>
          <Button size='small' className='chat-clear-button' type='text' icon={<DeleteOutlined />} onClick={handleDelete}
            title={selectedDate ? '删除当天记录' : '清空全部记录'} aria-label={selectedDate ? '删除当天记录' : '清空全部记录'}
            disabled={!conversation.session || !history.ready || blocked || deleting} loading={deleting}><span className='chat-action-label'>{selectedDate ? '删除当天记录' : '清空全部记录'}</span></Button></Space>
      </div>
      <div className='chat-feedback'>
        {submission.interrupted?.session === conversation.session && <Alert type='warning'
          title='上一条操作结果请核实'
          description={`“${submission.interrupted.text.slice(0, 120)}${submission.interrupted.text.length > 120 ? '…' : ''}”：结果尚未核实，请检查设备状态和记录，避免重复提交同一操作。`}
          action={<Button size='small' onClick={submission.dismissInterrupted}>我已核实</Button>} />}
        {configError && <Alert type='warning' title='对话配置未更新' description={configError}
          action={<Button size='small' onClick={() => void refreshConfig(true)}>重新加载</Button>} />}
        {history.error && <Alert type='warning' title='聊天记录暂时无法更新' description={history.error + (history.ready ? '；下方保留上次读取的记录。' : '')}
          action={<Button size='small' onClick={refreshRecords}>重试</Button>} />}
        {dates.error && dates.error !== history.error && <Alert type='warning' title='记录日期待核实' description={dates.error}
          action={<Button size='small' onClick={() => void dates.refresh()}>重试</Button>} />}
        {(history.unsupported || dates.unsupported) && mode === 'paged' && <Alert type='info' title='当前服务尚不支持新版记录查询'
          description='可更新服务，或暂时查看兼容记录；兼容模式需要手动刷新。'
          action={<Button size='small' onClick={() => setMode('legacy')}>查看兼容记录</Button>} />}
        {mode === 'legacy' && <Alert type='info' title='正在查看兼容记录，记录需手动刷新'
          action={<Button size='small' onClick={() => setMode('paged')}>尝试新版查询</Button>} />}
        {deleteError && <Alert type='warning' title='删除结果待核实' description={deleteError} action={<Button size='small' onClick={refreshRecords}>核实记录</Button>} />}
      </div>
      <div className='message-stream' ref={streamRef} role='log' aria-label='对话消息' aria-live='polite' aria-busy={history.loading}
        onScroll={() => { const element = streamRef.current; if (element) {
          followMessages.current = element.scrollHeight - element.scrollTop - element.clientHeight < 90;
          if (followMessages.current) setUnread(false);
        } }}>
        {history.hasMoreBefore && <div className='chat-load-older'><Button size='small' onClick={() => void loadOlder()} loading={history.loadingOlder}>加载更早的记录</Button></div>}
        {history.loading && !history.ready ? <div className='chat-stream-loading'><Spin /></div> : !visibleMessages.length ?
          <div className='chat-empty'><RobotOutlined /><h3>{history.error || !conversation.session ? '聊天记录待核实'
            : selectedDate ? '这一天没有聊天记录' : '有什么想和' + conversation.name + '聊聊的？'}</h3>
            <p>{history.error || !conversation.session ? '请检查服务连接后重新加载。' : selectedDate ? '换个日期看看，或返回全部记录。' : '可以分享今天的小事，也可以直接问一个问题。'}</p></div> : messageRows}
      </div>
      {(unread || history.outsideUnread) && <Button className='new-message-button' onClick={() => {
        followMessages.current = true; setUnread(false); setSelectedDate('');
        if (streamRef.current) streamRef.current.scrollTop = streamRef.current.scrollHeight;
      }}>有新消息，回到最新</Button>}
      {conversation.session ? <div className='composer'>
        <TextArea aria-label={'发送给' + conversation.name + '的消息'} value={inputText} maxLength={20000}
          onChange={event => { ++draftRevision.current; setInputText(event.target.value); }} onKeyDown={handleKeyDown}
          placeholder={'和' + conversation.name + '说点什么…'} autoSize={{ minRows: 1, maxRows: 4 }} />
        <div className='composer-toolbar'><span className='composer-hint'>{posting && !activeRequest ? '正在提交另一段对话，请稍候。'
          : connectionError || (blocked && !posting ? '处理期间可发送“别说了”或“待命一下” · Enter 发送'
            : '网页文字聊天需要在线设备 · Enter 发送 · Shift + Enter 换行')}</span>
          <Button type='primary' icon={<SendOutlined />} onClick={() => void handleSend()} loading={posting}
            disabled={!inputText.trim() || sendBlocked || deleting}>发送消息</Button></div>
      </div> : <div className='history-notice'><span>正在连接当前对话。</span><Button type='link' loading={configLoading} onClick={() => void refreshConfig(true)}>重新加载</Button></div>}
    </section>
  </div>;
}
