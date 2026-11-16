"""MORVIX API (FastAPI + SQLite). Page text and media are never persisted (FR-HIST-05, BR-08).

Module map (SRS section 2): authentication, dashboard data, extension registration,
policy, text/image/video/audio analysis, detection history, alerts, analytics.
Classification is delegated to backend/classifiers.py (API-02, NFR-MNT-02).
"""
import contextlib, hashlib, hmac, json, os, secrets, sqlite3, tempfile, time
from contextlib import asynccontextmanager
from datetime import datetime, timezone, timedelta
from pathlib import Path
from urllib.parse import urlparse, quote
from fastapi import FastAPI, Depends, HTTPException, Header, UploadFile, File, Query, Request
from starlette.concurrency import run_in_threadpool
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field, field_validator

from backend import classifiers as ml
from backend import mailer

ROOT = Path(__file__).resolve().parents[1]
DB_PATH = Path(os.getenv("MORVIX_DATABASE", str(ROOT / "data" / "morvix.db")))
DB_PATH.parent.mkdir(parents=True, exist_ok=True)
EVAL_PATH = Path(os.getenv("MORVIX_EVALUATION_FILE", str(ROOT / "data" / "evaluation.json")))
MAX_UPLOAD = int(os.getenv("MORVIX_MAX_UPLOAD_MB", "12")) * 1024 * 1024
CATEGORIES = ml.CATEGORIES
ACTIONS = {"allow", "warn", "block"}
MODALITIES = ("text", "image", "video", "audio")
DEFAULT_MODALITIES = {"text": True, "image": True, "video": True, "audio": False}  # audio optional (BR-03)
FUNCTION_BY_MODALITY = {"text": "Text Analysis", "image": "Image Analysis", "video": "Video Analysis", "audio": "Audio Analysis"}
DEFAULT_FEATURE = {"text": "Visible page text", "image": "Page image element", "video": "Sampled video frames", "audio": "Audio transcript"}
IMAGE_TYPES = ("image/jpeg", "image/png", "image/webp")
GOOGLE_CLIENT_ID = lambda: os.getenv("MORVIX_GOOGLE_CLIENT_ID", "").strip()  # noqa: E731


def now():
    """DR-06: every timestamp is stored as ISO-8601 UTC."""
    return datetime.now(timezone.utc).isoformat()


def db():
    c = sqlite3.connect(DB_PATH, timeout=15)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA foreign_keys=ON")
    return c


def add_col(c, table, ddl):
    if ddl.split()[0] not in {r[1] for r in c.execute(f"PRAGMA table_info({table})")}:
        c.execute(f"ALTER TABLE {table} ADD COLUMN {ddl}")


def init_db():
    with db() as c:
        c.executescript("""
        CREATE TABLE IF NOT EXISTS users(id TEXT PRIMARY KEY,email TEXT UNIQUE NOT NULL,password_hash TEXT NOT NULL,created_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS schema_info(version INTEGER NOT NULL);
        INSERT INTO schema_info SELECT 1 WHERE NOT EXISTS(SELECT 1 FROM schema_info);
        CREATE TABLE IF NOT EXISTS sessions(token_hash TEXT PRIMARY KEY,user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,expires_at REAL NOT NULL);
        CREATE TABLE IF NOT EXISTS settings(user_id TEXT PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,enabled INTEGER NOT NULL DEFAULT 1,category_json TEXT NOT NULL DEFAULT '{}',retention_days INTEGER NOT NULL DEFAULT 90);
        CREATE TABLE IF NOT EXISTS extensions(id TEXT PRIMARY KEY,token_hash TEXT NOT NULL UNIQUE,user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,name TEXT NOT NULL,created_at TEXT NOT NULL,last_seen TEXT,revoked INTEGER NOT NULL DEFAULT 0);
        CREATE TABLE IF NOT EXISTS pair_codes(code_hash TEXT PRIMARY KEY,user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,expires_at REAL NOT NULL);
        CREATE TABLE IF NOT EXISTS rules(id TEXT PRIMARY KEY,user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,kind TEXT NOT NULL,target TEXT NOT NULL,action TEXT NOT NULL,enabled INTEGER NOT NULL DEFAULT 1,created_at TEXT NOT NULL,updated_at TEXT NOT NULL,UNIQUE(user_id,kind,target));
        CREATE TABLE IF NOT EXISTS events(id TEXT PRIMARY KEY,user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,extension_id TEXT REFERENCES extensions(id) ON DELETE SET NULL,created_at TEXT NOT NULL,source_url TEXT,domain TEXT,modality TEXT NOT NULL,category TEXT NOT NULL,confidence REAL,action TEXT NOT NULL,reviewed INTEGER NOT NULL DEFAULT 0,model TEXT);
        CREATE INDEX IF NOT EXISTS idx_events_user_time ON events(user_id,created_at DESC);
        CREATE TABLE IF NOT EXISTS alerts(id TEXT PRIMARY KEY,event_id TEXT UNIQUE NOT NULL REFERENCES events(id) ON DELETE CASCADE,user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,read INTEGER NOT NULL DEFAULT 0,created_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS model_metadata(name TEXT PRIMARY KEY,version TEXT NOT NULL,modality TEXT NOT NULL,categories_json TEXT NOT NULL,description TEXT);
        """)
        # additive migrations so an existing morvix.db keeps working
        for ddl in ("name TEXT", "provider TEXT NOT NULL DEFAULT 'password'", "google_sub TEXT", "status TEXT NOT NULL DEFAULT 'active'", "role TEXT NOT NULL DEFAULT 'parent'"):
            add_col(c, "users", ddl)
        # FR-AUTH-09: accounts created before email verification existed stay usable (default 1);
        # new password sign-ups are inserted with email_verified=0 until the emailed code is entered.
        add_col(c, "users", "email_verified INTEGER NOT NULL DEFAULT 1")
        c.execute("CREATE TABLE IF NOT EXISTS email_codes(user_id TEXT PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,code_hash TEXT NOT NULL,expires_at REAL NOT NULL,attempts INTEGER NOT NULL DEFAULT 0,sent_at REAL NOT NULL)")
        for ddl in ("modality_json TEXT NOT NULL DEFAULT '{}'", "block_threshold REAL NOT NULL DEFAULT 0.80", "warn_threshold REAL NOT NULL DEFAULT 0.50", "sync_interval_seconds INTEGER NOT NULL DEFAULT 30",
                    "updated_at TEXT"):  # SRS §5 Policy Setting: update time
            add_col(c, "settings", ddl)
        for ddl in ("license TEXT", "evaluation_ref TEXT", "evaluated_at TEXT"):  # SRS §5 Model Metadata: evaluation reference/date
            add_col(c, "model_metadata", ddl)
        for ddl in ("function TEXT", "feature TEXT", "latency_ms INTEGER"):
            add_col(c, "events", ddl)
        add_col(c, "alerts", "severity TEXT NOT NULL DEFAULT 'medium'")
        add_col(c, "extensions", "paused INTEGER NOT NULL DEFAULT 0")  # parent turned this browser off (reversible, unlike revoke)
        # FR-ACT-04: whether flagged (lower-confidence) content is hidden from the child (default) or shown with a warning
        add_col(c, "settings", "flag_hides INTEGER NOT NULL DEFAULT 1")
        c.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_users_google ON users(google_sub) WHERE google_sub IS NOT NULL")
        c.execute("CREATE INDEX IF NOT EXISTS idx_events_dedupe ON events(extension_id,source_url,modality,category,created_at)")
    sync_model_metadata()


