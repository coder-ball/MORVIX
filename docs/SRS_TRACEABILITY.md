# SRS traceability matrix (MORVIX v3.7.3, 2026-11-15)

Every requirement in the SRS ("SOFTWARE REQUIREMENTS SPECIFICATION — MORVIX", Draft 1.0) mapped to where it is implemented and how it is verified. Tests: `tests/test_api.py` (API, deterministic lexicon) and `tests/test_models.py` (local ML engines); run `.\.venv\Scripts\python.exe -m pytest -q` (45 tests). "Browser" = checked by running the extension script on the demo pages (`web_dashboard/demo/`); see docs/EVALUATION.md for measured numbers.

File shorthand: **API** `backend/main.py`, **ML** `backend/classifiers.py`, **DASH** `web_dashboard/app.js`, **EXT** `browser_extension/content.js`, **SW** `browser_extension/background.js`, **POP** `browser_extension/popup.js`.

## 4.1 Authentication and access control

| ID | Implemented in | Verified by |
|---|---|---|
| FR-AUTH-01 | API `/auth/login`, `/auth/google`, `auth()` dependency on every parent route | `test_health_auth_and_password_hash`, `test_google_signin_option` |
| FR-AUTH-02 | API `issue_session` (random token, only its SHA-256 stored, 14-day expiry) | `test_health_auth_and_password_hash`, `test_password_change_keeps_current_session` |
| FR-AUTH-03 | API `auth()` returns 401 without a valid session; DASH returns to sign-in | `test_ac11_cross_account_denied_and_ac02`, `test_functions_analytics_models_and_no_fake_metrics` |
| FR-AUTH-04 | API: every query filters by the session's `user_id`; extensions only see their owner's policy | `test_ac11_cross_account_denied_and_ac02`, `test_rules_isolation_and_policy_sync` |
| FR-AUTH-05 | API `pw_hash`: PBKDF2-SHA256, random 16-byte salt, 310,000 iterations; plaintext never stored | `test_health_auth_and_password_hash` |

Email verification of new accounts and Google sign-in are additions requested by the course professor (SRS §9 treats email services as out of scope "unless later approved").

## 4.2 Parent dashboard

| ID | Implemented in | Verified by |
|---|---|---|
| FR-DASH-01 | DASH `render()` → `dashboard()` after sign-in | Browser |
| FR-DASH-02 | DASH protection card (Active/Inactive + registered browser); API `/summary` `protection_ready` | `test_event_filters_alert_review_and_summary`, Browser |
| FR-DASH-03 | DASH cards: Blocked, Flagged, Allowed, Pending review, Unread alerts | `test_event_filters_alert_review_and_summary` |
| FR-DASH-04 | DASH channel chips All/Text/Image/Video (+Audio only when enabled) above the summary cards; the selected channel narrows the summary statistics, recent detections and unread alerts; API `/summary?modality=` | `test_summary_channel_filter_applies_to_counts`, `test_functions_analytics_models_and_no_fake_metrics`, Browser |
| FR-DASH-05 | DASH recent table: time, source/domain, modality, category, confidence ("n/a" when absent), action | Browser |
| FR-DASH-06 | DASH alerts card + 15 s check for new alerts (notice + badge) | Browser |
| FR-DASH-07 | DASH Analytics: by category, modality, action, per day; preset or custom date range; API `/analytics` | `test_functions_analytics_models_and_no_fake_metrics`, Browser |
| FR-DASH-08 | API `evaluation_results()` shows only entries with dataset, sample count, date and valid rates; DASH labels them offline test-set results | `test_functions_analytics_models_and_no_fake_metrics` (no fake metrics) |

## 4.3 Browser extension

| ID | Implemented in | Verified by |
|---|---|---|
| FR-EXT-01 | `browser_extension/manifest.json` (Manifest V3) | Loaded in Chrome |
| FR-EXT-02 | POP: logo, connection status, protection status | Chrome |
| FR-EXT-03 | EXT `pageText()` (visible text, first 3,500 chars) and search query from the address | Browser |
| FR-EXT-04 | EXT images ≥ 100×80 px incl. `data:` thumbnails (40 per page view); first frame + one frame every 5 s of accessible videos | Browser |
| FR-EXT-05 | SW posts text, images and video frames to the local API (`/extensions/analyze/*`) | Browser, `test_ac04_text_classification_records_event_and_function` |
| FR-EXT-06 | EXT `applyResult`: allow / flag / block | Browser |
| FR-EXT-07 | EXT block screen, "hidden" screen, warning banner, hidden-media note | Browser |
| FR-EXT-08 | API `save_event` from every extension analysis (flag/block) | `test_ac04_text_classification_records_event_and_function`, `test_fast_pass_records_blocks_but_leaves_flags_to_full_pass` |
| FR-EXT-09 | SW keeps the last policy in `chrome.storage`; POP shows "Backend offline — last policy in effect"; cached website rules still apply | Manual (AC-13) |
| FR-EXT-10 | Permissions: `storage`, `alarms`, and host access. Host access to all http/https pages is needed because the extension must read any page the child opens and fetch its images for checking; no other permissions (no tabs, history, cookies, downloads) | Manifest review |

