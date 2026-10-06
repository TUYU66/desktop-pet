import { useCallback, useEffect, useRef, useState } from 'react';
import { App, Alert, Button, Form, Input, Modal, Popconfirm, Select, Slider, Space, Spin, Tag } from 'antd';
import { ClockCircleOutlined, ReloadOutlined } from '@ant-design/icons';
import { createReminder, getReminders, getReminderDevices, reminderAction, changeReminder, planAction as legacyPlanAction, deleteReminderHistory, getReminderSettings, saveReminderMusicSettings } from '../../api/reminder';
import type { Reminder, ReminderPlan, ReminderPage } from '../../api/reminder';
import request, { ApiError } from '../../api/request';
import FutureDateInput from './FutureDateInput';
import ScheduleFields from './ScheduleFields';
import { recurrenceCode, recurrenceLabel, nextTrigger, type ScheduleTime } from './scheduleTime';
import { useUserStore } from '../../store/userStore';
import './reminders.css';

const reminderStates: Record<string, [string, string]> = {
  scheduled: ['等待发送', 'blue'], dispatching: ['正在发送', 'processing'],
  awaiting_confirmation: ['等待回应', 'gold'], delivery_unknown: ['发送待核实', 'orange'],
  completed: ['已完成', 'green'], cancelled: ['已取消', 'default'], expired: ['已逾期', 'orange'],
  retry_pending: ['等待下次提醒', 'gold'], missed: ['本轮未回应', 'orange'],
};
const unanswered = new Set(['awaiting_confirmation', 'retry_pending', 'delivery_unknown', 'missed']);
const legacyTerminal = new Set(['completed', 'cancelled', 'expired', 'missed']);
const historyCategories = {
  expired: { label: '已逾期', color: 'orange' },
  cancelled: { label: '已取消', color: 'default' },
  completed: { label: '已完成', color: 'green' },
};
type HistoryCategory = keyof typeof historyCategories;
type HistoryFilter = 'all' | HistoryCategory;
const historyCategory = (item: Reminder): HistoryCategory => item.status === 'missed' ? 'expired' : item.status as HistoryCategory;
const dateLabel = (value: number) => Number.isFinite(value) ? new Date(value).toLocaleString('zh-CN', { timeZone: 'Asia/Shanghai' }) : '时间待核实';
const inputDate = (value: number | null) => value === null || !Number.isFinite(value) ? undefined : new Date(value + 8 * 3600000).toISOString().slice(0, 16);
const timestamp = (value?: string) => value ? new Date(value + '+08:00').getTime() : null;
const validTime = (value: unknown): value is number => typeof value === 'number' && Number.isFinite(value);
function queueTime(value: number) {
  const date = new Date(value);
  return {
    date: date.toLocaleDateString('zh-CN', { timeZone: 'Asia/Shanghai', year: 'numeric', month: '2-digit', day: '2-digit' }),
    clock: date.toLocaleTimeString('zh-CN', { timeZone: 'Asia/Shanghai', hour12: false, hour: '2-digit', minute: '2-digit', ...(date.getUTCSeconds() ? { second: '2-digit' as const } : {}) }),
  };
}
function queueHint(item: Reminder) {
  if (item.status === 'retry_pending' && item.nextAttemptAt > 0) return '下次尝试：' + dateLabel(item.nextAttemptAt);
  return ({ scheduled: '到点由桌面宠物提醒', dispatching: '正在发送到桌面宠物',
    awaiting_confirmation: '回复“收到”或点击“知道了”，完成本次提醒',
    retry_pending: '未回应，等待下次提醒', delivery_unknown: '发送结果待核实，请查看设备状态' } as Record<string, string>)[item.status];
}
type Device = { deviceId: string; name?: string };
type LegacyValues = { title: string; date: string };
type LegacyPlanValues = ScheduleTime & { title: string };
function createRequestId() {
  if (typeof crypto.randomUUID === 'function') return crypto.randomUUID();
  const bytes = crypto.getRandomValues(new Uint8Array(16));
  bytes[6] = (bytes[6] & 15) | 64;
  bytes[8] = (bytes[8] & 63) | 128;
  const hex = Array.from(bytes, byte => byte.toString(16).padStart(2, '0')).join('');
  return [hex.slice(0, 8), hex.slice(8, 12), hex.slice(12, 16), hex.slice(16, 20), hex.slice(20)].join('-');
}

