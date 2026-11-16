// MORVIX parent dashboard (vanilla JS). Talks only to the local MORVIX API.
// Served by the backend (http://127.0.0.1:8000/) -> same origin; served on :5500 -> local API on :8000.
const API = location.protocol.startsWith('http') && location.port !== '5500' ? location.origin + '/api/v1' : 'http://127.0.0.1:8000/api/v1';
const app = document.querySelector('#app');
const CATS = ['Graphic Violence', 'Threat Language', 'Self-harm Indicators'];
const MODS = ['text', 'image', 'video', 'audio'];
let token = localStorage.getItem('morvix_token'), page = 'dashboard', notice = '', channel = '';

async function req(path, opts = {}) {
  const headers = { ...(opts.headers || {}) };
  if (token) headers.Authorization = 'Bearer ' + token;
  if (opts.body && !(opts.body instanceof FormData)) headers['Content-Type'] = 'application/json';
  let r;
  try { r = await fetch(API + path, { ...opts, headers }); }
  catch { throw Error('Cannot reach the MORVIX server. Start it with the MORVIX desktop shortcut, then try again.'); }
  if (r.status === 204) return null;
  let d; try { d = await r.json(); } catch { d = { detail: r.statusText }; }
  if (!r.ok) { const e = Error(typeof d.detail === 'string' ? d.detail : (d.detail?.[0]?.msg || `Request failed (${r.status})`)); e.status = r.status; throw e; }
  return d;
}
const esc = (s = '') => String(s).replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const $ = s => document.querySelector(s);
const $$ = s => document.querySelectorAll(s);
const when = t => t ? new Date(t).toLocaleString([], { month: 'short', day: 'numeric', year: 'numeric', hour: 'numeric', minute: '2-digit' }) : '—';
const pct = c => c == null ? '—' : Math.round(c * 100) + '%';
const cap = s => s ? s[0].toUpperCase() + s.slice(1) : s;

// Line icons (24×24, stroked) used by the navigation, stat cards and sign-in screens
const ICONS = {
  shield: '<path d="M12 2 4 5v6c0 5 3.4 9.4 8 11 4.6-1.6 8-6 8-11V5z"/><path d="m9 12 2 2 4-4"/>',
  dashboard: '<rect x="3" y="3" width="7" height="7" rx="1.5"/><rect x="14" y="3" width="7" height="7" rx="1.5"/><rect x="3" y="14" width="7" height="7" rx="1.5"/><rect x="14" y="14" width="7" height="7" rx="1.5"/>',
  history: '<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>',
  alerts: '<path d="M6 8a6 6 0 0 1 12 0c0 7 3 9 3 9H3s3-2 3-9"/><path d="M10.3 21a1.94 1.94 0 0 0 3.4 0"/>',
  analytics: '<path d="M4 20V10M10 20V4M16 20v-7M21 20H3"/>',
  extensions: '<path d="M9 2v6M15 2v6M6 8h12v4a6 6 0 0 1-12 0zM12 18v4"/>',
  test: '<path d="M9 3h6M10 3v6L4 19a1.5 1.5 0 0 0 1.3 2h13.4a1.5 1.5 0 0 0 1.3-2L14 9V3"/><path d="M7 15h10"/>',
  rules: '<path d="M10 6h10M10 12h10M10 18h10"/><path d="m3 6 1.5 1.5L7 5M3 12l1.5 1.5L7 11M3 18l1.5 1.5L7 17"/>',
  settings: '<path d="M4 6h10M18 6h2M4 12h4M12 12h8M4 18h12"/><circle cx="16" cy="6" r="2"/><circle cx="10" cy="12" r="2"/><circle cx="18" cy="18" r="2"/>',
  logout: '<path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4"/><path d="m16 17 5-5-5-5M21 12H9"/>',
  block: '<circle cx="12" cy="12" r="9"/><path d="m5.6 5.6 12.8 12.8"/>',
  flag: '<path d="M4 22V4M4 4h13l-2 4 2 4H4"/>',
  allow: '<circle cx="12" cy="12" r="9"/><path d="m8 12 3 3 5-6"/>',
  mail: '<rect x="3" y="5" width="18" height="14" rx="2"/><path d="m3 7 9 6 9-6"/>',
};
const icon = n => `<svg viewBox="0 0 24 24" fill="none" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${ICONS[n] || ''}</svg>`;
const logo = () => `<span class="logo">${icon('shield')}</span>`;
// One colour per category / action / modality, reused by every chart
const COLOR = { 'Graphic Violence': '#e27d8c', 'Threat Language': '#e3a857', 'Self-harm Indicators': '#a58de0', block: '#e27d8c', warn: '#e3a857', allow: '#6cbf9a',
  text: '#8180e6', image: '#73a9dc', video: '#68bdb3', audio: '#b597e3' };

// UI-04: visible feedback after actions
function message(text, type = '') {
  notice = text ? `<div class="notice ${type}" role="status">${esc(text)}</div>` : '';
  const el = $('#msg'); if (el) el.innerHTML = notice;
  if (text && type === 'success') setTimeout(() => { if ($('#msg')?.innerHTML === notice) { notice = ''; $('#msg').innerHTML = ''; } }, 4000);
}

