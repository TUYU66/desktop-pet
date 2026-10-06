import { useEffect, useRef, useState } from 'react';
import { Alert, Button, Checkbox, InputNumber, Select, Space, Table, Tag } from 'antd';
import request from '../../api/request';
import { holdingOutput, holdingValuesValid, observationReady, START_ASSIST_MS, TRIAL_LIMIT_MS } from './calibrationTrial';
import type { HoldingTrial } from './calibrationTrial';
import './motor-calibration.css';

export type CalibrationState = {
  supported: boolean; valid: boolean; tick: number | null; active: number | null;
  session: number | null; wheel: number | null; pwm: number | null;
  leftCounts: number | null; rightCounts: number | null;
  leftTravel: number | null; rightTravel: number | null;
  reason: number | null; runMs: number | null; batteryMv: number | null;
};
type Measurement = { key: string; label: string; start?: number; running?: number; batteryMv?: number; at?: string };
type TrialResult = { key: string; pwm: number; batteryMv?: number; seconds: number };
const reasons: Record<number, string> = {
  0: '等待手动操作', 1: '网页指令超时，已停轮', 2: '校准超时，已退出',
  3: '电压不足，已退出', 4: '姿态数据超时，已退出', 5: '倾角异常，已退出',
  6: '按键停止，已退出', 7: '连续试转达到 8 秒，已停轮', 8: '已退出校准',
};
const blank: Measurement[] = [
  { key: '1:1', label: '左轮 · 正转' }, { key: '1:-1', label: '左轮 · 反转' },
  { key: '2:1', label: '右轮 · 正转' }, { key: '2:-1', label: '右轮 · 反转' },
];

