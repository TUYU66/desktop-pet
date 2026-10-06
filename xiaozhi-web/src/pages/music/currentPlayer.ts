import type { Source } from './PlayerControls';

// Browsing another library does not retarget pause/next/seek for the active song.
export function currentPlayerSource(
  state: { source?: Source; trackId: string | null } | undefined,
  library: Source,
  pending: Source | null,
): Source {
  if (pending) return pending;
  if (state?.trackId) return state.source || (state.trackId.startsWith('netease:') ? 'netease' : 'local');
  return library;
}