function ScheduleCreate({ devices, deviceError, now, onSaved }: { devices: Device[]; deviceError: string; now: number; onSaved: () => void }) {
  const [form] = Form.useForm<ScheduleTime & { title: string; deviceId: string }>();
  const recurrence = Form.useWatch('recurrence', form);
  const [busy, setBusy] = useState(false);
  const owner = useUserStore(state => state.user?.id);
  const storageKey = 'schedule-create:' + String(owner);
  type Draft = Parameters<typeof createReminder>[0];
  const [pending, setPending] = useState<Draft | null>(() => {
    try {
      const value = JSON.parse(sessionStorage.getItem(storageKey) || 'null');
      return value && typeof value.requestId === 'string' && typeof value.title === 'string' && typeof value.deviceId === 'string' && validTime(value.triggerAt) ? value : null;
    } catch { return null; }
  });
  const lock = useRef(false);
  const { message } = App.useApp();
  const submit = async () => {
    if (lock.current) return;
    lock.current = true; setBusy(true);
    const verifying = pending !== null;
    let body = pending;
    try {
      if (!body) {
        const values = await form.validateFields();
        const triggerAt = nextTrigger(values, now);
        const title = values.title?.trim();
        if (!title || title.length > 120 || !Number.isFinite(triggerAt) || triggerAt <= now) throw new Error('请填写提醒内容并选择未来时间。');
        const deviceId = devices.length === 1 ? devices[0].deviceId : values.deviceId;
        if (deviceError || !devices.some(device => device.deviceId === deviceId)) throw new Error(deviceError || '请确认提醒设备后再保存。');
        body = { requestId: createRequestId(), title, deviceId, triggerAt, recurrence: recurrenceCode(values) };
        sessionStorage.setItem(storageKey, JSON.stringify(body));
        setPending(body);
      }
      const result = await createReminder(body);
      const saved = result.data;
      const original = saved?.creation;
      if (saved?.id !== body.requestId || original?.requestId !== body.requestId || original.deviceId !== body.deviceId || original.title !== body.title || original.triggerAt !== body.triggerAt || original.recurrence !== (body.recurrence ?? 'once') || original.seconds !== (body.seconds ?? null) || !saved.current || ![...Object.keys(reminderStates), 'active', 'paused', 'history_removed'].includes(saved.current.status)) throw new Error('创建回执未确认，请核实原请求。');
      sessionStorage.removeItem(storageKey); setPending(null); form.resetFields();
      const state = reminderStates[saved.current.status]?.[0] || ({ active: '进行中', paused: '已暂停', history_removed: '历史已清理' } as Record<string,string>)[saved.current.status];
      message.success(`已核实原创建：${body.title}，原安排 ${dateLabel(body.triggerAt!)}；核实时状态：${state}。`); onSaved();
    } catch (error) {
      const code = errorCode(error);
      // Only a first definitive refusal permits a new creation ID. A conflict
      // or a refusal during verification cannot resolve an earlier unknown result.
      if (!verifying && [400, 401, 403, 404].includes(code || 0)) { sessionStorage.removeItem(storageKey); setPending(null); }
      if (error instanceof Error) message.error(body && (verifying || code === 409)
        ? errorText(error) + ' 原请求仍待核实，请检查日程记录或继续核实原请求。'
        : errorText(error));
    } finally { lock.current = false; setBusy(false); }
  };
  return <section className="surface reminder-create task-editor"><h2>添加日程提醒</h2>
    {pending && <Alert type="warning" showIcon title="原请求尚待核实" description={<Space wrap><span>{pending.title} · {dateLabel(pending.triggerAt!)}</span><Button loading={busy} onClick={() => void submit()}>核实原请求</Button></Space>} />}
    <Form form={form} layout="vertical" initialValues={{ recurrence: 'once' }} disabled={busy || !!pending} onFinish={() => void submit()}>
      <Form.Item name="title" label="提醒内容" rules={[{ required: true, whitespace: true }, { max: 120 }]}><Input maxLength={120} placeholder="例如：拿快递" /></Form.Item>
      <ScheduleFields recurrence={recurrence} now={now} />
      {devices.length > 1 && <Form.Item name="deviceId" label="提醒设备" rules={[{ required: true, message: '请选择提醒设备' }]}><Select options={devices.map(device => ({ value: device.deviceId, label: device.name || device.deviceId }))} /></Form.Item>}
      {deviceError && <p role="alert">{deviceError}</p>}
      <Button type="primary" htmlType="submit" loading={busy} disabled={!!pending || !devices.length || !!deviceError}>保存提醒</Button>
    </Form><p className="reminder-meta">“收到”完成本次提醒，周期安排继续生效。</p>
  </section>;
}

