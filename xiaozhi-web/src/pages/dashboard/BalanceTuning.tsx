import { useCallback, useEffect, useRef, useState } from 'react';
import { Alert, Button, InputNumber, Progress, Space, Tag } from 'antd';
import request from '../../api/request';
import { completePoint, mergePoints, paramsValid, samplesConnect, stateValid, tuningFields } from './tuningData';
import type { TuningParams, TuningPoint, TuningState } from './tuningData';
import './balance-tuning.css';

type Series = { index: number; label: string; color: string; scale?: number };
const gyroErrors = ['', '设备状态变化，本次校准已停止', '检测到移动或振动，请静止后重新校准',
  '采样中断，本次校准未生效', '采集超时，本次校准未生效', '零偏超出允许范围，请检查传感器', '已取消本次校准'];
type Operation = 'apply' | 'undo' | 'save' | 'load' | 'gyro_start' | 'gyro_cancel' | 'gyro_clear';
function Trace({ points, title, unit, series }: { points: TuningPoint[]; title: string; unit: string; series: Series[] }) {
  const width = 600, height = 155, top = 16, bottom = 128, left = 45, right = 588;
  const values = points.flatMap(p => series.map(s => p[s.index] / (s.scale ?? 1)));
  let min = Math.min(0, ...values), max = Math.max(0, ...values);
  if (max - min < 1) { min -= .5; max += .5; }
  const padding = (max - min) * .08;
  min -= padding; max += padding;
  const times = [0];
  points.slice(1).forEach((p, i) => times.push(times[i] + ((p[1] - points[i][1]) & 0x7fffffff)));
  const span = Math.max(1000, times.at(-1) ?? 0);
  const y = (v: number) => bottom - (v - min) / (max - min) * (bottom - top);
  return <figure className="balance-trace">
    <figcaption><strong>{title}</strong><span>{unit}</span></figcaption>
    <svg viewBox={`0 0 ${width} ${height}`} role="img" aria-label={`${title}曲线，当前保留 ${points.length} 个采样点`}>
      {[min, (min + max) / 2, max].map((value, i) => <g key={i}>
        <line x1={left} x2={right} y1={y(value)} y2={y(value)} stroke="#dce6f3" />
        <text x={left - 6} y={y(value) + 4} textAnchor="end">{value.toFixed(Math.abs(value) < 10 ? 1 : 0)}</text>
      </g>)}
      {series.map(s => {
        let path = '';
        points.forEach((p, i) => {
          const x = left + times[i] / span * (right - left);
          const connected = i > 0 && samplesConnect(points[i - 1], p);
          path += `${connected ? 'L' : 'M'}${x.toFixed(1)},${y(p[s.index] / (s.scale ?? 1)).toFixed(1)} `;
        });
        return <path key={s.index} d={path} fill="none" stroke={s.color} strokeWidth="1.8" />;
      })}
      <text x={left} y={149}>0 秒</text><text x={right} y={149} textAnchor="end">{(span / 1000).toFixed(1)} 秒</text>
    </svg>
    <div className="balance-trace-legend">{series.map(s => <span key={s.index}>
      <i style={{ background: s.color }} />{s.label}：{points.length ? (points.at(-1)![s.index] / (s.scale ?? 1)).toFixed(2) : '—'}
    </span>)}</div>
  </figure>;
}

