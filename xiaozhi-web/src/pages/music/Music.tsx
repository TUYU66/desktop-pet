import { useCallback, useEffect, useRef, useState } from 'react';
import { Alert, App, Avatar, Button, Empty, Input, Popconfirm, Progress, Select, Space, Spin, Upload } from 'antd';
import { UploadOutlined, PlayCircleOutlined, DeleteOutlined, ReloadOutlined, CustomerServiceOutlined } from '@ant-design/icons';
import request from '../../api/request';
import './music.css';
import PlayerControls, { labels } from './PlayerControls';
import type { Status, Source, PlaybackMode } from './PlayerControls';
import NeteaseMusic from './NeteaseMusic';
import { neteaseApi } from '../../api/netease';
import { albumCover } from './lyrics';
import { currentPlayerSource } from './currentPlayer';

type Track = { id: string; name: string; title: string; artist: string; size: number; album?: string; cover?: string; neteaseId?: string };
type Device = { id: string; name?: string; status: Status };

export default function Music() {
  const { message } = App.useApp();
  const [tracks, setTracks] = useState<Track[]>([]);
  const [devices, setDevices] = useState<Device[]>([]);
  const [deviceId, setDeviceId] = useState('');
  const [search, setSearch] = useState('');
  const [librarySource, setLibrarySource] = useState<Source>('netease');
  const [busy, setBusy] = useState(false);
  const [busySource, setBusySource] = useState<Source | null>(null);
  const [deleting, setDeleting] = useState<string | null>(null);
  const [uploading, setUploading] = useState(false);
  const [progress, setProgress] = useState(0);
  const [error, setError] = useState('');
  const [libraryError, setLibraryError] = useState('');
  const [loading, setLoading] = useState(true);
  const mounted = useRef(true);
  const libraryRequest = useRef<Promise<void> | null>(null);
  const libraryRefreshQueued = useRef(false);
  const statusFetching = useRef(false);
  const lifecycle = useRef(0);
  const libraryController = useRef<AbortController | null>(null);
  const statusController = useRef<AbortController | null>(null);
  const controlRevision = useRef(0);
  const snapshotRevision = useRef(0);
  const operationRevision = useRef(0);
  const refreshStatus = useCallback(async () => {
    if (statusFetching.current) return;
    statusFetching.current = true;
    const epoch = lifecycle.current;
    const revision = controlRevision.current;
    const snapshot = ++snapshotRevision.current;
    const controller = new AbortController();
    statusController.current = controller;
    try {
      const result = await request.get('/music/status', { signal: controller.signal }) as unknown as { data: { devices: Device[] } };
      if (!mounted.current || epoch !== lifecycle.current) return;
      if (revision === controlRevision.current && snapshot === snapshotRevision.current) {
        setDevices(result.data.devices);
        setDeviceId(old => result.data.devices.some(d => d.id === old) ? old : result.data.devices[0]?.id || '');
      }
      setError('');
    } catch { if (mounted.current && epoch === lifecycle.current && !controller.signal.aborted) setError('暂时无法连接音乐服务，请确认服务已启动后刷新。'); }
    finally { if (epoch === lifecycle.current) statusFetching.current = false; }
  }, []);
  const refresh = useCallback((): Promise<void> => {
    if (libraryRequest.current) {
      libraryRefreshQueued.current = true;
      return libraryRequest.current;
    }
    const epoch = lifecycle.current;
    const current = () => mounted.current && epoch === lifecycle.current;
    const load = async () => {
      try {
        do {
          libraryRefreshQueued.current = false;
          const revision = controlRevision.current;
          const snapshot = ++snapshotRevision.current;
          const controller = new AbortController();
          libraryController.current = controller;
          try {
            const result = await request.get('/music', { signal: controller.signal }) as unknown as { data: { tracks: Track[]; devices: Device[] } };
            if (!current()) return;
            // An upload/download/delete finished during this scan: fetch again.
            if (libraryRefreshQueued.current) continue;
            setTracks(result.data.tracks);
            if (revision === controlRevision.current && snapshot === snapshotRevision.current) {
              setDevices(result.data.devices);
              setDeviceId(old => result.data.devices.some(d => d.id === old) ? old : result.data.devices[0]?.id || '');
            }
            setLibraryError('');
          } catch {
            if (current() && !controller.signal.aborted && !libraryRefreshQueued.current) setLibraryError('暂时无法加载歌曲列表，请刷新重试。');
          }
        } while (current() && libraryRefreshQueued.current);
      } finally { if (current()) setLoading(false); }
    };
    const pending = load().finally(() => { if (libraryRequest.current === pending) libraryRequest.current = null; });
    libraryRequest.current = pending;
    return pending;
  }, []);
  useEffect(() => {
    mounted.current = true;
    ++lifecycle.current;
    void refresh();
    const poll = () => { if (!document.hidden) void refreshStatus(); };
    const id = setInterval(poll, 2000);
    document.addEventListener('visibilitychange', poll);
    return () => {
      mounted.current = false; ++lifecycle.current;
      clearInterval(id); document.removeEventListener('visibilitychange', poll);
      libraryController.current?.abort(); statusController.current?.abort();
      libraryRequest.current = null; libraryRefreshQueued.current = false; statusFetching.current = false;
    };
  }, [refresh, refreshStatus]);
  const state = devices.find(d => d.id === deviceId)?.status;
  const activeSource = state?.source || (state?.trackId ? currentPlayerSource(state, librarySource, null) : undefined);
  const localState = state?.players?.local || (activeSource !== 'netease' ? state : undefined);
  const filteredTracks = tracks.filter(track => [track.name, track.title, track.artist, track.album].filter(Boolean).join(' ').toLowerCase().includes(search.toLowerCase()));
  const onlineState = state?.players?.netease || (activeSource === 'netease' ? state : undefined);
  const control = async (action: string, source: Source, trackId?: string, volume?: number, mode?: PlaybackMode, position?: number) => {
    const operation = ++operationRevision.current;
    controlRevision.current += 1;
    setBusy(true); setBusySource(source);
    try {
      const result = await request.post('/music/control', { action, source, trackId, deviceId, volume, mode, position }, { timeout: 70000 }) as unknown as { data: Status };
      if (mounted.current && operation === operationRevision.current) setDevices(old => old.map(d => d.id === deviceId ? { ...d, status: result.data } : d));
    }
    catch (e) { if (mounted.current && operation === operationRevision.current) message.error(e instanceof Error ? e.message : '播放操作失败'); }
    finally { if (operation === operationRevision.current) { controlRevision.current += 1; if (mounted.current) { setBusy(false); setBusySource(null); } } }
  };
  const onlinePlay = async (trackId: string, playlistId?: string) => {
    const operation = ++operationRevision.current;
    controlRevision.current++; setBusy(true); setBusySource('netease');
    try {
      const result = await neteaseApi.play(deviceId, trackId, playlistId);
      if (mounted.current && operation === operationRevision.current) setDevices(old => old.map(d => d.id === deviceId ? { ...d, status: result } : d));
    } catch (e) { if (mounted.current && operation === operationRevision.current) message.error(e instanceof Error ? e.message : '网易云播放失败'); }
    finally { if (operation === operationRevision.current) { controlRevision.current++; if (mounted.current) { setBusy(false); setBusySource(null); } } }
  };
  const accountChanged = useCallback(() => { controlRevision.current++; void refreshStatus(); }, [refreshStatus]);
  const remove = async (id: string) => {
    setDeleting(id);
    try { await request.delete(`/music/${id}`); message.success('已删除音乐'); await refresh(); }
    catch (e) { message.error(e instanceof Error ? e.message : '删除失败'); }
    finally { if (mounted.current) setDeleting(null); }
  };
  const playerSource = currentPlayerSource(state, librarySource, busySource);
  const playerState = activeSource === playerSource ? state : playerSource === 'local' ? localState : onlineState;
  const localLibrary = <>
    <div className="music-local-library"><div className="music-toolbar"><span>{tracks.length} 首 · MP3</span><Space wrap><Input.Search aria-label="搜索音乐" placeholder="搜索歌名或歌手" allowClear value={search} onChange={e => setSearch(e.target.value)} style={{ width: 220 }} />
      <Upload accept=".mp3,audio/mpeg" showUploadList={false} disabled={uploading || !!deleting} beforeUpload={file => { if (!file.name.toLowerCase().endsWith('.mp3') || file.size > 32 * 1024 * 1024) { message.error('请选择不超过 32 MB 的 MP3 文件'); return Upload.LIST_IGNORE; } return true; }} customRequest={async ({ file, onSuccess, onError }) => {
        setUploading(true); setProgress(0);
        try { await request.put('/music', file, { params: { name: (file as File).name }, headers: { 'Content-Type': 'application/octet-stream' }, timeout: 120000, onUploadProgress: e => setProgress(Math.round((e.progress || 0) * 100)) }); onSuccess?.({}); message.success('音乐已加入'); await refresh(); }
        catch (e) { onError?.(e as Error); message.error(e instanceof Error ? e.message : '上传失败'); }
        finally { setUploading(false); }
      }}><Button icon={<UploadOutlined />} loading={uploading} disabled={!!deleting}>上传音乐</Button></Upload></Space></div>
      <p className="music-hint">单首最大 32 MB，建议文件名为“歌手-歌名.mp3”，例如“周杰伦-彩虹.mp3”。播放时唤醒会临时暂停并询问操作，说“继续播放”可接着听。</p>
      {uploading && <Progress percent={progress} status="active" />}
      {loading ? <Spin /> : !tracks.length ? <Empty description="还没有音乐，上传第一首 MP3 吧" /> : <div className="music-tracks">{filteredTracks.map((track, index) => <div className={`music-track ${localState?.trackId === track.id ? 'selected' : ''}`} key={track.id}><span className="music-number">{String(index + 1).padStart(2, '0')}</span><Avatar className="music-row-cover" shape="square" size={44} src={albumCover(track.cover)} icon={<CustomerServiceOutlined />} /><div className="music-track-title"><strong>{track.title || track.name}</strong><small>{track.artist || "未知演唱者"}{track.album ? ` · ${track.album}` : ""} · {(track.size / 1024 / 1024).toFixed(1)} MB{localState?.trackId === track.id ? ` · ${labels[localState.state]}` : ''}</small></div><Button aria-label={`播放 ${track.name}`} icon={<PlayCircleOutlined />} disabled={busy || !deviceId || deleting === track.id} onClick={() => void control('play', 'local', track.id)}>播放</Button><Popconfirm disabled={uploading || !!deleting} title="删除这首音乐？" description="正在播放的歌曲会停止。" onConfirm={() => remove(track.id)} okText="删除" cancelText="取消"><Button danger type="text" disabled={uploading || !!deleting} loading={deleting === track.id} aria-label={`删除 ${track.name}`} icon={<DeleteOutlined />} /></Popconfirm></div>)}{search && !filteredTracks.length && <Empty description="没有匹配的音乐" />}</div>}
    </div>
  </>;
  return <div className="music-page">
    <header className="music-heading"><div><h1>音乐空间</h1><p>从网易云歌单或本地歌曲中选歌，在桌面宠物上播放。</p></div><Button icon={<ReloadOutlined />} onClick={() => void refresh()}>刷新</Button></header>
    {(error || libraryError) && <Alert type="warning" showIcon message={error || libraryError} />}
    {devices.length > 1 && <Select aria-label="播放设备" disabled={busy} value={deviceId || undefined} placeholder="选择在线设备"
      options={devices.map(d => ({ value: d.id, label: d.name?.trim() || `桌面宠物 · ${d.id}` }))} onChange={setDeviceId} />}
    <NeteaseMusic deviceId={deviceId} state={onlineState} busy={busy} play={onlinePlay}
      librarySource={librarySource} onLibrarySourceChange={setLibrarySource} localLibrary={localLibrary}
      renderPlayer={(accountLoading, loggedIn) => <PlayerControls key={`${deviceId}:${playerSource}`} source={playerSource} state={playerState}
        deviceId={deviceId} busy={busy || (playerSource === 'netease' && (accountLoading || !loggedIn))}
        canCancel={busySource === playerSource || (!busy && playerSource === 'netease')}
        canStart={playerSource === 'local' && !!tracks.length} control={control} />}
      onAccountChanged={accountChanged} onDownloaded={refresh}
      downloadedIds={tracks.map(track => track.neteaseId || '').filter(Boolean)} />
  </div>;
}