def sync_model_metadata():
    """Rebuild model_metadata from the engine inventory plus the latest measured evaluation (if any)."""
    try:  # read directly: this runs at import time, before evaluation_results() is defined
        ev = json.loads(EVAL_PATH.read_text(encoding="utf-8"))
        ev = ev if isinstance(ev, dict) else {}
    except Exception:
        ev = {}
    with db() as c:
        c.execute("DELETE FROM model_metadata")
        for row in ml.engine_inventory():
            e = ev.get(row["name"]) or {}
            c.execute("INSERT OR REPLACE INTO model_metadata(name,version,modality,categories_json,description,license,evaluation_ref,evaluated_at) VALUES(?,?,?,?,?,?,?,?)",
                      (row["name"], row["version"], row["modality"], json.dumps(CATEGORIES), row["description"], row.get("license"),
                       e.get("dataset"), e.get("evaluated_at")))


_last_purge = [0.0]


def apply_retention(force=False):  # DR-05: delete events older than each parent's retention period
    if not force and time.time() - _last_purge[0] < 3600:
        return
    _last_purge[0] = time.time()
    with db() as c:
        for row in c.execute("SELECT user_id,retention_days FROM settings").fetchall():
            cutoff = (datetime.now(timezone.utc) - timedelta(days=row["retention_days"])).isoformat()
            c.execute("DELETE FROM events WHERE user_id=? AND created_at<?", (row["user_id"], cutoff))


@asynccontextmanager
async def lifespan(_app):
    apply_retention(force=True)
    if os.getenv("MORVIX_WARMUP", "1") == "1":
        ml.warmup()
    yield


init_db()
app = FastAPI(title="MORVIX API", version="3.0.0", lifespan=lifespan,
              description="Local content protection API. Classifier readiness is reported separately from API health.")
cors_origins = [x.strip() for x in os.getenv("MORVIX_CORS_ORIGINS", "http://127.0.0.1:5500,http://localhost:5500").split(",") if x.strip()]
app.add_middleware(CORSMiddleware, allow_origins=cors_origins, allow_origin_regex=r"(chrome|moz)-extension://[a-zA-Z0-9-]+", allow_credentials=True,
                   allow_methods=["GET", "POST", "PATCH", "PUT", "DELETE"], allow_headers=["Authorization", "Content-Type"])


# ---------------------------------------------------------------- authentication
def digest(s): return hashlib.sha256(s.encode()).hexdigest()


def pw_hash(p):  # FR-AUTH-05 / NFR-SEC-03
    salt = secrets.token_bytes(16)
    return salt.hex() + "$" + hashlib.pbkdf2_hmac("sha256", p.encode(), salt, 310000).hex()


def pw_check(p, h):
    try:
        salt, val = h.split("$")
        return hmac.compare_digest(hashlib.pbkdf2_hmac("sha256", p.encode(), bytes.fromhex(salt), 310000).hex(), val)
    except Exception:
        return False


def auth(authorization: str | None = Header(default=None)):
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(401, "Sign in required")
    with db() as c:
        r = c.execute("SELECT s.user_id FROM sessions s JOIN users u ON u.id=s.user_id WHERE s.token_hash=? AND s.expires_at>? AND u.status='active'",
                      (digest(authorization[7:]), time.time())).fetchone()
    if not r:
        raise HTTPException(401, "Session expired or invalid")
    return r["user_id"]


def ext_auth(authorization: str | None = Header(default=None)):
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(401, "Extension registration required")
    with db() as c:
        r = c.execute("SELECT id,user_id,paused FROM extensions WHERE token_hash=? AND revoked=0", (digest(authorization[7:]),)).fetchone()
        if not r:
            raise HTTPException(401, "Extension registration revoked or invalid")
        c.execute("UPDATE extensions SET last_seen=? WHERE id=?", (now(), r["id"]))
    return dict(r)


class EmailIn(BaseModel):
    email: str = Field(min_length=5, max_length=254)

    @field_validator("email")
    @classmethod
    def clean_email(cls, v):
        v = v.strip().lower()
        if "@" not in v or "." not in v.rsplit("@", 1)[-1]:
            raise ValueError("Enter a valid email")
        return v


class Credentials(EmailIn):
    password: str = Field(min_length=10, max_length=256)


class VerifyIn(EmailIn):
    code: str = Field(pattern=r"^\s*\d{6}\s*$")


class RegisterIn(Credentials):
    name: str | None = Field(default=None, max_length=80)


class GoogleIn(BaseModel):
    credential: str = Field(min_length=20, max_length=4096)


class PasswordChange(BaseModel):
    current_password: str = Field(min_length=1, max_length=256)
    new_password: str = Field(min_length=10, max_length=256)


class RuleIn(BaseModel):
    kind: str
    target: str = Field(min_length=1, max_length=253)
    action: str
    enabled: bool = True


class RulePatch(BaseModel):
    target: str | None = Field(default=None, min_length=1, max_length=253)
    action: str | None = None
    enabled: bool | None = None


# Parent-facing sensitivity 1-10 (10 = most sensitive: flags and blocks the most, at lower model
# confidence). Each level is a pair (flag/warn threshold, block threshold); level 6 is the default
# 0.50 / 0.80 at which the held-out evaluation was measured. Measured effect: docs/EVALUATION.md.
SENSITIVITY_LEVELS = {1: (0.90, 0.98), 2: (0.85, 0.95), 3: (0.75, 0.92), 4: (0.65, 0.88), 5: (0.55, 0.84),
                      6: (0.50, 0.80), 7: (0.45, 0.70), 8: (0.40, 0.60), 9: (0.35, 0.50), 10: (0.30, 0.40)}


def sensitivity_of(warn, block):
    """The level whose thresholds match exactly, or None for custom thresholds set through the API."""
    return next((lvl for lvl, (w, b) in SENSITIVITY_LEVELS.items() if abs(w - warn) < 1e-6 and abs(b - block) < 1e-6), None)


class SettingsIn(BaseModel):
    enabled: bool | None = None
    category_actions: dict[str, str] | None = None
    modalities: dict[str, bool] | None = None
    sensitivity: int | None = Field(default=None, ge=1, le=10)  # sets both thresholds from SENSITIVITY_LEVELS
    flag_hides: bool | None = None  # FR-ACT-04: True = flagged content stays hidden, False = shown with a warning
    block_threshold: float | None = Field(default=None, ge=0.05, le=1)
    warn_threshold: float | None = Field(default=None, ge=0.05, le=1)
    sync_interval_seconds: int | None = Field(default=None, ge=30, le=3600)  # 30 s = Chrome's minimum alarm period
    retention_days: int | None = Field(default=None, ge=1, le=3650)


class TextIn(BaseModel):
    text: str = Field(min_length=1, max_length=12000)
    url: str | None = Field(default=None, max_length=2000)
    feature: str | None = Field(default=None, max_length=80)
    fast: bool = False  # pre-access first pass: opening text only, quick engines only (see classifiers.classify_text)
    query: str | None = Field(default=None, max_length=300)  # search-box query on a search results page, checked on its own


class EventIn(BaseModel):
    modality: str
    category: str
    confidence: float | None = Field(default=None, ge=0, le=1)
    action: str
    url: str | None = Field(default=None, max_length=2000)
    model: str | None = Field(default=None, max_length=120)
    feature: str | None = Field(default=None, max_length=80)


class PairIn(BaseModel):
    code: str = Field(min_length=20, max_length=100)
    name: str = Field(default="MORVIX browser", max_length=80)


def issue_session(uid, email):
    token = secrets.token_urlsafe(40)
    with db() as c:
        c.execute("INSERT INTO sessions VALUES(?,?,?)", (digest(token), uid, time.time() + 1209600))
    return {"access_token": token, "token_type": "bearer", "expires_in": 1209600, "user": {"id": uid, "email": email}}


@app.get("/api/v1/health")
def health():
    return {"status": "ok", "database": "available", "classifiers": ml.status()}


@app.get("/api/v1/classifiers/status")
def classifier_status():
    return ml.status()


