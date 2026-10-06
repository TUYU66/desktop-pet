// Keep this admission rule aligned with core/handle/standby.py. The server
// remains responsible for deciding and executing the actual control command.
export function isChatControl(text: string, wakeWord = '') {
  const value = text.trim();
  if (/^(?:请|你|先)*(?:停|停一下|别说了|停止说话|停止播报|暂停说话|不用说了)(?:吧|了)?[。！!\s]*$/.test(value)) return true;
  if (/[“”"'？?]|如果|假如|不要|不用|是不是|是否/.test(value)) return false;
  let phrase = value.replace(/[\s，,。！!、~～]/g, '');
  if (wakeWord && phrase.startsWith(wakeWord)) phrase = phrase.slice(wakeWord.length);
  return /^(?:(?:小智|小兰|你|请|麻烦你|现在|先))*(?:再见|拜拜|不聊了|结束对话|结束聊天|停止聆听|停止监听|别听了|(?:(?:去|进入|回到|回|切换到))?(?:待命|待机)(?:状态)?(?:休息|休息一下|休息一会儿)?)(?:一下|一会儿|一会)?(?:吧|啦|了|好吗|好不好)?$/.test(phrase);
}

type SubmissionState = {
  session: string; text: string; posting: boolean; status: string;
  tracking: boolean; stopPolling: boolean;
};

export function submissionBlocks(record: SubmissionState | null, session: string | null, text = '', wakeWord = '') {
  if (!record || record.session !== session) return false;
  if (record.posting) return true;
  const pending = record.tracking && !record.stopPolling;
  if (isChatControl(text, wakeWord)) {
    // Allow a deliberate interruption, but do not duplicate a control command
    // whose receipt is still being tracked.
    return pending && isChatControl(record.text, wakeWord);
  }
  if (record.status === 'unknown' && record.stopPolling) {
    // A terminal uncertainty must not lock all future conversation. Repeating
    // the same operation still requires the explicit result-checking flow.
    return !text.trim() || text.trim() === record.text.trim();
  }
  return pending;
}
