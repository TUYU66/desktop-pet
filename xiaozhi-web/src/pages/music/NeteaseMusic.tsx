import { useCallback, useEffect, useRef, useState } from 'react';
import type { ReactNode } from 'react';
import { Alert, App, Avatar, Button, Empty, Form, Input, Modal, Popconfirm, Space, Spin, Tabs, Tag } from 'antd';
import { ArrowLeftOutlined, PlayCircleOutlined, QrcodeOutlined, ReloadOutlined, SearchOutlined, DownloadOutlined, CheckOutlined, CustomerServiceOutlined } from '@ant-design/icons';
import { neteaseApi } from '../../api/netease';
import type { Account, OnlineTrack, Page, Playlist } from '../../api/netease';
import { labels } from './PlayerControls';
import type { PlayerState, Source } from './PlayerControls';
import { albumCover } from './lyrics';
import './netease-music.css';

const errorText = (e: unknown) => e instanceof Error ? e.message : '网易云请求未完成，请稍后重试';
const merge = <T extends { id: string },>(old: T[], added: T[]) => [...new Map([...old, ...added].map(item => [item.id, item])).values()];

export default function NeteaseMusic({ deviceId, state, busy, play, librarySource, onLibrarySourceChange, localLibrary, renderPlayer, onAccountChanged, onDownloaded, downloadedIds }:
  { deviceId: string; state?: PlayerState; busy: boolean;
    librarySource: Source; onLibrarySourceChange: (source: Source) => void; localLibrary: ReactNode;
    renderPlayer: (accountLoading: boolean, loggedIn: boolean) => ReactNode;
    play: (trackId: string, playlistId?: string) => Promise<void>; onAccountChanged: () => void;
    onDownloaded: () => Promise<void>; downloadedIds: string[] }) {
  const { message } = App.useApp();
  const [account, setAccount] = useState<Account>();
  const [accountLoading, setAccountLoading] = useState(true);
  const [error, setError] = useState('');
  const [qrOpen, setQrOpen] = useState(false);
  const [qr, setQr] = useState<{ token: string; image: string }>();
  const [qrState, setQrState] = useState('');
  const [qrLoading, setQrLoading] = useState(false);
  const [qrError, setQrError] = useState('');
  const [playlists, setPlaylists] = useState<Page<Playlist>>();
  const [listsBusy, setListsBusy] = useState(false);
  const [selected, setSelected] = useState<Playlist>();
  const [view, setView] = useState<'library' | 'playlist' | 'search'>('library');
  const detailHeading = useRef<HTMLHeadingElement>(null);
  const detailPage = useRef<HTMLElement>(null);
  const libraryPage = useRef<HTMLElement>(null);
  const previousView = useRef(view);
  const [songs, setSongs] = useState<Page<OnlineTrack>>();
  const [songsBusy, setSongsBusy] = useState(false);
  const [playlistQuery, setPlaylistQuery] = useState('');
  const scanTimer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);
  const [downloading, setDownloading] = useState('');
  const [results, setResults] = useState<Page<OnlineTrack>>();
  const [query, setQuery] = useState<{ artist: string; title: string }>();
  const [searchBusy, setSearchBusy] = useState(false);
  const [form] = Form.useForm<{ artist: string; title: string }>();
  const mounted = useRef(true);
  const accountRevision = useRef(0);
  const qrRevision = useRef(0);
  const songsRevision = useRef(0);
  const searchRevision = useRef(0);
  const listRevision = useRef(0);
  const timer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);
  const clearMusic = useCallback(() => {
    listRevision.current++; songsRevision.current++; searchRevision.current++;
    clearTimeout(scanTimer.current); setView('library'); setPlaylistQuery('');
    setPlaylists(undefined); setSelected(undefined); setSongs(undefined); setResults(undefined); setQuery(undefined);
    setListsBusy(false); setSongsBusy(false); setSearchBusy(false);
  }, []);
  const backToLibrary = () => {
    songsRevision.current++; searchRevision.current++;
    clearTimeout(scanTimer.current);
    setView('library'); setSongsBusy(false); setSearchBusy(false); setError('');
  };
  useEffect(() => {
    const previous = previousView.current;
    previousView.current = view;
    if (librarySource !== 'netease') return;
    if (view === 'library') {
      if (previous !== 'library') {
        libraryPage.current?.focus({ preventScroll: true });
        libraryPage.current?.scrollIntoView({ block: 'start', behavior: 'instant' });
      }
      return;
    }
    detailHeading.current?.focus({ preventScroll: true });
    detailPage.current?.scrollIntoView({ block: 'start', behavior: 'instant' });
  }, [view, librarySource]);
  const loadLists = useCallback(async (offset = 0) => {
    const revision = ++listRevision.current;
    setListsBusy(true); setError('');
    try {
      const page = await neteaseApi.playlists(offset);
      if (mounted.current && revision === listRevision.current) setPlaylists(old => ({ ...page, items: offset ? merge(old?.items || [], page.items) : page.items }));
    } catch (e) { if (mounted.current && revision === listRevision.current) setError(errorText(e)); }
    finally { if (mounted.current && revision === listRevision.current) setListsBusy(false); }
  }, []);
  const reloadAccount = useCallback(async () => {
    const revision = ++accountRevision.current;
    setAccountLoading(true); setError('');
    try {
      const data = await neteaseApi.account();
      if (!mounted.current || revision !== accountRevision.current) return;
      setAccount(data);
      clearMusic();
      if (data.loggedIn) void loadLists();
    } catch (e) { if (mounted.current && revision === accountRevision.current) setError(errorText(e)); }
    finally { if (mounted.current && revision === accountRevision.current) setAccountLoading(false); }
  }, [loadLists, clearMusic]);
  useEffect(() => {
    mounted.current = true; void reloadAccount();
    return () => { mounted.current = false; qrRevision.current++; accountRevision.current++;
      listRevision.current++; songsRevision.current++; searchRevision.current++; clearTimeout(timer.current); clearTimeout(scanTimer.current); };
  }, [reloadAccount]);
  const closeQr = () => { qrRevision.current++; clearTimeout(timer.current); setQrOpen(false); setQrLoading(false); };
  const startQr = async () => {
    const revision = ++qrRevision.current;
    clearTimeout(timer.current); setQrOpen(true); setQr(undefined); setQrLoading(true); setQrError(''); setQrState('waiting');
    try {
      const data = await neteaseApi.qr();
      if (!mounted.current || revision !== qrRevision.current) return;
      setQr(data);
      const poll = async () => {
        if (!mounted.current || revision !== qrRevision.current) return;
        try {
          const result = await neteaseApi.check(data.token);
          if (!mounted.current || revision !== qrRevision.current) return;
          setQrState(result.state); setQrError('');
          if (result.state === 'authorized') {
            accountRevision.current++; setAccount(result.account); setAccountLoading(false);
            clearMusic(); setQrOpen(false); onAccountChanged(); void loadLists(); message.success('网易云账号已连接'); return;
          }
          if (result.state === 'expired') return;
        } catch (e) { if (!mounted.current || revision !== qrRevision.current) return; setQrError(errorText(e)); }
        timer.current = setTimeout(() => void poll(), 3000);
      };
      timer.current = setTimeout(() => void poll(), 2000);
    } catch (e) { if (mounted.current && revision === qrRevision.current) setQrError(errorText(e)); }
    finally { if (mounted.current && revision === qrRevision.current) setQrLoading(false); }
  };
  const logout = async () => {
    const revision = ++accountRevision.current;
    closeQr(); setAccountLoading(true);
    try {
      const data = await neteaseApi.logout();
      if (mounted.current && revision === accountRevision.current) { setAccount(data); clearMusic(); onAccountChanged(); setError(''); }
    } catch (e) { if (mounted.current && revision === accountRevision.current) setError(errorText(e)); }
    finally { if (mounted.current && revision === accountRevision.current) setAccountLoading(false); }
  };
  const loadSongs = async (playlist: Playlist, offset = 0, filter = '') => {
    clearTimeout(scanTimer.current);
    const revision = ++songsRevision.current;
    if (!offset) setSongs(undefined); setSongsBusy(true); setError('');
    try {
      let cursor = offset;
      while (true) {
        const page = await neteaseApi.songs(playlist.id, cursor, filter);
        if (!mounted.current || revision !== songsRevision.current) return;
        if (page.hasMore && page.nextOffset <= cursor) throw new Error('歌单分页异常，请刷新后重试');
        const append = cursor > 0;
        setSongs(old => ({ ...page, items: append ? merge(old?.items || [], page.items) : page.items }));
        if (!filter.trim() || !page.hasMore) break;
        cursor = page.nextOffset;
      }
    } catch (e) { if (mounted.current && revision === songsRevision.current) setError(errorText(e)); }
    finally { if (mounted.current && revision === songsRevision.current) setSongsBusy(false); }
  };
  useEffect(() => {
    if (view !== 'playlist' || !selected) return;
    setSongs(undefined); setSongsBusy(true);
    const timer = setTimeout(() => void loadSongs(selected, 0, playlistQuery.trim()), playlistQuery.trim() ? 350 : 0);
    scanTimer.current = timer;
    return () => { clearTimeout(timer); songsRevision.current++; };
    // Only changing the chosen playlist or its query starts a new scan.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [view, selected?.id, playlistQuery]);
  const download = async (track: OnlineTrack) => {
    setDownloading(track.id);
    try {
      const result = await neteaseApi.download(track.id);
      if (!mounted.current) return;
      message.success(result.alreadyPresent ? '歌曲已在本地歌曲中' : '已加入本地歌曲');
      if (result.warning) message.warning(result.warning);
      void onDownloaded();
    } catch (e) { if (mounted.current) { message.error(errorText(e)); void onDownloaded(); } }
    finally { if (mounted.current) setDownloading(''); }
  };
  const search = async (values: { artist: string; title: string }, offset = 0) => {
    const revision = ++searchRevision.current;
    if (!offset) { onLibrarySourceChange('netease'); setView('search'); }
    setQuery(values); if (!offset) setResults(undefined); setSearchBusy(true); setError('');
    try {
      const page = await neteaseApi.search((values.artist || '').trim(), (values.title || '').trim(), offset);
      if (mounted.current && revision === searchRevision.current) setResults(old => ({ ...page, items: offset ? merge(old?.items || [], page.items) : page.items }));
    } catch (e) { if (mounted.current && revision === searchRevision.current) setError(errorText(e)); }
    finally { if (mounted.current && revision === searchRevision.current) setSearchBusy(false); }
  };
  const trackRows = (items: OnlineTrack[], playlistId?: string) => <div className="music-tracks">{items.map((track, index) =>
    <div className={`music-track ${state?.trackId === track.id ? 'selected' : ''}`} key={track.id}>
      <span className="music-number">{String(index + 1).padStart(2, '0')}</span>
      <Avatar className="music-row-cover" shape="square" size={48} src={albumCover(track.cover)} icon={<CustomerServiceOutlined />} />
      <div className="music-track-title"><strong>{track.title}</strong><small>{track.artist} · {track.album || '未知专辑'}
        {track.durationMs > 0 ? ` · ${Math.floor(track.durationMs / 60000)}:${String(Math.floor(track.durationMs / 1000) % 60).padStart(2, '0')}` : ''}
        {state?.trackId === track.id ? ` · ${labels[state.state]}` : ''}</small></div>
      <Button aria-label={`播放 ${track.artist}的${track.title}`} icon={<PlayCircleOutlined />} disabled={busy || !deviceId || accountLoading || !account?.loggedIn}
        onClick={() => void play(track.id, playlistId)}>播放</Button>
      <Button aria-label={`下载 ${track.artist}的${track.title}到本地歌曲`} loading={downloading === track.id}
        icon={downloadedIds.includes(track.id.replace('netease:', '')) ? <CheckOutlined /> : <DownloadOutlined />}
        disabled={!!downloading || accountLoading || !account?.loggedIn || downloadedIds.includes(track.id.replace('netease:', ''))}
        onClick={() => void download(track)}>{downloadedIds.includes(track.id.replace('netease:', '')) ? '已下载' : '下载到本地'}</Button>
    </div>)}</div>;
  return <div className="netease-panel">
    <div className="netease-account"><div><h2>网易云账号</h2><p>连接账号，查看歌单与查找平台歌曲。</p></div>
      <Space wrap>{account?.loggedIn && account.profile && <><Avatar src={account.profile.avatar} /><span>{account.profile.nickname}</span><Tag color="green">已连接</Tag></>}
        <Button icon={<ReloadOutlined />} loading={accountLoading} disabled={accountLoading} onClick={() => void reloadAccount()}>刷新账号与歌单</Button>
        {account?.loggedIn ? <Popconfirm title="退出网易云账号？" description="停止网易云播放并清除本机登录信息，本地音乐可继续使用。" onConfirm={logout} okText="退出" cancelText="取消">
          <Button disabled={accountLoading || busy || !!downloading}>退出账号</Button></Popconfirm> : <Button type="primary" icon={<QrcodeOutlined />} disabled={accountLoading || !account?.ready} onClick={() => void startQr()}>扫码登录</Button>}
      </Space>
    </div>
    {error && <Alert type="warning" showIcon message={error} />}
    {downloading && <Alert type="info" showIcon message="正在下载歌曲，完成后可切换到“本地歌曲”查看。" />}
    {!accountLoading && !account?.loggedIn && <Alert type="info" showIcon message={account?.ready ? '使用网易云音乐 App 扫码并确认登录，即可查看创建和收藏的歌单。' : account?.message || '网易云组件尚未就绪，请确认服务配置后刷新。'} />}
    {renderPlayer(accountLoading, !!account?.loggedIn)}
    {account?.loggedIn && (view === 'library' || librarySource === 'local') && <>
      <section className="music-library netease-search" aria-label="网易云点歌">
        <h3>按歌名或歌手点歌</h3><p className="music-hint">任选一项即可查询，两项一起填写可缩小范围。聊天点歌先查本地，没有再搜索网易云。可说“查找歌名晴天”“我想听周杰伦的晴天”，有多个匹配时再说“播放第二首”。</p>
        <Form form={form} layout="vertical" onFinish={values => void search(values)} className="netease-search-form">
          <Form.Item name="artist" label="歌手（选填）" rules={[{ max: 100, message: '最多100字' }]}><Input placeholder="例如：周杰伦" maxLength={100} allowClear /></Form.Item>
          <Form.Item name="title" label="歌名（选填）" dependencies={['artist']} rules={[{ max: 100, message: '最多100字' },
            { validator: (_, value: string | undefined) => value?.trim() || (form.getFieldValue('artist') as string | undefined)?.trim() ? Promise.resolve() : Promise.reject(new Error('请至少填写歌名或歌手其中一项')) }]}>
            <Input placeholder="例如：晴天" maxLength={100} allowClear /></Form.Item>
          <Button type="primary" htmlType="submit" icon={<SearchOutlined />} loading={searchBusy}>查询歌曲</Button>
        </Form>
      </section>
    </>}
    <section ref={libraryPage} tabIndex={-1} className="music-library music-collection" aria-label="选择网易云歌单或本地歌曲">
      <Tabs className="music-collection-tabs" activeKey={librarySource}
        onChange={source => onLibrarySourceChange(source as Source)}
        tabBarExtraContent={<span className="music-collection-note">{librarySource === 'netease' ? '创建与收藏的歌单' : '上传与下载的歌曲'}</span>}
        items={[
          { key: 'netease', label: '网易云歌单', forceRender: true, children: <>
            {accountLoading ? <Spin /> : !account?.loggedIn ? <Empty description="连接网易云账号后，即可查看你的歌单" /> : <>
              {view !== 'library' && <section ref={detailPage} className="netease-detail" aria-label={view === 'playlist' ? '歌单详情' : '搜索结果'}>
                <Button className="netease-back" type="text" icon={<ArrowLeftOutlined />} onClick={backToLibrary}>返回网易云歌单</Button>
                <div className="netease-detail-heading">
                  {view === 'playlist' && selected && <Avatar shape="square" size={72} src={albumCover(selected.cover)} icon={<CustomerServiceOutlined />} />}
                  <div><h3 ref={detailHeading} tabIndex={-1}>{view === 'playlist' ? selected?.name : '搜索结果'}</h3>
                    <p>{view === 'playlist' ? `${selected?.trackCount || 0} 首歌曲` : [query?.artist, query?.title].filter(Boolean).join(' · ')}</p></div>
                </div>
                {view === 'playlist' && selected && <>
                  <Input.Search className="netease-playlist-filter" aria-label="检索当前歌单" placeholder="在整个歌单中搜索歌名、歌手或专辑" maxLength={100}
                    allowClear value={playlistQuery} onChange={e => setPlaylistQuery(e.target.value)} onSearch={() => void loadSongs(selected, 0, playlistQuery.trim())} />
                  {playlistQuery.trim() && songs && <p className="music-hint">已扫描 {songs.nextOffset} / {songs.total} 首，找到 {songs.items.length} 首{songsBusy ? '，仍在搜索' : ''}</p>}
                  {songs && <>{trackRows(songs.items, selected.id)}{!songsBusy && !songs.items.length && <Empty description={playlistQuery.trim() ? '整个歌单中没有匹配歌曲' : songs.hasMore ? '本页歌曲暂不可用，可加载后续歌曲' : '歌单中没有可查看的歌曲'} />}</>}
                  {songsBusy && <Spin />}{songs?.hasMore && !playlistQuery.trim() && <Button loading={songsBusy} onClick={() => void loadSongs(selected, songs.nextOffset)}>更多歌曲</Button>}
                </>}
                {view === 'search' && <>
                  {results && <>{trackRows(results.items)}{!results.items.length && <Empty description={results.hasMore ? '本页没有匹配歌曲，可继续查找' : '没有匹配歌曲，请核对查询条件'} />}
                    {results.hasMore && query && <Button loading={searchBusy} onClick={() => void search(query, results.nextOffset)}>继续查找更多版本</Button>}</>}
                  {searchBusy && <Spin />}
                </>}
              </section>}
              {view === 'library' && <>
                <div className="netease-playlists">{playlists?.items.map(playlist => <button type="button" key={playlist.id} className="netease-playlist"
                  onClick={() => { setSongs(undefined); setSelected(playlist); setPlaylistQuery(''); setError(''); setView('playlist'); }}>
                  {albumCover(playlist.cover) ? <img src={albumCover(playlist.cover)} alt="" loading="lazy" referrerPolicy="no-referrer" /> : <span className="netease-cover">♫</span>}
                  <span><strong>{playlist.name}</strong><small>{playlist.created ? '我创建的' : '我收藏的'} · {playlist.trackCount} 首</small></span>
                </button>)}</div>
                {listsBusy && <Spin />}{playlists && !playlists.items.length && <Empty description="此账号还没有歌单" />}
                {playlists?.hasMore && <Button loading={listsBusy} onClick={() => void loadLists(playlists.nextOffset)}>更多歌单</Button>}
              </>}
            </>}
          </> },
          { key: 'local', label: '本地歌曲', forceRender: true, children: localLibrary },
        ]} />
    </section>
    <Modal title="扫码登录网易云" open={qrOpen} onCancel={closeQr} footer={<Button loading={qrLoading} onClick={() => void startQr()}>重新生成二维码</Button>} destroyOnHidden>
      <div className="netease-qr">{qrLoading ? <Spin /> : qr && <img src={qr.image} alt="网易云登录二维码" />}
        <p>{qrState === 'expired' ? '二维码已过期，请重新生成' : qrState === 'confirming' ? '已扫码，请在网易云音乐 App 确认登录' : '打开网易云音乐 App，扫码并确认登录'}</p>
      </div>{qrError && <Alert type="warning" showIcon message={qrError} />}
    </Modal>
  </div>;
}