@app.get("/api/v1/auth/config")
def auth_config():
    """Sign-in options offered by the dashboard: traditional email+password and Google."""
    return {"password": True, "google_client_id": GOOGLE_CLIENT_ID() or None,
            "email_verification": True, "email_configured": mailer.configured()}


# ------------------------------------------------- email verification (FR-AUTH-09/10)
CODE_TTL = 600          # a code is valid for 10 minutes
CODE_ATTEMPTS = 5       # wrong entries allowed per code
RESEND_COOLDOWN = 60    # seconds between codes for one account


def code_digest(uid, code):
    return digest(f"{uid}:{code.strip()}")


def send_verification_code(uid, email, force=False):
    """Create a new 6-digit code, store only its hash, and email it. Returns seconds until a resend is allowed."""
    with db() as c:
        r = c.execute("SELECT sent_at FROM email_codes WHERE user_id=?", (uid,)).fetchone()
    wait = int(RESEND_COOLDOWN - (time.time() - r["sent_at"])) if r else 0
    if wait > 0 and not force:
        return wait
    code = f"{secrets.randbelow(10 ** 6):06d}"
    text = (f"Your MORVIX verification code is {code}\n\n"
            f"Enter it on the MORVIX dashboard to confirm this email address. It expires in {CODE_TTL // 60} minutes.\n\n"
            "If you did not create a MORVIX parent account, you can ignore this email; the account will not be activated.")
    html = (f"<p>Your MORVIX verification code is</p><p style=\"font-size:28px;font-weight:700;letter-spacing:6px\">{code}</p>"
            f"<p>Enter it on the MORVIX dashboard to confirm this email address. It expires in {CODE_TTL // 60} minutes.</p>"
            "<p style=\"color:#666\">If you did not create a MORVIX parent account, you can ignore this email; the account will not be activated.</p>")
    try:
        mailer.send_mail(email, f"MORVIX verification code: {code}", text, html)
    except mailer.MailError as e:
        raise HTTPException(503, str(e))
    with db() as c:
        c.execute("INSERT OR REPLACE INTO email_codes(user_id,code_hash,expires_at,attempts,sent_at) VALUES(?,?,?,0,?)",
                  (uid, code_digest(uid, code), time.time() + CODE_TTL, time.time()))
    return RESEND_COOLDOWN


def verification_needed(email, retry_after):
    return {"verification_required": True, "email": email, "expires_in": CODE_TTL, "resend_after": retry_after}


@app.post("/api/v1/auth/register", status_code=201)
def register(x: RegisterIn):
    """FR-AUTH-09: a password account stays inactive until the 6-digit code emailed to it is entered."""
    if not mailer.configured():
        raise HTTPException(503, "Email verification is not configured on this server. Set the MORVIX_SMTP_* values in .env (see README).")
    name = (x.name or "").strip() or None
    with db() as c:
        r = c.execute("SELECT id,email_verified FROM users WHERE email=?", (x.email,)).fetchone()
        if r and r["email_verified"]:
            raise HTTPException(409, "Account already exists")
        if r:  # an earlier sign-up that was never verified: whoever owns the inbox may start over
            uid = r["id"]
            c.execute("UPDATE users SET password_hash=?,name=? WHERE id=?", (pw_hash(x.password), name, uid))
        else:
            uid = secrets.token_hex(16)
            c.execute("INSERT INTO users(id,email,password_hash,created_at,name,provider,email_verified) VALUES(?,?,?,?,?,'password',0)", (uid, x.email, pw_hash(x.password), now(), name))
            c.execute("INSERT INTO settings(user_id) VALUES(?)", (uid,))
    return {"id": uid, **verification_needed(x.email, send_verification_code(uid, x.email, force=True))}


@app.post("/api/v1/auth/verify-email")
def verify_email(x: VerifyIn):
    """FR-AUTH-09: activate the account and sign in when the emailed code matches."""
    bad = HTTPException(400, "That code is not valid. Check the email or request a new code.")
    with db() as c:
        u = c.execute("SELECT id,email,email_verified FROM users WHERE email=?", (x.email,)).fetchone()
        if not u or u["email_verified"]:
            raise bad
        r = c.execute("SELECT * FROM email_codes WHERE user_id=?", (u["id"],)).fetchone()
        if not r or r["expires_at"] < time.time():
            raise HTTPException(400, "This code has expired. Request a new code.")
        if r["attempts"] >= CODE_ATTEMPTS:
            raise HTTPException(429, "Too many wrong codes. Request a new code.")
        matched = hmac.compare_digest(r["code_hash"], code_digest(u["id"], x.code))
        if matched:
            c.execute("UPDATE users SET email_verified=1 WHERE id=?", (u["id"],))
            c.execute("DELETE FROM email_codes WHERE user_id=?", (u["id"],))
        else:  # committed when the block exits normally (raising inside it would roll the count back)
            c.execute("UPDATE email_codes SET attempts=attempts+1 WHERE user_id=?", (u["id"],))
    if not matched:
        left = CODE_ATTEMPTS - r["attempts"] - 1
        raise HTTPException(400, f"Wrong code. {left} attempt{'s' if left != 1 else ''} left." if left else "Wrong code. Request a new code.")
    return issue_session(u["id"], u["email"])


@app.post("/api/v1/auth/resend-code")
def resend_code(x: EmailIn):
    """Send a fresh code (at most one per minute). The reply is the same for unknown emails."""
    with db() as c:
        u = c.execute("SELECT id,email_verified FROM users WHERE email=?", (x.email,)).fetchone()
    if not u or u["email_verified"]:
        return {"sent": True, "resend_after": RESEND_COOLDOWN}
    wait = send_verification_code(u["id"], x.email)
    if wait < RESEND_COOLDOWN:
        raise HTTPException(429, f"Please wait {wait} seconds before requesting another code.")
    return {"sent": True, "resend_after": wait}


@app.post("/api/v1/auth/login")
def login(x: Credentials):
    with db() as c:
        r = c.execute("SELECT * FROM users WHERE email=?", (x.email,)).fetchone()
    if not r or r["status"] != "active" or not pw_check(x.password, r["password_hash"]):
        raise HTTPException(401, "Email or password is incorrect")
    if not r["email_verified"]:  # FR-AUTH-10: no session until the email is verified
        try:
            send_verification_code(r["id"], r["email"])
        except HTTPException:
            pass
        raise HTTPException(403, "Verify your email first. Enter the 6-digit code we sent to " + r["email"] + ".")
    return issue_session(r["id"], r["email"])


def verify_google_credential(credential: str) -> dict:
    """Validate a Google Sign-In ID token. This is identity only; it is the single
    external call in MORVIX and is never used for content classification (NI-02)."""
    cid = GOOGLE_CLIENT_ID()
    if not cid:
        raise HTTPException(503, "Google sign-in is not configured. Set MORVIX_GOOGLE_CLIENT_ID in .env.")
    import urllib.request
    try:
        with urllib.request.urlopen("https://oauth2.googleapis.com/tokeninfo?id_token=" + quote(credential, safe=""), timeout=8) as r:
            info = json.load(r)
    except Exception:
        raise HTTPException(401, "Google credential was rejected")
    if info.get("aud") != cid or str(info.get("email_verified")).lower() != "true" or not info.get("email") or not info.get("sub"):
        raise HTTPException(401, "Google credential was rejected")
    return info


