// MORVIX service worker: pairing, policy sync, image/video analysis relay.
const API = 'http://127.0.0.1:8000/api/v1';
const STORE = ['token', 'policy', 'modelStatus', 'lastSync', 'connected', 'connectionError'];

// FR-SET-05: the sync interval comes from the parent's policy (default 30 s).
async function schedule(seconds) {
  const minutes = Math.max(0.5, (seconds || 30) / 60);
  const existing = await chrome.alarms.get('morvix-sync');
  if (!existing || Math.abs(existing.periodInMinutes - minutes) > 0.01) chrome.alarms.create('morvix-sync', { periodInMinutes: minutes });
}

// FR-EXT-09 / NFR-REL-02: on failure only the connection flag changes; the last valid policy stays in storage.
async function sync() {
  const { token } = await chrome.storage.local.get(['token']);
  if (!token) return { connected: false };
  try {
    const r = await fetch(API + '/extensions/policy', { headers: { Authorization: 'Bearer ' + token } });
    if (r.status === 401) {
      // The parent removed this browser: drop the dead token so the popup shows the pairing box again.
      await chrome.storage.local.remove(['token', 'policy', 'lastSync']);
      await chrome.storage.local.set({ connected: false, connectionError: 'removed' });
      return { connected: false, error: 'revoked' };
    }
    if (!r.ok) throw Error('HTTP ' + r.status);
    const policy = await r.json();
    const mr = await fetch(API + '/classifiers/status');
    const modelStatus = mr.ok ? await mr.json() : {};
    await chrome.storage.local.set({ policy, modelStatus, lastSync: Date.now(), connected: true, connectionError: '' });
    await schedule(policy.sync_interval_seconds);
    return { connected: true, policy, modelStatus };
  } catch (e) {
    await chrome.storage.local.set({ connected: false, connectionError: String(e) });
    return { connected: false, error: String(e) };
  }
}

// Chrome may clear alarms when the browser restarts, so the sync alarm is re-created whenever the
// service worker starts (install, browser start-up, or any wake-up), not only on install.
async function ensureAlarm() {
  const { policy } = await chrome.storage.local.get(['policy']);
  if (!(await chrome.alarms.get('morvix-sync'))) await schedule(policy?.sync_interval_seconds);
}
chrome.runtime.onInstalled.addListener(() => { ensureAlarm(); sync(); });
chrome.runtime.onStartup.addListener(() => { ensureAlarm(); sync(); });
chrome.alarms.onAlarm.addListener(a => { if (a.name === 'morvix-sync') sync(); });
ensureAlarm();

async function authed() {
  const { token } = await chrome.storage.local.get(['token']);
  if (!token) throw Error('Not paired');
  return token;
}
async function post(path, form, pageUrl) {
  const token = await authed();
  const r = await fetch(API + path + '?url=' + encodeURIComponent(pageUrl), { method: 'POST', headers: { Authorization: 'Bearer ' + token }, body: form });
  const result = await r.json();
  if (!r.ok) throw Error(result.detail || 'Analysis failed');
  maybeSync();
  return result;
}
// Refresh the policy/connection state after analysis, at most every 10 s (a page can send up to 40 images).
let lastAnalysisSync = 0;
function maybeSync() {
  if (Date.now() - lastAnalysisSync < 10000) return;
  lastAnalysisSync = Date.now();
  sync();
}

// GIF/AVIF/SVG/BMP or very large images are re-encoded as a <=1024 px JPEG so the backend
// accepts them and uploads stay small. Images the browser cannot decode are skipped.
async function normaliseImage(blob) {
  const ok = ['image/jpeg', 'image/png', 'image/webp'].includes(blob.type);
  if (ok && blob.size < 1.5 * 1024 * 1024) return blob;
  let bmp;
  try { bmp = await createImageBitmap(blob); } catch { throw Error('Unsupported image type'); }
  const scale = Math.min(1, 1024 / Math.max(bmp.width, bmp.height));
  const canvas = new OffscreenCanvas(Math.max(1, Math.round(bmp.width * scale)), Math.max(1, Math.round(bmp.height * scale)));
  canvas.getContext('2d').drawImage(bmp, 0, 0, canvas.width, canvas.height);
  bmp.close();
  return canvas.convertToBlob({ type: 'image/jpeg', quality: 0.85 });
}

const handlers = {
  sync: () => sync(),
  status: () => chrome.storage.local.get(STORE),
  async pair(msg) {
    const r = await fetch(API + '/extensions/pair', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ code: msg.code, name: 'MORVIX Chromium' }) });
    const d = await r.json();
    if (!r.ok) throw Error(d.detail || 'Pairing failed');
    await chrome.storage.local.set({ token: d.extension_token });
    return { ok: true, ...(await sync()) };
  },
  // Page text is posted from here, not from the content script: a content script's request counts as
  // a cross-site request from the web page and the MORVIX server refuses it (CORS), whereas the service
  // worker's host permission allows it.
  async 'analyze-text'(msg) {
    const token = await authed();
    const r = await fetch(API + '/extensions/analyze/text', { method: 'POST', headers: { 'Content-Type': 'application/json', Authorization: 'Bearer ' + token },
      body: JSON.stringify({ text: msg.text, query: msg.query || null, url: msg.pageUrl, feature: 'Visible page text', fast: !!msg.fast }) });
    const result = await r.json();
    if (!r.ok) throw Error(result.detail || 'Analysis failed');
    maybeSync();
    return { ok: true, result };
  },
  async 'analyze-image'(msg) {
    const source = await fetch(msg.imageUrl, { credentials: 'omit', cache: 'no-store' });
    if (!source.ok) throw Error('Image unavailable');
    let blob = await source.blob();
    if (blob.size > 12 * 1024 * 1024) throw Error('Image exceeds local limit');
    blob = await normaliseImage(blob);
    const form = new FormData();
    form.append('file', blob, 'page-image');
    return { ok: true, result: await post('/extensions/analyze/image', form, msg.pageUrl) };
  },
  // FR-EXT-04/05: frames sampled by the content script from an accessible <video>.
  async 'analyze-video'(msg) {
    const form = new FormData();
    for (const [i, data] of msg.frames.entries()) form.append('files', await (await fetch(data)).blob(), 'frame' + i + '.jpg');
    return { ok: true, result: await post('/extensions/analyze/video', form, msg.pageUrl) };
  },
};

chrome.runtime.onMessage.addListener((msg, sender, reply) => {
  const h = handlers[msg.type];
  if (!h) return false;
  Promise.resolve(h(msg)).then(reply).catch(e => reply({ ok: false, error: e.message }));
  return true;
});