function errorCode(error: unknown) { return error instanceof ApiError ? Number(error.code || error.status) : undefined; }
function errorText(error: unknown) { return error instanceof Error ? error.message : '操作未完成，请刷新核实。'; }
function reminderValid(item: Reminder): boolean {
  return !!item && typeof item.id === 'string' && typeof item.title === 'string' && typeof item.deviceId === 'string' && item.status in reminderStates && Number.isInteger(item.version) && validTime(item.dueAt) && validTime(item.scheduledAt);
}
function planValid(item: ReminderPlan): boolean {
  return !!item && typeof item.id === 'string' && typeof item.title === 'string' && typeof item.recurrence === 'string' && Number.isInteger(item.version) && ['active', 'paused', 'cancelled'].includes(item.status) && validTime(item.initialAt) && validTime(item.nextAt);
}
function pageValid(page: ReminderPage): boolean {
  return !!page && Array.isArray(page.items) && page.items.every(reminderValid) && Array.isArray(page.plans) && page.plans.every(planValid) && validTime(page.serverNow) && Number.isInteger(page.itemTotal) && page.itemTotal >= page.items.length && Number.isInteger(page.unansweredCount) && page.unansweredCount >= 0;
}
function ReminderDetails({ reminder, remindAt, showError = true }: { reminder: Reminder; remindAt: number; showError?: boolean }) {
  const arranged = reminder.scheduledAt || remindAt;
  return <div className="reminder-meta"><p>安排时间：{dateLabel(arranged)}</p>
    {arranged !== remindAt && <p>原定时间：{dateLabel(remindAt)}</p>}
    {reminder.dueAt !== arranged && <p>当前发送时间：{dateLabel(reminder.dueAt)}</p>}
    {reminder.status === 'retry_pending' && reminder.nextAttemptAt > 0 && <p>下次尝试：{dateLabel(reminder.nextAttemptAt)}</p>}
    {reminder.status === 'awaiting_confirmation' && <p>回复“收到”或“知道了”完成本次提醒。</p>}
    {reminder.status === 'missed' && <p>多次提醒后仍未回应，本轮提醒已结束。</p>}
    {showError && reminder.lastError && <p role="alert">{reminder.lastError}</p>}
  </div>;
}