## 4.4 Multimodal classification

| ID | Implemented in | Verified by |
|---|---|---|
| FR-ML-01 | ML `classify_text`: normalisation, 300-char windows, lexicon + 4 local transformer engines, fusion | `test_ensemble_scores_harmful_text_higher_than_benign`, held-out evaluation (docs/EVALUATION.md) |
| FR-ML-02 | ML `classify_image`: local CLIP + violence ViT | `test_image_engines_return_normalised_result` |
| FR-ML-03 | Video frame sampling (extension: first frame + every 5 s; upload: every 3 s, ≤ 60 frames) | Manual (AC-06) |
| FR-ML-04 | Optional audio: local faster-whisper transcript → text pipeline; off by default | Manual (Test classifiers → Audio) |
| FR-ML-05 | Every result has modality, category, confidence, action | `test_ac04_text_classification_records_event_and_function` |
| FR-ML-06 | All engines run from `./models`; `MORVIX_LOCAL_ONLY=1` forces offline | `test_dashboard_served_by_backend_and_engine_inventory` |
| FR-ML-07 | Graphic Violence, Threat Language, Self-harm Indicators | `test_self_harm_and_violence_and_benign_and_negation` |
| FR-ML-08 | Two thresholds via the 1–10 sensitivity scale; mid-confidence is flagged, never auto-blocked | `test_thresholds_block_and_low_confidence_warn`, `test_sensitivity_scale_sets_both_thresholds` |

## 4.5 Detection actions and policy

| ID | Implemented in | Verified by |
|---|---|---|
| FR-ACT-01 | API `decide()` applies website rules, category actions and thresholds to every result | `test_thresholds_block_and_low_confidence_warn`, `test_rules_isolation_and_policy_sync` |
| FR-ACT-02 | `events.action` = allow / warn / block | `test_event_filters_alert_review_and_summary` |
| FR-ACT-03 | EXT block screen: "blocked by the protection settings your parent or guardian chose"; a page script cannot remove it | Browser (incl. removal attempt) |
| FR-ACT-04 | Parent setting "When MORVIX flags something": hide (default) or show with a warning; flagged events always recorded for review | `test_flag_behaviour_is_a_parent_setting_synced_to_the_extension`, Browser |
| FR-ACT-05 | Policy changes only through the parent's settings/rules endpoints | Code review |

## 4.6 Detection history and alerts

| ID | Implemented in | Verified by |
|---|---|---|
| FR-HIST-01 | `events` table: id, extension, time, modality, category, confidence, URL/domain, action | `test_ac04_text_classification_records_event_and_function` |
| FR-HIST-02 | DASH History filters; API `/events` (date, modality, category, action, domain, search) | `test_extension_status_revoke_and_history_filters`, `test_event_filters_alert_review_and_summary` |
| FR-HIST-03 | Alerts with severity (block = high, flag = medium) on the dashboard | `test_event_filters_alert_review_and_summary` |
| FR-HIST-04 | Mark alert read; mark event reviewed | `test_event_filters_alert_review_and_summary` |
| FR-HIST-05 | No page text or media stored; text-score cache keyed by SHA-256 only | `test_ac04_text_classification_records_event_and_function` |

## 4.7 Protection settings

