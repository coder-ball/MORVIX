# Acceptance checklist (SRS section 12, AC-01 … AC-15)

Automated: `.\.venv\Scripts\python.exe -m pytest -q`. `tests/test_api.py` covers the API with the deterministic lexicon. `tests/test_models.py` smoke-tests each installed ML engine and is skipped when weights are missing.

| AC | How to verify | Automated? |
|---|---|---|
| AC-01 | Sign in with email and password, or with Google (needs `MORVIX_GOOGLE_CLIENT_ID`). The dashboard opens. | `test_health_auth_and_password_hash`, `test_google_signin_option` |
| AC-02 | A wrong or missing password is rejected (401) and the dashboard does not open. | `test_health_auth_and_password_hash` |
| AC-03 | Pair the extension. Dashboard → Extension status shows *Connected*. Revoke it, and the extension is rejected. | `test_functions_*`, `test_extension_status_revoke_*` |
| AC-04 | Test classifiers → Text: `I am going to kill you` → *Threat Language*, confidence ≥ 0.8, configured action. | `test_ac04_*`, `test_models.py` |
| AC-05 | Test classifiers → Image: upload a test image. The result shows category, confidence and per-engine scores. | `test_models.py` (wiring only) |
| AC-06 | Test classifiers → Video: upload a short MP4. The result shows the sampled frame count and the frames above threshold. | manual |
| AC-07 | Set a category to *Block*, then open a test page with matching text. The block screen appears and History has the row. | `test_thresholds_*` (API side) |
| AC-08 | Leave a category on *Warn*. With Settings → "When MORVIX flags something" = *Hide* (default) the page shows the "hidden" screen; with *Show it with a warning* it shows a banner. Either way the event is stored as *flagged*, not blocked. | `test_ac04_*`, `test_flag_behaviour_*` |
| AC-09 | Change the toggle, thresholds or modalities. The extension applies the change within the sync interval. | `test_ac09_*` |
| AC-10 | The dashboard channel chips (All/Text/Image/Video/Audio) show only matching rows. | `test_functions_*` |
| AC-11 | A second account cannot read the first account's events or alerts. | `test_ac11_*` |
| AC-12 | Disconnect the internet (keep localhost). Classification still works. | by design: `MORVIX_LOCAL_ONLY=1` |
| AC-13 | Stop the backend after a sync. The popup says *Backend offline — last policy in effect*, and cached website blocks still apply. | manual |
| AC-14 | The History filters (date, modality, category, action, domain, search) return only matching rows. | `test_event_filters_*`, `test_extension_status_*` |
| AC-15 | Inspect the `events` table: it holds only metadata columns, never page text or media. | `test_ac04_*` (`'kill you' not in event`) |

Optional audio (FR-ML-04): enable Audio in Settings, then Test classifiers → Audio → upload a short spoken clip. The result shows the category, confidence and the transcript length (the transcript itself is never stored).

Full requirement-by-requirement mapping: docs/SRS_TRACEABILITY.md.

Extra checks added in v3:
- A page that re-renders does not create duplicate events or alerts (`test_repeat_detection_*`).
- Curly apostrophes (`I’m going to…`) are classified like straight ones (`test_curly_apostrophes_*`).
- The dashboard is served by the backend at `http://127.0.0.1:8000/` (`test_dashboard_served_*`).
- A missing model returns HTTP 503, never a fake "safe" result (`test_image_endpoint_*`).

Model accuracy is never asserted by the project. Measure it with `scripts/evaluate.py --save` on a labeled, held-out dataset; only then does the dashboard show accuracy and macro-F1 (FR-DASH-08).

## AC-19 Email verification of new parent accounts (v3.2)

1. Create an account on the dashboard. The verification screen appears and a 6-digit code arrives by email (check spam).
2. Enter a wrong code: "Wrong code. 4 attempts left." Sign in with the password: refused with "Verify your email first".
3. Enter the correct code: the dashboard opens. Reusing the code fails.
Automated: `test_signup_requires_emailed_code_before_login`, `test_wrong_codes_are_limited_and_resend_replaces_code`, `test_expired_code_unknown_email_and_restart_unverified_signup`, `test_signup_refused_when_email_not_configured`.