@app.post("/api/v1/auth/google")
def google_login(x: GoogleIn):
    info = verify_google_credential(x.credential)
    email = info["email"].strip().lower()
    with db() as c:
        r = c.execute("SELECT * FROM users WHERE google_sub=? OR email=?", (info["sub"], email)).fetchone()
        if r and r["status"] != "active":
            raise HTTPException(401, "Account is disabled")
        if r:
            if not r["google_sub"]:
                c.execute("UPDATE users SET google_sub=? WHERE id=?", (info["sub"], r["id"]))
            if not r["email_verified"]:
                # Google has proved who owns this inbox; the unverified password was set by an
                # unconfirmed sign-up (possibly someone else), so it is disabled.
                c.execute("UPDATE users SET email_verified=1,password_hash='!' WHERE id=?", (r["id"],))
                c.execute("DELETE FROM email_codes WHERE user_id=?", (r["id"],))
            uid, email = r["id"], r["email"]
        else:
            uid = secrets.token_hex(16)
            c.execute("INSERT INTO users(id,email,password_hash,created_at,name,provider,google_sub,email_verified) VALUES(?,?,?,?,?,'google',?,1)", (uid, email, "!", now(), (info.get("name") or "")[:80] or None, info["sub"]))
            c.execute("INSERT INTO settings(user_id) VALUES(?)", (uid,))
    return issue_session(uid, email)


@app.post("/api/v1/auth/logout")
def logout(uid: str = Depends(auth), authorization: str = Header()):
    with db() as c:
        c.execute("DELETE FROM sessions WHERE token_hash=?", (digest(authorization[7:]),))
    return {"ok": True}


@app.get("/api/v1/auth/me")
def me(uid: str = Depends(auth)):
    with db() as c:
        return dict(c.execute("SELECT id,email,name,provider,role,created_at FROM users WHERE id=?", (uid,)).fetchone())


@app.put("/api/v1/auth/password")
def change_password(x: PasswordChange, uid: str = Depends(auth), authorization: str = Header()):
    with db() as c:
        r = c.execute("SELECT password_hash FROM users WHERE id=?", (uid,)).fetchone()
    if not r or not pw_check(x.current_password, r["password_hash"]):
        raise HTTPException(400, "Current password is incorrect")
    with db() as c:
        c.execute("UPDATE users SET password_hash=? WHERE id=?", (pw_hash(x.new_password), uid))
        c.execute("DELETE FROM sessions WHERE user_id=? AND token_hash<>?", (uid, digest(authorization[7:])))
    return {"password_changed": True, "other_sessions_revoked": True}


# ------------------------------------------------------------- rules and settings
def checked_rule(x):
    if x.kind not in ("website", "category") or x.action not in ACTIONS:
        raise HTTPException(422, "Use a website/category rule and allow/warn/block action")
    if x.kind == "category" and x.target not in CATEGORIES:
        raise HTTPException(422, "Unsupported category")
    if x.kind == "website":
        x.target = x.target.lower().strip().removeprefix("https://").removeprefix("http://").split("/")[0]
        if "." not in x.target or " " in x.target:
            raise HTTPException(422, "Enter a domain such as example.com")
    return x


@app.get("/api/v1/rules")
def list_rules(uid: str = Depends(auth)):
    with db() as c:
        return [dict(r) for r in c.execute("SELECT * FROM rules WHERE user_id=? ORDER BY created_at DESC", (uid,))]


@app.get("/api/v1/rules/{rid}")
def get_rule(rid: str, uid: str = Depends(auth)):
    with db() as c:
        r = c.execute("SELECT * FROM rules WHERE id=? AND user_id=?", (rid, uid)).fetchone()
    if not r:
        raise HTTPException(404, "Rule not found")
    return dict(r)


@app.post("/api/v1/rules", status_code=201)
def create_rule(x: RuleIn, uid: str = Depends(auth)):
    x = checked_rule(x)
    rid = secrets.token_hex(16)
    try:
        with db() as c:
            c.execute("INSERT INTO rules VALUES(?,?,?,?,?,?,?,?)", (rid, uid, x.kind, x.target, x.action, int(x.enabled), now(), now()))
    except sqlite3.IntegrityError:
        raise HTTPException(409, "A rule for this target already exists")
    return get_rule(rid, uid)


@app.patch("/api/v1/rules/{rid}")
def update_rule(rid: str, x: RulePatch, uid: str = Depends(auth)):
    old = get_rule(rid, uid)
    vals = {k: v for k, v in x.model_dump(exclude_unset=True).items() if v is not None}
    if "action" in vals and vals["action"] not in ACTIONS:
        raise HTTPException(422, "Invalid action")
    if "target" in vals:
        probe = RuleIn(kind=old["kind"], target=vals["target"], action=vals.get("action", old["action"]))
        vals["target"] = checked_rule(probe).target
    if vals:
        vals["updated_at"] = now()
        with db() as c:
            c.execute("UPDATE rules SET " + ", ".join(f"{k}=?" for k in vals) + " WHERE id=? AND user_id=?", (*vals.values(), rid, uid))
    return get_rule(rid, uid)


@app.delete("/api/v1/rules/{rid}", status_code=204)
def delete_rule(rid: str, uid: str = Depends(auth)):
    with db() as c:
        r = c.execute("DELETE FROM rules WHERE id=? AND user_id=?", (rid, uid))
    if not r.rowcount:
        raise HTTPException(404, "Rule not found")


def load_settings(uid):
    with db() as c:
        r = c.execute("SELECT * FROM settings WHERE user_id=?", (uid,)).fetchone()
    mods = {**DEFAULT_MODALITIES, **json.loads(r["modality_json"] or "{}")}
    return {"enabled": bool(r["enabled"]), "category_actions": json.loads(r["category_json"]), "modalities": mods,
            "block_threshold": r["block_threshold"], "warn_threshold": r["warn_threshold"],
            "sensitivity": sensitivity_of(r["warn_threshold"], r["block_threshold"]), "flag_hides": bool(r["flag_hides"]),
            # FR-SET-05: Chrome runs extension alarms at most every 30 s, so older stored values below that are reported as 30
            "sync_interval_seconds": max(30, r["sync_interval_seconds"]), "retention_days": r["retention_days"], "updated_at": r["updated_at"]}


@app.get("/api/v1/settings")
def get_settings(uid: str = Depends(auth)):
    return load_settings(uid)


@app.put("/api/v1/settings")
def put_settings(x: SettingsIn, uid: str = Depends(auth)):
    s = load_settings(uid)
    if x.category_actions is not None:
        if set(x.category_actions) - set(CATEGORIES) or set(x.category_actions.values()) - ACTIONS:
            raise HTTPException(422, "Unsupported category or action")
        s["category_actions"] = x.category_actions
    if x.modalities is not None:
        if set(x.modalities) - set(MODALITIES):
            raise HTTPException(422, "Unsupported modality")
        s["modalities"] = {**s["modalities"], **x.modalities}
    for k in ("enabled", "block_threshold", "warn_threshold", "sync_interval_seconds", "retention_days", "flag_hides"):
        if getattr(x, k) is not None:
            s[k] = getattr(x, k)
    if x.sensitivity is not None:
        s["warn_threshold"], s["block_threshold"] = SENSITIVITY_LEVELS[x.sensitivity]
    if s["warn_threshold"] > s["block_threshold"]:
        raise HTTPException(422, "Flag/warn threshold cannot be higher than the block threshold")
    with db() as c:
        c.execute("UPDATE settings SET enabled=?,category_json=?,modality_json=?,block_threshold=?,warn_threshold=?,sync_interval_seconds=?,retention_days=?,flag_hides=?,updated_at=? WHERE user_id=?",
                  (int(s["enabled"]), json.dumps(s["category_actions"]), json.dumps(s["modalities"]), s["block_threshold"], s["warn_threshold"], s["sync_interval_seconds"], s["retention_days"], int(s["flag_hides"]), now(), uid))
    if x.retention_days is not None:
        apply_retention(force=True)
    return load_settings(uid)


# --------------------------------------------------------- extension registration
_pair_failures: dict[str, list[float]] = {}


@app.post("/api/v1/extensions/pair-code")
def pair_code(uid: str = Depends(auth)):
    code = secrets.token_urlsafe(24)
    with db() as c:
        c.execute("INSERT INTO pair_codes VALUES(?,?,?)", (digest(code), uid, time.time() + 600))
    return {"code": code, "expires_in": 600}


