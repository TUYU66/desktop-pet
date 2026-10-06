import { useCallback, useEffect, useRef, useState } from 'react';
import { Alert, Button, Select, Slider, Space, Tag } from 'antd';
import request from '../../api/request';
import './device-panel.css';
import MotorCalibration, { type CalibrationState } from './MotorCalibration';
import BalanceTuning from './BalanceTuning';
import FacePreview from './FacePreview';

type Snapshot = { deviceId: string; online: boolean; observedAt: number; deviceState: number | null; configState?: string;
  hardware: { audio_speaker?: {volume?:number}; screen?:{brightness?:number}; battery?:{level:number;charging:boolean}; network?:{signal:string} };
  chassis?: {connected:boolean;statusValid:boolean;batteryMv:number;batteryPercent?:number;balanceStopped:boolean|null;lowBattery:boolean|null;
    activeAction?:string;busy?:boolean;phase?:string;lastResult?:string;motionValid?:boolean;calibration?:CalibrationState};
  face: {eyeColor:number;mouthColor:number;expression:string} | null; controls: Record<string,boolean>;
  music: {state:string;title:string;artist:string;name:string;error?:string} | null };
const expressions=[['neutral','普通'],['happy','开心'],['laughing','大笑'],['loving','喜爱'],['angry','生气'],['sad','难过'],['crying','哭泣'],['tired','疲惫'],['sleepy','困倦'],['confused','疑惑'],['curious','好奇'],['surprised','惊讶'],['shy','害羞'],['confident','自信'],['winking','眨眼'],['peek_left','向左偷看'],['peek_right','向右偷看'],['daydream','发呆'],['yawn','打哈欠']].map(([value,label])=>({value,label}));
const stateNames: Record<number,string>={0:'未知',1:'启动中',2:'配网中',3:'待命',4:'连接中',5:'聆听中',6:'说话中',7:'升级中',8:'激活中',9:'音频测试',10:'设备异常'};
const faceNames: Record<string,string>={...Object.fromEntries(expressions.map(e=>[e.value,e.label])),singing:'唱歌',thinking:'思考',listening:'聆听',reminder_ack:'提醒已确认',reminder_call:'提醒呼叫',reminder_wait:'等待提醒确认'};
const hex=(n:number)=>'#'+n.toString(16).padStart(6,'0');
const actionNames: Record<string,string>={stand_up:'起立',rest:'休息',turn_left:'左转',turn_right:'右转',turn_around:'向后转',bluetooth_on:'开启蓝牙控制',bluetooth_off:'退出蓝牙控制'};
const phaseNames: Record<string,string>={idle:'待命',rising:'正在起立',settling_before:'等待站稳后转向',rotating:'正在转向',settling_after:'等待转向后站稳',resting:'休息阶段',waiting_audio:'等待播报结束',awaiting_confirmation:'等待接收确认',awaiting_feedback:'等待动作反馈'};
const resultNames: Record<string,string>={pending:'请求处理中',completed:'设备已反馈完成',accepted:'已接受请求（尚未确认动作完成）',failed:'动作未完成',not_sent:'未发送',unconfirmed:'结果待核实',unknown:'暂无有效结果'};

