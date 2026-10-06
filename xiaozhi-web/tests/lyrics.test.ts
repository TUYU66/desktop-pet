import { test } from 'node:test';
import assert from 'node:assert/strict';
import { activeLyric, albumCover, parseLrc } from '../src/pages/music/lyrics.ts';

test('LRC fractions, repeated timestamps, offsets and chronological order', () => {
  const parsed = parseLrc('[ar:歌手]\n[offset:-500]\n[00:04.50][00:08.500]副歌\n[00:01.5]开头\n[00:04.50]副歌\n[00:60]无效时间');
  assert.deepEqual(parsed.lines, [
    { time: 1, text: '开头' }, { time: 4, text: '副歌' }, { time: 8, text: '副歌' },
  ]);
  assert.deepEqual(parsed.plain, []);
  assert.equal(activeLyric(parsed.lines, .9), -1);
  assert.equal(activeLyric(parsed.lines, 4), 1);
  assert.equal(activeLyric(parsed.lines, 100), 2);
  assert.equal(activeLyric([], 10), -1);
});

test('untimed lyrics retain text and ignore metadata', () => {
  assert.deepEqual(parseLrc('[ti:歌曲]\n[al:专辑]\n第一句\n第二句'), { lines: [], plain: ['第一句','第二句'] });
  assert.deepEqual(parseLrc('[offset:-1000]\n[00:00.25]开头').lines, [{ time: 0, text: '开头' }]);
});

test('covers use HTTPS and invalid URLs fall back to a placeholder', () => {
  assert.equal(albumCover('http://p1.music.126.net/cover.jpg'), 'https://p1.music.126.net/cover.jpg');
  assert.equal(albumCover('file:///cover.jpg'), undefined);
  assert.equal(albumCover('javascript:alert(1)'), undefined);
  assert.equal(albumCover(''), undefined);
});
