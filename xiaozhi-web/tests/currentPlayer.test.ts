import { test } from 'node:test';
import assert from 'node:assert/strict';
import { currentPlayerSource } from '../src/pages/music/currentPlayer.ts';

test('library switching keeps controls on the current song, including paused songs', () => {
  assert.equal(currentPlayerSource({ source: 'local', trackId: 'local-song' }, 'netease', null), 'local');
  assert.equal(currentPlayerSource({ source: 'netease', trackId: 'netease:123' }, 'local', null), 'netease');
});

test('cancel targets the source of an in-flight playback request', () => {
  assert.equal(currentPlayerSource({ source: 'local', trackId: 'local-song' }, 'local', 'netease'), 'netease');
  assert.equal(currentPlayerSource({ source: 'netease', trackId: 'netease:123' }, 'netease', 'local'), 'local');
});

test('an empty player follows the selected library and legacy snapshots infer a source from the track id', () => {
  assert.equal(currentPlayerSource(undefined, 'local', null), 'local');
  assert.equal(currentPlayerSource({ source: 'local', trackId: null }, 'netease', null), 'netease');
  assert.equal(currentPlayerSource({ trackId: 'local-song' }, 'netease', null), 'local');
  assert.equal(currentPlayerSource({ trackId: 'netease:123' }, 'local', null), 'netease');
});