/* ------------------------------------------------------------------ sign in / sign up */
async function authView() {
  let cfg = { google_client_id: null };
  try { cfg = await req('/auth/config'); } catch { /* API offline: still show the form */ }
  let signup = false, verifyEmail = null, resendAt = 0, timer = null;
  const err = m => { $('#msg').innerHTML = `<div class="notice error">${esc(m)}</div>`; };
  const ok = m => { $('#msg').innerHTML = `<div class="notice success">${esc(m)}</div>`; };
  // FR-AUTH-09: enter the 6-digit code that was emailed to the new parent account
  const drawVerify = (info = '') => {
    clearInterval(timer);
    app.innerHTML = `<main class="auth"><section class="auth-card"><div class="auth-brand">${logo()}<div><h1>MORVIX</h1><p class="sub">Verify your email</p></div></div>
      <div class="mail-icon">${icon('mail')}</div>
      <div id="msg">${info ? `<div class="notice success">${esc(info)}</div>` : ''}</div>
      <p class="sub">We sent a 6-digit code to <b>${esc(verifyEmail)}</b>. Enter it below to activate your parent account. The code expires in 10 minutes. Check your spam folder if you can't find it.</p>
      <form id="verifyform">
        <div class="field"><label for="otp">Verification code</label><input id="otp" name="code" class="otp" inputmode="numeric" autocomplete="one-time-code" pattern="\\d{6}" maxlength="6" required placeholder="000000"></div>
        <button class="button primary wide">Verify and continue</button>
      </form>
      <div class="row verify-actions"><button type="button" class="button" id="resend">Resend code</button><button type="button" class="button ghost" id="back">Use a different email</button></div>
      </section></main>`;
    $('#otp').focus();
    $('#otp').oninput = e => { e.target.value = e.target.value.replace(/\D/g, '').slice(0, 6); };
    const tick = () => {
      const s = Math.ceil((resendAt - Date.now()) / 1000), b = $('#resend'); if (!b) return clearInterval(timer);
      b.disabled = s > 0; b.textContent = s > 0 ? `Resend code (${s}s)` : 'Resend code';
    };
    tick(); timer = setInterval(tick, 1000);
    $('#verifyform').onsubmit = async e => {
      e.preventDefault();
      try { clearInterval(timer); finish(await req('/auth/verify-email', { method: 'POST', body: JSON.stringify({ email: verifyEmail, code: $('#otp').value }) })); }
      catch (x) { err(x.message); $('#otp').select(); tick(); timer = setInterval(tick, 1000); }
    };
    $('#resend').onclick = async () => {
      try { const d = await req('/auth/resend-code', { method: 'POST', body: JSON.stringify({ email: verifyEmail }) }); resendAt = Date.now() + (d.resend_after || 60) * 1000; ok('A new code was sent. Only the newest code works.'); }
      catch (x) { err(x.message); }
      tick();
    };
    $('#back').onclick = () => { clearInterval(timer); verifyEmail = null; signup = true; draw(); };
  };
  const draw = () => {
    app.innerHTML = `<main class="auth"><section class="auth-card"><div class="auth-brand">${logo()}<div><h1>MORVIX</h1><p class="sub">Content protection · Parent / guardian ${signup ? 'sign-up' : 'sign-in'}</p></div></div>
      <div id="msg">${notice}</div>
      <div class="tabs"><button type="button" data-mode="in" class="${signup ? '' : 'on'}">Sign in</button><button type="button" data-mode="up" class="${signup ? 'on' : ''}">Create account</button></div>
      ${signup ? `<p class="sub">We'll email a 6-digit code to confirm the address belongs to you.</p>${cfg.email_configured === false ? '<div class="notice error"><b>Email is not set up yet</b>, so MORVIX cannot send the sign-up code. Open <b>Start menu → MORVIX → MORVIX Email Setup</b>, enter the Gmail address and its App Password, then open MORVIX again from the Desktop shortcut.</div>' : ''}` : ''}
      <form id="authform">
        ${signup ? '<div class="field"><label>Name</label><input name="name" maxlength="80" autocomplete="name" placeholder="Parent / guardian name"></div>' : ''}
        <div class="field"><label>Email</label><input name="email" type="email" required autocomplete="email"></div>
        <div class="field"><label>Password ${signup ? '(10+ characters)' : ''}</label><input name="password" type="password" minlength="10" required autocomplete="${signup ? 'new-password' : 'current-password'}"></div>
        <button class="button primary wide" id="submit">${signup ? 'Create account' : 'Sign in'}</button>
      </form>
      <div class="or"><span>or</span></div>
      ${cfg.google_client_id ? '<div id="gbtn" class="gbtn"></div>' : `<button class="button wide" id="googledisabled" type="button">${signup ? 'Sign up' : 'Sign in'} with Google</button><p class="sub" id="gnote" hidden>Google sign-in is not configured. Set <b>MORVIX_GOOGLE_CLIENT_ID</b> in <code>.env</code> and restart the backend (see README).</p>`}
      </section></main>`;
    $$('[data-mode]').forEach(b => b.onclick = () => { signup = b.dataset.mode === 'up'; notice = ''; draw(); });
    if ($('#googledisabled')) $('#googledisabled').onclick = () => { $('#gnote').hidden = false; };
    $('#authform').onsubmit = async e => {
      e.preventDefault();
      const body = Object.fromEntries(new FormData(e.target));
      const btn = $('#submit'); btn.disabled = true; btn.textContent = signup ? 'Sending code…' : 'Signing in…';
      try {
        if (signup) {
          const d = await req('/auth/register', { method: 'POST', body: JSON.stringify(body) });
          verifyEmail = d.email; resendAt = Date.now() + (d.resend_after || 60) * 1000;
          return drawVerify('Account created. Check your email for the code.');
        }
        finish(await req('/auth/login', { method: 'POST', body: JSON.stringify({ email: body.email, password: body.password }) }));
      } catch (x) {
        if (x.status === 403 && !signup) { verifyEmail = String(body.email).trim().toLowerCase(); resendAt = Date.now() + 60000; return drawVerify(x.message); }
        err(x.message); btn.disabled = false; btn.textContent = signup ? 'Create account' : 'Sign in';
      }
    };
    if (cfg.google_client_id) mountGoogle();
  };
  const finish = d => { token = d.access_token; localStorage.setItem('morvix_token', token); notice = ''; page = 'dashboard'; render(); };
  const mountGoogle = () => {
    const init = () => {
      google.accounts.id.initialize({
        client_id: cfg.google_client_id,
        callback: async resp => { try { finish(await req('/auth/google', { method: 'POST', body: JSON.stringify({ credential: resp.credential }) })); } catch (err) { $('#msg').innerHTML = `<div class="notice error">${esc(err.message)}</div>`; } },
      });
      google.accounts.id.renderButton($('#gbtn'), { theme: 'outline', size: 'large', text: signup ? 'signup_with' : 'signin_with', width: 356 });
    };
    if (window.google?.accounts?.id) return init();
    const s = document.createElement('script'); s.src = 'https://accounts.google.com/gsi/client'; s.async = true; s.onload = init;
    s.onerror = () => { $('#gbtn').innerHTML = '<span class="sub">Could not load Google sign-in (offline?).</span>'; };
    document.head.append(s);
  };
  draw();
}

/* ------------------------------------------------------------------------- shell */
const NAV = [['dashboard', 'Dashboard'], ['history', 'Detection history'], ['alerts', 'Alerts'], ['analytics', 'Analytics'], ['extensions', 'Extension status'], ['test', 'Test classifiers'], ['rules', 'Domain & category rules'], ['settings', 'Settings']];
const SUBTITLE = {
  dashboard: 'Protection status and recent activity at a glance',
  history: 'Search and review every recorded detection',
  alerts: 'Blocked and flagged content waiting for your review',
  analytics: 'Detection trends by category, channel and action',
  extensions: 'Browsers paired with this account',
  test: 'Run a sample through the same local classifiers the extension uses',
  rules: 'Allow, warn or block specific websites and categories',
  settings: 'Protection, channels, thresholds and data retention',
};
function shell(content, badge = 0) {
  app.innerHTML = `<div class="shell"><aside class="side"><div class="brand">${logo()}<div>MORVIX<small>CONTENT PROTECTION</small></div></div>
    <nav class="nav">${NAV.map(([k, v]) => `<button data-page="${k}" class="${page === k ? 'active' : ''}">${icon(k)}<span>${v}</span>${k === 'alerts' && badge ? `<span class="badge">${badge}</span>` : ''}</button>`).join('')}</nav>
    <div class="sidebar-foot"><div class="note">Local analysis only</div><button id="logout">${icon('logout')}Log out</button></div></aside>
    <main class="main"><header class="top"><div><h1>${NAV.find(x => x[0] === page)?.[1] || 'MORVIX'}</h1><div class="sub">${SUBTITLE[page] || 'Parent / guardian protection console'}</div></div><span class="local">Local server online</span></header>
    <div id="msg">${notice}</div><section id="content">${content}</section></main></div>`;
  $$('[data-page]').forEach(b => b.onclick = () => { page = b.dataset.page; notice = ''; render(); });
  $('#logout').onclick = async () => { try { await req('/auth/logout', { method: 'POST' }); } catch { /* ignore */ } localStorage.removeItem('morvix_token'); token = null; seenAlerts = null; render(); };
}

const bars = (obj, empty = 'No data in this range.') => {
  const entries = Object.entries(obj || {}), max = Math.max(1, ...entries.map(e => e[1]));
  const label = k => ({ warn: 'Flagged', block: 'Blocked', allow: 'Allowed' }[k] || cap(k));
  return entries.length ? `<div class="bars">${entries.map(([k, v]) => `<div class="bar"><span>${esc(label(k))}</span><div><i style="width:${Math.max(3, v / max * 100)}%${COLOR[k.split(' (')[0]] ? `;--c:${COLOR[k.split(' (')[0]]}` : ''}"></i></div><b>${v}</b></div>`).join('')}</div>` : `<div class="empty small">${empty}</div>`;
};

const actionPill = a => `<span class="pill ${a}">${a === 'warn' ? 'flagged' : a === 'block' ? 'blocked' : esc(a)}</span>`;
function table(items, compact = false) {
  if (!items.length) return '<div class="empty">No recorded activity yet.</div>';
  // FR-DASH-05: category, source/domain, time, modality and confidence (when the engine reported one)
  if (compact) return `<div class="table-wrap"><table><thead><tr><th>TIME</th><th>SOURCE / DOMAIN</th><th>MODALITY</th><th>CATEGORY</th><th>CONFIDENCE</th><th>ACTION</th><th></th></tr></thead><tbody>${items.map(e => `<tr>
    <td class="nowrap">${e.created_at ? new Date(e.created_at).toLocaleString([], { month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' }) : '—'}</td><td><b>${esc(e.domain || '—')}</b><div class="muted tiny">${esc(e.function || cap(e.modality) + ' Analysis')}</div></td><td>${esc(cap(e.modality) || '—')}</td><td>${esc(e.category || '—')}</td><td>${e.confidence == null ? '<span class="muted">n/a</span>' : pct(e.confidence)}</td><td>${actionPill(e.action)}</td>
    <td class="right">${e.reviewed ? '<span class="muted tiny">Reviewed</span>' : `<button class="button" data-review="${e.id}">Review</button>`}</td></tr>`).join('')}</tbody></table></div>`;
  return `<div class="table-wrap"><table><thead><tr><th>TIME</th><th>SOURCE</th><th>FUNCTION</th><th>APPLIED TO</th><th>CATEGORY</th><th>CONFIDENCE</th><th>ACTION</th><th>REVIEW</th></tr></thead><tbody>${items.map(e => `<tr>
    <td>${when(e.created_at)}</td><td>${esc(e.domain || '—')}</td><td>${esc(e.function || cap(e.modality) + ' Analysis')}<div class="muted tiny">${esc(cap(e.modality))}</div></td><td>${esc(e.feature || '—')}</td><td>${esc(e.category)}</td><td>${pct(e.confidence)}</td>
    <td><span class="pill ${e.action}">${esc(e.action === 'warn' ? 'flagged' : e.action === 'block' ? 'blocked' : e.action)}</span></td>
    <td>${e.reviewed ? 'Reviewed' : `<button class="button" data-review="${e.id}">Mark reviewed</button>`}</td></tr>`).join('')}</tbody></table></div>`;
}

/* --------------------------------------------------------------------- pages */
const channelBar = enabled => `<div class="chips" role="group" aria-label="Filter the dashboard by channel">${['', ...MODS.filter(m => m !== 'audio' || enabled.includes('audio'))].map(m => `<button class="chip ${channel === m ? 'on' : ''}" data-channel="${m}">${m ? cap(m) : 'All'}</button>`).join('')}</div>`;

async function dashboard() {
  // FR-DASH-04: the Audio filter only exists while audio analysis is enabled
  const first = await req('/summary' + (channel ? '?modality=' + channel : ''));
  const audioOff = channel === 'audio' && !first.enabled_modalities.includes('audio');
  if (audioOff) channel = '';
  const [d, allAlerts, models] = await Promise.all([audioOff ? req('/summary') : first, req('/alerts?read=false'), req('/models')]);
  const alerts = channel ? allAlerts.filter(a => a.modality === channel) : allAlerts;
  const chLabel = channel ? cap(channel) : 'All channels';
  const metrics = Object.entries(d.validated_metrics || {});
  // FR-DASH-02: protection state of the registered extension
  const extName = d.extension.name || 'No browser registered';
  const protNote = d.protection_ready ? `${extName} · connected` : !d.protection_enabled ? 'Protection is switched off in Settings' : d.extension.name ? `${extName} · not checked in recently` : 'Pair a browser extension to start';
  const cards = [  // [label, value, tone, icon, small text, note]  FR-DASH-03: aggregate counts per event state
    ['Protection', d.protection_ready ? 'Active' : 'Inactive', d.protection_ready ? 'green' : d.protection_enabled ? 'amber' : 'red', 'shield', true, protNote],
    ['Blocked', d.blocked, 'red', 'block'], ['Flagged', d.warned, 'amber', 'flag'], ['Allowed', d.allowed, 'blue', 'allow'],
    ['Pending review', d.unreviewed, 'violet', 'history', false, 'Detections not yet reviewed'], ['Unread alerts', d.unread_alerts, 'violet', 'alerts']];
  return `<div class="row channel-row"><div><h2 class="section-title">Summary statistics</h2><div class="muted tiny">${esc(chLabel)} · last ${d.period_days} days</div></div><div class="channel-pick"><span class="muted tiny">Channel</span>${channelBar(d.enabled_modalities)}</div></div>
  <div class="grid stats">${cards.map(([a, b, tone, ic, small, note]) => `<article class="card stat tone-${tone}"><div class="stat-head"><div class="stat-label">${a}</div><span class="stat-icon">${icon(ic)}</span></div><div class="stat-num ${small ? 'small' : ''}">${esc(b)}</div>${note ? `<div class="stat-note">${esc(note)}</div>` : ''}</article>`).join('')}</div>
  <div class="grid two"><article class="card"><div class="row"><h2 class="section-title">Recent detections</h2><span class="pill">${esc(chLabel)}</span></div>${table(d.recent, true)}<p class="muted tiny">Counts cover the last ${d.period_days} days. Open “Detection history” to search every record.</p></article>
  <div class="stack"><article class="card"><div class="row"><h2 class="section-title">Extension</h2><span class="pill ${d.extension.connected ? 'ok' : 'warn'}">${d.extension.connected ? 'Connected' : 'Disconnected'}</span></div>
    <dl class="kv"><dt>Protection</dt><dd>${d.protection_enabled ? 'Enabled' : 'Disabled'}</dd><dt>Checking</dt><dd>${esc(d.enabled_modalities.map(cap).join(', ') || 'Nothing')}</dd><dt>Last seen</dt><dd>${d.extension.last_seen ? when(d.extension.last_seen) : 'Never'}</dd></dl>
    <button class="button wide" id="pair">Generate pairing code</button><div id="pairout"></div></article>
  <article class="card"><div class="row"><h2 class="section-title">Alerts</h2><button class="button" data-page-link="alerts">View all</button></div>${alerts.length ? alerts.slice(0, 4).map(a => `<div class="mini"><span class="pill ${a.action}">${a.action === 'block' ? 'blocked' : 'flagged'}</span> <b>${esc(a.category)}</b> <span class="muted tiny">${esc(a.severity || '')} priority</span><div class="muted tiny">${esc(a.domain || 'Unknown source')} · ${esc(cap(a.modality) || '')} · ${pct(a.confidence)} · ${a.event_time ? new Date(a.event_time).toLocaleString([], { month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' }) : ''}</div></div>`).join('') : `<div class="empty small">No unread alerts${channel ? ' for ' + esc(cap(channel)) : ''}.</div>`}${alerts.length > 4 ? `<div class="muted tiny">+${alerts.length - 4} more unread</div>` : ''}</article>
  ${perfCard(metrics, models)}</div></div>`;
}

// FR-DASH-08: the headline is the combined MORVIX result; single-engine results are folded under "Details".
function perfCard(metrics, models) {
  const line = m => `accuracy ${pct(m.accuracy)}${m.macro_f1 != null ? ` · macro-F1 ${pct(m.macro_f1)}` : ''}${m.harmful_detection_rate != null ? ` · harmful detected ${pct(m.harmful_detection_rate)} · false positives ${pct(m.false_positive_rate)}` : ''}`;
  const engines = `<div class="muted tiny">Active engines: ${models.filter(m => m.available).map(m => esc(m.name)).join(', ') || 'none'}</div>`;
  if (!metrics.length) return `<article class="card"><h2 class="section-title">Model performance</h2><div class="muted">No validated evaluation available. Accuracy is shown only after you run <code>scripts/evaluate.py --save</code> on a labeled dataset.</div><details class="perf-more"><summary>Details</summary>${engines}</details></article>`;
  const [mainName, main] = metrics.find(([n]) => n === 'morvix-text-ensemble') || metrics[0];
  const rest = metrics.filter(([n]) => n !== mainName);
  const stat = (label, v) => `<div class="perf-stat"><b>${pct(v)}</b><span>${label}</span></div>`;
  return `<article class="card"><h2 class="section-title">Model performance</h2>
    <div class="muted tiny">${mainName === 'morvix-text-ensemble' ? 'MORVIX text detection (all engines combined)' : esc(mainName)}</div>
    <div class="perf-stats">${stat('accuracy', main.accuracy)}${main.harmful_detection_rate != null ? stat('harmful caught', main.harmful_detection_rate) + stat('false alarms', main.false_positive_rate) : ''}</div>
    <p class="muted tiny">Measured offline on ${esc(main.dataset)} (${main.samples} samples, ${esc(main.evaluated_at)}). Not a measurement of real-world accuracy.</p>
    <details class="perf-more"><summary>Details${rest.length ? `: ${rest.length} single engine${rest.length > 1 ? 's' : ''}` : ''}</summary>
      ${rest.map(([n, m]) => `<div class="mini"><b>${esc(n)}</b><div class="muted tiny">${line(m)}</div></div>`).join('')}${engines}</details></article>`;
}

async function historyPage() {
  return `<article class="card"><div class="toolbar"><input id="q" placeholder="Search domain, category, function"><input id="dom" placeholder="Domain"><input id="from" type="date" aria-label="Start date"><input id="to" type="date" aria-label="End date">
  <select id="mod"><option value="">All modalities</option>${MODS.map(x => `<option value="${x}">${cap(x)}</option>`).join('')}</select>
  <select id="act"><option value="">All actions</option><option value="allow">Allowed</option><option value="warn">Flagged / warned</option><option value="block">Blocked</option></select>
  <select id="cat"><option value="">All categories</option>${CATS.map(x => `<option>${esc(x)}</option>`).join('')}</select><button class="button primary" id="search">Apply filters</button><button class="button" id="clear">Clear</button></div>
  <div id="results">Loading…</div><div class="row"><span id="count" class="muted"></span><div><button class="button" id="prev">Previous</button> <button class="button" id="next">Next</button></div></div></article>`;
}

async function alertsPage() {
  const mode = new URLSearchParams(location.search).get('alertstate') || 'unread';
  const rows = await req('/alerts' + (mode === 'all' ? '' : '?read=' + (mode === 'read' ? 'true' : 'false')));
  return `<article class="card"><div class="row"><h2 class="section-title">Blocked and flagged events</h2><select id="alertfilter"><option value="unread" ${mode === 'unread' ? 'selected' : ''}>Unread</option><option value="read" ${mode === 'read' ? 'selected' : ''}>Reviewed / read</option><option value="all" ${mode === 'all' ? 'selected' : ''}>All alerts</option></select></div>
  ${rows.length ? `<div class="rule-list">${rows.map(a => `<div class="rule"><div><strong><span class="pill ${a.action}">${a.action === 'block' ? 'blocked' : 'flagged'}</span> ${esc(a.category)} <span class="pill ${a.severity === 'high' ? 'block' : 'warn'}">${esc(a.severity)} priority</span></strong><span class="muted">${esc(a.function || '')} · ${esc(a.feature || '')} · ${esc(a.domain || 'Unknown source')} · ${pct(a.confidence)} · ${when(a.event_time)}</span></div><div>${a.read ? 'Read' : `<button class="button" data-read="${a.id}">Mark as read</button>`} <button class="button" data-show-event="${a.event_id}">View event</button></div></div>`).join('')}</div>` : '<div class="empty">No alerts to review.</div>'}</article>`;
}

// FR-DASH-07 / SRS §2 Analytics: preset ranges or a custom date range
let range = 30, rangeFrom = '', rangeTo = '';
async function analyticsPage() {
  const custom = range === 'custom' && rangeFrom && rangeTo;
  const q = custom ? `start=${encodeURIComponent(new Date(rangeFrom + 'T00:00:00').toISOString())}&end=${encodeURIComponent(new Date(rangeTo + 'T23:59:59.999').toISOString())}` : 'days=' + (range === 'custom' ? 30 : range);
  const a = await req('/analytics?' + q), days = a.by_day;
  const max = Math.max(1, ...days.map(d => d.count));
  return `<div class="toolbar"><label class="muted" for="range">Time range</label><select id="range">${[[7, 'Last 7 days'], [30, 'Last 30 days'], [90, 'Last 90 days'], [365, 'Last year'], ['custom', 'Custom dates']].map(([v, l]) => `<option value="${v}" ${range === v ? 'selected' : ''}>${l}</option>`).join('')}</select>
  <span id="customrange" ${range === 'custom' ? '' : 'hidden'}><input id="rfrom" type="date" aria-label="From date" value="${rangeFrom}"> to <input id="rto" type="date" aria-label="To date" value="${rangeTo}"> <button class="button primary" id="applyrange">Apply</button></span>
  <span class="muted">${a.total} recorded events${custom ? ` from ${esc(rangeFrom)} to ${esc(rangeTo)}` : range === 'custom' ? ' (last 30 days until you pick dates)' : ''}</span></div>
  <div class="grid two2"><article class="card"><h2 class="section-title">By modality</h2>${bars(a.by_modality)}</article><article class="card"><h2 class="section-title">By category</h2>${bars(a.by_category)}</article>
  <article class="card"><h2 class="section-title">By action</h2>${bars(a.by_action)}</article><article class="card"><h2 class="section-title">By system function</h2>${bars(a.by_function)}</article></div>
  <article class="card" style="margin-top:16px"><h2 class="section-title">Detections per day</h2>${days.length ? `<div class="spark">${days.map(d => `<div title="${d.day}: ${d.count}"><i style="height:${Math.max(4, d.count / max * 100)}%"></i><span>${d.day.slice(5)}</span></div>`).join('')}</div>` : '<div class="empty small">No detections in this range.</div>'}</article>
  <article class="card" style="margin-top:16px"><h2 class="section-title">Measured inference time (NFR-PERF-03)</h2>${Object.keys(a.avg_latency_ms).length ? bars(Object.fromEntries(Object.entries(a.avg_latency_ms).map(([k, v]) => [k + ' (ms)', v]))) : '<div class="empty small">No timing data yet.</div>'}</article>`;
}


// AC-04..AC-06: run a sample through the same local pipeline the extension uses.
async function testPage() {
  const [cs, models] = await Promise.all([req('/classifiers/status'), req('/models')]);
  const st = m => `<span class="pill ${cs[m]?.ready ? 'ok' : 'warn'}">${cs[m]?.ready ? 'ready' : 'not installed'}</span>`;
  return `<div class="grid two2">
  <article class="card"><div class="row"><h2 class="section-title">Text</h2>${st('text')}</div><p class="muted tiny">Engines: ${esc(cs.text.model)}</p>
    <textarea id="ttext" rows="6" maxlength="12000" placeholder="Paste a sample sentence or paragraph"></textarea>
    <div class="row"><button class="button primary" data-test="text">Classify text</button></div></article>
  <article class="card"><div class="row"><h2 class="section-title">Image</h2>${st('image')}</div><p class="muted tiny">Engines: ${esc(cs.image.model)}</p>
    <input type="file" id="timage" accept="image/jpeg,image/png,image/webp"><div class="row"><button class="button primary" data-test="image">Classify image</button></div></article>
  <article class="card"><div class="row"><h2 class="section-title">Video</h2>${st('video')}</div><p class="muted tiny">Uniform frame sampling (every 3 s, max 60 frames, clips up to 3 minutes) through the image engines.</p>
    <input type="file" id="tvideo" accept="video/mp4,video/webm,video/ogg"><div class="row"><button class="button primary" data-test="video">Classify video</button></div></article>
  <article class="card"><div class="row"><h2 class="section-title">Audio (optional)</h2>${st('audio')}</div><p class="muted tiny">Local transcript, then the text pipeline. Enable Audio in Settings first.</p>
    <input type="file" id="taudio" accept="audio/*"><div class="row"><button class="button primary" data-test="audio">Classify audio</button></div></article></div>
  <article class="card" style="margin-top:16px"><h2 class="section-title">Result</h2><div id="testout" class="muted">Run a sample to see the category, confidence, per-engine scores and the action your current policy would apply. Detections above the flag threshold are recorded in history like extension events.</div></article>
  <article class="card" style="margin-top:16px"><h2 class="section-title">Installed engines</h2><div class="table-wrap"><table><thead><tr><th>ENGINE</th><th>MODALITY</th><th>STATUS</th><th>LICENSE</th><th>DESCRIPTION</th><th>MEASURED RESULT</th></tr></thead><tbody>${models.map(m => `<tr><td><b>${esc(m.name)}</b></td><td>${esc(cap(m.modality))}</td><td><span class="pill ${m.available ? 'ok' : 'warn'}">${m.available ? 'ready' : 'not installed'}</span></td><td class="tiny">${esc(m.license || '—')}</td><td class="tiny">${esc(m.description)}</td><td class="tiny">${m.evaluation ? `acc ${pct(m.evaluation.accuracy)} · macro-F1 ${pct(m.evaluation.macro_f1)} (${m.evaluation.samples} samples)` : 'not evaluated'}</td></tr>`).join('')}</tbody></table></div></article>`;
}

function testResult(r) {
  if (r.skipped) return `<div class="notice">${esc(r.skipped)}</div>`;
  const engines = Object.entries(r.engines || {});
  const harmless = r.action === 'allow' && (!r.category || r.confidence < (r.thresholds?.warn ?? 0.5));
  return `<div class="mini"><span class="pill ${r.action}">${r.action === 'warn' ? 'flagged' : r.action === 'block' ? 'blocked' : 'allowed'}</span> <b>${harmless ? 'No harmful content detected' : esc(r.category)}</b>${harmless && r.category ? ` <span class="muted tiny">(highest score: ${esc(r.category)} ${pct(r.confidence)}, below the flag threshold)</span>` : ` · confidence ${pct(r.confidence)}`} · ${r.latency_ms ?? '—'} ms ${r.recorded ? '· recorded in history' : '· not recorded'}</div>
    ${r.sampled_frames ? `<p class="muted tiny">${r.sampled_frames} frames sampled · ${r.flagged_frames ?? 0} above the flag threshold</p>` : ''}
    ${engines.length ? `<div class="table-wrap"><table><thead><tr><th>ENGINE</th>${CATS.map(c => `<th>${esc(c.toUpperCase())}</th>`).join('')}</tr></thead><tbody>${engines.map(([n, s]) => `<tr><td class="tiny">${esc(n)}</td>${CATS.map(c => `<td>${pct(s[c] ?? 0)}</td>`).join('')}</tr>`).join('')}</tbody></table></div>` : ''}
    <p class="muted tiny">Thresholds: flag ≥ ${r.thresholds?.warn ?? '—'}, apply category action ≥ ${r.thresholds?.block ?? '—'}. ${esc(r.note || '')}</p>`;
}

async function extensionsPage() {
  const ex = await req('/extensions');
  const shown = ex.filter(e => !e.revoked);  // removed browsers stay in the database for history, not in this list
  return `<article class="card"><div class="row"><div><h2 class="section-title">Registered browsers</h2><div class="sub">An extension counts as connected when it checked in within the last 3 minutes. Turning a browser off pauses its checks without unpairing it.</div></div><button class="button primary" id="pair">Generate pairing code</button></div><div id="pairout"></div>
  <div class="rule-list" style="margin-top:14px">${shown.length ? shown.map(e => `<div class="rule"><div><strong>${esc(e.name)} <span class="pill ${e.status === 'connected' ? 'ok' : e.status === 'off' ? 'block' : 'warn'}">${e.status === 'off' ? 'turned off' : e.status}</span></strong><span class="muted">Registered ${when(e.created_at)} · Last seen ${e.last_seen ? when(e.last_seen) : 'never'}</span></div><div><button class="button ${e.paused ? 'primary' : ''}" data-pause="${e.id}" data-paused="${e.paused ? 1 : 0}">${e.paused ? 'Turn on' : 'Turn off'}</button> <button class="button danger" data-remove="${e.id}">Remove</button></div></div>`).join('') : '<div class="empty">No extension registered yet. Generate a pairing code and enter it in the extension popup.</div>'}</div></article>`;
}

async function rulesPage() {
  const rs = await req('/rules');
  return `<article class="card"><div class="row"><div><h2 class="section-title">Approved / blocked domains and category rules</h2><div class="sub">Website rules take precedence over category rules. “Allow” approves a domain; “block” blocks it.</div></div><div><button class="button" id="refresh-rules">Refresh</button> <button class="button primary" id="newrule">Add rule</button></div></div>
  <div class="rule-list" style="margin-top:16px">${rs.length ? rs.map(r => `<div class="rule"><div><strong>${esc(r.target)}</strong><span class="muted">${esc(r.kind)} · <span class="pill ${r.action}">${esc(r.action)}</span> · ${r.enabled ? 'Enabled' : 'Disabled'}</span></div><div><button class="button" data-toggle="${r.id}" data-enabled="${r.enabled}">${r.enabled ? 'Disable' : 'Enable'}</button> <button class="button" data-edit="${r.id}" data-kind="${r.kind}" data-target="${esc(r.target)}" data-action="${r.action}">Edit</button> <button class="button danger" data-delete="${r.id}">Delete</button></div></div>`).join('') : '<div class="empty">No rules yet. Add a website or category rule.</div>'}</div></article>`;
}

// Sensitivity levels (same table as SENSITIVITY_LEVELS in backend/main.py) with their measured effect on
// the held-out text set (90 harmful + 70 harmless sentences, docs/EVALUATION.md, 2026-11-15).
const LEVELS = {
  1: [0.90, 0.98, 74, 48, 1], 2: [0.85, 0.95, 77, 62, 1], 3: [0.75, 0.92, 81, 71, 3], 4: [0.65, 0.88, 90, 74, 4], 5: [0.55, 0.84, 92, 77, 7],
  6: [0.50, 0.80, 92, 80, 9], 7: [0.45, 0.70, 96, 81, 10], 8: [0.40, 0.60, 96, 90, 13], 9: [0.35, 0.50, 97, 92, 14], 10: [0.30, 0.40, 98, 96, 14],
};
const nearestLevel = warn => Object.keys(LEVELS).reduce((a, k) => Math.abs(LEVELS[k][0] - warn) < Math.abs(LEVELS[a][0] - warn) ? k : a, '6');
const levelInfo = n => {
  const [w, b, caught, blocked, wrong] = LEVELS[n];
  return `<b>Level ${n}${n == 6 ? ' (recommended default)' : ''}</b>: flags content when a model is at least ${Math.round(w * 100)}% sure, and <b>blocks</b> it from ${Math.round(b * 100)}%.
    <div class="muted tiny">On our test sentences this level caught ${caught}% of harmful text (${blocked}% as blocked) and wrongly caught ${wrong}% of harmless text. Higher levels catch more but also catch more harmless pages.</div>`;
};

async function settingsPage() {
  const [s, cs, user] = await Promise.all([req('/settings'), req('/classifiers/status'), req('/auth/me')]);
  const act = c => ['warn', 'block', 'allow'].map(a => `<option value="${a}" ${(s.category_actions[c] || 'warn') === a ? 'selected' : ''}>${{ warn: 'Warn / flag', block: 'Block', allow: 'Allow' }[a]}</option>`).join('');
  return `<div class="grid two2"><article class="card"><h2 class="section-title">Protection policy</h2>
  <label class="check"><input type="checkbox" id="enabled" ${s.enabled ? 'checked' : ''}> Protection enabled for registered extensions</label>
  <h3 class="sub-title">Modalities</h3>${MODS.map(m => `<label class="check"><input type="checkbox" data-mod="${m}" ${s.modalities[m] ? 'checked' : ''}> ${cap(m)} analysis ${m === 'audio' ? '<span class="muted">(optional)</span>' : ''} ${cs[m]?.ready ? '' : '<span class="pill warn">model not installed</span>'}</label>`).join('')}
  <h3 class="sub-title">Category actions</h3>${CATS.map(c => `<div class="field"><label>${esc(c)}</label><select data-cat="${esc(c)}">${act(c)}</select></div>`).join('')}
  <h3 class="sub-title">Sensitivity</h3>
  <div class="field"><label for="sens">How sensitive MORVIX is: 1 = least, 10 = most (flags and blocks the most)</label>
    <div class="sens-row"><span class="muted tiny">1</span><input id="sens" type="range" min="1" max="10" step="1" value="${s.sensitivity ?? nearestLevel(s.warn_threshold)}"><span class="muted tiny">10</span><b id="sensval" class="sens-val"></b></div></div>
  <div id="sensinfo" class="sens-info"></div>
  <div class="field"><label for="flaghides">When MORVIX flags something (lower confidence than a block)</label><select id="flaghides">
    <option value="1" ${s.flag_hides !== false ? 'selected' : ''}>Hide it from the child (recommended)</option>
    <option value="0" ${s.flag_hides === false ? 'selected' : ''}>Show it with a warning</option></select>
    <p class="muted tiny">Blocked content is always hidden. Either way, flagged content is recorded for your review.</p></div>
  ${s.sensitivity == null ? `<p class="notice error tiny">Your current thresholds (flag from ${s.warn_threshold}, block from ${s.block_threshold}) are not one of the levels${s.warn_threshold >= 0.95 ? ', and they are so high that almost nothing is flagged or blocked' : ''}. Pick a level and save to replace them.</p>` : ''}
  <div class="field"><label>Extension policy sync interval (seconds)</label><input id="syncint" type="number" min="30" max="3600" value="${s.sync_interval_seconds}"></div>
  <div class="field"><label>Event retention (days)</label><input id="retention" type="number" min="1" max="3650" value="${s.retention_days}"></div>
  <button class="button primary" id="savesettings">Save settings</button>
  <div class="sub" style="margin-top:12px">Local engines: ${Object.entries(cs).filter(([k]) => MODS.includes(k)).map(([k, v]) => `${k} ${v.ready ? 'ready' : 'unavailable'}`).join(' · ')}</div></article>
  <article class="card"><h2 class="section-title">Account</h2><p class="muted">Signed in as ${esc(user.email)}${user.name ? ' (' + esc(user.name) + ')' : ''} · ${user.provider === 'google' ? 'Google account' : 'email + password'}</p>
  ${user.provider === 'google' ? '<p class="muted">This account signs in with Google; there is no MORVIX password to change.</p>' : `<form id="passwordform"><div class="field"><label>Current password</label><input name="current_password" type="password" required></div><div class="field"><label>New password (10+ characters)</label><input name="new_password" type="password" minlength="10" required></div><button class="button" type="submit">Change password</button></form>`}</article></div>`;
}

/* ------------------------------------------------- live alerts (FR-DASH-06) */
// Every 15 s, look for unread alerts that were recorded since the last check. New ones raise a
// notice describing the blocked/flagged event, update the sidebar badge, and refresh the
// dashboard or alerts page so the new record appears without a manual reload.
let seenAlerts = null, alertTimer = null;
function watchAlerts() {
  if (alertTimer) return;
  alertTimer = setInterval(async () => {
    if (!token) { clearInterval(alertTimer); alertTimer = null; seenAlerts = null; return; }
    if (document.hidden || document.querySelector('.modal')) return;
    let un; try { un = await req('/alerts?read=false'); } catch { return; }
    const fresh = un.filter(a => !seenAlerts.has(a.id));
    un.forEach(a => seenAlerts.add(a.id));
    const b = document.querySelector('.nav [data-page="alerts"]');
    if (b) { b.querySelector('.badge')?.remove(); if (un.length) b.insertAdjacentHTML('beforeend', `<span class="badge">${un.length}</span>`); }
    if (!fresh.length) return;
    const a = fresh[0], verb = a.action === 'block' ? 'Blocked' : 'Flagged';
    const text = fresh.length === 1
      ? `New alert: ${verb} ${a.category} (${cap(a.modality)}) on ${a.domain || 'an unknown source'} · ${pct(a.confidence)} confidence.`
      : `${fresh.length} new alerts. Latest: ${verb} ${a.category} on ${a.domain || 'an unknown source'}.`;
    if ((page === 'dashboard' || page === 'alerts') && !$('#pairout')?.innerHTML) { notice = `<div class="notice error" role="alert">${esc(text)}</div>`; render(); }
    else { notice = `<div class="notice error" role="alert">${esc(text)}</div>`; const el = $('#msg'); if (el) el.innerHTML = notice; }
  }, 15000);
}

/* ------------------------------------------------------------------- render */
async function render() {
  if (!token) return authView();
  try {
    const pages = { dashboard, history: historyPage, alerts: alertsPage, analytics: analyticsPage, test: testPage, extensions: extensionsPage, rules: rulesPage, settings: settingsPage };
    const content = await pages[page]();
    let badge = 0; try { const un = await req('/alerts?read=false'); badge = un.length; if (!seenAlerts) seenAlerts = new Set(un.map(a => a.id)); } catch { /* ignore */ }
    shell(content, badge); bind(); watchAlerts();
  } catch (e) {
    if (/Session|Sign in/.test(e.message)) { token = null; localStorage.removeItem('morvix_token'); notice = 'Please sign in again.'; authView(); }
    else shell(`<article class="card"><div class="notice error">${esc(e.message)}</div><p class="muted">Is the backend running at ${API}?</p><button class="button" onclick="render()">Retry</button></article>`);
  }
}

function modal(kind, data = {}) {
  const body = kind === 'rule'
    ? `<h2>${data.id ? 'Edit' : 'Add'} rule</h2><form id="ruleform"><div class="field"><label>Rule type</label><select name="kind" ${data.id ? 'disabled' : ''}><option value="website">Website domain</option><option value="category">Content category</option></select></div>
       <div class="field"><label>Target</label><select name="target" id="targetcat" hidden>${CATS.map(c => `<option>${esc(c)}</option>`).join('')}</select><input name="target" id="targettext" placeholder="example.com" required></div>
       <div class="field"><label>Action</label><select name="action"><option value="warn">warn</option><option value="block">block</option><option value="allow">allow</option></select></div><div class="row"><button type="button" class="button" id="cancel">Cancel</button><button class="button primary">Save rule</button></div></form>`
    : `<h2>Detection event</h2><div>${table([data])}</div><p class="muted tiny">Model: ${esc(data.model || '—')} · latency ${data.latency_ms ?? '—'} ms · only metadata is stored, never page text or media.</p><button class="button" id="cancel">Close</button>`;
  const m = document.createElement('div'); m.className = 'modal'; m.innerHTML = `<div class="${kind === 'event' ? 'wide-modal' : ''}">${body}</div>`; document.body.append(m);
  m.querySelector('#cancel').onclick = () => m.remove();
  if (kind === 'event') m.querySelectorAll('[data-review]').forEach(b => b.onclick = async () => { await req(`/events/${b.dataset.review}/review`, { method: 'PATCH' }); m.remove(); render(); });
  if (kind !== 'rule') return;
  const f = m.querySelector('#ruleform'), kindEl = f.elements.kind, tt = m.querySelector('#targettext'), tc = m.querySelector('#targetcat');
  if (data.id) { kindEl.value = data.kind; f.elements.action.value = data.action; if (data.kind === 'category') { tt.hidden = true; tt.required = false; tc.hidden = false; tc.value = data.target; } else tt.value = data.target; }
  else kindEl.onchange = () => { const c = kindEl.value === 'category'; tt.hidden = c; tt.required = !c; tc.hidden = !c; };
  f.onsubmit = async e => {
    e.preventDefault();
    const kind = data.id ? data.kind : kindEl.value, p = { kind, target: kind === 'category' ? tc.value : tt.value, action: f.elements.action.value };
    try { await req(data.id ? `/rules/${data.id}` : '/rules', { method: data.id ? 'PATCH' : 'POST', body: JSON.stringify(data.id ? { target: p.target, action: p.action } : p) }); m.remove(); notice = '<div class="notice success">Rule saved. Extensions pick it up at the next policy sync.</div>'; render(); }
    catch (err) { m.querySelector('h2').insertAdjacentHTML('afterend', `<div class="notice error">${esc(err.message)}</div>`); }
  };
}

let pairWatch = null;
async function pairCode(out) {
  clearInterval(pairWatch);
  try {
    const known = new Set((await req('/extensions')).map(e => e.id));
    const x = await req('/extensions/pair-code', { method: 'POST' });
    const expires = Date.now() + (x.expires_in || 600) * 1000;
    const until = new Date(expires).toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' });
    $(out).innerHTML = `<div class="notice success">Pairing code (single use, valid until ${until}):<strong class="code">${esc(x.code)}</strong>Paste it into the MORVIX extension popup. This message updates when a browser pairs.</div>`;
    // The backend deletes a code once it is used, so stop showing it as valid after a new browser pairs or it expires.
    pairWatch = setInterval(async () => {
      if (page !== 'extensions' || !$(out)) return clearInterval(pairWatch);
      if (Date.now() >= expires) {
        clearInterval(pairWatch);
        $(out).innerHTML = '<div class="notice error">That pairing code has expired. Generate a new one to pair a browser.</div>';
        return;
      }
      try {
        const added = (await req('/extensions')).filter(e => !known.has(e.id));
        if (added.length) {
          clearInterval(pairWatch);
          notice = `<div class="notice success">Paired ${esc(added.map(e => e.name).join(', '))}. The pairing code has been used and no longer works.</div>`;
          render();
        }
      } catch { /* keep the code visible; try again on the next tick */ }
    }, 3000);
  } catch (e) { message(e.message, 'error'); }
}

function bind() {
  $$('[data-page-link]').forEach(b => b.onclick = () => { page = b.dataset.pageLink; render(); });
  $$('[data-channel]').forEach(b => b.onclick = () => { channel = b.dataset.channel; render(); });
  $$('[data-review]').forEach(b => b.onclick = async () => { await req(`/events/${b.dataset.review}/review`, { method: 'PATCH' }); notice = '<div class="notice success">Event marked as reviewed.</div>'; render(); });
  $$('[data-read]').forEach(b => b.onclick = async () => { await req(`/alerts/${b.dataset.read}/read`, { method: 'PATCH' }); notice = '<div class="notice success">Alert marked as read.</div>'; render(); });
  $$('[data-show-event]').forEach(b => b.onclick = async () => modal('event', await req('/events/' + b.dataset.showEvent)));
  $$('[data-pause]').forEach(b => b.onclick = async () => {
    const off = b.dataset.paused !== '1';
    try { await req('/extensions/' + b.dataset.pause, { method: 'PATCH', body: JSON.stringify({ paused: off }) }); }
    catch (e) { return message(e.message, 'error'); }
    notice = `<div class="notice success">${off ? 'Browser turned off. It stops checking pages at its next sync and stays paired, so you can turn it back on any time.' : 'Browser turned back on. It resumes checking at its next sync.'}</div>`; render();
  });
  $$('[data-remove]').forEach(b => b.onclick = async () => { if (confirm('Remove this browser? It stops receiving policy and returns to its pairing screen. To use it again, generate a new pairing code.')) { await req('/extensions/' + b.dataset.remove, { method: 'DELETE' }); notice = '<div class="notice success">Browser removed. Its extension will show the pairing screen again.</div>'; render(); } });
  if ($('#pair')) $('#pair').onclick = () => pairCode('#pairout');

  if (page === 'history') {
    let p = 1;
    const load = async () => {
      const params = new URLSearchParams({ page: p, page_size: 20 });
      for (const [id, key] of [['q', 'q'], ['dom', 'domain'], ['mod', 'modality'], ['act', 'action'], ['cat', 'category']]) if ($('#' + id).value) params.set(key, $('#' + id).value);
      if ($('#from').value) params.set('start', new Date($('#from').value + 'T00:00:00').toISOString());
      if ($('#to').value) params.set('end', new Date($('#to').value + 'T23:59:59').toISOString());
      const d = await req('/events?' + params);
      $('#results').innerHTML = table(d.items);
      $('#count').textContent = `${d.total} records · page ${d.page}`;
      $$('#results [data-review]').forEach(b => b.onclick = async () => { await req(`/events/${b.dataset.review}/review`, { method: 'PATCH' }); message('Event marked as reviewed.', 'success'); load(); });
    };
    $('#search').onclick = () => { p = 1; load(); };
    $('#clear').onclick = () => { $$('.toolbar input,.toolbar select').forEach(x => x.value = ''); p = 1; load(); };
    $('#prev').onclick = () => { if (p > 1) { p--; load(); } };
    $('#next').onclick = () => { p++; load(); };
    load();
  }
  if (page === 'alerts') $('#alertfilter').onchange = e => { history.replaceState(null, '', location.pathname + (e.target.value === 'unread' ? '' : `?alertstate=${e.target.value}`)); render(); };
  if (page === 'test') $$('[data-test]').forEach(b => b.onclick = async () => {
    const kind = b.dataset.test, out = $('#testout');
    let opts;
    if (kind === 'text') {
      const text = $('#ttext').value.trim(); if (!text) return message('Enter some text first.', 'error');
      opts = { method: 'POST', body: JSON.stringify({ text }) };
    } else {
      const f = $('#t' + kind).files[0]; if (!f) return message('Choose a file first.', 'error');
      const form = new FormData(); form.append('file', f); opts = { method: 'POST', body: form };
    }
    b.disabled = true; out.innerHTML = 'Analysing locally… (the first run loads the models and can take a while)';
    try { out.innerHTML = testResult(await req('/classify/' + kind, opts)); message(''); }
    catch (e) { out.innerHTML = `<div class="notice error">${esc(e.message)}</div>`; }
    finally { b.disabled = false; }
  });
  if (page === 'analytics') {
    $('#range').onchange = e => { range = e.target.value === 'custom' ? 'custom' : Number(e.target.value); if (range === 'custom') $('#customrange').hidden = false; else render(); };
    $('#applyrange').onclick = () => {
      const f = $('#rfrom').value, t = $('#rto').value;
      if (!f || !t) return message('Choose both a start and an end date.', 'error');
      if (f > t) return message('The start date must be on or before the end date.', 'error');
      rangeFrom = f; rangeTo = t; notice = ''; render();
    };
  }
  if (page === 'rules') {
    $('#refresh-rules').onclick = () => render();
    $('#newrule').onclick = () => modal('rule');
    // UI-04: visible feedback after every policy change
    $$('[data-delete]').forEach(b => b.onclick = async () => {
      if (!confirm('Delete this rule?')) return;
      try { await req(`/rules/${b.dataset.delete}`, { method: 'DELETE' }); } catch (e) { return message(e.message, 'error'); }
      notice = '<div class="notice success">Rule deleted. Extensions pick up the change at the next policy sync.</div>'; render();
    });
    $$('[data-toggle]').forEach(b => b.onclick = async () => {
      const on = b.dataset.enabled !== '1';
      try { await req(`/rules/${b.dataset.toggle}`, { method: 'PATCH', body: JSON.stringify({ enabled: on }) }); } catch (e) { return message(e.message, 'error'); }
      notice = `<div class="notice success">Rule ${on ? 'enabled' : 'disabled'}. Extensions pick up the change at the next policy sync.</div>`; render();
    });
    $$('[data-edit]').forEach(b => b.onclick = () => modal('rule', { id: b.dataset.edit, kind: b.dataset.kind, target: b.dataset.target, action: b.dataset.action }));
  }
  if (page === 'settings') {
    const showLevel = () => { $('#sensval').textContent = $('#sens').value; $('#sensinfo').innerHTML = levelInfo($('#sens').value); };
    $('#sens').oninput = showLevel; showLevel();
    if ($('#passwordform')) $('#passwordform').onsubmit = async e => { e.preventDefault(); try { await req('/auth/password', { method: 'PUT', body: JSON.stringify(Object.fromEntries(new FormData(e.target))) }); notice = '<div class="notice success">Password changed. Other sessions were signed out.</div>'; render(); } catch (err) { message(err.message, 'error'); } };
    $('#savesettings').onclick = async () => {
      const category_actions = {}, modalities = {};
      $$('[data-cat]').forEach(x => category_actions[x.dataset.cat] = x.value);
      $$('[data-mod]').forEach(x => modalities[x.dataset.mod] = x.checked);
      const si = Number($('#syncint').value);
      if (!(si >= 30 && si <= 3600)) return message('The sync interval must be between 30 and 3600 seconds. Chrome checks for policy updates at most every 30 seconds.', 'error');
      try {
        await req('/settings', { method: 'PUT', body: JSON.stringify({ enabled: $('#enabled').checked, category_actions, modalities, sensitivity: Number($('#sens').value), flag_hides: $('#flaghides').value === '1', sync_interval_seconds: Number($('#syncint').value), retention_days: Number($('#retention').value) }) });
        notice = `<div class="notice success">Policy saved (sensitivity ${$('#sens').value}). Registered extensions apply it within ${Number($('#syncint').value)} seconds.</div>`; render();
      } catch (e) { message(e.message, 'error'); }
    };
  }
}
render();
