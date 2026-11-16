# MORVIX — Intelligent Multimodal Content Protection (v3)

MORVIX is a local content-protection prototype. It helps a parent or guardian review potentially harmful content that a child encounters in a supported web browser. It combines a **browser extension** with a **parent web dashboard** and a **local server**.

You can use it to:

* Check accessible webpage text, images and playing videos for possible threats, self-harm indicators, or graphic violence.
* Set protection rules: allow content, show a warning, or block it.
* Review detection history, alerts and analytics in the dashboard.

## Final deliverables

| Deliverable | Where |
|---|---|
| Final SRS | [`docs/MORVIX_SRS_v3.docx`](docs/MORVIX_SRS_v3.docx) · [PDF](docs/MORVIX_SRS_v3.pdf) |
| Final wireframes (Figma) | [Figma: MORVIX wireframes](https://www.figma.com/design/5lUXumwGgXLc2AnKlrKPZp/Untitled) · images in [`docs/evidence/wireframes/`](docs/evidence/wireframes) |
| Final mockups (Figma) | [Figma: MORVIX Mockups](https://www.figma.com/design/iaeYsUBRXYoJTzVXetiUHk/MORVIX-Mockups) · images in [`docs/evidence/mockups/`](docs/evidence/mockups) |
| Final system | This repository (v3.7.3); setup in [Quick start](#quick-start-windows-powershell-from-this-folder) below |
| Diagrams (SDLC, architecture, UML deployment) | [`docs/evidence/diagrams/`](docs/evidence/diagrams) |
| Evidence screenshots | [`docs/screenshots/`](docs/screenshots) and the gallery below |

### Wireframes (low fidelity, August 2026)

| | |
|---|---|
| **Parent dashboard**<br>![Wireframe: parent dashboard](docs/evidence/wireframes/01-parent-dashboard.png) | **Detection details**<br>![Wireframe: detection details](docs/evidence/wireframes/02-detection-details.png) |
| **Protection settings**<br>![Wireframe: protection settings](docs/evidence/wireframes/03-protection-settings.png) | **Extension popup**<br>![Wireframe: extension popup](docs/evidence/wireframes/04-extension-popup.png) |
| **Content blocked**<br>![Wireframe: content blocked](docs/evidence/wireframes/05-blocked-content.png) | |

### Mockups (high fidelity, Figma)

Made in Figma on 16 Nov 2026 from the implemented interface; they record the design system the system follows.

| | |
|---|---|
| **Sign-in**<br>![Mockup: sign-in](docs/evidence/mockups/01-sign-in.png) | **Dashboard**<br>![Mockup: dashboard](docs/evidence/mockups/02-dashboard.png) |
| **Settings**<br>![Mockup: settings](docs/evidence/mockups/03-settings.png) | **Extension popup**<br>![Mockup: extension popup](docs/evidence/mockups/04-extension-popup.png) |
| **Page hidden**<br>![Mockup: page hidden](docs/evidence/mockups/05-page-hidden.png) | **Design system**<br>![Mockup: design system](docs/evidence/mockups/06-design-system.png) |

### Final system evidence (screenshots)

Captured from the running system on 16 Nov 2026 (local server, parent dashboard, and the real extension loaded in Microsoft Edge). Detections come from the project's fictional test pages (`/demo/`) on a demonstration account.

| | |
|---|---|
| **Sign-in**<br>![Sign-in](docs/screenshots/01-sign-in.jpg) | **Dashboard**<br>![Dashboard](docs/screenshots/02-dashboard.jpg) |
| **Create account**<br>![Create account](docs/screenshots/08-create-account.png) | **Email verification code**<br>![Email verification](docs/screenshots/09-email-verification.jpg) |
| **Test classifiers: sample threat sentence flagged by the local AI engines**<br>![Test classifiers result](docs/screenshots/03-test-classifiers-result.jpg) | **Alerts**<br>![Alerts](docs/screenshots/04-alerts.jpg) |
| **Detection history**<br>![Detection history](docs/screenshots/05-detection-history.jpg) | **Analytics**<br>![Analytics](docs/screenshots/06-analytics.jpg) |
| **Extension status / pairing**<br>![Extension status](docs/screenshots/07-extension-status.jpg) | **Settings**<br>![Settings](docs/screenshots/10-settings.png) |
| **Domain & category rules**<br>![Rules](docs/screenshots/11-domain-category-rules.png) | **Extension popup before / after pairing**<br>![Popup unpaired](docs/screenshots/12-extension-popup-unpaired.png) ![Popup paired](docs/screenshots/13-extension-popup-paired.png) |
| **Page held behind the "checking" cover**<br>![Checking cover](docs/screenshots/14-page-checking-cover.png) | **Threat page hidden**<br>![Threat page hidden](docs/screenshots/15-threat-page-hidden.png) |
| **Graphic violence page hidden**<br>![Violence page hidden](docs/screenshots/16-violence-page-hidden.png) | **Safe page shown normally**<br>![Safe page](docs/screenshots/17-safe-page-shown.png) |

MORVIX only sees content that the extension can access on supported sites; it does not monitor the whole device. On the team's held-out text test set (160 short synthetic sentences) the text pipeline scored **accuracy 0.90, macro-F1 0.90, with 92 % of harmful sentences detected and 8.6 % false positives** ([evaluation](docs/EVALUATION.md)). That small set does not show it is reliable on real browsing, and image/video detection is not evaluated, so treat it as a student prototype, not a dependable safety or crisis-detection tool. The specification is in [`docs/MORVIX_SRS_v3.docx`](docs/MORVIX_SRS_v3.docx).

## Everyday use (no VS Code or terminal)

- **Start:** double-click **MORVIX** on the Desktop (or Start menu → MORVIX → MORVIX). It starts the server in the background and opens the dashboard in its own app window. If MORVIX is already running, it just opens the window.
- **Stop:** Start menu → MORVIX → **Stop MORVIX**.
- **Email for sign-up codes:** Start menu → MORVIX → **MORVIX Email Setup** (asks for the Gmail address and its App Password, then sends a test email).
- **Install as an app:** in the dashboard window, use Chrome's menu → *Cast, save and share* → *Install page as app* to get a taskbar icon. The server still has to be running (use the MORVIX shortcut).
- **Extension test pages:** http://127.0.0.1:8000/demo/ has pages that should warn and pages that should not.
- Shortcuts are created by `launcher\install_shortcuts.ps1` (run it again after moving the folder). Server logs are in `data\server.log` and `data\server-error.log`.

## Quick start (Windows PowerShell, from this folder)

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-ml.txt   # includes requirements.txt; PyTorch is large
.\.venv\Scripts\python.exe scripts\download_models.py              # ~2.8 GB of local weights into .\models
powershell -ExecutionPolicy Bypass -File .\start_morvix.ps1
```

Open **http://127.0.0.1:8000**. The backend serves the dashboard and the API (`/api/v1`, docs at `/docs`) from one process. Create an account (password of 10 or more characters), or sign in with Google if `MORVIX_GOOGLE_CLIENT_ID` is set in `.env`. In that case add `http://127.0.0.1:8000` as an authorised JavaScript origin of the OAuth client.

### Email verification (needed before anyone can sign up)

A new password account stays inactive until the parent types the **6-digit code** MORVIX emails to them. A code works once, expires after 10 minutes, and allows 5 wrong tries; a new code can be requested once a minute. Google sign-in skips the code, because Google has already verified the address.

To send the codes from a Gmail account:
1. Turn on **2-Step Verification** for that Google account.
2. Create an **App Password** (Google Account → Security → App passwords). Your normal Gmail password will not work.
3. In `.env`, set `MORVIX_SMTP_USER` to the Gmail address and `MORVIX_SMTP_PASSWORD` to the 16-character app password, then restart MORVIX.

Until this is set, the Create account tab says email is not configured and sign-up is refused. For a classroom demo without email, `MORVIX_EMAIL_MODE=console` prints the code in the server window instead (never use that for real accounts). The code proves the parent controls that inbox; it does not prove they are the child's parent.

Without `requirements-ml.txt` and the weights, MORVIX still runs: text uses the built-in lexicon, and image/video return HTTP 503 ("model not installed"), never a fake "safe" result.

### Install the browser extension (Chrome or Edge)

1. Dashboard → **Generate pairing code** (single use, valid 10 minutes).
2. Open `chrome://extensions` (or `edge://extensions`), turn on **Developer mode**, choose **Load unpacked**, and select the `browser_extension` folder.
3. Open the MORVIX popup, paste the code, and choose **Pair extension**. The popup shows connection, protection state, the active modalities and the last sync.

**Moving the extension to a different parent account** (e.g. the parent forgot the old account and created a new one): sign in to the new account → **Generate pairing code**, then in the popup choose **Connect to a different parent account**, paste the code and choose **Switch account**. The old pairing is kept until the new code is accepted; afterwards the old account lists this browser as removed **and its email address is sent a notice** that the browser was moved. This means a child cannot quietly move protection to an account of their own, while a parent who forgot the old account can still switch (the notice needs the `MORVIX_SMTP_*` email settings; without them the switch still works and the failure is logged).

## What is in the system

| Part | Where | What it does |
|---|---|---|
| Browser extension (MV3) | `browser_extension/` | Extracts up to 3,500 chars of visible text, the 4 largest images and a frame every 5 s of playing videos. Applies allow / warn banner / block overlay. Caches the last policy for offline use. Handles single-page-app navigation and de-duplicates warnings. |
| Parent web dashboard | `web_dashboard/` | Sign-in (password or Google), Dashboard, Detection history, Alerts, Analytics, Extension status, **Test classifiers**, Rules, Settings. |
| API and policy engine | `backend/main.py` | Auth, pairing, policy decisions, events, alerts, analytics, retention, de-duplication, dashboard hosting. |
| Classifier layer | `backend/classifiers.py` | Local ensembles. **Text:** lexicon + DeBERTa-v3 zero-shot NLI + KoalaAI/Text-Moderation + sentinet/suicidality + toxic-bert, run in parallel and combined by evaluated fusion rules. **Image/video:** ViT violence detector + CLIP prompt ensemble. **Audio:** optional faster-whisper → text. |
| Data | `data/morvix.db` | SQLite. Stores metadata only, never page text or media. |

See [architecture](docs/ARCHITECTURE.md), [models](docs/MODELS.md), [API](docs/API.md), [acceptance checklist](docs/ACCEPTANCE.md), [SRS traceability matrix](docs/SRS_TRACEABILITY.md) and [licenses](LICENSES.md).

## Tests

```powershell
.\.venv\Scripts\python.exe -m pytest -q
```

47 tests:
- `tests/test_api.py` covers the API with the deterministic lexicon, including email verification (codes are captured, not sent).
- `tests/test_models.py` loads each installed model and checks label mapping and sanity; it is skipped when weights are missing.

Model accuracy comes only from a measured run. The dev and held-out sets are in `data/eval/`:

```powershell
.\.venv\Scripts\python.exe scripts\evaluate.py --csv data\eval\text_test_v1.csv --save --dataset "MORVIX held-out text test set v1 (160 rows)"
```

## Viewing the database

All records live in the SQLite file `data/morvix.db`; measured model results live in `data/evaluation.json`. `scripts/show_db.py` shows them **read-only** (safe while MORVIX runs) and never prints secrets: password, token and code hashes are hidden and email addresses are masked.

```powershell
.\.venv\Scripts\python.exe scripts\show_db.py                  # tables, row counts, columns
.\.venv\Scripts\python.exe scripts\show_db.py events           # latest detections (metadata only: AC-15)
.\.venv\Scripts\python.exe scripts\show_db.py events --limit 40
.\.venv\Scripts\python.exe scripts\show_db.py alerts           # also: settings, extensions, rules, users, model_metadata
.\.venv\Scripts\python.exe scripts\show_db.py evaluation       # the measured results the dashboard shows
```

For a graphical view, open `data/morvix.db` with *DB Browser for SQLite* (**Open Database Read Only**) or the VS Code *SQLite Viewer* extension. Do not edit `evaluation.json` by hand: the dashboard treats it as measured results (FR-DASH-08).

## Configuration (`.env`)

| Variable | Default | Meaning |
|---|---|---|
| `MORVIX_TEXT_MODELS` | `MoritzLaurer/deberta-v3-base-zeroshot-v2.0,KoalaAI/Text-Moderation,sentinet/suicidality,unitary/toxic-bert` | Text engines to use, if installed |
| `MORVIX_VIOLENCE_MODEL` / `MORVIX_IMAGE_MODEL` | ViT violence / CLIP ViT-B/32 | Image engines |
| `MORVIX_TEXT_ML`, `MORVIX_IMAGE_ML` | `1` | Set to `0` to use the lexicon only, or no image models |
| `MORVIX_LOCAL_ONLY` | `1` | Offline model loading, with no hub or network calls |
| `MORVIX_WARMUP` | `1` | Load the models in the background at start-up |
| `MORVIX_GOOGLE_CLIENT_ID` | empty | Enables Google sign-in (identity only) |
| `MORVIX_EMAIL_MODE` | `smtp` | `smtp` sends verification codes by email; `console` prints them (demo only) |
| `MORVIX_SMTP_HOST` / `_PORT` / `_USER` / `_PASSWORD` / `_FROM` | `smtp.gmail.com` / `587` / empty | Mail account that sends the codes (Gmail needs an App Password) |
| `MORVIX_MAX_UPLOAD_MB` | `12` | Upload limit |

Thresholds, modalities, category actions, sync interval and retention are per-parent settings in the dashboard.

## Privacy, security, limitations

- New accounts must confirm their email with a single-use 6-digit code; only a hash of the code is stored. The mail server is used for nothing else.
- Passwords are hashed with PBKDF2-SHA256 (310k iterations). Sessions and extension credentials are random bearer tokens, stored hashed. Pairing attempts are throttled.
- Events store URL/domain, modality, function, feature, category, confidence, action, model, latency and review state. They never store text, images, frames or transcripts. URLs can still be sensitive.
- Repeat detections on the same page within 10 minutes reuse one event and one alert. Retention runs at start-up, hourly, and whenever the setting changes.
- The block overlay covers the page; it does not remove content from the site. Cross-origin frames, closed shadow DOM, DRM or cross-origin video, and protected images can't be read. A failed analysis is never treated as "safe".
- Local HTTP is for a single machine. For a LAN deployment, put the server behind TLS and restrict CORS.

## Reset

Stop the server, then delete `data\morvix.db` (all accounts and history are removed). If port 8000 is busy, check `http://127.0.0.1:8000/api/v1/health`; MORVIX may already be running.
