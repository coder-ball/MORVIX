const $ = s => document.querySelector(s);
const el = (k, v) => `<dt>${k}</dt><dd>${v}</dd>`;

function host(url) { try { return new URL(url).hostname.toLowerCase(); } catch { return ''; } }

// UI-05: status, current-page protection and relevant actions
function pageStatus(x, url) {
  const h = host(url);
  if (!h || !/^https?:/.test(url)) return 'This page type is not analysed by MORVIX.';
  if (x?.policy?.paused) return `${h}: protection is turned off for this browser by your parent.`;
  if (!x?.policy?.enabled) return `${h}: protection is switched off by your parent.`;
  const rule = (x.policy.rules || []).find(r => r.enabled && r.kind === 'website' && (h === r.target || h.endsWith('.' + r.target)));
  if (rule?.action === 'block') return `${h}: blocked by a website rule.`;
  if (rule?.action === 'allow') return `${h}: approved by a website rule (not analysed).`;
  const mods = ['text', 'image', 'video'].filter(m => x.policy.modalities?.[m] !== false);
  const list = mods.length > 1 ? mods.slice(0, -1).join(', ') + ' and ' + mods.at(-1) : mods[0];
  if (!mods.length) return `${h}: all content checks are switched off by your parent.`;
  return x.connected ? `${h}: protected — ${list} checks are running.` : `${h}: backend offline — website rules from the last synced policy still apply; ${list} checks resume when it reconnects.`;
}

function status(fresh = true) {
  // Show the saved state at once, then sync so a browser the parent removed or turned off is shown correctly.
  chrome.runtime.sendMessage({ type: 'status' }, draw);
  if (fresh) chrome.runtime.sendMessage({ type: 'sync' }, () => chrome.runtime.sendMessage({ type: 'status' }, draw));
}
function draw(x) {
  {
    const connected = !!x?.connected, policy = x?.policy;
    const active = connected && policy?.enabled;
    $('#state').className = 'status ' + (active ? 'ok' : 'warn');
    $('#state').textContent = !x?.token ? 'Not paired' : !connected ? (policy?.enabled ? 'Backend offline — last policy in effect' : 'Backend disconnected') : policy?.paused ? 'Turned off by parent' : !policy?.enabled ? 'Protection inactive' : 'Protection active';
    if (x?.token) {
      const mods = policy ? Object.entries(policy.modalities || {}).filter(([, on]) => on).map(([m]) => m).join(', ') || 'none' : '—';
      $('#details').innerHTML = el('Connection', connected ? 'Connected' : 'Disconnected') + el('Protection', policy?.paused ? 'Off for this browser' : policy?.enabled ? 'On' : 'Off') + el('Checking', policy?.enabled ? mods : 'none') + el('Last sync', x.lastSync ? new Date(x.lastSync).toLocaleTimeString() : 'never');
    } else $('#details').innerHTML = '<dd style="grid-column:1/-1;text-align:left;font-weight:400;color:#4d5466">' + (x?.connectionError === 'removed' ? 'This browser was removed in the parent dashboard. ' : '') + 'In the parent dashboard choose <b>Generate pairing code</b>, then paste the code below.</dd>';
    // Already paired: the code box stays hidden unless the parent chose to move this browser to another account.
    $('#pairing').hidden = !!x?.token && !repairing;
    $('#repair').hidden = !x?.token || repairing;
    $('#repairNote').hidden = $('#cancelRepair').hidden = !(x?.token && repairing);
    $('#pairLabel').textContent = x?.token && repairing ? 'Connect to a different parent account' : 'Pair with the parent dashboard';
    $('#pair').textContent = x?.token && repairing ? 'Switch account' : 'Pair extension';
    chrome.tabs.query({ active: true, currentWindow: true }, tabs => { $('#page').textContent = x?.token ? pageStatus(x, tabs?.[0]?.url || '') : ''; });
  }
}
let repairing = false;
function setRepairing(on) { repairing = on; $('#code').value = ''; say('', true); status(false); if (on) $('#code').focus(); }
status();
$('#repair').onclick = () => setRepairing(true);
$('#cancelRepair').onclick = () => setRepairing(false);
$('#pair').onclick = () => chrome.runtime.sendMessage({ type: 'pair', code: $('#code').value.trim() }, r => {
  if (!r?.ok) return say(r?.error || 'Could not pair', false);
  const switched = repairing;
  repairing = false; $('#code').value = '';
  status(false);
  say(switched ? 'Connected to the new parent account' : 'Paired and synchronized', true);
});
$('#sync').onclick = () => chrome.runtime.sendMessage({ type: 'sync' }, r => { status(false); say(r?.connected ? 'Policy synchronized' : 'MORVIX server unavailable; the last policy stays in effect', !!r?.connected); });
function say(text, ok) { $('#msg').textContent = text; $('#msg').style.color = ok ? '#24785a' : '#a73b4e'; }