@app.post("/api/v1/extensions/pair", status_code=201)
def pair(x: PairIn, request: Request):
    who = request.client.host if request.client else "?"
    recent = [t for t in _pair_failures.get(who, []) if time.time() - t < 60]
    if len(recent) >= 10:  # FR-CON-03: throttle guessing
        raise HTTPException(429, "Too many pairing attempts. Wait a minute.")
    with db() as c:
        r = c.execute("SELECT user_id FROM pair_codes WHERE code_hash=? AND expires_at>?", (digest(x.code), time.time())).fetchone()
        if not r:
            _pair_failures[who] = recent + [time.time()]
            raise HTTPException(400, "Pairing code invalid or expired")
        token = secrets.token_urlsafe(40)
        eid = secrets.token_hex(16)
        c.execute("DELETE FROM pair_codes WHERE code_hash=?", (digest(x.code),))
        c.execute("INSERT INTO extensions(id,token_hash,user_id,name,created_at,last_seen) VALUES(?,?,?,?,?,?)", (eid, digest(token), r["user_id"], x.name, now(), now()))
    return {"extension_token": token, "extension_id": eid}


def is_connected(last_seen):
    return bool(last_seen and time.time() - datetime.fromisoformat(last_seen).timestamp() < 180)


@app.get("/api/v1/extensions")
def extensions(uid: str = Depends(auth)):
    with db() as c:
        rows = [dict(r) for r in c.execute("SELECT id,name,created_at,last_seen,revoked,paused FROM extensions WHERE user_id=? ORDER BY created_at DESC", (uid,))]
    for r in rows:
        r["paused"] = bool(r["paused"])
        r["connected"] = (not r["revoked"]) and is_connected(r["last_seen"])
        r["status"] = "revoked" if r["revoked"] else "off" if r["paused"] else "connected" if r["connected"] else "disconnected"
    return rows


class ExtensionPatch(BaseModel):
    paused: bool


@app.patch("/api/v1/extensions/{eid}")
def set_extension_paused(eid: str, x: ExtensionPatch, uid: str = Depends(auth)):
    """Turn one browser's protection off or back on. The pairing is kept, so no new code is needed."""
    with db() as c:
        r = c.execute("UPDATE extensions SET paused=? WHERE id=? AND user_id=? AND revoked=0", (int(x.paused), eid, uid))
    if not r.rowcount:
        raise HTTPException(404, "Extension not found")
    return {"id": eid, "paused": x.paused}


@app.delete("/api/v1/extensions/{eid}", status_code=204)
def revoke(eid: str, uid: str = Depends(auth)):
    """Remove a browser: its token stops working and it disappears from the list. The extension then
    returns to its pairing screen, so it can be paired again with a new code."""
    with db() as c:
        r = c.execute("UPDATE extensions SET revoked=1 WHERE id=? AND user_id=?", (eid, uid))
    if not r.rowcount:
        raise HTTPException(404, "Extension not found")


@app.post("/api/v1/extensions/self/release", status_code=204)
def release_self(ext: dict = Depends(ext_auth)):
    """Called by the extension with its OLD token after it was paired to a different parent account,
    so the old account lists this browser as removed instead of 'disconnected'."""
    with db() as c:
        c.execute("UPDATE extensions SET revoked=1 WHERE id=?", (ext["id"],))


def ext_paused(ext, modality):
    return skipped(modality, "This browser's protection is turned off by the parent") if ext.get("paused") else None


@app.get("/api/v1/extensions/policy")
def extension_policy(ext=Depends(ext_auth)):
    s = load_settings(ext["user_id"])
    with db() as c:
        rs = [dict(x) for x in c.execute("SELECT kind,target,action,enabled FROM rules WHERE user_id=?", (ext["user_id"],))]
    payload = {**s, "rules": rs, "fetched_at": now(), "paused": bool(ext["paused"])}
    if ext["paused"]:
        payload["enabled"] = False  # the content script stops all checks when the policy is disabled
    payload.pop("retention_days")
    payload["version"] = hashlib.sha256(json.dumps({k: v for k, v in payload.items() if k != "fetched_at"}, sort_keys=True).encode()).hexdigest()[:16]
    return payload


# ------------------------------------------------------- policy and analysis flow
def decide(s, rules, category, confidence, url):
    """FR-ACT-01 / FR-ML-08 / BR-05. Returns (action, recorded).

    * website allow/block rule overrides the classifier outcome for that domain;
    * confidence >= block threshold  -> configured category action;
    * warn <= confidence < block      -> never auto-blocked: warn (or allow if the category is set to allow);
    * confidence < warn threshold     -> not treated as a detection.
    """
    domain = (urlparse(url).hostname or "").lower() if url else ""
    for r in rules:
        if r["kind"] == "website" and domain and (domain == r["target"].lower() or domain.endswith("." + r["target"].lower())):
            return r["action"], True
    base = next((r["action"] for r in rules if r["kind"] == "category" and r["target"] == category), None) or s["category_actions"].get(category, "warn")
    if confidence >= s["block_threshold"]:
        return base, True
    if confidence >= s["warn_threshold"]:
        return ("allow" if base == "allow" else "warn"), True
    return "allow", False


DEDUPE_MINUTES = 10


def save_event(uid, ext, modality, category, confidence, action, url, model, feature=None, latency_ms=None):
    """Store one detection event (+ alert for warn/block). A repeat of the same detection from the
    same extension on the same page within DEDUPE_MINUTES reuses the existing event, so dynamic
    pages that re-render do not flood history and alerts (NFR-USA-01)."""
    eid = secrets.token_hex(16)
    domain = (urlparse(url).hostname or "")[:253] if url else None
    with db() as c:
        if ext and url:
            since = (datetime.now(timezone.utc) - timedelta(minutes=DEDUPE_MINUTES)).isoformat()
            dup = c.execute("SELECT id FROM events WHERE extension_id=? AND source_url=? AND modality=? AND category=? AND action=? AND created_at>=? ORDER BY created_at DESC LIMIT 1",
                            (ext, url, modality, category, action, since)).fetchone()
            if dup:
                if confidence is not None:
                    c.execute("UPDATE events SET confidence=max(coalesce(confidence,0),?) WHERE id=?", (confidence, dup["id"]))
                return dup["id"]
        c.execute("INSERT INTO events(id,user_id,extension_id,created_at,source_url,domain,modality,category,confidence,action,reviewed,model,function,feature,latency_ms) VALUES(?,?,?,?,?,?,?,?,?,?,0,?,?,?,?)",
                  (eid, uid, ext, now(), url, domain, modality, category, confidence, action, model, FUNCTION_BY_MODALITY[modality], feature or DEFAULT_FEATURE[modality], latency_ms))
        if action in ("warn", "block"):  # FR-HIST-03
            c.execute("INSERT INTO alerts(id,event_id,user_id,read,created_at,severity) VALUES(?,?,?,0,?,?)",
                      (secrets.token_hex(16), eid, uid, now(), "high" if action == "block" else "medium"))
    return eid


def gate(uid, modality):
    """Return a skip reason when protection or this modality is switched off (FR-SET-01/02)."""
    s = load_settings(uid)
    if not s["enabled"]:
        return s, "Protection is disabled by the parent"
    if not s["modalities"].get(modality):
        return s, f"{modality.capitalize()} analysis is disabled in the protection settings"
    return s, None


def skipped(modality, reason):
    return {"modality": modality, "function": FUNCTION_BY_MODALITY[modality], "action": "allow", "category": None, "confidence": 0.0, "skipped": reason}