export default function BalanceTuning({ device, online }: { device: string; online: boolean }) {
  const [open, setOpen] = useState(false);
  const [observing, setObserving] = useState(false);
  const [state, setState] = useState<TuningState | null>(null);
  const [draft, setDraft] = useState<Partial<TuningParams>>({});
  const [baseRevision, setBaseRevision] = useState(0);
  const [baseEpoch, setBaseEpoch] = useState(0);
  const [points, setPoints] = useState<TuningPoint[]>([]);
  const [currentPoint, setCurrentPoint] = useState<TuningPoint | null>(null);
  const [busy, setBusy] = useState(false);
  const [reading, setReading] = useState(false);
  const [feedback, setFeedback] = useState('');
  const [uncertain, setUncertain] = useState(false);
  const [error, setError] = useState('');
  const [gap, setGap] = useState(false);
  const [freshAt, setFreshAt] = useState(0);
  const [clock, setClock] = useState(Date.now());
  const cursor = useRef({ after: 0, epoch: 0 });
  const inFlight = useRef(false);
  const copyRequested = useRef(false);
  const mutating = useRef(false);
  const alive = useRef(true);
  const profiles = useRef<Record<string, TuningParams>>({});
  const gyroProfiles = useRef<Record<string, { calibrated: boolean; pitchBias: number; yawBias: number; revision: number }>>({});
  const latest = useRef<TuningState | null>(null);
  const capturing = useRef(observing);
  capturing.current = observing;
  useEffect(() => { alive.current = true; return () => { alive.current = false; }; }, []);

  const adopt = useCallback((next: TuningState) => {
    if (!alive.current) return;
    const previousGyro = latest.current?.gyro;
    if (next.gyro?.valid && previousGyro?.status === 1 && next.gyro.status !== 1) {
      setFeedback(next.gyro.status === 2 ? '静止校准已完成，请读取实际参数后再起立观察' : '本次采集已停止，请查看校准状态');
    }
    latest.current = next;
    cursor.current = { after: next.cursor, epoch: next.epoch };
    setState(next); setFreshAt(Date.now()); setError('');
    if (next.points.length) setCurrentPoint(completePoint(next.points.at(-1)!));
    else if (next.reset) setCurrentPoint(null);
    if (capturing.current) setPoints(previous => mergePoints(previous, next));
    else if (next.reset) setPoints([]);
    if (next.reset) { setGap(false); profiles.current = {}; gyroProfiles.current = {}; }
    if (next.gap) setGap(true);
    if (next.params) profiles.current[`${next.epoch}:${next.revision}`] = next.params;
    if (next.gyro?.valid) {
      const { calibrated, pitchBias, yawBias, revision } = next.gyro;
      gyroProfiles.current[`${next.epoch}:${next.revision}`] = { calibrated, pitchBias, yawBias, revision };
    }
    const profileKeys = Object.keys(profiles.current);
    profileKeys.slice(0, Math.max(0, profileKeys.length - 64)).forEach(key => { delete profiles.current[key]; delete gyroProfiles.current[key]; });
  }, []);

  const read = useCallback(async (copy = false) => {
    if (mutating.current || !online) return;
    if (copy) { copyRequested.current = true; setReading(true); }
    if (inFlight.current) return;
    inFlight.current = true;
    try {
      const res = await request.get('/devices/tuning', { params: { deviceId: device, ...cursor.current }, timeout: 5000 });
      if (!alive.current) return;
      if (!stateValid(res.data)) throw new Error('调参状态格式不完整，请刷新');
      adopt(res.data);
      if (copyRequested.current && res.data.valid && res.data.params) {
        setDraft(res.data.params); setBaseRevision(res.data.revision); setBaseEpoch(res.data.epoch);
        setUncertain(false); setFeedback('已读取设备实际参数');
      }
    } catch (e) {
      if (alive.current) setError(e instanceof Error ? e.message : '状态读取失败');
    } finally {
      inFlight.current = false; copyRequested.current = false;
      if (alive.current) setReading(false);
    }
  }, [adopt, device, online]);

  useEffect(() => {
    if (!open) return;
    let cancelled = false;
    let timer: number | undefined;
    const poll = async () => {
      if (cancelled) return;
      if (!document.hidden) await read();
      if (!cancelled) timer = window.setTimeout(() => void poll(), observing || latest.current?.gyro?.status === 1 ? 1000 : 5000);
    };
    void poll();
    const clockTimer = window.setInterval(() => setClock(Date.now()), 500);
    return () => { cancelled = true; window.clearTimeout(timer); window.clearInterval(clockTimer); };
  }, [open, observing, read]);

  const control = async (operation: Operation) => {
    if (mutating.current || inFlight.current || !online || uncertain) return;
    mutating.current = true; setBusy(true); setFeedback(''); setError('');
    let dispatched = false;
    try {
      const actual = await request.get('/devices/tuning', { params: { deviceId: device, ...cursor.current }, timeout: 5000 });
      if (!alive.current) return;
      if (!stateValid(actual.data) || !actual.data.valid) throw new Error('设备状态已过期，请重新读取');
      adopt(actual.data);
      if (operation !== 'gyro_cancel' && !actual.data.writable) throw new Error('请先坐下、退出蓝牙控制和校准，待动作结束后再操作');
      if (baseRevision !== actual.data.revision || baseEpoch !== actual.data.epoch)
        throw new Error('设备参数或连接已变化，请点击“读取实际参数”后再操作');
      if (operation === 'apply' && !paramsValid(draft)) throw new Error('请输入允许范围内的完整参数');
      if (operation.startsWith('gyro_') && !actual.data.gyro?.valid) throw new Error('请核对零偏校准固件并重新读取状态');
      dispatched = true;
      const res = await request.post('/devices/tuning', {
        deviceId: device, operation, revision: actual.data.revision, tick: actual.data.tick,
        ...(operation === 'apply' ? { params: draft } : {}),
        ...(operation === 'gyro_start' ? { confirmed: true } : {}),
      }, { timeout: 5000 });
      if (!alive.current) return;
      if (!stateValid(res.data) || !res.data.valid || !res.data.params) throw new Error('没有获得完整参数确认，请重新读取');
      const sameEpoch = res.data.epoch === cursor.current.epoch;
      adopt({ ...res.data, reset: !sameEpoch,
        points: sameEpoch ? res.data.points.filter(p => p[0] > cursor.current.after) : res.data.points });
      setDraft(res.data.params); setBaseRevision(res.data.revision); setBaseEpoch(res.data.epoch);
      setFeedback(operation === 'gyro_start' ? '已开始采集，请保持机身静止约 3 秒' : operation === 'gyro_cancel' ? '已取消本次采集，原有校准值保留' :
        operation === 'gyro_clear' ? '已清除角速度零偏校准' : operation === 'save' ? '设备实际参数已保存' : operation === 'undo' ? '已恢复上一组，实际参数已核对' : '参数已应用，实际值已核对');
      if (operation.startsWith('gyro_')) setObserving(true);
    } catch (e) {
      if (alive.current) { setError(e instanceof Error ? e.message : '操作未确认'); if (dispatched) setUncertain(true); }
    } finally { mutating.current = false; if (alive.current) setBusy(false); }
  };

  const ready = online && state?.valid && clock - freshAt < 6500 && !error;
  const editable = ready && state?.writable && baseRevision === state.revision && baseEpoch === state.epoch && !busy && !reading && !uncertain;
  const dirty = !!state?.params && tuningFields.some(f => draft[f.key] !== state.params![f.key]);
  const live = state?.telemetryValid && clock - freshAt < 2500 && !error && online;
  const last = currentPoint;
  const gyro = state?.gyro;
  const gyroReady = ready && gyro?.valid;
  const gyroBusy = gyroReady && gyro?.status === 1;
  const angleValues = points.filter(p => p[3] === 1 && p[17] === 0 && p[2] === state?.revision).map(p => p[4] / 100);
  const swing = angleValues.length > 1 ? Math.max(...angleValues) - Math.min(...angleValues) : null;
  const exportData = () => {
    const blob = new Blob([JSON.stringify({ deviceId: device, exportedAt: new Date().toISOString(), sampleRate: 10,
      note: '10 Hz snapshots; not the full control stream', epoch: latest.current?.epoch, profiles: profiles.current,
      gyro: latest.current?.gyro, gyroProfiles: gyroProfiles.current,
      columns: ['cursor', 'sensorTick', 'revision', 'enabled', 'angleX100', 'pitchGyroRaw', 'yawGyroRaw',
        'leftEncoder', 'rightEncoder', 'balancePwm', 'velocityPwm', 'yawPwm', 'leftPwm', 'rightPwm',
        'velocityIntegral', 'filteredVelocityX100', 'assistMask', 'motionFlags', 'pitchGyroCorrectedX100', 'yawGyroCorrectedX100'], points: points.map(completePoint) }, null, 2)], { type: 'application/json' });
    const url = URL.createObjectURL(blob), link = document.createElement('a');
    link.href = url; link.download = 'balance-observation.json'; link.click(); window.setTimeout(() => URL.revokeObjectURL(url), 1000);
  };

  return <section className="balance-tuning">
    <button className="balance-tuning-toggle" aria-expanded={open} onClick={() => setOpen(!open)}>
      <span>平衡调参与观察</span><span>{open ? '收起' : '展开'}</span>
    </button>
    {open && <>
      <p className="balance-tuning-note">坐下后修改参数，起立后观察曲线。数值与源码参数一致；静止转向阻尼不控制主动转向速度。</p>
      <Space wrap className="balance-tuning-actions">
        <Button loading={reading} disabled={!online || busy || reading} onClick={() => void read(true)}>读取实际参数</Button>
        <Tag color={ready ? 'blue' : undefined}>{!online ? '设备离线' : !state?.supported ? '等待调参固件' : ready ? `参数版本 ${state?.revision}` : '等待有效状态'}</Tag>
        {ready && <Tag>{state?.writable ? '可应用参数' : '当前仅可观察'}</Tag>}
      </Space>
      {error && <Alert type="warning" showIcon title={error} />}
      {feedback && <p role="status" className="balance-tuning-feedback">{feedback}</p>}
      {uncertain && <p className="balance-tuning-note">请读取实际参数核实本次结果，再进行下一次操作。</p>}
      <div className="balance-gyro">
        <div className="balance-observation-heading"><h4>角速度零偏校准</h4>
          <Tag color={gyroReady && gyro?.calibrated ? 'green' : undefined}>{!gyro?.supported ? '等待校准固件' : !gyroReady ? '状态未就绪' : gyroBusy ? '正在采集' : gyro?.calibrated ? '校准值已生效' : '尚未校准'}</Tag>
        </div>
        <p className="balance-tuning-note">先坐下，待电机停止，保持机身静止约 3 秒。实时数值允许小幅波动，不需要固定在零；校准结果以状态提示为准。碰轮子、移动或采样中断会使本次校准失败。</p>
        <div className="balance-gyro-values" aria-live="polite">
          <span>俯仰零偏：<strong>{gyroReady ? gyro!.pitchBias.toFixed(2) : '—'}</strong></span>
          <span>转向零偏：<strong>{gyroReady ? gyro!.yawBias.toFixed(2) : '—'}</strong></span>
          <span>{gyro?.calibrated ? '校准后俯仰' : '原始俯仰（未校准）'}：<strong>{gyroReady ? (gyro!.calibrated ? gyro!.pitchCorrected : gyro!.rawPitch).toFixed(2) : '—'}</strong></span>
          <span>{gyro?.calibrated ? '校准后转向' : '原始转向（未校准）'}：<strong>{gyroReady ? (gyro!.calibrated ? gyro!.yawCorrected : gyro!.rawYaw).toFixed(2) : '—'}</strong></span>
        </div>
        {gyroBusy && <Progress percent={gyro!.progress} size="small" status="active" />}
        {gyroReady && gyro?.status === 3 && <p role="status" className="balance-tuning-note">{gyroErrors[gyro.error]}。{gyro.calibrated ? '之前有效的校准值继续生效。' : '本次没有写入校准值。'}</p>}
        <Space wrap>
          <Button disabled={!editable || !gyroReady || gyroBusy} onClick={() => void control('gyro_start')}>开始静止校准</Button>
          <Button disabled={!gyroBusy || busy || uncertain || !online} onClick={() => void control('gyro_cancel')}>取消采集</Button>
          <Button disabled={!editable || !gyroReady || !gyro?.calibrated} onClick={() => void control('gyro_clear')}>清除校准值</Button>
        </Space>
        <p className="balance-tuning-note">零偏仅在本次 STM32 开机期间生效，重启后需重新校准。校准结束后读取实际参数，再起立观察；PID 参数和电机阈值不随校准改变。</p>
      </div>
      <div className="balance-parameter-scroll"><table className="balance-parameters">
        <thead><tr><th>参数</th><th>实际生效</th><th>编辑值</th><th>已保存</th></tr></thead>
        <tbody>{tuningFields.map(f => <tr key={f.key}><th scope="row">{f.label}</th>
          <td>{ready ? state?.params?.[f.key] ?? '—' : '—'}</td>
          <td><InputNumber aria-label={f.label} min={f.min} max={f.max} step={f.step}
            precision={f.key === 'midAngle' ? 3 : 2} value={draft[f.key] ?? null} disabled={!ready || busy || baseRevision === 0}
            onChange={value => setDraft(previous => ({ ...previous, [f.key]: value ?? undefined }))} /></td>
          <td>{state?.saved?.[f.key] ?? '—'}</td></tr>)}</tbody>
      </table></div>
      <Space wrap className="balance-tuning-actions">
        <Button type="primary" disabled={!editable || !dirty || !paramsValid(draft)} onClick={() => void control('apply')}>临时应用</Button>
        <Button disabled={!editable || !state?.hasPrevious} onClick={() => void control('undo')}>恢复上一组</Button>
        <Button disabled={!editable || dirty} onClick={() => void control('save')}>保存实际参数</Button>
        <Button disabled={!editable || !state?.saved} onClick={() => void control('load')}>应用已保存参数</Button>
      </Space>
      <p className="balance-tuning-note">保存值保留在设备中。重启后请读取实际值，需要时再点击“应用已保存参数”。建议每次只调整一项。</p>
      <div className="balance-observation-heading"><h4>站立与动作曲线</h4><Space wrap>
        <Button disabled={!online || !state?.supported} onClick={() => setObserving(!observing)}>{observing ? '暂停观察' : '开始观察'}</Button>
        <Button disabled={!points.length} onClick={exportData}>导出采样</Button>
      </Space></div>
      <div className="balance-observation-summary">
        <span>{observing && live ? '正在接收' : observing ? '采样已中断' : '观察已暂停'}</span>
        <span>当前版本无运动指令时的倾角范围：{swing === null ? '—' : `${swing.toFixed(2)}°`}</span>
        <span>启动补偿：{!live || !last ? '—' : ['未触发', '左轮', '右轮', '左右轮'][last[16]]}</span>
      </div>
      <p className="balance-tuning-note">曲线为 10 Hz 状态快照，保留最近 300 点；高频抖动需结合完整串口日志。参数、动作状态变化或数据缺口处断开连线。倾角范围排除起立过渡、转向、坐下和蓝牙控制采样。{gap ? '部分历史采样未读取，已保留缺口。' : ''}</p>
      <div className="balance-traces">
        <Trace points={points} title="机身倾角" unit="°" series={[{ index: 4, label: '倾角', color: '#315f9e', scale: 100 }]} />
        <Trace points={points} title="俯仰与转向角速度" unit="传感器原始值" series={[{ index: 5, label: '俯仰', color: '#315f9e' }, { index: 6, label: '转向', color: '#ac6634' }]} />
        <Trace points={points} title="校准后角速度" unit="与原始值相同的单位" series={[{ index: 18, label: '俯仰', color: '#315f9e', scale: 100 }, { index: 19, label: '转向', color: '#ac6634', scale: 100 }]} />
        <Trace points={points} title="左右轮速度" unit="编码器计数 / 控制周期" series={[{ index: 7, label: '左轮', color: '#315f9e' }, { index: 8, label: '右轮', color: '#ac6634' }]} />
        <Trace points={points} title="控制环输出" unit="PWM" series={[{ index: 9, label: '平衡', color: '#315f9e' }, { index: 10, label: '速度', color: '#ac6634' }, { index: 11, label: '转向', color: '#598168' }]} />
        <Trace points={points} title="电机输出" unit="PWM" series={[{ index: 12, label: '左轮', color: '#315f9e' }, { index: 13, label: '右轮', color: '#ac6634' }]} />
        <Trace points={points} title="速度积分" unit="积分累计值" series={[{ index: 14, label: '积分', color: '#598168' }]} />
      </div>
    </>}
  </section>;
}
