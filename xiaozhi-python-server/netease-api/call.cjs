// One isolated request per process. Credentials travel through stdin, never argv.
const fs = require('node:fs');
const path = require('node:path');
const os = require('node:os');
for (const name of ['log', 'info', 'warn', 'error', 'debug']) console[name] = () => {};
process.env.ENABLE_GENERAL_UNBLOCK = 'false';
// Isolate upstream runtime files from other Node applications on this computer.
const cache = path.resolve(__dirname, '../data/netease-runtime');
fs.mkdirSync(cache, { recursive: true });
os.tmpdir = () => cache;
try { fs.writeFileSync(path.join(cache, 'anonymous_token'), '', { flag: 'wx' }); }
catch (error) { if (error.code !== 'EEXIST') throw error; }
const allowed = new Set(['login_status', 'login_qr_key', 'login_qr_create', 'login_qr_check',
  'user_playlist', 'playlist_detail', 'song_detail', 'cloudsearch', 'song_url_v1', 'song_download_url_v1', 'lyric']);
let finished = false;
function finish(value) {
  if (finished) return;
  finished = true;
  const data = JSON.stringify(value);
  process.stdout.write(Buffer.byteLength(data) <= 8 * 1024 * 1024 ? data : '{"ok":false,"kind":"oversized"}', () => process.exit(0));
}
const timer = setTimeout(() => finish({ ok: false, kind: 'timeout' }), 20000);
(async () => {
  try {
    const input = fs.readFileSync(0, 'utf8');
    if (Buffer.byteLength(input) > 128 * 1024) throw new Error('input');
    const { method, params } = JSON.parse(input);
    if (!allowed.has(method) || !params || typeof params !== 'object' || Array.isArray(params)) throw new Error('input');
    let root;
    try { root = path.dirname(require.resolve('@neteasecloudmusicapienhanced/api/package.json')); }
    catch { finish({ ok: false, kind: 'dependency' }); return; }
    const { cookieToJson, generateDeviceId } = require(path.join(root, 'util/index.js'));
    const devicePath = path.join(cache, 'device-id');
    try { fs.writeFileSync(devicePath, generateDeviceId(), { flag: 'wx' }); }
    catch (error) { if (error.code !== 'EEXIST') throw error; }
    global.deviceId = fs.readFileSync(devicePath, 'utf8');
    if (method === 'song_url_v1') {
      const keyPath = path.join(cache, 'xeapi_public_key');
      let key = {};
      try { key = JSON.parse(fs.readFileSync(keyPath, 'utf8')); } catch {}
      if (!key.sk || !key.savedAt || Date.now() - key.savedAt > 3600000) {
        const { getXeapiPublicKey } = require(path.join(root, 'util/xeapiKey.js'));
        key = await getXeapiPublicKey(key, global.deviceId);
        fs.writeFileSync(keyPath, JSON.stringify({ ...key, savedAt: Date.now() }));
      }
    }
    const request = require(path.join(root, 'util/request.js'));
    const fn = require(path.join(root, 'module', `${method}.js`));
    const response = await fn({ ...params, cookie: cookieToJson(params.cookie || ''),
      timestamp: Date.now(), timeout: 15000, unblock: 'false', os: 'pc' }, request);
    clearTimeout(timer);
    finish({ ok: true, body: response.body });
  } catch (error) {
    clearTimeout(timer);
    finish({ ok: false, kind: 'platform', code: Number(error?.body?.code || error?.status || 0) });
  }
})();