function ReminderMusicSettings({ refreshKey }: { refreshKey: number }) {
  const { message } = App.useApp();
  const [musicVolume, setMusicVolume] = useState(30);
  const [musicTrackId, setMusicTrackId] = useState<string | null>(null);
  const [tracks, setTracks] = useState<{ id: string; name: string }[]>([]);
  const [tracksReady, setTracksReady] = useState(false);
  const [musicReady, setMusicReady] = useState(false);
  const [saving, setSaving] = useState<'track' | 'volume' | null>(null);
  const [musicError, setMusicError] = useState('');
  const [trackError, setTrackError] = useState('');
  const [playbackWarning, setPlaybackWarning] = useState('');
  const alive = useRef(false);
  const loadMusic = useCallback(async () => {
    try {
      const result = await getReminderSettings();
      if (!alive.current) return;
      if (!Number.isInteger(result.data?.musicVolume) || result.data.musicVolume < 0 || result.data.musicVolume > 100) throw new Error('提醒音乐设置返回不完整');
      setMusicVolume(result.data.musicVolume); setMusicTrackId(result.data.musicTrackId || null); setMusicReady(true); setMusicError('');
    } catch { if (alive.current) { setMusicReady(false); setMusicError('音乐设置读取失败，请刷新重试。'); } }
  }, []);
  const loadTracks = useCallback(async () => {
    try {
      const result = await request.get('/music');
      if (!alive.current) return;
      if (!Array.isArray(result.data?.tracks) || !result.data.tracks.every((track: { id: string; name: string }) => typeof track.id === 'string' && typeof track.name === 'string')) throw new Error('音乐库返回不完整');
      setTracks(result.data.tracks); setTracksReady(true); setTrackError('');
      setPlaybackWarning((result.data.devices || []).map((device: { reminderMusicWarning?: string }) => device.reminderMusicWarning).filter(Boolean).join('；'));
    } catch { if (alive.current) { setTracksReady(false); setTrackError('音乐库暂时无法读取，仍可选择默认提醒音乐。'); } }
  }, []);
  useEffect(() => {
    alive.current = true; void loadMusic(); void loadTracks();
    const timer = window.setInterval(() => { if (!document.hidden) void loadTracks(); }, 10000);
    return () => { alive.current = false; window.clearInterval(timer); };
  }, [loadMusic, loadTracks, refreshKey]);
  const save = async (field: 'track' | 'volume') => {
    if (saving || !musicReady) return;
    setSaving(field);
    try {
      const result = await saveReminderMusicSettings(field === 'volume' ? { musicVolume } : { musicTrackId });
      if (field === 'volume' ? result.data?.musicVolume !== musicVolume : (result.data?.musicTrackId || null) !== musicTrackId) throw new Error('音乐设置回执未确认，请刷新核实。');
      if (alive.current) message.success(field === 'volume' ? '提醒音量已保存。' : '提醒歌曲已保存。');
    } catch (error) { if (alive.current) { message.error(errorText(error)); await loadMusic(); } }
    finally { if (alive.current) setSaving(null); }
  };
  const missing = !!musicTrackId && !tracks.some(track => track.id === musicTrackId);
  return <section className="surface reminder-music-settings" aria-labelledby="reminder-music-label"><h2 id="reminder-music-label">提醒音乐</h2>
    <p className="reminder-meta">选曲和音量分别保存，对后续提醒统一生效。</p>
    {musicError && <Alert type="warning" title={musicError} action={<Button onClick={() => void loadMusic()}>重试</Button>} />}
    <div className="reminder-music-groups"><div className="reminder-music-group"><h3><label htmlFor="reminder-track">提醒歌曲</label></h3><div className="reminder-music-track-row"><Select id="reminder-track" value={musicTrackId || 'default'} onChange={value => setMusicTrackId(value === 'default' ? null : value)} disabled={!musicReady || !!saving}
      options={[{ value: 'default', label: '默认提醒音乐' }, ...tracks.map(track => ({ value: track.id, label: track.name })), ...(missing ? [{ value: musicTrackId!, label: tracksReady ? '已保存的歌曲已失效' : '已保存的歌曲待核实' }] : [])]} />
      <Button loading={saving === 'track'} disabled={!musicReady || !!saving} onClick={() => void save('track')}>保存选曲</Button></div>
    {missing && tracksReady && <Alert type="warning" title="已保存的歌曲已失效，请重新选曲。" />}
    {trackError && <p role="alert">{trackError}</p>}{playbackWarning && <Alert type="warning" title={playbackWarning} />}
    <p className="reminder-meta">回应“收到”后停止本次提醒音乐。</p></div>
    <div className="reminder-music-group"><h3><label htmlFor="reminder-volume">提醒音量</label></h3><div className="reminder-music-volume-row"><div className="reminder-music-slider"><Slider id="reminder-volume" min={0} max={100} step={5} value={musicVolume} onChange={setMusicVolume} disabled={!musicReady || !!saving} marks={{ 0: '静音', 100: '100%' }} /><output>{musicVolume}%</output></div>
      <Button loading={saving === 'volume'} disabled={!musicReady || !!saving} onClick={() => void save('volume')}>保存音量</Button></div>
    <p className="reminder-meta">0 为提醒音乐静音，不影响语音音量。</p></div></div>
  </section>;
}

