# Architecture and data model (MORVIX v3)

MORVIX is a client-server system with two front-end clients: the **parent web dashboard** and the **Chromium browser extension**. One local FastAPI process serves the REST API (`/api/v1/...`) and the dashboard files (`/`). The classification layer (`backend/classifiers.py`) sits behind that API and only runs local models.

```text
 Protected browser (child)                         Local MORVIX server (127.0.0.1:8000)
 +------------------------------+                  +------------------------------------------+
 | content.js                   |  text / image /  | FastAPI (backend/main.py)                |
 |  - visible text (3,500 chars)|  video frames    |  - auth, sessions, pairing               |
 |  - up to 4 large <img>       | ---------------> |  - policy decision (decide())            |
 |  - <video> frame every 5 s   |                  |  - events / alerts (SQLite)              |
 |  - warn banner / block page  | <--------------- |  - analytics, functions, models          |
 | background.js (service wkr)  |  action + result |        |                                 |
 |  - pairing, policy sync      |                  |        v                                 |
 |  - cached policy (offline)   | <-- policy ----- | classifiers.py (local only)              |
 | popup.html                   |                  |  text : lexicon + NLI + KoalaAI +        |
 +------------------------------+                  |     suicidality + toxic-bert (fused)     |
                                                   |  image: ViT violence + CLIP prompts      |
 Parent dashboard (browser)                        |  video: sampled frames -> image engines  |
 +------------------------------+  bearer session  |  audio: faster-whisper -> text engines   |
 | web_dashboard/app.js         | <--------------> +------------------------------------------+
 +------------------------------+                                   |
                                                                    v
                                                          data/morvix.db (SQLite)
```

## Analysis flow

1. The content script extracts accessible content (text prefix, eligible images, frames of playing videos).
2. The service worker / content script sends it to `/api/v1/extensions/analyze/{text|image|video|audio}` with the extension bearer credential.
3. The backend checks the parent's policy gates (protection enabled, modality enabled).
4. The classifier ensemble returns per-category scores; the highest category and score become the result.
5. `decide()` applies the policy (rule precedence below) and returns allow / warn / block.
6. Warn and block outcomes are stored as events plus alerts; raw content is discarded.
7. The extension shows the warn banner or block overlay. The dashboard reads the stored events.

### Pre-access protection (extension v3.7)

Rule: **while something is blurred or covered, MORVIX is still deciding; anything that matches the classifiers (flag or block) is never shown to the child.** Flag vs block only changes how the parent sees the event (flagged = lower confidence, for review).

The content script runs at `document_start`, before the page is drawn:
- **Images and videos** are blurred by a stylesheet from the first moment. Every image of at least 100 × 80 px (natural size), including inline `data:` thumbnails such as image-search results, is checked, up to 40 per page view, through a queue of two at a time (top of the page first). The first decoded frame of each video (up to 2) is checked the same way. Allowed media is shown; flagged or blocked media stays hidden (blurred, not clickable, video paused) and a small "MORVIX hid some content" note appears. Only that element is hidden, not the page.
- **Text** sits behind a "MORVIX is checking this page…" cover until the fast first pass answers. On a search results page (Google, Bing, DuckDuckGo, Yahoo, YouTube and others) the **search query** is checked on its own with every engine, because a short query is diluted when read with the results. A block or flag replaces the page with a MORVIX screen ("blocked" or "hidden") and a Go back button. The full check (all engines, whole extract) then runs and can still hide the page.
- **When nothing is held back:** the browser is not paired, protection or the modality is off, a website allow rule matches, or the MORVIX server cannot be reached (released immediately). As a safety net, anything with no answer at all after 20 s is shown, so browsing never gets stuck. Website block rules from the last synced policy still apply offline.
- Page text, like images and video, is sent to the backend by the extension's service worker. A request from the content script itself is a cross-site request from the web page and the server refuses it (CORS); before v3.7 this meant page text was never checked on real websites.

- **Single-page sites** (YouTube, Google tab switches): an address change without a page load is treated as a new page. The cover goes up again, the fast pass runs with the new search query, and an old MORVIX screen is removed. Sites that reuse `<img>` elements and only swap the picture get the new picture blurred before it is drawn and checked again.
- After the extension is reloaded or updated, scripts in tabs that were already open lose their connection to it; such a tab shows its content and stops checking until it is reloaded.

Not covered: CSS background images, images inside iframes (the script runs in the top frame only), images beyond 40 per page view, text drawn on a canvas, and search queries typed into a page that does not put them in the address (`q=` etc.).

### Inference scheduling