export default function MotorCalibration({ device, initial, balanceStopped, online }: {
  device: string; initial?: CalibrationState; balanceStopped: boolean | null | undefined; online: boolean;
}) {
  const [state, setState] = useState<CalibrationState | undefined>(initial);
  const [confirmed, setConfirmed] = useState(false);
  const [wheel, setWheel] = useState(1);
  const [direction, setDirection] = useState(1);
  const [value, setValue] = useState(0);
  const [step, setStep] = useState(50);
  const [busy, setBusy] = useState(false);
  const [running, setRunning] = useState(false);
  const [mode, setMode] = useState<'start' | 'holding'>('start');
  const [startup, setStartup] = useState(0);
  const [clock, setClock] = useState(0);
  const [result, setResult] = useState<TrialResult | null>(null);
  const [feedback, setFeedback] = useState('');
  const [records, setRecords] = useState<Measurement[]>(blank);
  const session = useRef(0);
  const epoch = useRef(0);
  const current = useRef({ wheel, direction, value });
  const observed = useRef({ tick: -1, received: 0 });
  const alive = useRef(true);
  const stopped = useRef(true);
  const runTimer = useRef<number | undefined>(undefined);
  const leaseTimer = useRef<number | undefined>(undefined);
  const trial = useRef<HoldingTrial | null>(null);
  const runStarted = useRef(0);
  const latestState = useRef(state);
  const preparing = useRef(false);
  const stopRequests = useRef(0);
  current.current = { wheel, direction, value };

  const apply = (next: CalibrationState) => {
    const tick = next.tick;
    if (typeof tick !== 'number' || next.valid !== true) return;
    const previous = observed.current.tick;
    if (previous >= 0 && ((tick - previous) >>> 0) > 0x7fffffff) return;
    if (tick !== previous) observed.current = { tick, received: Date.now() };
    latestState.current = next;
    if (alive.current) setState(next);
  };
  useEffect(() => { if (initial) apply(initial); }, [initial]);
  useEffect(() => {
    try {
      const saved = JSON.parse(localStorage.getItem(`motor-calibration:${device}`) || 'null');
      if (Array.isArray(saved)) setRecords(blank.map(row => ({ ...row, ...saved.find(s => s?.key === row.key) })));
    } catch { /* A corrupt local draft does not affect the device. */ }
  }, [device]);
  const savedStartup = records.find(row => row.key === `${wheel}:${direction}`)?.start;
  useEffect(() => { setStartup(savedStartup ?? 0); setResult(null); }, [wheel, direction, savedStartup]);
  useEffect(() => {
    if (!running) return;
    const timer = window.setInterval(() => setClock(performance.now()), 100);
    return () => window.clearInterval(timer);
  }, [running]);

  const send = async (operation: 'arm' | 'set' | 'exit', pwm = 0, targetSession = session.current) => {
    const res = await request.post('/devices/calibration', {
      deviceId: device, operation, wheel: current.current.wheel, pwm,
      confirmed, session: targetSession, sample_tick: Math.max(0, observed.current.tick) & 0x7fffffff,
    });
    const next = res.data?.state as CalibrationState | undefined;
    if (!next || next.valid !== true) throw new Error('没有获得完整设备确认，请停止并刷新');
    return next;
  };
  const stop = async () => {
    stopped.current = true;
    const token = ++epoch.current;
    trial.current = null;
    window.clearTimeout(runTimer.current);
    window.clearTimeout(leaseTimer.current);
    session.current = 0;
    ++stopRequests.current;
    if (alive.current) { setBusy(true); setRunning(false); setResult(null); setFeedback('正在请求停轮并退出校准…'); }
    try {
      const next = await send('exit', 0, 0);
      if (next.active !== 0 || next.pwm !== 0) throw new Error('设备尚未确认停轮并退出');
      if (alive.current && token === epoch.current) { apply(next); setFeedback('已确认停轮并退出校准；恢复平衡需重新发起起立。'); }
      return true;
    } catch (e) {
      if (alive.current && token === epoch.current) setFeedback(`${e instanceof Error ? e.message : '停止结果待确认'}。停止续发指令后，本地保护会在 1.5 秒内停轮。`);
      return false;
    } finally {
      --stopRequests.current;
      if (alive.current) setBusy(preparing.current || stopRequests.current > 0);
    }
  };
  useEffect(() => {
    alive.current = true;
    const leave = () => { if (session.current || preparing.current) void stop(); };
    const visibility = () => { if (document.hidden) leave(); };
    const escape = (e: KeyboardEvent) => { if (e.key === 'Escape') leave(); };
    document.addEventListener('visibilitychange', visibility);
    window.addEventListener('pagehide', leave);
    window.addEventListener('keydown', escape);
    return () => {
      alive.current = false;
      leave();
      ++epoch.current;
      window.clearTimeout(runTimer.current);
      window.clearTimeout(leaseTimer.current);
      document.removeEventListener('visibilitychange', visibility);
      window.removeEventListener('pagehide', leave);
      window.removeEventListener('keydown', escape);
    };
    // Component is keyed by device; stop uses that device even on navigation.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);
  useEffect(() => { if (!online && (session.current || preparing.current)) void stop(); }, [online]);

  useEffect(() => {
    if (!online || state?.active !== 1) return;
    const refresh = async () => {
      if (!session.current || !stopped.current || document.hidden) return;
      try {
        const res = await request.get('/devices/panel', { params: { deviceId: device } });
        const next = res.data?.selected?.chassis?.calibration as CalibrationState | undefined;
        if (alive.current && next) apply(next);
      } catch { if (alive.current) setFeedback('校准状态未更新，请刷新后再试转。'); }
    };
    const timer = window.setInterval(() => void refresh(), 1000);
    return () => window.clearInterval(timer);
    // The component is keyed by device; apply ignores older snapshots.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [online, state?.active, device]);

  const arm = async () => {
    if (!confirmed || busy || running || preparing.current || stopRequests.current > 0) return;
    preparing.current = true;
    setBusy(true); setFeedback('正在进入校准，电机保持停止…');
    const token = ++epoch.current;
    try {
      const next = await send('arm', 0, 0);
      if (!alive.current || token !== epoch.current || document.hidden) { await send('exit', 0, 0).catch(() => undefined); return; }
      session.current = next.session || 0;
      apply(next); setFeedback('已进入校准。请选择轮子、方向和输出，再开始限时试转。');
    } catch (e) { if (alive.current && token === epoch.current) setFeedback(e instanceof Error ? e.message : '校准未确认'); }
    finally { preparing.current = false; if (alive.current) setBusy(stopRequests.current > 0); }
  };
  const finishHolding = async () => {
    if (stopped.current) return;
    const activeTrial = trial.current;
    const now = performance.now();
    const nextResult = activeTrial && observationReady(activeTrial, now) &&
      latestState.current?.active === 1 && latestState.current.session === session.current &&
      latestState.current.reason === 0 && latestState.current.wheel === current.current.wheel &&
      latestState.current.pwm === activeTrial.target * current.current.direction &&
      Date.now() - observed.current.received <= 1500 ? {
        key: `${current.current.wheel}:${current.current.direction}`, pwm: activeTrial.target,
        batteryMv: latestState.current.batteryMv ?? undefined,
        seconds: Math.floor((now - activeTrial.targetConfirmedAt!) / 1000),
      } : null;
    const expectedEpoch = epoch.current + 1;
    const didStop = await stop();
    if (!alive.current || epoch.current !== expectedEpoch || !didStop) return;
    if (nextResult) {
      setResult(nextResult);
      setFeedback(`试转结束，已确认停轮。待测输出 ${nextResult.pwm} 已观察约 ${nextResult.seconds} 秒；请按实际转动情况选择结果。`);
    } else setFeedback('已停轮，本轮没有获得足够的待测输出确认，请重新测试；保护停轮不代表数值不通过。');
  };
  const start = async () => {
    if (!confirmed || busy || running || preparing.current || stopRequests.current > 0 || current.current.value === 0 || document.hidden) return;
    const isHolding = mode === 'holding';
    if (isHolding ? !holdingValuesValid(startup, current.current.value) : !session.current) return;
    if (session.current && Date.now() - observed.current.received > 1500) return;
    const token = ++epoch.current;
    setResult(null);
    if (isHolding && !session.current) {
      preparing.current = true;
      setBusy(true); setFeedback('正在准备维持值测试，电机保持停止…');
      try {
        const next = await send('arm', 0, 0);
        if (!alive.current || token !== epoch.current || document.hidden) { await send('exit', 0, 0).catch(() => undefined); return; }
        if (next.active !== 1 || !next.session || next.pwm !== 0) throw new Error('没有确认进入停轮校准状态');
        session.current = next.session;
        apply(next);
      } catch (e) {
        if (alive.current && token === epoch.current) {
          const message = e instanceof Error ? e.message : '准备测试失败';
          await stop();
          if (alive.current && epoch.current === token + 1) setFeedback(`${message}；请核对停轮状态后重试。`);
        }
        return;
      } finally { preparing.current = false; if (alive.current) setBusy(stopRequests.current > 0); }
    }
    const targetSession = session.current;
    runStarted.current = performance.now();
    trial.current = isHolding ? {
      startedAt: runStarted.current, startupConfirmedAt: null, targetConfirmedAt: null,
      startup, target: current.current.value,
    } : null;
    stopped.current = false; setRunning(true); setClock(runStarted.current);
    setFeedback(isHolding ? '先用启动值试转，确认输出后约 1.2 秒自动降到待测值。请观察是否持续转动。' : '正在试转，可调整输出；最多 8 秒，随时按停止或 Esc。');
    runTimer.current = window.setTimeout(() => { if (isHolding) void finishHolding(); else void stop(); }, TRIAL_LIMIT_MS);
    const pulse = async () => {
      if (stopped.current || token !== epoch.current || document.hidden) return;
      const activeTrial = trial.current;
      const output = activeTrial ? holdingOutput(activeTrial, performance.now()) : current.current.value;
      if (output === null || performance.now() - runStarted.current >= TRIAL_LIMIT_MS) {
        if (isHolding) await finishHolding(); else await stop();
        return;
      }
      const pwm = output * current.current.direction;
      try {
        const next = await send('set', pwm, targetSession);
        if (stopped.current || token !== epoch.current || !alive.current) return;
        if (next.active !== 1 || next.session !== targetSession || next.wheel !== current.current.wheel || next.pwm !== pwm || next.reason !== 0) {
          throw new Error('设备未确认当前试转输出，可能已触发保护');
        }
        apply(next);
        if (activeTrial) {
          const now = performance.now();
          if (activeTrial.startupConfirmedAt === null) activeTrial.startupConfirmedAt = now;
          // Matching numeric values do not count as observation until the assist phase has ended.
          if (output === activeTrial.target && now - activeTrial.startupConfirmedAt >= START_ASSIST_MS && activeTrial.targetConfirmedAt === null) {
            activeTrial.targetConfirmedAt = now;
            setFeedback(`已确认待测输出 ${output}，现在观察是否持续转动；转速变慢是正常的。`);
          }
        }
        leaseTimer.current = window.setTimeout(() => void pulse(), 400);
      } catch (e) {
        if (token !== epoch.current || !alive.current) return;
        const message = e instanceof Error ? e.message : '未获得试转确认';
        await stop();
        if (alive.current && epoch.current === token + 1) setFeedback(`${message}；已停止续发试转指令，请核对停轮状态。`);
      }
    };
    void pulse();
  };
  const adjust = (next: number | null) => {
    const n = Math.max(0, Math.min(2600, Math.round(next ?? 0)));
    current.current.value = n; setValue(n);
    if (n === 0 && running) void stop();
  };
  const actual = state?.pwm ?? 0;
  const owned = !!session.current && session.current === state?.session && state?.active === 1;
  const canRecord = owned && running && state?.valid === true && Date.now() - observed.current.received <= 1500 &&
    actual !== 0 && actual === direction * value && state?.wheel === wheel && state?.reason === 0;
  const canFinishHolding = mode === 'holding' && canRecord && !!trial.current && observationReady(trial.current, clock);
  const canPrepare = online && confirmed && state?.supported && state.valid && !busy && !running &&
    ((owned && Date.now() - observed.current.received <= 1500) || (state.active !== 1 && balanceStopped === true));
  const mark = (phase: 'start' | 'running') => {
    if (!canRecord || Date.now() - observed.current.received > 1500) return;
    const next = records.map(row => row.key === `${wheel}:${direction}` ? {
      ...row, [phase]: Math.abs(actual), batteryMv: state?.batteryMv ?? undefined, at: new Date().toISOString(),
    } : row);
    setRecords(next);
    try { localStorage.setItem(`motor-calibration:${device}`, JSON.stringify(next)); } catch { /* Measurements remain available for download. */ }
    setFeedback(`已记录${phase === 'start' ? '启动' : '维持'}输出 ${Math.abs(actual)}，尚未应用到平衡控制。`);
  };
  const recordHoldingResult = () => {
    if (!result || busy || running || result.key !== `${wheel}:${direction}`) return;
    const next = records.map(row => row.key === result.key ? {
      ...row, running: result.pwm, batteryMv: result.batteryMv, at: new Date().toISOString(),
    } : row);
    setRecords(next);
    try { localStorage.setItem(`motor-calibration:${device}`, JSON.stringify(next)); } catch { /* Keep the downloadable result. */ }
    setResult(null);
    setFeedback(`已按你的确认记录维持输出 ${result.pwm}，尚未应用到平衡控制。`);
  };
  const rejectHolding = async () => {
    if (stopped.current || busy) return;
    const target = trial.current?.target;
    const nextEpoch = epoch.current + 1;
    const didStop = await stop();
    if (!alive.current || epoch.current !== nextEpoch || !didStop || target === undefined) return;
    adjust(target + step);
    setFeedback(`本轮不记录，已停轮；待测值上调 ${step}，请点击开始重新验证。如果启动阶段就没有转动，请先重新确认启动值。`);
  };
  const exportResults = () => {
    const url = URL.createObjectURL(new Blob([JSON.stringify({ deviceId: device, condition: 'wheels_suspended', measurements: records }, null, 2)], { type: 'application/json' }));
    const link = document.createElement('a'); link.href = url; link.download = 'motor-thresholds.json'; link.click();
    window.setTimeout(() => URL.revokeObjectURL(url), 1000);
  };

  return <section className="motor-calibration" aria-label="电机校准">
    <div className="motor-calibration-heading"><div><h3>电机校准</h3><p>先测量四个方向，再调整平衡。记录的数值不会自动应用。</p></div>
      <Button danger onClick={() => void stop()} disabled={!session.current && state?.active !== 1 && !busy}>停止并退出</Button></div>
    <details onToggle={e => { if (!e.currentTarget.open && (session.current || busy)) void stop(); }}><summary>打开手动校准面板</summary>
      <Alert type="warning" title="固定车身，让两个轮子完全悬空" description="先坐下并关闭蓝牙控制。校准期间暂停平衡；不要放在地面试转。板载 KEY1、Esc 或下方停止按钮可停止。悬空测得的是空载阈值，之后还需核对地面负载。" />
      <Checkbox checked={confirmed} disabled={running || owned || busy} onChange={e => setConfirmed(e.target.checked)}>我已固定车身、两个轮子悬空，且已关闭蓝牙控制</Checkbox>
      <Space wrap className="motor-calibration-controls">
        <Select aria-label="测试类型" value={mode} disabled={running || busy} options={[{ value: 'start', label: '启动值测试' }, { value: 'holding', label: '维持值测试' }]} onChange={next => { setMode(next); setResult(null); }} />
        <Select aria-label="校准轮子" value={wheel} disabled={running || busy} options={[{ value: 1, label: '左轮' }, { value: 2, label: '右轮' }]} onChange={setWheel} />
        <Select aria-label="转动方向" value={direction} disabled={running || busy} options={[{ value: 1, label: '正转' }, { value: -1, label: '反转' }]} onChange={setDirection} />
        <Select aria-label="调整步长" value={step} options={[10, 25, 50, 100].map(n => ({ value: n, label: `每步 ${n}` }))} onChange={setStep} />
        <span>{mode === 'holding' ? '待测维持值' : '试转输出'}</span>
        <Button onClick={() => { setResult(null); adjust(value - step); }} disabled={value === 0 || busy || (running && mode === 'holding')}>−</Button>
        <InputNumber aria-label={mode === 'holding' ? '待测维持值' : '原始输出幅度'} min={0} max={2600} precision={0} value={value} disabled={busy || (running && mode === 'holding')} onChange={next => { setResult(null); adjust(next); }} />
        <Button onClick={() => { setResult(null); adjust(value + step); }} disabled={value === 2600 || busy || (running && mode === 'holding')}>＋</Button>
      </Space>
      {mode === 'holding' && <div className="motor-calibration-holding">
        <Space wrap><label htmlFor="motor-startup">可靠启动值</label><InputNumber id="motor-startup" min={1} max={2600} precision={0} value={startup || null} disabled={running || busy} onChange={next => { setStartup(Math.round(next ?? 0)); setResult(null); }} /><span>{savedStartup ? '已填入当前轮子与方向的启动记录，可修改' : '请先测出启动值，或填写已验证的数值'}</span></Space>
        <p>一次点击：启动值带动约 1.2 秒 → 自动降到待测值 → 观察 → 最多 8 秒停轮。每轮只测一个值；下一轮自动准备校准，仍需你点击开始。</p>
        {value > startup && startup > 0 && <p>待测维持值应小于或等于可靠启动值。</p>}
      </div>}
      <Space wrap>{mode === 'start' && <Button loading={busy} disabled={!online || !confirmed || owned || state?.active === 1 || !state?.supported || !state.valid || balanceStopped !== true} onClick={() => void arm()}>进入校准（保持停轮）</Button>}
        <Button type="primary" loading={busy} disabled={mode === 'holding' ? !canPrepare || !holdingValuesValid(startup, value) : !online || !owned || running || busy || value === 0 || Date.now() - observed.current.received > 1500} onClick={() => void start()}>{mode === 'holding' ? '启动并测试维持值' : '试转，最多 8 秒'}</Button>
        <Button danger disabled={!session.current && state?.active !== 1 && !busy} onClick={() => void stop()}>停止并退出</Button></Space>
      <div className="motor-calibration-readout" aria-live="polite"><Tag color={state?.active === 1 ? 'orange' : 'default'}>{state?.valid ? state.active === 1 ? '校准已开启' : '校准已关闭' : '状态待核实'}</Tag>
        {running && <span>剩余 <strong>{Math.max(0, (TRIAL_LIMIT_MS - (clock - runStarted.current)) / 1000).toFixed(1)} 秒</strong>{mode === 'holding' ? trial.current?.targetConfirmedAt === null ? ' · 启动阶段' : ' · 维持观察阶段' : ''}</span>}
        <span>实际输出：<strong>{state?.valid ? actual : '—'}</strong></span>
        <span>左轮计数：{state?.valid ? state.leftCounts : '—'}</span><span>右轮计数：{state?.valid ? state.rightCounts : '—'}</span>
        <span>{state?.valid ? reasons[state.reason ?? 0] : '请刷新设备状态'}</span></div>
      {!state?.supported && <p>请更新 STM32 和 ESP32 固件后刷新状态。</p>}
      {balanceStopped !== true && !owned && <p>请先发送“坐下”，确认电机停止后再校准。</p>}
      {feedback && <p role="status">{feedback}</p>}
      {mode === 'holding' && <Space wrap>
        <Button disabled={!canFinishHolding || busy} onClick={() => void finishHolding()}>观察足够，停轮后确认结果</Button>
        <Button danger disabled={!running || busy} onClick={() => void rejectHolding()}>停顿或停转，停轮并上调 {step}</Button>
      </Space>}
      {result && <div className="motor-calibration-result" role="status"><p>待测值 <strong>{result.pwm}</strong>，设备确认该输出后观察约 {result.seconds} 秒。是否一直持续转动？计数不会替你判定。</p><Space wrap>
        <Button type="primary" onClick={recordHoldingResult}>一直转动，记录 {result.pwm}</Button>
        <Button onClick={() => { setResult(null); adjust(result.pwm + step); setFeedback(`不记录本轮；待测值已增加 ${step}，请点击开始重新验证。`); }}>有停顿或停转，上调 {step} 再测</Button>
      </Space></div>}
      <ol><li>从低输出开始逐步增大。停轮后重复试转，找到能可靠启动的最小值，记为启动值。</li>
        <li>切换到维持值测试，填写可靠启动值和待测维持值。每轮自动先启动再降输出，停轮后按实际观察确认结果。通过后降低待测值，失败则提高待测值，再点击开始。</li>
        <li>分别测左、右轮的正、反转。计数只辅助观察，不能自动判定启动或持续转动。</li></ol>
      {mode === 'start' && <Space wrap><Button disabled={!canRecord} onClick={() => mark('start')}>确认转动，记为启动值</Button><Button disabled={!canRecord} onClick={() => mark('running')}>确认持续转动，记为维持值</Button></Space>}
      <Table size="small" pagination={false} dataSource={records} columns={[
        { title: '轮子与方向', dataIndex: 'label' },
        { title: '启动值', dataIndex: 'start', render: (v?: number) => v ?? '待测' },
        { title: '维持值', dataIndex: 'running', render: (v?: number) => v ?? '待测' },
      ]} />
      <Button onClick={exportResults} disabled={!records.some(row => row.start !== undefined || row.running !== undefined)}>下载测量结果</Button>
    </details>
  </section>;
}