export default function DevicePanel() {
  const [devices,setDevices]=useState<string[]>([]);
  const [device,setDevice]=useState('');
  const [state,setState]=useState<Snapshot|null>(null);
  const [error,setError]=useState('');
  const [requestBusy,setRequestBusy]=useState(false);
  const [refreshing,setRefreshing]=useState(false);
  const [needsRefresh,setNeedsRefresh]=useState(true);
  const [feedback,setFeedback]=useState('');
  const [verified,setVerified]=useState(false);
  const [volume,setVolume]=useState<number|null>(null);
  const [brightness,setBrightness]=useState<number|null>(null);
  const [eye,setEye]=useState<string|null>(null);
  const [mouth,setMouth]=useState<string|null>(null);
  const [expression,setExpression]=useState('happy');
  const [localExpression,setLocalExpression]=useState<string|null>(null);
  const generation=useRef(0);
  const inflight=useRef(false);
  const controlling=useRef(false);
  const snapshot=useRef<Snapshot|null>(null);
  const refreshQueued=useRef(false);
  const facePreviewUntil=useRef(0);
  const pollNow=useRef<(()=>void)|null>(null);
  const load=useCallback(async()=>{
    if(document.hidden)return;
    if(inflight.current || controlling.current){refreshQueued.current=true;return;}
    inflight.current=true;
    setRefreshing(true);
    const token=generation.current;
    try {
      const res=await request.get('/devices/panel',{params:device?{deviceId:device}:{}});
      if(token!==generation.current)return;
      if(!Array.isArray(res.data?.devices) || res.data.selected === undefined)throw new Error('设备状态格式不完整，请刷新核实');
      if(snapshot.current && snapshot.current.deviceId!==res.data.selected?.deviceId){
        setEye(null);setMouth(null);setLocalExpression(null);facePreviewUntil.current=0;
      }
      snapshot.current=res.data.selected;
      setDevices(res.data.devices); setState(res.data.selected); setError('');setNeedsRefresh(false);
      if(device && !res.data.devices.includes(device))setDevice('');
    } catch(e) {if(token===generation.current)setError(e instanceof Error?e.message:'状态读取失败');}
    finally {inflight.current=false;setRefreshing(false);}
  },[device]);
  useEffect(()=>{
    ++generation.current;snapshot.current=null;facePreviewUntil.current=0;setState(null);setNeedsRefresh(true);setError('');setFeedback('');setVolume(null);setBrightness(null);setEye(null);setMouth(null);setLocalExpression(null);
    let cancelled=false;
    let pollCycle=0;
    let timer: number | undefined;
    const poll=async()=>{
      if(cancelled || document.hidden)return;
      const cycle=pollCycle;
      refreshQueued.current=false;
      await load();
      if(cancelled || document.hidden || cycle!==pollCycle)return;
      const motion=snapshot.current?.chassis;
      const executing=motion?.busy===true || (motion?.motionValid===true && ['rising','settling_before','rotating','settling_after'].includes(motion.phase||''));
      const fast=controlling.current || inflight.current || refreshQueued.current || executing || Date.now()<facePreviewUntil.current || snapshot.current?.deviceState===6;
      // Follow reported expressions; local color/pose drafts render without a request.
      timer=window.setTimeout(()=>void poll(),fast?1000:2000);
    };
    pollNow.current=()=>{
      ++pollCycle;window.clearTimeout(timer);
      if(!document.hidden)void poll();
    };
    const visible=()=>{
      ++pollCycle;window.clearTimeout(timer);setNeedsRefresh(true);
      if(!document.hidden)void poll();
    };
    void poll();
    document.addEventListener('visibilitychange',visible);
    return()=>{cancelled=true;++generation.current;pollNow.current=null;window.clearTimeout(timer);document.removeEventListener('visibilitychange',visible);};
  },[load]);
  const control=async(action:string,values:Record<string,unknown>)=>{
    if(!state || !state.online || error || needsRefresh || controlling.current){
      if(action==='volume')setVolume(null);
      if(action==='brightness')setBrightness(null);
      if(!controlling.current){setVerified(false);setFeedback('操作未发送，请刷新设备状态后重新调整');}
      return;
    }
    controlling.current=true;setRequestBusy(true);setVerified(false);setFeedback('正在发送，等待设备确认…');
    // Ignore any polling response started before this control and its verified read-back.
    const token=++generation.current;
    try{
      const res=await request.post('/devices/panel',{deviceId:state.deviceId,action,...values});
      if(token!==generation.current)return;
      if(res.data.confirmation!=='verified')throw new Error('尚未获得执行确认，请刷新核实');
      if(!res.data.selected)throw new Error('实际状态尚未返回，请刷新核实');
      snapshot.current=res.data.selected;setState(res.data.selected);setNeedsRefresh(document.hidden);setError('');setVerified(true);setFeedback('设备已执行，实际状态已核对');
      if(action==='volume')setVolume(null);if(action==='brightness')setBrightness(null);if(action==='colors'){setEye(null);setMouth(null);}
      if(action==='expression'){setLocalExpression(null);facePreviewUntil.current=Date.now()+9000;}
    }catch(e){if(token===generation.current){setVolume(null);setBrightness(null);setFeedback(e instanceof Error?e.message:'执行结果未知，请刷新核实');setError('请刷新设备状态后再操作');}}
    finally{controlling.current=false;setRequestBusy(false);if(token===generation.current)pollNow.current?.();}
  };
  // Background polling keeps the last valid snapshot usable while it updates.
  const fresh=!!state&&!error&&!needsRefresh;
  const networkValid=fresh&&state?.online===true;
  useEffect(()=>{
    // A failed read or invalidated connection must not leave an unsent slider value visible.
    if(!networkValid){setVolume(null);setBrightness(null);}
  },[networkValid]);
  const chassis=state?.chassis;
  const chassisConnected=networkValid&&chassis?.connected===true;
  const batteryValid=chassisConnected&&chassis?.statusValid===true&&Number.isFinite(chassis.batteryMv)&&chassis.batteryMv>0;
  const percent=chassis?.batteryPercent;
  const percentValid=batteryValid&&typeof percent==='number'&&Number.isFinite(percent)&&percent>=0&&percent<=100;
  const motionValid=chassisConnected&&chassis?.motionValid===true;
  const motionSupported=chassis?.motionValid!==undefined;
  const requestPhase=chassis?.phase==='waiting_audio'||chassis?.phase==='awaiting_confirmation'||chassis?.phase==='awaiting_feedback';
  const available=(key:string)=>!!state?.controls?.[key]&&networkValid&&!requestBusy;
  const playing=state?.music?.state==='playing';
  const musicActive=playing||state?.music?.state==='loading';
  const mode=playing?'播放音乐':state?.deviceState==null?'状态未上报':stateNames[state.deviceState]||'未知';
  const faceDraft=localExpression!==null || eye!==null || mouth!==null;
  const shownExpression=localExpression??state?.face?.expression??null;
  const shownFaceName=shownExpression?(faceNames[shownExpression]||shownExpression):'未上报';
  return <section className="surface live-device-panel" aria-label="设备控制面板">
    <div className="section-heading"><div><h2>设备控制台</h2><p className="live-device-note">设备实际状态与快捷控制</p></div><Space wrap>{devices.length>1&&<Select aria-label="选择控制设备" value={device||undefined} placeholder="选择在线设备" allowClear disabled={requestBusy} style={{minWidth:220}} options={devices.map(id=>({value:id,label:id}))} onChange={value=>setDevice(value||'')}/>}<Button onClick={()=>{setNeedsRefresh(true);void load();}} loading={refreshing} disabled={requestBusy}>刷新状态</Button></Space></div>
    {error&&<Alert type="warning" title="状态暂时无法更新" description={error+'；下方可能是上次读取的状态。'}/>}
    {state?.configState==='pending'&&<Alert type="info" title="新配置已保存，将在当前聊天或音乐结束后应用。"/>}
    {state?.configState==='failed'&&<Alert type="warning" title="新配置尚未应用成功，请重新保存设置。"/>}
    {feedback&&<div aria-live="polite"><Alert type={requestBusy?'info':verified?'success':'warning'} title={feedback}/></div>}
    {!state?<p className="live-device-note">{error?'无法确认设备状态。':refreshing?'正在读取设备状态。':devices.length>1?'请选择在线设备。':devices.length===1?'正在确认设备状态，请稍后刷新。':'没有在线设备，连接桌面宠物后刷新。'}</p>:<>
      <div className="live-device-summary"><Tag color={networkValid?'green':'default'}>网络：{!fresh?'待核实':state.online?'在线':'离线'}</Tag><strong>{fresh?mode:'设备状态待核实'}</strong><span>{state.deviceId}</span><span>采集于 {new Date(state.observedAt).toLocaleTimeString('zh-CN')}</span><Tag color={chassisConnected?'blue':'default'}>底盘：{!networkValid?'待核实':chassisConnected?'串口已连接':'未连接'}</Tag><span>电池：{!batteryValid?'电量暂不可用':`${percentValid?`${percent}% · `:'百分比未上报 · '}${((chassis?.batteryMv??0)/1000).toFixed(2)} V`}{batteryValid&&chassis?.lowBattery?' · 电压偏低':''}</span></div>
      {batteryValid&&<p className="live-device-note">底盘电量百分比按 3 串锂电电压估算，行驶中会波动。</p>}
      <div className="live-motion-summary" aria-live="polite">
        <div><strong>身体动作</strong><Tag color={motionValid?'blue':'default'}>{!networkValid?'状态待核实':!motionSupported?'当前固件未上报动作状态':!motionValid?'动作反馈暂不可用':chassis?.busy===true?'动作处理中':chassis?.busy===false?'动作空闲':'忙碌状态未上报'}</Tag></div>
        {motionValid&&<p>{chassis?.busy&&chassis.activeAction?`${actionNames[chassis.activeAction]||'动作'} · `:''}{phaseNames[chassis?.phase||'']||'阶段未知'}</p>}
        {!motionValid&&networkValid&&requestPhase&&chassis?.busy&&<p>请求状态：{phaseNames[chassis.phase||'']}；尚未确认当前身体动作。</p>}
        {networkValid&&motionSupported&&<p>最近结果：{resultNames[chassis?.lastResult||'unknown']||'结果待核实'}</p>}
        {requestBusy&&<p>网页设置正在发送，等待设备确认。</p>}
      </div>
      <div className="live-device-grid">
        <div className="live-control"><h3>声音与屏幕</h3>
          <div className="device-level-control"><div className="device-level-heading"><span id="device-volume-label">音量</span><output>{volume??state.hardware.audio_speaker?.volume??'未上报'}</output></div><Slider ariaLabelledByForHandle="device-volume-label" min={0} max={100} value={volume??state.hardware.audio_speaker?.volume??0} onChange={setVolume} onChangeComplete={value=>{if(value!==state.hardware.audio_speaker?.volume)void control('volume',{value});else setVolume(null);}} disabled={!available('volume')||state.hardware.audio_speaker?.volume==null}/></div>
          <div className="device-level-control"><div className="device-level-heading"><span id="device-brightness-label">亮度</span><output>{brightness??state.hardware.screen?.brightness??'未上报'}</output></div><Slider ariaLabelledByForHandle="device-brightness-label" min={0} max={100} value={brightness??state.hardware.screen?.brightness??0} onChange={setBrightness} onChangeComplete={value=>{if(value!==state.hardware.screen?.brightness)void control('brightness',{value});else setBrightness(null);}} disabled={!available('brightness')||state.hardware.screen?.brightness==null}/></div>
          <p className="live-device-note">松开滑条后发送，设备确认后更新实际数值。</p>
        </div>
        <div className="live-control face-control"><h3>表情与颜色</h3>
          <div className="face-preview-section">
            <div className="face-preview-heading"><strong>LCD 脸部预览</strong><span className={`face-preview-status${faceDraft?' is-draft':''}`}><i aria-hidden="true"/>{faceDraft?'本地预览':networkValid?'跟随设备':'上次状态'}</span></div>
            <FacePreview expression={shownExpression} label={shownFaceName} eyeColor={eye??hex(state.face?.eyeColor??0x66ccff)} mouthColor={mouth??hex(state.face?.mouthColor??0x66ccff)} speaking={localExpression===null&&networkValid&&state.deviceState===6&&shownExpression!=='reminder_wait'} animate={faceDraft||!!networkValid}/>
            <div className="face-preview-caption"><span>{shownFaceName}</span>{faceDraft&&<Button type="text" size="small" disabled={requestBusy} onClick={()=>{setLocalExpression(null);setEye(null);setMouth(null);}}>取消本地预览</Button>}</div>
            <p className="face-preview-note">{faceDraft?[localExpression!==null?'本地表情预览':'',eye!==null||mouth!==null?'颜色待保存确认':''].filter(Boolean).join('；')+'。':networkValid?'表情与颜色随设备状态更新，动画为示意。':'当前显示上次上报的状态，连接恢复后继续同步。'}</p>
          </div>
          <div className="face-control-section"><div className="face-control-heading"><span>设备表情</span><span>当前：{faceNames[state.face?.expression||'']||'未上报'}</span></div>
            <div className="face-expression-row"><Select aria-label="选择表情" value={expression} onChange={value=>{setExpression(value);setLocalExpression(value);}} options={expressions} disabled={!available('expression')||musicActive}/><Button disabled={!available('expression')||musicActive} onClick={()=>{setLocalExpression(expression);void control('expression',{value:expression});}}>LCD 预览 8 秒</Button></div>
          </div>
          <div className="face-control-section"><div className="face-control-heading"><span>屏幕颜色</span><span>重启后保留</span></div>
            <div className="face-color-controls"><label className="face-color-choice"><span>眼睛</span><input aria-label="眼睛颜色" type="color" value={eye??hex(state.face?.eyeColor??0x66ccff)} disabled={!available('colors')} onChange={e=>setEye(e.target.value)}/></label><label className="face-color-choice"><span>嘴巴</span><input aria-label="嘴巴颜色" type="color" value={mouth??hex(state.face?.mouthColor??0x66ccff)} disabled={!available('colors')} onChange={e=>setMouth(e.target.value)}/></label></div>
            <div className="face-color-actions"><Button disabled={!available('colors')||(!eye&&!mouth)} onClick={()=>void control('colors',{...(eye?{eye_color:parseInt(eye.slice(1),16)}:{}),...(mouth?{mouth_color:parseInt(mouth.slice(1),16)}:{})})}>保存颜色</Button><Button type="text" disabled={!available('colors')} onClick={()=>void control('colors',{eye_color:0x66ccff,mouth_color:0x66ccff})}>恢复默认</Button></div>
          </div>
          {!state.controls?.colors&&<p className="live-device-note">当前固件未上报颜色控制能力，请更新固件。</p>}
        </div>
      </div>
      <BalanceTuning key={`tuning:${state.deviceId}`} device={state.deviceId} online={!!networkValid} />
      <MotorCalibration key={state.deviceId} device={state.deviceId} initial={chassis?.calibration} balanceStopped={chassis?.balanceStopped} online={!!networkValid} />
    </>}
  </section>;
}
