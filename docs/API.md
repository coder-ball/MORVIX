# MORVIX API contract

Interactive schema: `GET /docs`; OpenAPI: `GET /openapi.json`. All paths are under `/api/v1`. JSON errors use FastAPI's `detail` field. Upload routes accept multipart data and enforce `MORVIX_MAX_UPLOAD_MB` (12 MiB default).

## Identity

- Parent route auth: `Authorization: Bearer <access_token>` from login; expires after 14 days.
- Extension route auth: `Authorization: Bearer <extension_token>` received when redeeming a one-use pair code. Only token hash is stored.
- Public: health, classifier status, register, login, pair-code redemption. Remaining account and extension routes require their respective credential.

## Endpoint and payload summary

| Method and path | Auth | Request / response |
|---|---|---|
| `GET /health` | None | API/database status plus separate classifier readiness |
| `GET /classifiers/status` | None | `text`, `image`, `video` readiness and model IDs |
| `POST /auth/register` | None | `{email,password}`; 201 account |
| `POST /auth/login` | None | `{email,password}`; bearer token and account |
| `POST /auth/logout`, `GET /auth/me` | Parent | Revoke current session; retrieve account |
| `PUT /auth/password` | Parent | `{current_password,new_password}`; verifies and updates password, revokes other sessions |
| `GET /rules`, `GET /rules/{id}` | Parent | Rule list or account-owned rule |
| `POST /rules` | Parent | `{kind: website\|category,target,action: allow\|warn\|block,enabled?}` |
| `PATCH /rules/{id}`, `DELETE /rules/{id}` | Parent | Partial updates; delete returns 204 |
| `GET /settings`, `PUT /settings` | Parent | `enabled`, `category_actions`, `modalities`, `sensitivity` (1–10, sets both thresholds; `null` in GET when the thresholds are custom), `warn_threshold` / `block_threshold` (raw), `sync_interval_seconds` (30–3600), `retention_days` |
| `POST /extensions/pair-code` | Parent | Generates ten-minute one-use code |
| `POST /extensions/pair` | None | `{code,name?}`; returns extension token once |
| `GET /extensions`, `DELETE /extensions/{id}` | Parent | List/remove (revoke) a registered extension. A removed extension clears its token and shows the pairing screen again |
| `PATCH /extensions/{id}` `{"paused": true\|false}` | Parent | Turn one browser off or back on without unpairing it. While off, its policy has `enabled: false, paused: true` and its analysis/event calls are skipped |
| `GET /extensions/policy` | Extension | Latest policy and policy version hash |
| `POST /classify/text` | Parent | `{text,url?}`; local result, action and optional event ID |
| `POST /extensions/analyze/text` | Extension | Same request for paired account, plus optional `fast: true` for the pre-access first pass (opening ≈ 900 chars, no NLI engine; result has `stage: "fast"`; only a block is recorded) and optional `query` (search-box text, ≤ 300 chars, checked on its own with every engine; when it scores higher than the page the result has `feature: "Search query"` and `decided_by_query: true`) |
| `POST /extensions/analyze/image` | Extension | Multipart `file`, optional page URL query; local model result |
| `POST /classify/image`, `/classify/video` | Parent | Multipart `file`, optional URL; video interval 1–15 sec |
| `POST /extensions/events` | Extension | Minimal result event; `{modality,category,confidence,action,url,model}` |
| `GET /events` | Parent | Filters: `q,start,end,modality,category,action,reviewed,page,page_size` |
| `GET /events/{id}`, `PATCH /events/{id}/review` | Parent | Account-owned event; set reviewed |
| `GET /alerts` | Parent | `read` and `page`; warn/block alerts only |
| `PATCH /alerts/{id}/read` | Parent | Persist read state |
| `GET /summary` | Parent | `days` (1–3650); database counts, recent rows, extension/model state |

Categories: `Graphic Violence`, `Threat Language`, `Self-harm Indicators`. Rule precedence and threshold handling are described in [architecture](ARCHITECTURE.md) and [models](MODELS.md).

## v3 changes

- `GET /` and the dashboard files are served by the API process (http://127.0.0.1:8000/).
- `GET /classifiers/status` adds `engines` per modality and `failed_engines` (engine → load/run error).
- Classification results add `scores` (per category), `engines` (per engine and category), `decided_by`, and `context_dampened` (text) or `flagged_frames` (video).
- `GET /models` lists every engine with `available`, `license` and any measured `evaluation`.
- Extension detections repeated on the same URL, modality, category and action within 10 minutes return the existing `event_id` instead of creating a new event and alert.

## Email verification (v3.2, FR-AUTH-09/10)

| Method | Path | Body | Result |
|---|---|---|---|
| POST | `/api/v1/auth/register` | `{email, password, name?}` | 201 `{verification_required: true, email, expires_in: 600, resend_after: 60}`; a 6-digit code is emailed. 409 if a verified account exists; 503 if email sending is not configured. |
| POST | `/api/v1/auth/verify-email` | `{email, code}` | 200 session (same shape as login). 400 wrong/expired code, 429 after 5 wrong entries. |
| POST | `/api/v1/auth/resend-code` | `{email}` | 200 `{sent: true, resend_after}`; 429 within 60 s of the last code. Same reply for unknown emails. |
| POST | `/api/v1/auth/login` | `{email, password}` | 403 "Verify your email first" for an unverified account (a new code is sent if the cooldown allows). |

`GET /api/v1/auth/config` also returns `email_verification` and `email_configured`.
