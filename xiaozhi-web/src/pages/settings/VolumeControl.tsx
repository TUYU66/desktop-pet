import { useEffect, useRef, useState } from 'react';
import { Alert, Button, Select, Slider } from 'antd';
import request from '../../api/request';

type VolumeSnapshot = {
  deviceId: string;
  hardware: { audio_speaker?: { volume?: number } };
  controls: Record<string, boolean>;
};

export default function VolumeControl() {
  const [device, setDevice] = useState('');
  const [devices, setDevices] = useState<string[]>([]);
  const [snapshot, setSnapshot] = useState<VolumeSnapshot | null>(null);
  const [value, setValue] = useState<number | null>(null);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [feedback, setFeedback] = useState('');
  const [refreshKey, setRefreshKey] = useState(0);
  const busyRef = useRef(false);
  const version = useRef(0);

  useEffect(() => {
    let active = true;
    const read = async () => {
      if (busyRef.current) return;
      const currentVersion = version.current;
      try {
        const result = await request.get('/devices/panel', { params: device ? { deviceId: device } : {} });
        if (!active || currentVersion !== version.current) return;
        setDevices(result.data.devices || []);
        setSnapshot(result.data.selected || null);
        setError('');
        if (device && !result.data.devices?.includes(device)) setDevice('');
      } catch (cause) {
        if (active && currentVersion === version.current) setError(cause instanceof Error ? cause.message : '设备状态读取失败');
      } finally { if (active) setLoading(false); }
    };
    setLoading(true);
    setSnapshot(null);
    setValue(null);
    setError('');
    setFeedback('');
    void read();
    const timer = window.setInterval(() => { if (!document.hidden) void read(); }, 6000);
    return () => { active = false; window.clearInterval(timer); };
  }, [device, refreshKey]);

  const volume = snapshot?.hardware.audio_speaker?.volume;
  const enabled = !!snapshot?.controls.volume && volume != null && !busy && !error;
  const apply = async (target: number) => {
    if (!snapshot || busyRef.current) return;
    if (target === volume) { setValue(null); return; }
    busyRef.current = true;
    version.current += 1;
    setBusy(true);
    setFeedback('正在发送，等待设备确认…');
    try {
      const result = await request.post('/devices/panel', { deviceId: snapshot.deviceId, action: 'volume', value: target });
      if (result.data.confirmation !== 'verified') throw new Error('尚未获得执行确认，请刷新核实');
      setSnapshot(result.data.selected);
      setValue(null);
      setError('');
      setFeedback('设备已执行，实际音量已核对');
    } catch (cause) {
      setValue(null);
      setError('请刷新设备状态后再操作');
      setFeedback(cause instanceof Error ? cause.message : '执行结果未知，请刷新核实');
    } finally { busyRef.current = false; setBusy(false); }
  };

  return <section className="settings-inline-section" aria-labelledby="settings-volume-title">
    <div className="settings-inline-heading"><div><h3 id="settings-volume-title">扬声器音量</h3><p>连接设备后自动读取当前音量。</p></div><Button size="small" disabled={busy} onClick={() => setRefreshKey(key => key + 1)}>刷新状态</Button></div>
    {devices.length > 1 && <Select aria-label="选择音量控制设备" value={device || undefined} placeholder="选择在线设备" allowClear disabled={busy} options={devices.map(id => ({ value: id, label: id }))} onChange={id => setDevice(id || '')} style={{ width: '100%', marginBottom: 12 }} />}
    <div className="settings-volume-heading"><span id="settings-volume-label">音量</span><output>{value ?? volume ?? (loading ? '读取中' : '未上报')}</output></div>
    <Slider ariaLabelledByForHandle="settings-volume-label" min={0} max={100} value={value ?? volume ?? 0} onChange={setValue} onChangeComplete={target => void apply(target)} disabled={!enabled} />
    <p className="settings-control-hint">松开滑条后发送，设备确认后更新实际数值。</p>
    {error && <Alert type="warning" title={error} />}
    {feedback && <div aria-live="polite"><Alert type={busy ? 'info' : error ? 'warning' : 'success'} title={feedback} /></div>}
  </section>;
}
