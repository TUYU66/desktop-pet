import { test } from 'node:test';
import assert from 'node:assert/strict';
import { isChatControl, submissionBlocks } from '../src/pages/chat/chatControl.ts';

const responding = {
  session: 'session', text: '明天提醒我取快递', posting: false,
  status: 'responding', tracking: true, stopPolling: false,
};

test('only explicit stop and standby commands bypass a pending reply', () => {
  for (const text of ['别说了', '先停一下', '停止播报', '待命一下', '去待命休息', '请你进入待机状态吧', '小智，先回到待命休息一下吧']) {
    assert.equal(isChatControl(text), true, text);
    assert.equal(submissionBlocks(responding, 'session', text), false, text);
  }
  for (const text of ['休息一下', '暂停播放', '不要去待命', '如果去待命会怎样', '解释“停止聆听”', '你是不是去待命了', '下一首', '明天改到14点']) {
    assert.equal(isChatControl(text), false, text);
    assert.equal(submissionBlocks(responding, 'session', text), true, text);
  }
});

test('custom wake prefix follows the server rule', () => {
  assert.equal(submissionBlocks(responding, 'session', '小鹿小鹿，待命一下', '小鹿小鹿'), false);
  assert.equal(submissionBlocks(responding, 'session', '小鹿小鹿，待命一下'), true);
});

test('posting and duplicate controls remain blocked, unresolved operations permit a deliberate stop', () => {
  assert.equal(submissionBlocks({ ...responding, posting: true }, 'session', '别说了'), true);
  assert.equal(submissionBlocks({ ...responding, text: '别说了' }, 'session', '别说了'), true);
  const unknown = { ...responding, status: 'unknown', stopPolling: true };
  assert.equal(submissionBlocks(unknown, 'session', '普通问题'), false);
  assert.equal(submissionBlocks(unknown, 'session', unknown.text), true);
  assert.equal(submissionBlocks(unknown, 'session'), true);
  assert.equal(submissionBlocks({ ...unknown, stopPolling: false }, 'session', '普通问题'), true);
  assert.equal(submissionBlocks(unknown, 'session', '待命一下'), false);
  assert.equal(submissionBlocks(responding, 'session'), true);
  assert.equal(submissionBlocks(null, 'session', '普通问题'), false);
});