def finish_analysis(uid, ext, modality, result, url, s, feature=None, started=None, record_actions=("allow", "warn", "block")):
    """``record_actions`` limits which outcomes are stored; the text fast pass stores only blocks
    (the child is actually stopped), and leaves flags to the full pass so it never records a flag
    the full ensemble would not confirm."""
    latency = int((time.perf_counter() - started) * 1000) if started else None
    result.update({"modality": modality, "function": FUNCTION_BY_MODALITY[modality], "feature": feature or DEFAULT_FEATURE[modality], "latency_ms": latency})
    action, recorded = "allow", False
    if not result.get("benign") and result.get("category"):
        with db() as c:
            rules = c.execute("SELECT * FROM rules WHERE user_id=? AND enabled=1", (uid,)).fetchall()
        action, recorded = decide(s, rules, result["category"], result["confidence"], url)
        recorded = recorded and action in record_actions
    result.update({"action": action, "recorded": recorded, "thresholds": {"warn": s["warn_threshold"], "block": s["block_threshold"]},
                   "decision_note": "Below-threshold or benign output is not blocked; scores are not validated category probabilities."})
    if recorded:
        result["event_id"] = save_event(uid, ext, modality, result["category"], result["confidence"], action, url, result.get("model"), feature, latency)
    return result


def run_ml(fn, *args):
    try:
        return fn(*args)
    except ml.ModelUnavailable as e:
        raise HTTPException(503, str(e))
    except ml.InputRejected as e:
        raise HTTPException(413, str(e))


async def read_upload(file: UploadFile, kinds, label):
    if file.content_type not in kinds:
        raise HTTPException(415, f"Unsupported {label} type")
    data = await file.read(MAX_UPLOAD + 1)
    if len(data) > MAX_UPLOAD:
        raise HTTPException(413, f"{label.capitalize()} exceeds upload limit")
    return data


def analyze_text(uid, ext, x: TextIn):
    s, why = gate(uid, "text")
    if why:
        return skipped("text", why)
    t = time.perf_counter()
    # The fast pass and its search-query check share one turn of the inference gate, so the page cover
    # waits behind at most one running job instead of two.
    with ml.inference(ml.PRIORITY_FAST) if x.fast else contextlib.nullcontext():
        return _analyze_text_turn(uid, ext, x, s, t)


def _analyze_text_turn(uid, ext, x: TextIn, s, t):
    result, feature = run_ml(ml.classify_text, x.text, x.fast), x.feature
    if x.query and x.query.strip():
        # A search query is short and states intent; read with the results page it is diluted. It always
        # gets every engine, even in the fast pass: the quick engines alone miss queries such as "gore"
        # (0.31) or "dead body photos" (0.00) that the full ensemble catches (0.66 / 0.69), and a query
        # is only a few words, so the full check takes about as long as the fast pass on a page.
        q = run_ml(ml.classify_text, x.query.strip(), False, ml.PRIORITY_FAST)
        if q.get("category") and q["confidence"] > (result.get("confidence") or 0):
            result, feature = {**q, "decided_by_query": True}, "Search query"
    record = ("block",) if x.fast else ("allow", "warn", "block")
    return finish_analysis(uid, ext, "text", result, x.url, s, feature, t, record_actions=record)


def analyze_frames(uid, ext, frames, url, extra=None, started=None):
    s, why = gate(uid, "video")
    if why:
        return skipped("video", why)
    t = started or time.perf_counter()
    results = []
    for f in frames:
        try:
            results.append(run_ml(ml.classify_image, f))
        except HTTPException as e:
            if e.status_code != 413:  # skip undecodable/oversized frames, fail on missing models
                raise
    if not results:
        raise HTTPException(422, "No decodable frames")
    best = ml.aggregate_frames(results, s["warn_threshold"])
    r = finish_analysis(uid, ext, "video", best, url, s, None, t)
    r.update({"sampled_frames": len(results), "aggregation": "highest above-threshold non-benign sampled frame; otherwise highest frame", **(extra or {})})
    return r


@app.post("/api/v1/classify/text")
def classify_text(x: TextIn, uid: str = Depends(auth)):
    x.feature = x.feature or "Dashboard test text"
    return analyze_text(uid, None, x)


@app.post("/api/v1/extensions/analyze/text")
def extension_analyze(x: TextIn, ext=Depends(ext_auth)):
    return ext_paused(ext, "text") or analyze_text(ext["user_id"], ext["id"], x)


async def _image(uid, ext, file, url, feature):
    s, why = gate(uid, "image")
    if why:
        return skipped("image", why)
    data = await read_upload(file, IMAGE_TYPES, "image")
    t = time.perf_counter()
    # Model work runs on a worker thread: run on the event loop it froze every other request meanwhile.
    result = await run_in_threadpool(run_ml, ml.classify_image, data)
    return finish_analysis(uid, ext, "image", result, url, s, feature, t)


@app.post("/api/v1/extensions/analyze/image")
async def extension_analyze_image(file: UploadFile = File(...), url: str | None = None, ext=Depends(ext_auth)):
    return ext_paused(ext, "image") or await _image(ext["user_id"], ext["id"], file, url, None)


@app.post("/api/v1/classify/image")
async def classify_image(file: UploadFile = File(...), url: str | None = None, uid: str = Depends(auth)):
    return await _image(uid, None, file, url, "Dashboard test image")


@app.post("/api/v1/extensions/analyze/video")
async def extension_analyze_video(files: list[UploadFile] = File(...), url: str | None = None, ext=Depends(ext_auth)):
    """FR-EXT-04/05: the extension samples frames from an accessible <video> and sends only those frames."""
    if len(files) > 12:
        raise HTTPException(413, "At most 12 frames per request")
    if ext["paused"]:
        return ext_paused(ext, "video")
    frames = [await read_upload(f, IMAGE_TYPES, "frame") for f in files]
    return await run_in_threadpool(analyze_frames, ext["user_id"], ext["id"], frames, url, {"strategy": "extension frame sampling"})


@app.post("/api/v1/classify/video")
async def classify_video(file: UploadFile = File(...), url: str | None = None, interval_seconds: float = Query(default=3, ge=1, le=15), uid: str = Depends(auth)):
    s, why = gate(uid, "video")
    if why:
        return skipped("video", why)
    data = await read_upload(file, ("video/mp4", "video/webm", "video/ogg"), "video")
    if not ml.video_ready():
        raise HTTPException(503, "Local video analysis needs OpenCV and the local image model (requirements-ml.txt).")
    return await run_in_threadpool(_classify_video_file, uid, data, file.content_type, url, interval_seconds)


def _classify_video_file(uid, data, content_type, url, interval_seconds):
    """Decode, sample and classify an uploaded clip (runs on a worker thread)."""
    import cv2
    started = time.perf_counter()
    path = cap = None
    try:
        with tempfile.NamedTemporaryFile(suffix="." + content_type.split("/")[1], delete=False) as f:
            f.write(data)
            path = f.name
        cap = cv2.VideoCapture(path)
        fps = cap.get(cv2.CAP_PROP_FPS)
        total = cap.get(cv2.CAP_PROP_FRAME_COUNT)
        duration = total / fps if fps else 0
        if duration <= 0 or duration > 180:
            raise HTTPException(422, "Video must be decodable and at most 3 minutes")
        interval = max(interval_seconds, duration / 60)
        step = max(1, int(fps * interval))
        frames = []
        for pos in range(0, int(total), step):
            cap.set(cv2.CAP_PROP_POS_FRAMES, pos)
            ok, frame = cap.read()
            if ok:
                ok, enc = cv2.imencode(".jpg", frame)
                if ok:
                    frames.append(enc.tobytes())
            if len(frames) >= 60:
                break
        return analyze_frames(uid, None, frames, url, {"duration_seconds": round(duration, 1), "sample_interval_seconds": round(interval, 2), "strategy": "uniform frame sampling"}, started)
    finally:
        if cap:
            cap.release()
        if path and os.path.exists(path):
            os.unlink(path)


