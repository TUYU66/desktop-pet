import { Button, Select, Slider, Space } from 'antd';
import { CustomerServiceOutlined, PauseCircleOutlined, PlayCircleOutlined, StepBackwardOutlined, StepForwardOutlined, StopOutlined } from '@ant-design/icons';
import { useEffect, useRef, useState } from 'react';
import LyricsPanel from './LyricsPanel';
import { albumCover } from './lyrics';

export type Source = 'local' | 'netease';
export type PlaybackMode = 'sequence' | 'shuffle' | 'single';
export type PlayerState = { state: string; trackId: string | null; name: string; title: string; artist: string;
  volume: number; position: number; error: string; durationMs?: number; cover?: string; album?: string; mode?: PlaybackMode };
export type Status = PlayerState & { source?: Source; players?: Record<Source, PlayerState> };
export const labels: Record<string, string> = { stopped: '已停止', loading: '准备播放', playing: '正在播放', paused: '已暂停', error: '播放失败' };
export type Control = (action: string, source: Source, trackId?: string, volume?: number, mode?: PlaybackMode, position?: number) => Promise<void>;
const clock = (seconds: number) => `${Math.floor(seconds / 60)}:${String(Math.floor(seconds % 60)).padStart(2, '0')}`;

export default function PlayerControls({ source, state, deviceId, busy, canCancel = false, canStart, control }:
  { source: Source; state?: PlayerState; deviceId: string; busy: boolean; canCancel?: boolean; canStart: boolean; control: Control }) {
  const [volumeDraft, setVolumeDraft] = useState<number | null>(null);
  const [seekDraft, setSeekDraft] = useState<number | null>(null);
  const [seconds, setSeconds] = useState(state?.position || 0);
  const [coverFailed, setCoverFailed] = useState(false);
  const seekTarget = useRef<{ trackId: string; deviceId: string; source: Source } | undefined>(undefined);
  useEffect(() => { setVolumeDraft(null); }, [deviceId, state?.volume]);
  const playing = state?.state === 'playing' || state?.state === 'loading';
  const duration = (state?.durationMs || 0) / 1000;
  useEffect(() => {
    const at = performance.now(), position = state?.position || 0;
    const update = () => setSeconds(Math.min(duration || Infinity, position + (state?.state === 'playing' ? (performance.now()-at)/1000 : 0)));
    update(); const timer = setInterval(update, 200); return () => clearInterval(timer);
  }, [state, duration]);
  useEffect(() => { seekTarget.current = undefined; setSeekDraft(null); }, [state?.trackId, deviceId, source]);
  useEffect(() => { setCoverFailed(false); }, [state?.cover, state?.trackId, deviceId, source]);
  const cover = !coverFailed && albumCover(state?.cover);
  return <div className="music-player-details"><section className="music-player" aria-label={source === 'local' ? '本地歌曲播放控制' : '网易云播放控制'}>
    {cover ? <img className="music-album-cover" src={cover} alt={`${state?.album || state?.title || '歌曲'}专辑封面`} referrerPolicy="no-referrer" onError={() => setCoverFailed(true)} /> :
      <div className={`music-disc ${playing ? 'is-playing' : ''}`} aria-hidden="true"><CustomerServiceOutlined /></div>}
    <div className="music-current"><span>{deviceId ? labels[state?.state || 'stopped'] : '设备未连接'}{state?.trackId ? ` · ${source === 'local' ? '本地歌曲' : '网易云'}` : ''}</span>
      <h2>{state?.title || state?.name || '选一首喜欢的歌'}</h2>
      <p>{state?.name ? `${state.artist || '未知演唱者'}${state.album ? ' · '+state.album : ''}` : '选择下方歌曲，在桌面宠物上播放'}</p>
      {state?.trackId && <div className="music-timeline">
        <Slider ariaLabelForHandle="歌曲播放进度" min={0} max={duration || 1} step={.1} value={seekDraft ?? Math.min(seconds, duration || 1)}
          disabled={busy || !deviceId || !duration || state.state === 'loading'} tooltip={{ formatter: value => clock(value || 0) }}
          onChange={value => { if (!seekTarget.current && state.trackId) seekTarget.current = { trackId: state.trackId, deviceId, source }; setSeekDraft(value); }} onChangeComplete={async value => {
            const target = seekTarget.current; seekTarget.current = undefined;
            if (!target || target.deviceId !== deviceId || target.trackId !== state.trackId || target.source !== source) { setSeekDraft(null); return; }
            await control('seek', source, target.trackId, undefined, undefined, value); setSeekDraft(null);
          }} />
        <div><span>{clock(seekDraft ?? seconds)}</span><span>{duration ? clock(duration) : '时长读取中'}</span></div>
      </div>}
      <Space wrap>
        <Button aria-label="上一首" icon={<StepBackwardOutlined />} disabled={busy || !deviceId || !state?.trackId} onClick={() => void control('previous', source)} />
        <Button type="primary" size="large" disabled={busy && !(playing && canCancel) || !deviceId || (!state?.trackId && !canStart)}
          icon={playing ? <PauseCircleOutlined /> : <PlayCircleOutlined />}
          onClick={() => void control(playing ? 'pause' : state?.trackId ? 'resume' : 'play', source)}>
          {playing ? '暂停' : state?.trackId ? state.state === 'stopped' ? '重新播放' : '继续播放' : '播放'}</Button>
        <Button aria-label="下一首" icon={<StepForwardOutlined />} disabled={busy || !deviceId || !state?.trackId} onClick={() => void control('next', source)} />
        <Button icon={<StopOutlined />} disabled={busy && !canCancel || !deviceId} onClick={() => void control('stop', source)}>停止</Button>
      </Space>
      {state?.error && <p role="alert" className="music-error">{state.error}</p>}
    </div>
    <div className="music-device">
      <label htmlFor={`music-mode-${source}`}>播放模式</label>
      <Select id={`music-mode-${source}`} aria-label="播放模式" value={state?.mode || 'sequence'} disabled={busy || !deviceId}
        options={[{ value: 'sequence', label: '顺序播放' }, { value: 'shuffle', label: '随机播放' }, { value: 'single', label: '单曲循环' }]}
        onChange={mode => void control('mode', source, undefined, undefined, mode)} />
      <label id={`music-volume-${source}`}>音乐音量 · {volumeDraft ?? state?.volume ?? 70}%</label>
      <Slider ariaLabelledByForHandle={`music-volume-${source}`} min={0} max={100} value={volumeDraft ?? state?.volume ?? 70}
        disabled={busy || !deviceId} onChange={setVolumeDraft} onChangeComplete={async value => {
          await control('volume', source, undefined, value); setVolumeDraft(null);
        }} />
      <small>说“暂停播放”或“下一首”控制当前音乐。切换下方歌曲列表可继续选歌。</small>
    </div>
  </section>{state?.trackId && <LyricsPanel source={source} trackId={state.trackId} seconds={seconds} />}</div>;
}
