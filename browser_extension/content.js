// MORVIX content script: extracts accessible text/images/video, sends them ONLY to the local backend,
// and applies the returned allow / warn / block action (FR-EXT-03..07, FR-ACT-03/04).
//
// Pre-access protection (runs at document_start, before the page is drawn):
//  * images and videos are blurred until MORVIX has decided; a blocked or flagged one stays hidden;
//  * page text sits behind a "checking" cover until the fast first pass answers (search query plus
//    opening text, quick engines); the full check keeps running afterwards and can still block;
//  * nothing is held back when the browser is not paired, protection is off, or the MORVIX server
//    cannot be reached; as a safety net, anything with no answer at all after NO_ANSWER_MS is shown.
(() => {
  // Never analyse MORVIX's own parent dashboard or API docs (they list category names);
  // the local test pages under /demo/ are analysed like any other site.
  if (/^(127\.0\.0\.1|localhost)$/.test(location.hostname) && location.port === '8000' && !location.pathname.startsWith('/demo/')) return;
  // Content stays hidden until MORVIX has decided. It is released without a verdict only when the
  // server cannot be reached (immediately) or, as a safety net, after NO_ANSWER_MS with no answer at all.
  const MAX_IMAGES = 40, IMAGE_CONCURRENCY = 2, MAX_VIDEOS = 2, NO_ANSWER_MS = 20000, MIN_W = 100, MIN_H = 80;
  const seen = new Set(), videosSeen = new WeakSet(), videosWaiting = new WeakSet(), warned = new Set();
  const imageVerdict = new Map();  // src -> 'pending' | 'ok' | 'blocked'
  const pageUrl = () => location.href.split('#')[0];
  let timer, mediaTimer, coverTimer, overlay = false, imageCount = 0, videoCount = 0, lastUrl = pageUrl();
  const domain = location.hostname.toLowerCase();
  // Single-page sites (YouTube, Google tabs, …) change the address without loading a new page. Each such
  // change is treated as a new page: fresh budgets, the old MORVIX screen removed, and the "checking"
  // cover plus the fast pass (with the new search query) run again before the new content is shown.
  const checkNavigation = () => {
    if (pageUrl() === lastUrl) return;
    lastUrl = pageUrl(); imageCount = 0; videoCount = 0; warned.clear();
    navigated();
  };

  // After the extension is reloaded or updated, scripts left in already-open tabs lose their connection
  // to it and every chrome.* call throws "Extension context invalidated". Such a tab then shows
  // everything it was holding back and stops checking; reloading the tab brings protection back.
  const alive = () => { try { return !!chrome.runtime?.id; } catch { return false; } };
  let retired = false;
  function retire() {
    if (retired) return;
    retired = true;
    try { liftCover(); } catch { /* not created yet */ }
    shieldCss.textContent = BLOCKED_CSS;  // already-hidden harmful elements stay hidden
  }
  // chrome.runtime.sendMessage that never throws: resolves with the reply, or null when there is none.
  const send = msg => new Promise(resolve => {
    if (!alive()) { retire(); return resolve(null); }
    try { chrome.runtime.sendMessage(msg, r => { void chrome.runtime.lastError; resolve(r ?? null); }); }
    catch { retire(); resolve(null); }
  });
  const state = () => new Promise(resolve => {
    if (!alive()) { retire(); return resolve({}); }
    try { chrome.storage.local.get(['token', 'policy', 'connected'], x => { void chrome.runtime.lastError; resolve(x || {}); }); }
    catch { retire(); resolve({}); }
  });
  // Small non-cryptographic hash used only to skip re-sending identical text (crypto.subtle does not
  // exist on plain http:// pages).
  const hashText = s => { let h = 2166136261; for (let i = 0; i < s.length; i++) { h ^= s.charCodeAt(i); h = Math.imul(h, 16777619); } return (h >>> 0).toString(16) + ':' + s.length; };
  const modalityOn = (policy, m) => !!policy?.enabled && policy?.modalities?.[m] !== false;
  const safe = s => { const d = document.createElement('span'); d.textContent = s; return d.innerHTML; };
  const FONT = '"Segoe UI",system-ui,-apple-system,sans-serif';
  const SEARCH = Symbol('search query');
  const SHIELD = '<svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="#fff" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 2 4 5v6c0 5 3.4 9.4 8 11 4.6-1.6 8-6 8-11V5z"/><path d="m9 12 2 2 4-4"/></svg>';
  const ruleFor = policy => (policy?.rules || []).find(r => r.enabled && r.kind === 'website' && (domain === r.target || domain.endsWith('.' + r.target)));

  /* ------------------------------------------------------------ pre-access shield */
  const shieldCss = document.createElement('style');
  shieldCss.id = 'morvix-shield';
  const MEDIA_CSS = {
    image: 'img:not([data-morvix-ok]){filter:blur(28px)!important;transition:filter .15s}',
    video: 'video:not([data-morvix-ok]){filter:blur(28px)!important}',
  };
  const BLOCKED_CSS = '[data-morvix-blocked]{filter:blur(40px) grayscale(1)!important;pointer-events:none!important}'
    + '[data-morvix-flagged]{filter:blur(32px)!important;pointer-events:none!important}';
  shieldCss.textContent = MEDIA_CSS.image + MEDIA_CSS.video + BLOCKED_CSS;
  document.documentElement.append(shieldCss);

  const cover = document.createElement('div');
  cover.id = 'morvix-checking';
  cover.setAttribute('role', 'status');
  cover.style = `position:fixed;inset:0;z-index:2147483646;background:#f6f7f9;color:#4d5466;display:grid;place-items:center;font:15px/1.5 ${FONT}`;
  cover.innerHTML = `<div style="display:flex;align-items:center;gap:12px"><div style="width:36px;height:36px;border-radius:11px;display:grid;place-items:center;background:linear-gradient(140deg,#7472e8,#9a80ea)">${SHIELD}</div><span>MORVIX is checking this page…</span></div>`;
  document.documentElement.append(cover);
  let coverWanted = false, overlayEl = null;
  const liftCover = () => { coverWanted = false; clearTimeout(coverTimer); cover.remove(); };
  const raiseCover = () => {
    coverWanted = true;
    document.documentElement.append(cover);
    clearTimeout(coverTimer); coverTimer = setTimeout(liftCover, NO_ANSWER_MS);  // safety net only; normally lifted by the fast pass's answer
  };
  raiseCover();

  // Apply the shield only where it is meant to work; everything else is released at once.
  let flagHides = true;
  const ready = state().then(st => {
    const policy = st.policy, rule = policy?.enabled ? ruleFor(policy) : null;
    flagHides = policy?.flag_hides !== false;
    const active = !!st.token && !!st.connected && !!policy?.enabled && rule?.action !== 'allow';
    if (rule?.action === 'block') { liftCover(); overlayBlock('Blocked website', 'Website rule'); }
    if (!active || !modalityOn(policy, 'text')) liftCover();
    shieldCss.textContent = (active && modalityOn(policy, 'image') ? MEDIA_CSS.image : '') + (active && modalityOn(policy, 'video') ? MEDIA_CSS.video : '') + BLOCKED_CSS;
    return { st, active };
  });

  /* --------------------------------------------------------------- interfaces */
  // UI-06 / FR-ACT-03 / FR-ACT-04: the page is hidden behind a MORVIX screen. "block" = the parent's
  // policy blocks it; "flag" = lower-confidence match, hidden as well and recorded for the parent's review.
  function overlayBlock(category, fn, mode = 'block') {
    liftCover();
    if (overlay) return;
    overlay = true;
    const flag = mode === 'flag';
    const el = document.createElement('div');
    el.id = 'morvix-block-screen';
    el.style = `position:fixed;inset:0;z-index:2147483647;background:#f6f7f9;color:#1d2130;display:grid;place-items:center;padding:24px;font:15px/1.55 ${FONT};text-align:center`;
    el.innerHTML = `<section style="max-width:440px;padding:36px 34px;background:#fff;border:1px solid #e7e9ef;border-radius:18px;box-shadow:0 10px 36px rgba(29,33,48,.07)">
      <div style="width:52px;height:52px;margin:0 auto 18px;border-radius:15px;display:grid;place-items:center;background:linear-gradient(140deg,#7472e8,#9a80ea)">${SHIELD}</div>
      <div style="font-size:11px;font-weight:700;letter-spacing:.16em;color:#8a90a0">MORVIX</div>
      <h2 style="margin:6px 0 10px;font:600 21px/1.3 ${FONT};color:#1d2130">${flag ? 'This page has been hidden' : 'This page has been blocked'}</h2>
      <p style="margin:0 0 16px;color:#4d5466">${flag ? 'It may contain upsetting content, so MORVIX hid it. Your parent or guardian can review it.' : 'It was blocked by the protection settings your parent or guardian chose.'}</p>
      <span style="display:inline-block;padding:4px 12px;border-radius:999px;background:${flag ? '#fdf3e2;color:#8f6113' : '#fcedef;color:#a73b4e'};font-size:13px;font-weight:600">${safe(category)}</span>
      ${fn ? `<p style="margin:10px 0 0;color:#8a90a0;font-size:13px">${fn === SEARCH ? 'Found in your search' : 'Detected by ' + safe(fn)}</p>` : ''}
      <p style="margin:18px 0 0"><button type="button" id="morvix-back" style="all:unset;cursor:pointer;padding:9px 18px;border-radius:10px;background:#7472e8;color:#fff;font-weight:600;font-size:14px">Go back</button></p>
      <p style="margin:22px 0 0;padding-top:18px;border-top:1px solid #f0f1f5;color:#8a90a0;font-size:13px">If you think this is a mistake, ask your parent or guardian to review it.</p></section>`;
    document.documentElement.append(el);
    overlayEl = el;
    el.querySelector('#morvix-back').onclick = () => { if (history.length > 1) history.back(); else location.replace('about:blank'); };
    document.querySelectorAll('video').forEach(v => { try { v.pause(); } catch { /* ignore */ } });
  }
  // Small note when MORVIX hid an image or video on an otherwise visible page.
  function hiddenNote(category) {
    if (warned.has('media') || !warned.add('media')) return;
    const n = document.createElement('div');
    n.setAttribute('role', 'status');
    n.style = `position:fixed;right:18px;top:18px;z-index:2147483645;display:flex;gap:12px;align-items:flex-start;max-width:340px;padding:14px 14px 14px 16px;background:#fff;color:#1d2130;border:1px solid #e7e9ef;border-left:4px solid #7472e8;border-radius:12px;box-shadow:0 8px 28px rgba(29,33,48,.12);font:14px/1.5 ${FONT};text-align:left`;
    n.innerHTML = `<div style="flex:1"><div style="font-weight:600;margin-bottom:2px">MORVIX hid some content</div>
      <div style="color:#4d5466;font-size:13px">An image or video on this page may show <b style="font-weight:600">${safe(category)}</b>, so it stays hidden. Your parent or guardian can review it.</div></div>
      <button type="button" aria-label="Dismiss" style="all:unset;cursor:pointer;color:#8a90a0;font-size:18px;line-height:1;padding:2px 4px">×</button>`;
    n.querySelector('button').onclick = () => n.remove();
    document.documentElement.append(n);
    setTimeout(() => n.remove(), 8000);
  }
  // FR-ACT-04 when the parent chose "show flagged content with a warning": access is kept, the child
  // sees a warning, and the event is still recorded for review.
  function warnBanner(category) {
    if (warned.has('warn:' + category) || !warned.add('warn:' + category)) return;
    const n = document.createElement('div');
    n.setAttribute('role', 'status');
    n.style = `position:fixed;right:18px;top:18px;z-index:2147483645;display:flex;gap:12px;align-items:flex-start;max-width:340px;padding:14px 14px 14px 16px;background:#fff;color:#1d2130;border:1px solid #e7e9ef;border-left:4px solid #c88a22;border-radius:12px;box-shadow:0 8px 28px rgba(29,33,48,.12);font:14px/1.5 ${FONT};text-align:left`;
    n.innerHTML = `<div style="flex:1"><div style="font-weight:600;margin-bottom:2px">Heads up: this may be upsetting</div>
      <div style="color:#4d5466;font-size:13px">MORVIX noticed possible <b style="font-weight:600;color:#8f6113">${safe(category)}</b>. Your parent or guardian can review it.</div></div>
      <button type="button" aria-label="Dismiss" style="all:unset;cursor:pointer;color:#8a90a0;font-size:18px;line-height:1;padding:2px 4px">×</button>`;
    n.querySelector('button').onclick = () => n.remove();
    document.documentElement.append(n);
    setTimeout(() => n.remove(), 10000);
  }
  // Blocked content is always hidden. Flagged content is hidden too unless the parent chose "show with a
  // warning" (policy.flag_hides = false). Text: the whole page. Image/video: only that element.
  function applyResult(d, el) {
    if (!d || d.skipped || (d.action !== 'block' && d.action !== 'warn')) return;
    if (d.action === 'warn' && !flagHides) {
      if (el) release(el);
      return warnBanner(d.category);
    }
    if (el) {
      (d.action === 'block' ? blockElement : flagElement)(el, d.category);
      hiddenNote(d.category);
    } else overlayBlock(d.category, d.feature === 'Search query' ? SEARCH : d.function, d.action === 'block' ? 'block' : 'flag');
  }
  // The picture a verdict belongs to: sites such as YouTube reuse <img> elements and only swap the picture.
  // Keyed on the src/srcset attributes: right after a swap, currentSrc still reports the old picture.
  const picKey = el => el.src + '|' + (el.getAttribute('srcset') || '');
  const mark = el => { if (el.tagName === 'IMG') el.dataset.morvixSrc = picKey(el); };
  function release(el) { el.setAttribute('data-morvix-ok', ''); mark(el); }
  // A reused element showing a different picture is blurred again (before the next paint, as mutation
  // callbacks run first) and re-checked once the new picture has loaded.
  function recheckIfChanged(el) {
    if (el.tagName !== 'IMG' || !el.dataset.morvixSrc || el.dataset.morvixSrc === picKey(el)) return;
    delete el.dataset.morvixSrc;
    el.removeAttribute('data-morvix-ok'); el.removeAttribute('data-morvix-flagged'); el.removeAttribute('data-morvix-blocked'); el.title = '';
  }
  function navigated() {
    const old = document.getElementById('morvix-block-screen');
    if (old) { overlayEl = null; old.remove(); overlay = false; }
    if (retired) return;
    ready.then(({ st, active }) => {
      if (!active || !modalityOn(st.policy, 'text')) return;
      raiseCover();
      // give the site a moment to put the new results on the page, then run the fast pass
      setTimeout(() => quiet(firstPass().catch(liftCover)).finally(() => setTimeout(text, 300)), 250);
    });
  }
  // A flagged image also stays hidden: anything that matches the classifiers is never shown to the
  // child. Flag vs block only changes how the event is recorded for the parent (FR-ACT-02).
  function flagElement(el, category) {
    el.removeAttribute('data-morvix-ok');
    el.setAttribute('data-morvix-flagged', '');
    el.title = `Hidden by MORVIX: ${category}`; mark(el);
    if (el.tagName === 'VIDEO') { try { el.pause(); } catch { /* ignore */ } el.addEventListener('play', () => el.pause()); }
  }
  function blockElement(el, category) {
    el.removeAttribute('data-morvix-ok');
    el.removeAttribute('data-morvix-flagged');
    el.setAttribute('data-morvix-blocked', '');
    el.title = `Hidden by MORVIX: ${category}`; mark(el);
    if (el.tagName === 'VIDEO') { try { el.pause(); } catch { /* ignore */ } el.addEventListener('play', () => el.pause()); }
  }

  /* --------------------------------------------------------------------- text */
  const pageText = () => (document.body?.innerText || '').replace(/\s+/g, ' ').trim().slice(0, 3500);
  // Sent through the service worker: a request from this content script would be a cross-site request
  // from the web page, which the MORVIX server refuses (CORS). null = no verdict (fail-open).
  const postText = async (text, fast) => { const r = await send({ type: 'analyze-text', text, query: searchQuery(), pageUrl: location.href, fast }); return r?.ok ? r.result : null; };
  // What the child typed into a search box (Google, Bing, DuckDuckGo, Yahoo, YouTube, …). It is checked
  // on its own, because a short query is diluted when it is read together with the results page.
  function searchQuery() {
    try {
      const u = new URL(location.href), h = u.hostname;
      if (!/(^|\.)(google|bing|duckduckgo|yahoo|youtube|ecosia|brave|yandex|baidu)\./.test(h) && !/search/i.test(u.pathname)) return '';
      const key = /(^|\.)youtube\./.test(h) ? 'search_query' : /(^|\.)yahoo\./.test(h) ? 'p' : 'q';
      return (u.searchParams.get(key) || '').trim().slice(0, 300);
    } catch { return ''; }
  }
  // Fast first pass while the cover is up: block or hide now, otherwise lift the cover.
  async function firstPass() {
    const { st, active } = await ready;
    if (overlay || !active || !modalityOn(st.policy, 'text') || !cover.isConnected) return liftCover();
    const text = pageText();
    if (text.length < 100 && !searchQuery()) return liftCover();  // nothing to read yet (e.g. app shell); the full pass checks later content
    applyResult(await postText(text || ' ', true));  // no verdict (server unreachable) -> null: shown
    liftCover();
  }
  // Full pass (all engines, whole extract): runs after the first pass and on later page changes.
  async function inspectText() {
    const text = pageText();
    if (text.length < 100 && !searchQuery()) return;
    const hash = hashText(location.href + '|' + text);
    if (seen.has(hash)) return;
    seen.add(hash);
    if (seen.size > 20) seen.delete(seen.values().next().value);
    const st = await state(), policy = st.policy;
    if (!st.token) return;
    // FR-EXT-09: cached website block rules keep working while the backend is unreachable.
    const rule = policy?.enabled ? ruleFor(policy) : null;
    if (rule?.action === 'block') { overlayBlock('Blocked website', 'Website rule'); return; }
    if (rule?.action === 'allow') return;
    if (!st.connected || !modalityOn(policy, 'text')) return;
    applyResult(await postText(text || ' ', false));  // backend unavailable -> null: last valid policy remains in effect
  }

  /* ------------------------------------------------------------------- images */
  // Every image of at least MIN_W x MIN_H (natural size) is checked, including inline data: thumbnails
  // such as image-search results, up to MAX_IMAGES per page view. Checks run through a small queue,
  // top of the page first; an image stays blurred while it waits. The fail-open timer starts when its
  // check is sent, so a long queue does not release images that were never checked.
  const queue = [];
  let inFlight = 0;
  const srcOf = im => im.currentSrc || im.src;
  const sameSrc = src => [...document.images].filter(x => srcOf(x) === src);
  function settle(src, verdict, category) {
    imageVerdict.set(src, verdict);
    for (const x of sameSrc(src)) {
      if (x.hasAttribute('data-morvix-blocked')) continue;
      if (verdict === 'blocked') blockElement(x, category);
      else if (verdict === 'flagged') flagElement(x, category);
      else release(x);
    }
  }
  function pump() {
    while (inFlight < IMAGE_CONCURRENCY && queue.length) {
      const src = queue.shift();
      inFlight++;
      let answered = false;
      const fallback = setTimeout(() => { if (imageVerdict.get(src) === 'pending') settle(src, 'ok'); }, NO_ANSWER_MS);  // safety net
      send({ type: 'analyze-image', imageUrl: src, pageUrl: location.href }).then(r => {  // null: no verdict (fail-open)
        if (answered) return;
        answered = true; clearTimeout(fallback); inFlight--;
        const d = r?.ok ? r.result : null;
        // a late block still hides an image the fail-open timer had already shown
        if (d?.action === 'block') { settle(src, 'blocked', d.category); hiddenNote(d.category); }
        else if (d?.action === 'warn' && flagHides) { settle(src, 'flagged', d.category); hiddenNote(d.category); }
        else if (d?.action === 'warn') { settle(src, 'ok'); warnBanner(d.category); }
        else if (imageVerdict.get(src) === 'pending') settle(src, 'ok');
        pump();
      });
    }
  }
  function screenImage(im) {
    if (im.hasAttribute('data-morvix-ok') || im.hasAttribute('data-morvix-blocked') || im.hasAttribute('data-morvix-flagged')) return;
    if (!im.complete) { if (!im.dataset.morvixWait) { im.dataset.morvixWait = '1'; im.addEventListener('load', () => { delete im.dataset.morvixWait; screenImage(im); }, { once: true }); } return; }
    const src = srcOf(im);
    const known = imageVerdict.get(src);
    if (known === 'pending') return;  // settled together with the first copy
    if (known) return settle(src, known, 'Hidden image');
    // icons, tiny images, sources that are neither http(s) nor data:image, and images over budget are shown (fail-open)
    if (im.naturalWidth < MIN_W || im.naturalHeight < MIN_H || !/^(https?:|data:image\/)/.test(src) || imageCount >= MAX_IMAGES) return release(im);
    imageVerdict.set(src, 'pending');
    imageCount++;
    queue.push(src);
    pump();
  }
  async function inspectImages() {
    const { st, active } = await ready;
    if (!active || !modalityOn(st.policy, 'image')) return;
    for (const im of document.images) screenImage(im);
  }

  /* ------------------------------------------------------------------- videos */
  // A frame is checked as soon as the video has one (even before it plays); the video stays blurred
  // until then. While playing, a frame every 5 s is checked in batches of 3 (max 2 videos per page).
  function grabFrame(v) {
    const c = document.createElement('canvas');
    const scale = Math.min(1, 640 / v.videoWidth);
    c.width = Math.round(v.videoWidth * scale); c.height = Math.round(v.videoHeight * scale);
    c.getContext('2d').drawImage(v, 0, 0, c.width, c.height);
    return c.toDataURL('image/jpeg', 0.7);  // throws for cross-origin video: not accessible
  }
  function sendFrames(v, frames) {
    send({ type: 'analyze-video', frames, pageUrl: location.href }).then(r => { if (r?.ok) applyResult(r.result, v); });
  }
  async function watchVideos() {
    const { st, active } = await ready;
    if (!active || !modalityOn(st.policy, 'video')) return;
    for (const v of document.querySelectorAll('video')) {
      if (videosSeen.has(v)) continue;
      if (videoCount >= MAX_VIDEOS) { videosSeen.add(v); release(v); continue; }  // over budget: fail-open
      if (v.readyState < 2) {  // no frame yet: check it as soon as one is decoded (it stays blurred meanwhile)
        if (!videosWaiting.has(v)) { videosWaiting.add(v); v.addEventListener('loadeddata', () => { videosWaiting.delete(v); watchVideos(); }, { once: true }); }
        continue;
      }
      videosSeen.add(v);
      if (v.videoWidth < 160) { release(v); continue; }
      videoCount++;
      let firstFrame;
      try { firstFrame = grabFrame(v); } catch { release(v); continue; }  // cross-origin: cannot be analysed
      const hidden = () => v.hasAttribute('data-morvix-blocked') || v.hasAttribute('data-morvix-flagged');
      const fallback = setTimeout(() => { if (!hidden()) release(v); }, NO_ANSWER_MS);  // safety net
      send({ type: 'analyze-video', frames: [firstFrame], pageUrl: location.href }).then(r => {
        clearTimeout(fallback);
        if (r?.ok) applyResult(r.result, v);
        if (!hidden()) release(v);
      });
      const frames = [];
      let tick;
      const grab = () => {
        if (v.hasAttribute('data-morvix-blocked')) return clearInterval(tick);
        try { frames.push(grabFrame(v)); } catch { clearInterval(tick); return; }
        if (frames.length >= 3) flush();
      };
      const flush = () => { if (frames.length) sendFrames(v, frames.splice(0, frames.length)); };
      v.addEventListener('play', () => { clearInterval(tick); tick = setInterval(() => { if (!v.paused && !v.ended) grab(); }, 5000); });
      v.addEventListener('pause', () => { clearInterval(tick); flush(); });
      v.addEventListener('ended', () => { clearInterval(tick); flush(); });
      if (!v.paused) v.dispatchEvent(new Event('play'));
    }
  }

  /* --------------------------------------------------------------- scheduling */
  // A failure inside a check must never leave the page covered or surface as an uncaught error.
  const quiet = p => Promise.resolve(p).catch(() => {});
  const text = () => quiet(retired ? null : inspectText());
  const media = () => { if (retired) return; checkNavigation(); quiet(inspectImages()); quiet(watchVideos()); };
  const start = () => {
    quiet(firstPass().catch(liftCover)).finally(() => setTimeout(text, 300));
    media();
    // media is screened quickly (it stays blurred meanwhile); text is re-checked after the page settles
    const observer = new MutationObserver(records => {
      if (retired) return observer.disconnect();
      for (const r of records) if (r.type === 'attributes') recheckIfChanged(r.target);
      clearTimeout(mediaTimer); mediaTimer = setTimeout(media, 150);
      clearTimeout(timer); timer = setTimeout(() => { checkNavigation(); text(); }, 1700);
    });
    observer.observe(document.body || document.documentElement, { childList: true, subtree: true, characterData: true, attributes: true, attributeFilter: ['src', 'srcset'] });
  };
  // BR-06 / FR-ACT-03: the web page cannot undo protection. If a page script removes the block screen,
  // the "checking" cover or the blur stylesheet, they are put straight back (before the next paint).
  const guard = new MutationObserver(() => {
    if (retired) return guard.disconnect();
    const root = document.documentElement;
    if (!shieldCss.isConnected) root.append(shieldCss);
    if (overlayEl && !overlayEl.isConnected) root.append(overlayEl);
    if (coverWanted && !cover.isConnected) root.append(cover);
  });
  guard.observe(document.documentElement, { childList: true });
  // Address changes on single-page sites do not always change the page content right away.
  const urlWatch = setInterval(() => { if (retired) return clearInterval(urlWatch); checkNavigation(); }, 300);
  window.addEventListener('popstate', () => checkNavigation());
  const safeStart = () => { try { start(); } catch { liftCover(); } };
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', safeStart, { once: true });
  else safeStart();
})();