async def _audio(uid, ext, file, url):
    s, why = gate(uid, "audio")
    if why:
        return skipped("audio", why)
    data = await read_upload(file, ("audio/mpeg", "audio/wav", "audio/x-wav", "audio/ogg", "audio/webm", "audio/mp4", "audio/x-m4a"), "audio")
    t = time.perf_counter()
    path = None
    try:
        with tempfile.NamedTemporaryFile(suffix=".audio", delete=False) as f:
            f.write(data)
            path = f.name
        transcript = await run_in_threadpool(run_ml, ml.transcribe_audio, path)  # held in memory only, never stored
    finally:
        if path and os.path.exists(path):
            os.unlink(path)
    result = await run_in_threadpool(run_ml, ml.classify_text, transcript) if transcript.strip() else {"category": None, "confidence": 0.0, "benign": True, "model": ""}
    result["model"] = ("faster-whisper:" + ml.AUDIO_MODEL + " -> " + result["model"]).rstrip(" ->")
    result["transcript_chars"] = len(transcript)  # length only; the transcript itself is never stored
    return finish_analysis(uid, ext, "audio", result, url, s, None, t)


@app.post("/api/v1/classify/audio")
async def classify_audio(file: UploadFile = File(...), url: str | None = None, uid: str = Depends(auth)):
    return await _audio(uid, None, file, url)


@app.post("/api/v1/extensions/analyze/audio")
async def extension_analyze_audio(file: UploadFile = File(...), url: str | None = None, ext=Depends(ext_auth)):
    return ext_paused(ext, "audio") or await _audio(ext["user_id"], ext["id"], file, url)


# ------------------------------------------------- detection history and alerts
@app.post("/api/v1/extensions/events")
def record_extension_event(x: EventIn, ext=Depends(ext_auth)):
    if x.modality not in MODALITIES or x.category not in CATEGORIES or x.action not in ACTIONS:
        raise HTTPException(422, "Invalid event fields")
    if ext["paused"]:
        return {"event_id": None, "recorded": False, "skipped": "This browser's protection is turned off by the parent"}
    return {"event_id": save_event(ext["user_id"], ext["id"], x.modality, x.category, x.confidence, x.action, x.url, x.model, x.feature), "recorded": True}


@app.get("/api/v1/events")
def events(uid: str = Depends(auth), q: str | None = None, start: str | None = None, end: str | None = None, modality: str | None = None, category: str | None = None,
           action: str | None = None, domain: str | None = None, reviewed: bool | None = None, page: int = Query(default=1, ge=1), page_size: int = Query(default=25, ge=1, le=100)):
    clauses, args = ["user_id=?"], [uid]
    for col, val in (("modality", modality), ("category", category), ("action", action)):
        if val:
            clauses.append(col + "=?")
            args.append(val)
    if domain:
        clauses.append("domain LIKE ?")
        args.append(f"%{domain}%")
    if reviewed is not None:
        clauses.append("reviewed=?")
        args.append(int(reviewed))
    if start:
        clauses.append("created_at>=?")
        args.append(start)
    if end:
        clauses.append("created_at<=?")
        args.append(end)
    if q:
        clauses.append("(domain LIKE ? OR source_url LIKE ? OR category LIKE ? OR function LIKE ? OR feature LIKE ?)")
        args.extend([f"%{q}%"] * 5)
    where = " AND ".join(clauses)
    with db() as c:
        total = c.execute("SELECT count(*) FROM events WHERE " + where, args).fetchone()[0]
        rows = c.execute(f"SELECT * FROM events WHERE {where} ORDER BY created_at DESC LIMIT ? OFFSET ?", (*args, page_size, (page - 1) * page_size)).fetchall()
    return {"items": [dict(r) for r in rows], "total": total, "page": page, "page_size": page_size}


@app.get("/api/v1/events/{eid}")
def get_event(eid: str, uid: str = Depends(auth)):
    with db() as c:
        r = c.execute("SELECT * FROM events WHERE id=? AND user_id=?", (eid, uid)).fetchone()
    if not r:
        raise HTTPException(404, "Event not found")
    return dict(r)


@app.patch("/api/v1/events/{eid}/review")
def review(eid: str, uid: str = Depends(auth)):
    with db() as c:
        r = c.execute("UPDATE events SET reviewed=1 WHERE id=? AND user_id=?", (eid, uid))
    if not r.rowcount:
        raise HTTPException(404, "Event not found")
    return {"reviewed": True}


@app.get("/api/v1/alerts")
def alerts(uid: str = Depends(auth), read: bool | None = None, page: int = Query(default=1, ge=1)):
    sql = "SELECT a.id,a.read,a.created_at,a.severity,e.id event_id,e.created_at event_time,e.domain,e.modality,e.category,e.action,e.function,e.feature,e.confidence FROM alerts a JOIN events e ON e.id=a.event_id WHERE a.user_id=?"
    args = [uid]
    if read is not None:
        sql += " AND a.read=?"
        args.append(int(read))
    with db() as c:
        rows = c.execute(sql + " ORDER BY a.created_at DESC LIMIT 50 OFFSET ?", (*args, (page - 1) * 50)).fetchall()
    return [dict(r) for r in rows]


@app.patch("/api/v1/alerts/{aid}/read")
def read_alert(aid: str, uid: str = Depends(auth)):
    with db() as c:
        r = c.execute("UPDATE alerts SET read=1 WHERE id=? AND user_id=?", (aid, uid))
    if not r.rowcount:
        raise HTTPException(404, "Alert not found")
    return {"read": True}


# ----------------------------------------------------- dashboard, analytics, functions
def evaluation_results():
    """FR-DASH-08: only measured results from a real evaluation run are ever exposed."""
    try:
        data = json.loads(EVAL_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}
    if not isinstance(data, dict):
        return {}

    def validated(m) -> bool:
        # A result counts only if it names its labeled dataset, sample count and run date and its rates are real fractions.
        # Hand-typed prototype/mock numbers without that provenance are never shown as measured accuracy.
        if not isinstance(m, dict) or m.get("mock") or m.get("prototype"):
            return False
        if not (isinstance(m.get("dataset"), str) and m["dataset"].strip() and isinstance(m.get("evaluated_at"), str) and m["evaluated_at"].strip()):
            return False
        if not (isinstance(m.get("samples"), int) and m["samples"] > 0):
            return False
        rates = [m.get(k) for k in ("accuracy", "macro_f1", "harmful_detection_rate", "false_positive_rate") if m.get(k) is not None]
        return isinstance(m.get("accuracy"), (int, float)) and all(isinstance(v, (int, float)) and 0 <= v <= 1 for v in rates)

    return {name: m for name, m in data.items() if validated(m)}


@app.get("/api/v1/models")
def models(uid: str = Depends(auth)):
    ev = evaluation_results()
    ready = {r["name"]: r for r in ml.engine_inventory()}
    with db() as c:
        rows = [dict(r) for r in c.execute("SELECT * FROM model_metadata ORDER BY modality,name")]
    for r in rows:
        r["categories"] = json.loads(r.pop("categories_json"))
        r["evaluation"] = ev.get(r["name"])
        r["available"] = bool(ready.get(r["name"], {}).get("ready"))
        r["license"] = ready.get(r["name"], {}).get("license")
    return rows


