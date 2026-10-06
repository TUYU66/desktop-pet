import { useEffect, useMemo, useRef, useState } from 'react';
import { Alert, Button, Empty, Spin } from 'antd';
import request from '../../api/request';
import { neteaseApi } from '../../api/netease';
import type { Lyrics } from '../../api/netease';
import type { Source } from './PlayerControls';
import { activeLyric, parseLrc } from './lyrics';

export default function LyricsPanel({ source, trackId, seconds }: { source: Source; trackId: string; seconds: number }) {
  const [data, setData] = useState<Lyrics>();
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [retry, setRetry] = useState(0);
  const active = useRef<HTMLDivElement>(null);
  const viewport = useRef<HTMLDivElement>(null);
  useEffect(() => {
    let canceled = false;
    setData(undefined); setLoading(true); setError('');
    const load = async () => {
      try {
        const result = source === 'netease' ? await neteaseApi.lyrics(trackId) :
          (await request.get(`/music/details/${trackId}`) as unknown as { data: Lyrics }).data;
        if (!canceled) setData(result);
      } catch (e) { if (!canceled) setError(e instanceof Error ? e.message : '歌词暂时无法读取'); }
      finally { if (!canceled) setLoading(false); }
    };
    void load(); return () => { canceled = true; };
  }, [source, trackId, retry]);
  const original = useMemo(() => parseLrc(data?.lyric || ''), [data?.lyric]);
  const translated = useMemo(() => parseLrc(data?.translation || ''), [data?.translation]);
  const lines = useMemo(() => original.lines.map(line => ({ ...line,
    translation: translated.lines.find(row => Math.abs(row.time - line.time) <= .25)?.text,
  })), [original, translated]);
  const index = activeLyric(lines, seconds);
  useEffect(() => {
    const followPlayback = () => {
      const row = active.current, box = viewport.current;
      if (row && box && box.clientHeight && !document.hidden) box.scrollTo({ top: row.getBoundingClientRect().top - box.getBoundingClientRect().top + box.scrollTop - box.clientHeight / 2 + row.clientHeight / 2,
        behavior: window.matchMedia('(prefers-reduced-motion: reduce)').matches ? 'instant' : 'smooth' });
    };
    followPlayback();
    document.addEventListener('visibilitychange', followPlayback);
    const observer = new ResizeObserver(followPlayback);
    if (viewport.current) observer.observe(viewport.current);
    return () => { document.removeEventListener('visibilitychange', followPlayback); observer.disconnect(); };
  }, [index, lines]);
  return <section className="music-lyrics" aria-label="歌曲歌词">
    <div className="music-lyrics-heading"><h3>歌词</h3></div>
    {loading ? <Spin /> : error ? <Alert type="warning" message={error} action={<Button size="small" onClick={() => setRetry(value => value + 1)}>重试</Button>} /> :
      !lines.length && !original.plain.length ? <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description={data?.instrumental ? '纯音乐，没有歌词' : '暂无歌词'} /> :
        <div className="music-lyrics-scroll" ref={viewport}>
          {lines.length ? lines.map((line, i) => <div key={`${line.time}-${i}`} ref={i === index ? active : undefined}
            className={`music-lyric-line ${i === index ? 'active' : ''}`} aria-current={i === index ? 'true' : undefined}>
            <span>{line.text}</span>{line.translation && <small>{line.translation}</small>}
          </div>) : <><p className="music-lyrics-note">此歌词没有时间轴</p>{original.plain.map((line, i) => <div className="music-lyric-line" key={i}>{line}</div>)}</>}
        </div>}
  </section>;
}