export default function Reminders() {
  const { message } = App.useApp();
  const [legacyForm] = Form.useForm<LegacyValues>();
  const [legacyPlanForm] = Form.useForm<LegacyPlanValues>();
  const legacyPlanRecurrence = Form.useWatch('recurrence', legacyPlanForm);
  const [snapshot, setSnapshot] = useState<{ data: ReminderPage } | null>(null);
  const [loading, setLoading] = useState(false);
  const [readError, setReadError] = useState('');
  const [devices, setDevices] = useState<Device[]>([]);
  const [deviceError, setDeviceError] = useState('');
  const [now, setNow] = useState(Date.now());
  const [working, setWorking] = useState('');
  const [editConflict, setEditConflict] = useState(false);
  const [legacyEditing, setLegacyEditing] = useState<Reminder | null>(null);
  const [legacyPlanEditing, setLegacyPlanEditing] = useState<ReminderPlan | null>(null);
  const [historyPage, setHistoryPage] = useState(1);
  const [historyFilter, setHistoryFilter] = useState<HistoryFilter>('all');
  const [musicRefreshKey, setMusicRefreshKey] = useState(0);
  const alive = useRef(false);
  const life = useRef(0);
  const readEpoch = useRef(0);
  const readController = useRef<AbortController | null>(null);
  const readPending = useRef<Promise<void> | null>(null);
  const writeLock = useRef(false);
  const refreshBadge = () => window.dispatchEvent(new Event('xiaozhi:reminders-state'));
  const stopRead = useCallback(() => { readEpoch.current += 1; readController.current?.abort(); }, []);

  const load = useCallback(async () => {
    const epoch = readEpoch.current;
    if (!alive.current || document.hidden || writeLock.current) return;
    if (readPending.current) await readPending.current;
    if (!alive.current || document.hidden || writeLock.current || epoch !== readEpoch.current) return;
    if (readPending.current) return readPending.current;
    const controller = new AbortController();
    readController.current = controller;
    setLoading(true);
    const current = () => alive.current && !document.hidden && epoch === readEpoch.current && !controller.signal.aborted;
    const run = (async () => {
      try {
        const result = await getReminders(controller.signal);
        if (!current()) return;
        if (!pageValid(result.data)) throw new Error('日程列表返回不完整，请刷新核实。');
        setSnapshot({ data: result.data });
        setNow(result.data.serverNow);
        setReadError('');
      } catch (error) {
        if (current()) setReadError(errorText(error));
      } finally { if (current()) setLoading(false); }
    })();
    readPending.current = run;
    await run;
    if (readPending.current === run) readPending.current = null;
  }, []);

  const loadDevices = useCallback(async () => {
    const currentLife = life.current;
    try {
      const result = await getReminderDevices();
      if (!alive.current || currentLife !== life.current) return;
      if (!Array.isArray(result.data) || !result.data.every((device: Device) => typeof device.deviceId === 'string'))
        throw new Error('设备列表返回不完整');
      setDevices(result.data);
      setDeviceError('');

    } catch (error) {
      if (alive.current && currentLife === life.current) setDeviceError('提醒设备读取失败：' + errorText(error));
    }
  }, []);

  useEffect(() => {
    alive.current = true; life.current += 1;
    void loadDevices();
    return () => { alive.current = false; life.current += 1; stopRead(); };
  }, [loadDevices, stopRead]);
  useEffect(() => {
    stopRead();
    void load();
    const timer = window.setInterval(() => { void load(); }, 4000);
    const visibility = () => { stopRead(); if (!document.hidden) void load(); };
    document.addEventListener('visibilitychange', visibility);
    return () => { stopRead(); window.clearInterval(timer); document.removeEventListener('visibilitychange', visibility); };
  }, [load, stopRead]);

  const fresh = !!snapshot && !readError;
  const disabled = !fresh || !!working;
  const manualRefresh = () => { stopRead(); void load(); void loadDevices(); setMusicRefreshKey(value => value + 1); refreshBadge(); };
  const runLegacyPlanAction = async (item: ReminderPlan, action: 'pause' | 'resume' | 'cancel' | 'edit', values?: { title: string; triggerAt: number; recurrence: string }) => {
    if (disabled || writeLock.current) return;
    writeLock.current = true; setWorking('legacy-plan:' + item.id); stopRead();
    const currentLife = life.current;
    try {
      const result = await legacyPlanAction(item, action, values);
      if (typeof result.data !== 'string' || !result.data) throw new Error('周期操作回执未确认，请刷新核实。');
      if (alive.current && currentLife === life.current) {
        message.success('独立提醒周期已更新，已生成的提醒保留。');
        if (action === 'edit') { setLegacyPlanEditing(null); setEditConflict(false); }
      }
    } catch (error) {
      if (alive.current && currentLife === life.current) {
        if (errorCode(error) === 409) setEditConflict(true);
        message.error(errorText(error) + ' 请刷新核实，不会自动重试。');
      }
    } finally {
      writeLock.current = false;
      if (alive.current && currentLife === life.current) { setWorking(''); refreshBadge(); void load(); }
    }
  };
  const openLegacyPlanEdit = (item: ReminderPlan) => {
    const anchor = item.initialAt;
    setLegacyPlanEditing(item); setEditConflict(false);
    legacyPlanForm.setFieldsValue({ title: item.title, recurrence: item.recurrence.startsWith('weekly:') ? 'selected' : item.recurrence, selectedDays: item.recurrence.startsWith('weekly:') ? item.recurrence.slice(7).split(',').map(d => Number(d) % 7) : [],
      time: inputDate(anchor)?.slice(11, 16), weekday: new Date(anchor + 8 * 3600000).getUTCDay() });
  };
  const saveLegacyPlanEdit = async () => {
    if (!legacyPlanEditing || disabled || editConflict) return;
    try {
      const values = await legacyPlanForm.validateFields();
      const title = values.title?.trim();
      const triggerAt = nextTrigger(values, now);
      if (!title || title.length > 120 || !Number.isFinite(triggerAt) || triggerAt <= now || !['daily', 'weekly', 'weekdays', 'selected'].includes(values.recurrence || ''))
        throw new Error('请填写提醒内容并选择有效的周期时间。');
      await runLegacyPlanAction(legacyPlanEditing, 'edit', { title, triggerAt, recurrence: recurrenceCode(values) });
    } catch (error) { if (error instanceof Error) message.error(error.message); }
  };
  const legacyAction = async (item: Reminder, action: 'confirm' | 'snooze' | 'cancel' | 'delete' | 'edit', values?: LegacyValues) => {
    if (disabled || writeLock.current) return;
    writeLock.current = true; setWorking('legacy:' + item.id); stopRead();
    const currentLife = life.current;
    try {
      if (action === 'delete') {
        const result = await deleteReminderHistory(item);
        if (result.data !== '记录已删除') throw new Error('删除结果尚未确认，请刷新核实。');
      } else {
        const at = values ? timestamp(values.date) : null;
        if (action === 'edit' && (!values?.title?.trim() || !validTime(at) || at <= now)) throw new Error('请填写提醒内容并选择未来时间。');
        const result = action === 'edit' ? await changeReminder(item, values!.title.trim(), at!) :
          await reminderAction(item, action);
        if (!reminderValid(result.data) || result.data.id !== item.id) throw new Error('提醒操作回执未确认，请刷新核实。');
      }
      if (!alive.current || currentLife !== life.current) return;
      message.success(action === 'confirm' ? '本次提醒已完成。' : action === 'delete' ? '提醒历史已删除。' : action === 'snooze' ? '提醒已延后5分钟。' : action === 'cancel' ? '后续提醒已取消。' : '提醒已保存。');
      if (action === 'edit') setLegacyEditing(null);
    } catch (error) {
      if (alive.current && currentLife === life.current) message.error(errorText(error) + ' 请刷新核实，不会自动重试。');
    } finally {
      writeLock.current = false;
      if (alive.current && currentLife === life.current) { setWorking(''); refreshBadge(); void load(); }
    }
  };
  const data = snapshot?.data;
  const visiblePlans = data?.plans.filter(plan => plan.status === 'active' || plan.status === 'paused') || [];
  const legacyActive = data?.items.filter(item => !legacyTerminal.has(item.status)) || [];
  const history = (data?.items.filter(item => legacyTerminal.has(item.status)) || []).sort((a, b) => b.scheduledAt - a.scheduledAt);
  const filteredHistory = historyFilter === 'all' ? history : history.filter(item => historyCategory(item) === historyFilter);
  const historyCounts = {
    all: history.length,
    expired: history.filter(item => historyCategory(item) === 'expired').length,
    cancelled: history.filter(item => historyCategory(item) === 'cancelled').length,
    completed: history.filter(item => historyCategory(item) === 'completed').length,
  };
  const historyPages = Math.max(1, Math.ceil(filteredHistory.length / 6));
  const visibleHistoryPage = Math.min(historyPage, historyPages);
  const historyItems = filteredHistory.slice((visibleHistoryPage - 1) * 6, visibleHistoryPage * 6);
  const legacyButtons = (item: Reminder, highlight = false) => <Space size={highlight ? 8 : 4} wrap>
    {unanswered.has(item.status) && <><Button size="small" type={highlight ? 'primary' : 'default'} disabled={disabled} onClick={() => void legacyAction(item, 'confirm')}>知道了</Button>
      <Button size="small" disabled={disabled} onClick={() => void legacyAction(item, 'snooze')}>5分钟后提醒</Button></>}
    {!legacyTerminal.has(item.status) && <><Button size="small" disabled={disabled} onClick={() => { setLegacyEditing(item); legacyForm.setFieldsValue({ title: item.title, date: inputDate(item.scheduledAt || item.dueAt) }); }}>修改提醒</Button>
      <Popconfirm title="取消后续提醒？" onConfirm={() => void legacyAction(item, 'cancel')}><Button size="small" disabled={disabled}>取消提醒</Button></Popconfirm></>}
    {legacyTerminal.has(item.status) && <Popconfirm title="删除这条日程提醒历史？" description="删除后不再显示，周期模板不受影响。" onConfirm={() => void legacyAction(item, 'delete')}>
      <Button size="small" danger disabled={disabled}>删除历史</Button></Popconfirm>}
  </Space>;

  return <div className="reminders-page page-width">
    <header className="page-heading"><div><h1><ClockCircleOutlined /> 日程提醒</h1><p>安排单次或周期提醒，到点由设备通知。</p></div>
      <Button icon={<ReloadOutlined />} onClick={manualRefresh} disabled={!!working}>刷新</Button></header>
    {readError && <Alert type="warning" showIcon title={'日程列表读取失败：' + readError} description={snapshot ? '保留上次读取的内容，操作暂不可用。请刷新核实。' : '请刷新重试。'} />}
    {snapshot && snapshot.data.itemTotal > snapshot.data.items.length && <Alert type="info" showIcon title={`当前展示 ${snapshot.data.items.length} / ${snapshot.data.itemTotal} 条提醒；未回应总数 ${snapshot.data.unansweredCount} 按全部未删除记录统计。`} />}
    {loading && <div className="task-loading" role="status"><Spin size="small" /><span>正在读取日程</span></div>}
    <div className="reminder-workspace"><div className="schedule-panels">
      <ScheduleCreate devices={devices} deviceError={deviceError} now={now} onSaved={manualRefresh} />
        <section className="surface reminder-plans"><div className="section-heading"><h2>周期安排</h2><span>{data ? visiblePlans.length + ' 个' : '等待更新'}</span></div>
          <p className="task-list-note">暂停或取消只影响未来周期。本次提醒可单独完成或取消。</p>
          {visiblePlans.map(plan => <article className="reminder-row" key={plan.id}>
            <div className="reminder-row-heading"><h3>{plan.title}</h3><Tag color={plan.status === 'active' ? 'green' : 'default'}>{plan.status === 'active' ? '进行中' : plan.status === 'paused' ? '已暂停' : '已取消'}</Tag></div>
            <p className="reminder-meta">{recurrenceLabel(plan.recurrence, plan.initialAt)}</p>
            {plan.status === 'active' && <p className="reminder-meta">下次安排：{dateLabel(plan.nextAt)}</p>}
            <Space wrap>{plan.status === 'active' && <Button size="small" disabled={disabled} onClick={() => void runLegacyPlanAction(plan, 'pause')}>暂停周期</Button>}
              {plan.status === 'paused' && <Button size="small" disabled={disabled} onClick={() => void runLegacyPlanAction(plan, 'resume')}>恢复周期</Button>}
              {plan.status !== 'cancelled' && <Button size="small" disabled={disabled} onClick={() => openLegacyPlanEdit(plan)}>修改周期</Button>}
              {plan.status !== 'cancelled' && <Popconfirm title="取消未来周期？" description="已生成的本次提醒继续保留。" onConfirm={() => void runLegacyPlanAction(plan, 'cancel')}>
                <Button size="small" disabled={disabled}>取消周期</Button></Popconfirm>}</Space>
          </article>)}
          {data && !visiblePlans.length && <p className="reminder-meta">暂无周期安排。</p>}
        </section>
        {data && <section className="surface reminder-queue" aria-labelledby="reminder-queue-title">
          <div className="section-heading reminder-queue-heading"><h2 id="reminder-queue-title">等待与进行中</h2><span>{legacyActive.length} 条提醒</span></div>
          <p className="task-list-note">“收到”或“知道了”即完成本次，未回应时按原规则再次提醒。</p>
          <div className="reminder-queue-list">{legacyActive.map(item => {
            const arranged = item.scheduledAt || item.originalDueAt || item.dueAt;
            const time = queueTime(arranged);
            return <article className={`active-reminder${unanswered.has(item.status) ? ' needs-response' : ''}`} key={item.id}>
              <div className="active-reminder-time"><span>本次安排</span><time dateTime={new Date(arranged).toISOString()} aria-label={dateLabel(arranged)}><strong>{time.clock}</strong><span>{time.date}</span></time></div>
              <div className="active-reminder-content"><div className="active-reminder-heading"><h3>{item.title}</h3><Tag color={reminderStates[item.status][1]}>{reminderStates[item.status][0]}</Tag></div>
                <p className="active-reminder-kind">{item.planId ? '周期中的本次提醒' : '单次提醒'}</p>
                <p className="active-reminder-hint">{queueHint(item)}</p>
                {item.lastError && <p className="active-reminder-error" role="alert">{item.lastError}</p>}
                <details className="active-reminder-details"><summary>时间与发送详情</summary><ReminderDetails reminder={item} remindAt={item.originalDueAt || item.dueAt} showError={false} /></details>
              </div>
              <div className="active-reminder-actions">{legacyButtons(item, true)}</div>
            </article>;
          })}</div>
          {!legacyActive.length && <div className="reminder-queue-empty"><ClockCircleOutlined aria-hidden="true" /><div><h3>暂无等待中的提醒</h3><p>在上方添加日程，到点后桌面宠物会提醒你。</p></div></div>}
        </section>}
      <ReminderMusicSettings refreshKey={musicRefreshKey} />
      </div>
    </div>
    {data && <details className="surface reminder-history"><summary><span>日程提醒历史 <strong>{history.length} 条</strong></span><span className="history-summary-hint">保留7天</span></summary>
      <p className="task-list-note">已结束的日程提醒保留7天，周期计划不受历史清理影响。</p>
      <div className="history-filters" role="group" aria-label="日程提醒历史分类">
        {(['all', 'expired', 'cancelled', 'completed'] as const).map(category => <Button key={category}
          type={historyFilter === category ? 'primary' : 'default'} aria-pressed={historyFilter === category}
          onClick={() => { setHistoryFilter(category); setHistoryPage(1); }}>
          {category === 'all' ? '全部' : historyCategories[category].label} <span className="history-filter-count">{historyCounts[category]}</span>
        </Button>)}
      </div>
      {data.itemTotal > data.items.length && <p className="task-list-note">分类数量按当前已加载的历史记录统计。</p>}
      <div className="reminder-history-list">{historyItems.map(item => <article className="reminder-history-item" key={item.id}>
        <div className="history-item-main"><h3>{item.title}</h3><Tag color={historyCategories[historyCategory(item)].color}>{historyCategories[historyCategory(item)].label}</Tag><time dateTime={new Date(item.scheduledAt).toISOString()}>{dateLabel(item.scheduledAt)}</time></div>
        <details className="history-item-details"><summary>查看详情与操作</summary><div className="history-item-body">
          <ReminderDetails reminder={item} remindAt={item.originalDueAt ?? item.dueAt} /><div className="reminder-actions">{legacyButtons(item)}</div>
        </div></details></article>)}</div>
      {!filteredHistory.length && <p className="history-empty" role="status">{historyFilter === 'all' ? '暂无日程提醒历史。' : `暂无${historyCategories[historyFilter].label}的提醒记录。`}</p>}
      {historyPages > 1 && <div className="history-pagination"><span>第 {visibleHistoryPage} / {historyPages} 页</span><Space>
        <Button size="small" disabled={visibleHistoryPage === 1} onClick={() => setHistoryPage(visibleHistoryPage - 1)}>上一页</Button>
        <Button size="small" disabled={visibleHistoryPage === historyPages} onClick={() => setHistoryPage(visibleHistoryPage + 1)}>下一页</Button></Space></div>}
    </details>}
    <Modal title="修改日程提醒周期" open={!!legacyPlanEditing} onCancel={() => !working && setLegacyPlanEditing(null)}
      onOk={() => void saveLegacyPlanEdit()} confirmLoading={!!working} okButtonProps={{ disabled: disabled || editConflict }} cancelButtonProps={{ disabled: !!working }} destroyOnHidden>
      <p className="task-list-note">修改仅影响未来周期，已生成的本次提醒继续保留。</p>
      {editConflict && <Alert type="warning" title="周期已变化。草稿保留，请关闭窗口，从刷新后的列表重新打开。" />}
      <Form form={legacyPlanForm} layout="vertical" disabled={!!working}>
        <Form.Item name="title" label="提醒内容" rules={[{ required: true, whitespace: true }, { max: 120 }]}><Input maxLength={120} /></Form.Item>
        <ScheduleFields recurrence={legacyPlanRecurrence} now={now} plan />
      </Form>
    </Modal>
    <Modal title="修改日程提醒" open={!!legacyEditing} onCancel={() => !working && setLegacyEditing(null)}
      onOk={async () => { if (!legacyEditing || disabled) return; try { const values = await legacyForm.validateFields(); await legacyAction(legacyEditing, 'edit', values); } catch { /* Form keeps validation errors visible. */ } }}
      confirmLoading={!!working} okButtonProps={{ disabled }} cancelButtonProps={{ disabled: !!working }} destroyOnHidden>
      <Form form={legacyForm} layout="vertical" disabled={!!working}><Form.Item name="title" label="提醒内容" rules={[{ required: true, whitespace: true }, { max: 120 }]}><Input maxLength={120} /></Form.Item>
        <Form.Item name="date" label="提醒时间（北京时间）" rules={[{ required: true }]}><FutureDateInput now={now} /></Form.Item></Form>
    </Modal>
  </div>;
}