@app.get("/api/v1/summary")
def summary(uid: str = Depends(auth), days: int = Query(default=30, ge=1, le=3650), modality: str | None = None):
    apply_retention()
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    if modality and modality not in MODALITIES:
        raise HTTPException(422, "Unsupported modality")
    # FR-DASH-04: the channel filter applies to the summary statistics, recent detections and unread alerts alike
    mf, ma = (" AND modality=?", [modality]) if modality else ("", [])
    with db() as c:
        ev = c.execute("SELECT count(*) total,sum(action='block') blocked,sum(action='warn') warned,sum(action='allow') allowed,sum(reviewed=0) unreviewed FROM events WHERE user_id=? AND created_at>=?" + mf, (uid, cutoff, *ma)).fetchone()
        mods = c.execute("SELECT modality,count(*) n FROM events WHERE user_id=? AND created_at>=? GROUP BY modality", (uid, cutoff)).fetchall()
        cats = c.execute("SELECT category,count(*) n FROM events WHERE user_id=? AND created_at>=?" + mf + " GROUP BY category", (uid, cutoff, *ma)).fetchall()
        recent = c.execute("SELECT * FROM events WHERE user_id=?" + mf + " ORDER BY created_at DESC LIMIT 8", (uid, *ma)).fetchall()
        ext = c.execute("SELECT * FROM extensions WHERE user_id=? AND revoked=0 AND paused=0 ORDER BY last_seen DESC LIMIT 1", (uid,)).fetchone()
        unread = c.execute("SELECT count(*) FROM alerts a JOIN events e ON e.id=a.event_id WHERE a.user_id=? AND a.read=0" + mf.replace("modality", "e.modality"), (uid, *ma)).fetchone()[0]
    s = load_settings(uid)
    connected = bool(ext and is_connected(ext["last_seen"]))
    st = ml.status()
    return {"protection_enabled": s["enabled"], "protection_ready": s["enabled"] and connected, "text_model_ready": st["text"]["ready"],
            "enabled_modalities": [m for m, on in s["modalities"].items() if on], "unread_alerts": unread,
            "extension": {"connected": connected, "last_seen": ext["last_seen"] if ext else None, "name": ext["name"] if ext else None},
            "period_days": days, "channel": modality or "all", "total_detections": ev["total"] or 0, "blocked": ev["blocked"] or 0, "warned": ev["warned"] or 0, "allowed": ev["allowed"] or 0,
            "unreviewed": ev["unreviewed"] or 0, "by_modality": {r["modality"]: r["n"] for r in mods}, "by_category": {r["category"]: r["n"] for r in cats},
            "recent": [dict(r) for r in recent], "validated_metrics": evaluation_results()}


@app.get("/api/v1/analytics")
def analytics(uid: str = Depends(auth), days: int = Query(default=30, ge=1, le=3650), start: str | None = None, end: str | None = None):
    """FR-DASH-07: aggregate counts by modality, category, action, function and day for a time range."""
    lo = start or (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    hi = end or "9999"
    out = {"range": {"start": lo, "end": None if end is None else end}}
    with db() as c:
        for key, col in (("by_modality", "modality"), ("by_category", "category"), ("by_action", "action"), ("by_function", "function")):
            out[key] = {(r[0] or "Unknown"): r[1] for r in c.execute(f"SELECT {col},count(*) FROM events WHERE user_id=? AND created_at>=? AND created_at<=? GROUP BY {col} ORDER BY 2 DESC", (uid, lo, hi))}
        out["by_day"] = [{"day": r[0], "count": r[1]} for r in c.execute("SELECT substr(created_at,1,10),count(*) FROM events WHERE user_id=? AND created_at>=? AND created_at<=? GROUP BY 1 ORDER BY 1", (uid, lo, hi))]
        out["avg_latency_ms"] = {r[0]: round(r[1]) for r in c.execute("SELECT modality,avg(latency_ms) FROM events WHERE user_id=? AND created_at>=? AND created_at<=? AND latency_ms IS NOT NULL GROUP BY modality", (uid, lo, hi))}  # NFR-PERF-03
    out["total"] = sum(out["by_action"].values())
    return out


SYSTEM_FUNCTIONS = [
    # (module, scope, primary user, main output, applied to / feature, modality)
    ("Authentication & Access", "Authenticate the parent/guardian and restrict protected dashboard functions to authorized users.", "Parent/Guardian", "Authorized dashboard session", "Dashboard sign-in (email + password, or Google), API session tokens", None),
    ("Parent Dashboard", "Display protection status, summary statistics, channel filters, recent detections and current alerts.", "Parent/Guardian", "Monitoring and protection overview", "Dashboard home page", None),
    ("Browser Extension", "Operate on the protected browser, report connection/protection status, analyze accessible page content and apply protection actions.", "Child browser user / Parent for settings", "Allow, warn/flag, or block result", "Web page DOM: text, <img>, <video>; popup; block/warn overlay", None),
    ("Text Analysis", "Extract accessible text from web pages and classify configured harmful/morbid content categories.", "System", "Text category + confidence + action", "Visible page text extracted by the extension (first ~3,500 characters; opening ~900 checked before the page is shown)", "text"),
    ("Image Analysis", "Analyze accessible web images using the selected local image-classification approach.", "System", "Image category + confidence + action", "Large <img> elements on the page (max 8 per page view), blurred until checked", "image"),
    ("Video Analysis", "Analyze supported video content using a defined frame/segment sampling strategy.", "System", "Video category + confidence + action", "<video> elements: first frame checked before it is shown, then a frame every 5 s while playing, sent in batches of 3 (max 2 videos per page); uploaded clips: one frame every 3 s (max 60)", "video"),
    ("Audio Analysis (Optional)", "Analyze accessible audio where technically feasible and approved by the team.", "System", "Audio category + confidence + action", "Uploaded audio files: local transcript passed to the text classifier (off by default)", "audio"),
    ("Detection History", "Store and display classified events with time, modality, category, confidence, source/domain, and action.", "Parent/Guardian", "Searchable detection history", "Every recorded detection event", None),
    ("Alerts", "Record and surface newly blocked or flagged events to the parent dashboard.", "Parent/Guardian", "Alert records / status", "Warn and block events", None),
    ("Protection Settings", "Enable/disable protection, configure modality/category behavior, and maintain approved/blocked domains where supported.", "Parent/Guardian", "Active policy configuration", "Global switch, modalities, categories, thresholds, domain rules", None),
    ("Extension Status", "Show current extension connection/protection status and last-seen state for the registered browser.", "Parent/Guardian", "Device/extension status", "Registered extension instances", None),
    ("Analytics", "Provide aggregate counts by modality, category, date range, and action for monitoring.", "Parent/Guardian", "Summary statistics and charts", "Detection events within the selected range", None),
]


@app.get("/api/v1/functions")
def system_functions(uid: str = Depends(auth), days: int = Query(default=30, ge=1, le=3650)):
    """Section 2 (Scope and System Functions): each function, the feature it is applied to, readiness and recent use."""
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    st, s = ml.status(), load_settings(uid)
    with db() as c:
        counts = {r[0]: r[1] for r in c.execute("SELECT function,count(*) FROM events WHERE user_id=? AND created_at>=? GROUP BY function", (uid, cutoff))}
    out = []
    for module, scope, user, output, applied, modality in SYSTEM_FUNCTIONS:
        item = {"module": module, "scope": scope, "primary_user": user, "main_output": output, "applied_to": applied, "modality": modality}
        if modality:
            item.update({"enabled": bool(s["enabled"] and s["modalities"].get(modality)), "ready": st[modality]["ready"], "engine": st[modality]["model"], "detections": counts.get(FUNCTION_BY_MODALITY[modality], 0), "reason": st[modality]["reason"]})
        else:
            item.update({"enabled": True, "ready": True, "engine": None, "detections": None, "reason": None})
        out.append(item)
    return out


# ------------------------------------------------------------ web dashboard hosting
# The parent dashboard is also served by the backend itself (http://127.0.0.1:8000/),
# so the web app and the API run as one local process. Port 5500 still works.
DASHBOARD_DIR = ROOT / "web_dashboard"
if DASHBOARD_DIR.is_dir():
    from fastapi.staticfiles import StaticFiles
    app.mount("/", StaticFiles(directory=str(DASHBOARD_DIR), html=True), name="dashboard")

