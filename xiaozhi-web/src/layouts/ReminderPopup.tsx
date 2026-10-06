import { useEffect, useRef, useState } from 'react';
import { Alert, App, Button, Modal, Space, Tag } from 'antd';
import { ClockCircleOutlined } from '@ant-design/icons';
import { reminderAction } from '../api/reminder';
import type { Reminder, ReminderPage } from '../api/reminder';
import { canConfirmPopup, popupKey, reconcilePopup } from './reminderPopupState';

const timeLabel = (value: number) => new Date(value).toLocaleString('zh-CN', {
  timeZone: 'Asia/Shanghai', month: 'numeric', day: 'numeric', hour: '2-digit', minute: '2-digit', hour12: false,
});
const stateLabel: Partial<Record<Reminder['status'], string>> = {
  scheduled: '等待设备提醒', dispatching: '正在播报', awaiting_confirmation: '等你回应',
  delivery_unknown: '还没确认是否收到', retry_pending: '还没收到回应', expired: '已逾期', missed: '还没收到回应',
};

export default function ReminderPopup({ page }: { page: ReminderPage | null }) {
  const [items, setItems] = useState<Reminder[]>([]);
  const [working, setWorking] = useState<string | null>(null);
  const [error, setError] = useState('');
  const dismissed = useRef(new Set<string>());
  const lock = useRef(false);
  const alive = useRef(false);
  const { message } = App.useApp();
  useEffect(() => { alive.current = true; return () => { alive.current = false; }; }, []);
  useEffect(() => {
    if (page) {
      const present = new Set(page.items.map(popupKey));
      for (const key of dismissed.current) if (!present.has(key)) dismissed.current.delete(key);
      setItems(previous => reconcilePopup(previous, page, dismissed.current));
    }
  }, [page]);

  const dismiss = () => {
    if (lock.current) return;
    for (const item of items) dismissed.current.add(popupKey(item));
    setItems([]); setError(''); // Closing the window does not acknowledge a reminder.
  };
  const act = async (selected: Reminder[], action: 'confirm' | 'snooze') => {
    if (lock.current || !selected.length || selected.some(item => !canConfirmPopup(item))) return;
    lock.current = true; setWorking(selected.length > 1 ? 'all' : selected[0].id); setError('');
    const failures: string[] = [];
    let succeeded = 0;
    try {
      for (const item of selected) {
        if (!alive.current) break;
        try {
          const result = await reminderAction(item, action, 300);
          const saved = result.data as Reminder | undefined;
          if (!saved || saved.id !== item.id || !Number.isInteger(saved.version) || saved.version <= item.version
            || saved.status !== (action === 'confirm' ? 'completed' : 'scheduled')
            || (action === 'snooze' && (!Number.isFinite(saved.dueAt) || saved.dueAt <= item.dueAt))) {
            throw new Error('操作回执未确认，请核实日程记录');
          }
          succeeded += 1;
          dismissed.current.add(popupKey(item));
          if (alive.current) setItems(previous => previous.filter(row => popupKey(row) !== popupKey(item)));
        } catch (cause) {
          failures.push(`“${item.title}”：${cause instanceof Error ? cause.message : '操作未确认，请核实日程记录'}`);
        }
      }
      if (alive.current) {
        if (failures.length) setError(failures.join('；') + '。未确认的操作不会自动重试。');
        if (succeeded) message.success(action === 'confirm'
          ? (succeeded === 1 ? '好，这条提醒记为收到了。' : `好，这${succeeded}条提醒都记为收到了。`)
          : '好，5分钟后再提醒你。');
        window.dispatchEvent(new Event('xiaozhi:reminders-state'));
      }
    } finally { lock.current = false; if (alive.current) setWorking(null); }
  };
  return <Modal open={items.length > 0} title={<Space><ClockCircleOutlined />{items.length > 1 ? '有几件事到时间了' : '提醒你一件事'}</Space>}
    onCancel={dismiss} maskClosable={false} keyboard={!working} closable={!working} width={560}
    footer={<Space wrap><Button disabled={!!working} onClick={dismiss}>稍后处理</Button>
      {items.length > 1 && <Button type="primary" loading={working === 'all'}
        disabled={!!working || items.some(item => !canConfirmPopup(item))} onClick={() => void act(items, 'confirm')}>都收到了</Button>}</Space>}>
    <p style={{ color: '#66758c' }}>可以在这里确认，或唤醒后说“收到”。关闭弹窗会保留未确认状态。</p>
    {error && <Alert type="warning" showIcon title={error} style={{ marginBottom: 12 }} />}
    <div style={{ maxHeight: '55vh', overflowY: 'auto' }}>
      {items.map(item => <section key={popupKey(item)} style={{ padding: '16px 0', borderBottom: '1px solid #edf0f6' }}>
        <Space wrap style={{ marginBottom: 8 }}><Tag color={item.status === 'dispatching' ? 'processing' : 'gold'}>{stateLabel[item.status] || '待确认'}</Tag>
          <span style={{ color: '#66758c', fontSize: 13 }}>{timeLabel(item.scheduledAt || item.originalDueAt || item.dueAt)}（北京时间）</span></Space>
        <p style={{ margin: '0 0 12px', fontSize: 17, overflowWrap: 'anywhere' }}>{item.title}</p>
        <Space wrap><Button type="primary" loading={working === item.id} disabled={!!working || !canConfirmPopup(item)} onClick={() => void act([item], 'confirm')}>收到了</Button>
          <Button disabled={!!working || !canConfirmPopup(item)} onClick={() => void act([item], 'snooze')}>5分钟后再提醒</Button>
          {!canConfirmPopup(item) && <span style={{ color: '#66758c', fontSize: 12 }}>{item.status === 'scheduled' ? '等设备提醒后可确认' : '播报结束后可确认'}</span>}</Space>
      </section>)}
    </div>
  </Modal>;
}