Every model call already uses all CPU cores, so requests running side by side only slow each other down. In a real Chrome test (2026-11-15) page-text verdicts took 45–47 s while tabs sent dozens of thumbnails, and the pre-access cover gave up before its answer arrived. Two changes:
- Model work runs **one job at a time by priority** (`inference` gate in `backend/classifiers.py`): the cover's fast pass and the search-query check first (they share one turn), then images, video frames and the full text pass in FIFO order. The full pass takes 2 windows per turn, so a waiting fast check never sits behind a whole page.
- The image, video and audio endpoints run their model work on worker threads; on the event loop each image check had frozen every other request.

Measured with 40 image checks and a full page check running: the cover's check (fast page pass + query) answered in 2.2–2.6 s, three runs in a row.

### Sensitivity (dashboard Settings)

The parent picks a sensitivity from 1 (least) to 10 (most). Each level sets both thresholds (`SENSITIVITY_LEVELS` in `backend/main.py`): content is hidden from the flag threshold and recorded as blocked from the block threshold. Level 6 = 0.50 / 0.80, the defaults the held-out evaluation uses. Measured effect per level: docs/EVALUATION.md. The API still accepts raw `warn_threshold` / `block_threshold`; the settings then report `sensitivity: null` (custom).

## Rule precedence (`decide()`)

1. Protection disabled, or the modality is disabled: the request is skipped (allow, nothing recorded).
2. An enabled **website rule** that matches the exact host or a subdomain: its action is used.
3. Confidence at or above the **block threshold**: the category rule action, or else the category default (warn if unset).
4. Confidence between the **warn** and **block** thresholds: warn (never an automatic block), or allow if the category is set to allow.
5. Confidence below the warn threshold, or a benign result: allow, and no event is created.

## Text ensemble

| Engine | Labels used | MORVIX category |
|---|---|---|
| `morvix-lexicon-v1` | phrase rules with negation and context damping | all three |
| `MoritzLaurer/deberta-v3-base-zeroshot-v2.0` | NLI entailment of one plain-language statement per category | all three |
| `KoalaAI/Text-Moderation` | `V2` / `SH` / `V` (×0.85) / `H2` (×0.7) | Graphic Violence / Self-harm / Threat / Threat |
| `sentinet/suicidality` | `LABEL_1` (suicidal) | Self-harm Indicators |
| `unitary/toxic-bert` | `threat` | Threat Language |

Page text is split into sentence-packed windows of 300 characters or fewer (at most 14, which covers the extension's 3,500-character extract). Every engine scores every window; the engines run in parallel. `fuse_text` then combines the engines **per window, using that window's own text**:
- maximum score for Threat and Graphic Violence;
- the self-harm specialist votes are halved without a first-person reference, and halved again when NLI reads a threat or gore aimed at someone else;
- a strong first-person self-harm signal halves the threat and graphic scores.

The page result is the strongest window. Judging each window separately means one harmful sentence in a long page is not diluted by the text around it, and first-person or target cues come from the sentence itself rather than from anywhere on the page. A sentence or short paragraph is a single window and is classified exactly as before.

See docs/EVALUATION.md. If a window or a neighbouring window reads as news, fiction or prevention material, that window's model scores are multiplied by 0.5 and its lexicon scores by 0.6.

## Image and video

| Engine | Output | MORVIX category |
|---|---|---|
| `jaranohaal/vit-base-violence-detection` | violent / non-violent | Graphic Violence |
| `openai/clip-vit-base-patch32` | softmax over 11 harmful + 16 benign prompts, summed per category | Graphic Violence, Self-harm, Threat |

Video: the extension grabs one frame every 5 s while a `<video>` plays and sends them in batches of 3 (at most 2 videos per page view). Uploaded clips are sampled every 3 s, capped at 60 frames and 3 minutes. The result is the highest-scoring non-benign frame above the warn threshold, or else the highest frame.

## SQLite tables

- `users`: email (unique, lower-case), PBKDF2-SHA256 hash, name, provider (password/google), google_sub, status, role.
- `sessions`: SHA-256 of a random bearer token, user, expiry (14 days).
- `settings`: enabled, category actions (JSON), modalities (JSON), warn/block thresholds, sync interval, retention days, updated_at.
- `extensions`: id, SHA-256 of the extension credential, owner, name, created, last_seen, revoked.
- `pair_codes`: hashed single-use codes, 10-minute expiry.
- `rules`: website or category rule, with action and enabled flag (unique per account, kind and target).
- `events`: id, user, extension, time (UTC ISO-8601), URL, domain, modality, function, feature, category, confidence, action, model, latency_ms, reviewed.
- `alerts`: event reference, severity (high = block, medium = warn), read state; cascade-deleted with the event.
- `model_metadata`: engine name, version, modality, categories, description, license, evaluation reference (dataset) and evaluation date. Rebuilt at start-up from the engine inventory and `data/evaluation.json`.

Repeat detections are de-duplicated: the same extension, page URL, modality, category and action within 10 minutes reuse one event and one alert. Retention runs at startup, hourly while the dashboard is in use, and immediately when the retention setting changes.