| ID | Implemented in | Verified by |
|---|---|---|
| FR-SET-01 | Global protection switch; per-browser turn off/on | `test_ac09_policy_toggle_modality_and_sync_payload`, `test_extension_turn_off_and_on_keeps_pairing` |
| FR-SET-02 | Modality switches | `test_ac09_policy_toggle_modality_and_sync_payload` |
| FR-SET-03 | Category action block / warn / allow | `test_thresholds_block_and_low_confidence_warn` |
| FR-SET-04 | Domain & category rules (allow / warn / block) | `test_rules_isolation_and_policy_sync`, `test_invalid_rules_and_settings` |
| FR-SET-05 | Sync interval 30–3600 s (Chrome's alarm minimum is 30 s) | `test_ac09_policy_toggle_modality_and_sync_payload` |

## 4.8 Extension registration and connection

| ID | Implemented in | Verified by |
|---|---|---|
| FR-CON-01 | One-use 10-minute pairing code → extension token (hash stored) | `test_rules_isolation_and_policy_sync` |
| FR-CON-02 | Dashboard and Extension status show connected / last seen | `test_extension_status_revoke_and_history_filters` |
| FR-CON-03 | Used, expired or wrong codes rejected | `test_rules_isolation_and_policy_sync` |
| FR-CON-04 | Remove (revoke) a browser | `test_extension_status_revoke_and_history_filters` |

## 5 Data requirements

| ID | Implemented in | Verified by |
|---|---|---|
| Entities | users (id, name, email, hash, role, status), extensions, events, settings (incl. update time), alerts (severity, read), model_metadata (version, evaluation ref/date, categories) | `test_dashboard_served_by_backend_and_engine_inventory` |
| DR-01 | Foreign keys between users, extensions, events, alerts, rules, settings | Schema in API `init_db` |
| DR-02 | Events kept for the retention period; analytics read them | `test_functions_analytics_models_and_no_fake_metrics` |
| DR-03 | Pydantic validation, size limits (text 12,000, query 300, uploads), 413/415/422 errors | `test_invalid_rules_and_settings`, `test_image_endpoint_reports_missing_models_as_503_not_a_result` |
| DR-04 | Account-scoped access to all monitoring data | `test_ac11_cross_account_denied_and_ac02` |
| DR-05 | Retention setting (days); raw content never stored | `test_ac04_text_classification_records_event_and_function` |
| DR-06 | All timestamps ISO-8601 UTC (`now()`) | Code review |

## 6 External interfaces

| ID | Implemented in |
|---|---|
| UI-01 | Dashboard sign-in and console (DASH) |
| UI-02 | Sidebar: Dashboard, Detection history, Alerts, Analytics, Extension status, Test classifiers, Domain & category rules, Settings |
| UI-03 | Protection card, summary cards, channel filters, detection tables |
| UI-04 | Success/error notice after every save, rule change, review and pairing |
| UI-05 | Popup: status, current-page protection, Sync / Pair actions (POP) |
| UI-06 | Block screen, hidden screen, warning banner (EXT) |
| API-01 | docs/API.md: auth, policy, pairing/status, detection submission, history, alerts |
| API-02 | ML module functions `classify_text/image`, `transcribe_audio` behind the API |
| DB-01 | SQLite `data/morvix.db` (the "agreed lightweight alternative" of SRS §9) |
| NI-01 / NI-02 | Extension and dashboard talk to `127.0.0.1:8000`; no external moderation API |

## 7 Non-functional requirements

| ID | How it is met | Evidence |
|---|---|---|
| NFR-PERF-01 | Local dashboard over local API | Browser |
| NFR-PERF-02 | Child-facing check (fast pass + query) ≈ 0.5 s idle, 2.2–2.6 s under heavy load; full page check runs after | docs/EVALUATION.md |
| NFR-PERF-03 | Image/video inference times measured and shown (Analytics → inference time) | docs/EVALUATION.md |
| NFR-REL-01 | Events stored server-side before the response; dashboard refresh cannot lose them | `test_event_filters_alert_review_and_summary` |
| NFR-REL-02 | Last valid policy kept during an outage | Manual (AC-13) |
| NFR-SEC-01..04 | Auth on all protected routes, account scoping, hashed passwords and tokens | `test_ac11_*`, `test_health_auth_and_password_hash` |
| NFR-SEC-05 | Least permissions (see FR-EXT-10) | Manifest review |
| NFR-PRIV-01/02 | Only text extract, images and sampled frames are sent, to the local server only; nothing raw is kept | `test_ac04_*` |
| NFR-USA-01/02 | Status and recent detections on one page; plain-language labels | Browser |
| NFR-COM-01/02 | Chromium extension; modern desktop browser | Chrome |
| NFR-MNT-01/02 | Separate `backend/`, `web_dashboard/`, `browser_extension/`, ML module behind the API | Repository layout |

## 11 Business rules

| ID | How it is met |
|---|---|
| BR-01 | FR-AUTH-01..04 |
| BR-02 | Extension policy endpoint returns its owner's policy only |
| BR-03 | Text, image, video required; audio optional and off by default |
| BR-04 | Category and confidence are computed before `decide()` picks an action |
| BR-05 | Block threshold vs flag threshold (sensitivity scale) |
| BR-06 | Settings only via the parent's authenticated API; the extension's protection screens cannot be removed by the web page |
| BR-07 | Local models only (FR-ML-06) |
| BR-08 | Metadata only (FR-HIST-05) |
| BR-09 | `save_event` runs after classification and the action decision |
| BR-10 | Categories, thresholds (sensitivity levels), browser (Chromium) and retention are documented for approval |

## 12 Acceptance criteria

See docs/ACCEPTANCE.md for AC-01 … AC-15.

## Known limits (documented, not hidden)

- Long pages: 31 of 48 buried harmful sentences found (single sentences: 92 % at sensitivity 6). docs/EVALUATION.md.
- The fast pass misses graphic descriptions; the full check catches them a few seconds after the page appears.
- Not screened: CSS background images, iframes, more than 40 images per page view, canvas-drawn text, page audio.
- Image and video accuracy are not yet evaluated on a labeled dataset (FR-DASH-08 therefore shows text results only).
