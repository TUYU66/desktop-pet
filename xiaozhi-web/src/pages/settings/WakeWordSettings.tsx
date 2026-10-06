import { useEffect, useState } from 'react';
import { Alert, App, Button, Input, Space, Typography } from 'antd';
import { getBotConfig, getWakeWordStatus, saveBotConfig } from '../../api/user';

type Status = { state: string; word: string; reason?: string; pinyin?: string; pronunciation?: string };
const statusText: Record<string, string> = {
  applied: '设备已生效，重启后也会保留',
  pending: '已保存，正在等待设备确认',
  offline: '已保存，等待设备上线',
  unsupported: '设备固件暂不支持，请烧录支持自定义唤醒词的新固件',
  failed: '设备尚未应用成功，将自动重试；“你好小智”仍可使用',
  unreachable: '暂时无法读取设备状态，请检查 Python 服务',
  multiple_devices: '检测到多台在线设备，暂不下发；初版仅支持一台设备',
};

export default function WakeWordSettings({ onDirtyChange, onWordChange }: { onDirtyChange: (dirty: boolean) => void; onWordChange: (word: string) => void }) {
  const { message } = App.useApp();
  const [word, setWord] = useState('');
  const [saved, setSaved] = useState<string | null>(null);
  const [pinyin, setPinyin] = useState('');
  const [savedPinyin, setSavedPinyin] = useState('');
  const [busy, setBusy] = useState(false);
  const [loadError, setLoadError] = useState(false);
  const [status, setStatus] = useState<Status | null>(null);
  const dirty = saved !== null && (word !== saved || pinyin !== savedPinyin);
  const valid = (word === '' || /^[\u4e00-\u9fff]{3,8}$/.test(word)) &&
    (!pinyin || /^[a-z]+(?: [a-z]+)*$/.test(pinyin) && pinyin.split(' ').length === word.length);
  useEffect(() => { onDirtyChange(dirty); }, [dirty, onDirtyChange]);
  useEffect(() => {
    let cancelled = false;
    getBotConfig().then(result => {
      if (!cancelled) { const value = result.data?.customWakeWord || ''; setWord(value); setSaved(value); onWordChange(value);
        const pronunciation = result.data?.customWakePinyin || ''; setPinyin(pronunciation); setSavedPinyin(pronunciation); }
    }).catch(() => { if (!cancelled) setLoadError(true); });
    return () => { cancelled = true; };
  }, [onWordChange]);
  useEffect(() => {
    if (saved === null) return;
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout>;
    const refresh = async () => {
      try {
        const result = await getWakeWordStatus();
        if (!cancelled) setStatus(result.data?.word === saved && (result.data.state !== 'applied' || (result.data.pronunciation || '') === savedPinyin) ? result.data : { state: 'pending', word: saved });
      } catch { if (!cancelled) setStatus({ state: 'unreachable', word: saved }); }
      if (!cancelled) timer = setTimeout(refresh, 5000);
    };
    void refresh();
    return () => { cancelled = true; clearTimeout(timer); };
  }, [saved, savedPinyin]);
  const save = async () => {
    if (!valid || saved === null) return;
    setBusy(true);
    try {
      await saveBotConfig({ customWakeWord: word, customWakePinyin: pinyin });
      setSaved(word);
      setSavedPinyin(pinyin);
      setStatus({ state: 'pending', word });
      message.success('唤醒词已保存，正在等待设备确认');
    } catch { message.error('保存失败，请重试'); }
    finally { setBusy(false); }
  };
  return <section className="settings-inline-section" aria-labelledby="settings-wake-title">
    <div className="settings-inline-heading"><div><h3 id="settings-wake-title">唤醒设置</h3><p>保存后同步到设备，“你好小智”始终可用。</p></div></div>
    {loadError ? <Alert type="error" title="无法读取唤醒词，请刷新页面重试" /> : <>
      <label htmlFor="custom-wake-word">自定义唤醒词</label>
      <Input id="custom-wake-word" value={word} onChange={event => { setWord(event.target.value); setPinyin(''); onWordChange(event.target.value); }}
        maxLength={8} allowClear placeholder="输入 3～8 个汉字，留空关闭" disabled={busy || saved === null}
        status={!valid ? 'error' : undefined} style={{ margin: '8px 0 12px', maxWidth: 400, display: 'flex' }} />
      <label htmlFor="custom-wake-pinyin">指定读音（可选，用于多音字）</label>
      <Input id="custom-wake-pinyin" value={pinyin} onChange={event => setPinyin(event.target.value.toLowerCase())}
        disabled={busy || !word} maxLength={63} placeholder="留空自动识别，例如：ni hao xiao lan"
        style={{ margin: '8px 0 12px', maxWidth: 400, display: 'flex' }} />
      {status?.pinyin && !dirty && <Typography.Paragraph>设备使用的读音：{status.pinyin}</Typography.Paragraph>}
      {!valid && <Typography.Paragraph type="danger">唤醒词填写 3～8 个汉字；指定读音需每字一个拼音、空格分隔且不带声调。</Typography.Paragraph>}
      <Space wrap style={{ marginBottom: 16 }}>
        <Button type="primary" onClick={save} loading={busy} disabled={!valid || !dirty}>保存唤醒词</Button>
        <Typography.Text type="secondary">{dirty ? '有未保存的修改' : saved ? `已保存：${saved}` : '当前只使用“你好小智”'}</Typography.Text>
      </Space>
      {status && <Alert showIcon type={status.state === 'applied' ? 'success' : status.state === 'failed' ? 'warning' : 'info'}
        title={statusText[status.state] || '等待设备确认'}
        description={status.reason === 'dependency_missing' ? 'Python 服务缺少 pypinyin，请安装更新后的依赖并重启。'
          : status.reason === 'not_ready' ? '设备模型尚未就绪；若持续出现，请确认完整烧录了新的模型资源。'
          : status.reason === 'rejected' ? '设备不支持这个词的读音，请换一个词。'
          : status.reason === 'storage_error' ? '设备保存失败，请检查串口日志。' : undefined} />}
    </>}
  </section>;
}
