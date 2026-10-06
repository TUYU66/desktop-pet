export type LyricLine = { time: number; text: string };

export function parseLrc(value: string): { lines: LyricLine[]; plain: string[] } {
  const lines: LyricLine[] = [], plain: string[] = [];
  const offset = Number(value.match(/\[offset:\s*([+-]?\d+)\s*\]/i)?.[1] || 0) / 1000;
  for (const raw of value.split(/\r?\n/)) {
    const tags = [...raw.matchAll(/\[(\d{1,3}):(\d{1,2})(?:[.:](\d{1,3}))?\]/g)];
    const text = raw.replace(/\[\d{1,3}:\d{1,2}(?:[.:]\d{1,3})?\]/g, '')
      .replace(/\[(?:ar|al|ti|by|offset|length|re|ve):[^\]]*\]/gi, '').trim();
    if (!text) continue;
    if (!tags.length) { plain.push(text); continue; }
    for (const tag of tags) {
      if (Number(tag[2]) >= 60) continue;
      const time = Number(tag[1]) * 60 + Number(tag[2]) + Number(`0.${tag[3] || '0'}`) + offset;
      lines.push({ time: Math.max(0, time), text });
    }
  }
  const unique = new Map(lines.map(line => [`${line.time}\0${line.text}`, line]));
  return { lines: [...unique.values()].sort((a, b) => a.time - b.time), plain };
}

export function activeLyric(lines: LyricLine[], seconds: number): number {
  let low = 0, high = lines.length;
  while (low < high) { const mid = (low + high) >>> 1; if (lines[mid].time <= seconds) low = mid + 1; else high = mid; }
  return low - 1;
}

export function albumCover(url?: string): string | undefined {
  if (!url) return;
  try { const parsed = new URL(url); if (parsed.protocol === 'http:') parsed.protocol = 'https:';
    if (parsed.protocol === 'https:') return parsed.href;
  } catch { /* Missing or invalid covers use the player placeholder. */ }
}
