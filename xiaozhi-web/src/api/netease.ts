import request from './request';
import type { Status } from '../pages/music/PlayerControls';
export type Account = { ready: boolean; loggedIn: boolean; profile: { userId: string; nickname: string; avatar: string } | null; message?: string };
export type OnlineTrack = { id: string; name: string; title: string; artist: string; album: string; cover: string; durationMs: number };
export type Playlist = { id: string; name: string; cover: string; trackCount: number; created: boolean };
export type Page<T> = { items: T[]; hasMore: boolean; nextOffset: number; total?: number; candidateTotal?: number; name?: string };
export type Lyrics = { lyric: string; translation: string; instrumental: boolean };
type Envelope<T> = { data: T };
const read = async <T,>(path: string, params?: Record<string, string | number>) =>
  (await request.get(`/music/netease/${path}`, { params, timeout: 70000 }) as unknown as Envelope<T>).data;
export const neteaseApi = {
  account: () => read<Account>('account'),
  qr: async () => (await request.post('/music/netease/qr', undefined, { timeout: 70000 }) as unknown as Envelope<{ token: string; image: string; expiresIn: number }>).data,
  check: (token: string) => read<{ state: 'waiting' | 'confirming' | 'authorized' | 'expired'; account?: Account }>('qr', { token }),
  logout: async () => (await request.delete('/music/netease/account', { timeout: 70000 }) as unknown as Envelope<Account>).data,
  playlists: (offset = 0) => read<Page<Playlist>>('playlists', { offset }),
  songs: (playlistId: string, offset = 0, query = '') => read<Page<OnlineTrack>>('songs', { playlistId, offset, query }),
  search: (artist: string, title: string, offset = 0) => read<Page<OnlineTrack>>('search', { artist, title, offset }),
  play: async (deviceId: string, trackId: string, playlistId?: string) =>
    (await request.post('/music/netease/play', { deviceId, trackId, playlistId }, { timeout: 70000 }) as unknown as Envelope<Status>).data,
  lyrics: (trackId: string) => read<Lyrics>('lyrics', { trackId }),
  download: async (trackId: string) => (await request.post('/music/netease/download', { trackId }, { timeout: 250000 }) as unknown as Envelope<{ alreadyPresent: boolean; warning?: string }>).data,
};
