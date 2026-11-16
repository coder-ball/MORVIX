"""Read-only viewer for the MORVIX database (data/morvix.db) and data/evaluation.json.

Safe to run while MORVIX is running and safe to show in a demo: the database is opened read-only,
and secrets are never printed (password/token/code hashes are hidden, email addresses are masked).

  python scripts/show_db.py                     tables with row counts and columns
  python scripts/show_db.py events              latest 15 detection events (only metadata is stored)
  python scripts/show_db.py events --limit 40   more rows
  python scripts/show_db.py alerts | settings | extensions | rules | users | model_metadata
  python scripts/show_db.py evaluation          measured model results from data/evaluation.json
"""
import argparse, json, os, sqlite3, sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DB = Path(os.getenv("MORVIX_DATABASE", str(ROOT / "data" / "morvix.db")))
EVAL = Path(os.getenv("MORVIX_EVALUATION_FILE", str(ROOT / "data" / "evaluation.json")))
HIDDEN = {"password_hash", "token_hash", "code_hash", "google_sub"}  # never printed
LONG = {"source_url": 60, "model": 40, "description": 50, "categories_json": 60, "category_json": 70, "modality_json": 60, "function": 16}
# readable column selections for the main tables; other tables show every non-secret column
VIEWS = {
    "events": "created_at,domain,modality,category,confidence,action,reviewed,feature,latency_ms",
    "alerts": "alerts.created_at,severity,read,events.category,events.action,events.domain",
    "extensions": "name,created_at,last_seen,paused,revoked",
    "settings": "enabled,warn_threshold,block_threshold,flag_hides,modality_json,category_json,sync_interval_seconds,retention_days,updated_at",
    "users": "email,name,provider,role,status,email_verified,created_at",
}


def connect():
    if not DB.exists():
        sys.exit(f"No database at {DB}. Start MORVIX once to create it.")
    c = sqlite3.connect(f"file:{DB.as_posix()}?mode=ro", uri=True)  # read-only: nothing can be changed
    c.row_factory = sqlite3.Row
    return c


def mask_email(e):
    if not e or "@" not in e:
        return e
    name, dom = e.split("@", 1)
    return name[:2] + "***@" + dom


def fmt(col, v):
    if v is None:
        return "-"
    if col.endswith("created_at") or col in ("last_seen", "updated_at"):
        try:
            return datetime.fromisoformat(v).astimezone().strftime("%Y-%m-%d %H:%M:%S")  # shown in local time
        except (TypeError, ValueError):
            return str(v)
    if col == "email":
        return mask_email(v)
    if col == "confidence" and isinstance(v, float):
        return f"{v:.2f}"
    s = str(v)
    limit = LONG.get(col.split(".")[-1])
    return s if not limit or len(s) <= limit else s[:limit - 1] + "…"


def print_table(rows, cols):
    labels = [c.split(".")[-1] for c in cols]
    cells = [[fmt(c, r[i]) for i, c in enumerate(cols)] for r in rows]
    widths = [max([len(l)] + [len(row[i]) for row in cells]) for i, l in enumerate(labels)]
    print("  ".join(l.upper().ljust(w) for l, w in zip(labels, widths)))
    print("  ".join("-" * w for w in widths))
    for row in cells:
        print("  ".join(v.ljust(w) for v, w in zip(row, widths)))


def overview(c):
    print(f"Database: {DB}  (opened read-only)\n")
    for (t,) in c.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"):
        n = c.execute(f'SELECT count(*) FROM "{t}"').fetchone()[0]
        cols = [r[1] + (" (hidden)" if r[1] in HIDDEN else "") for r in c.execute(f'PRAGMA table_info("{t}")')]
        print(f"{t:15} {n:6} rows   {', '.join(cols)}")
    print("\nShow a table: python scripts/show_db.py events   (or alerts, settings, extensions, rules, users, model_metadata)")


def show(c, table, limit):
    tables = {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if table not in tables:
        sys.exit(f"Unknown table '{table}'. Tables: {', '.join(sorted(tables))}")
    if table in ("sessions", "pair_codes", "email_codes"):
        n = c.execute(f'SELECT count(*) FROM "{table}"').fetchone()[0]
        print(f"{table}: {n} rows. Only hashes and expiry times are stored here, so nothing is shown.")
        return
    if table == "alerts":
        sql = f"SELECT {VIEWS['alerts']} FROM alerts JOIN events ON events.id=alerts.event_id ORDER BY alerts.created_at DESC LIMIT ?"
        cols, ordered = VIEWS["alerts"].split(","), True
    else:
        cols = VIEWS.get(table, "").split(",") if table in VIEWS else \
            [r[1] for r in c.execute(f'PRAGMA table_info("{table}")') if r[1] not in HIDDEN and r[1] not in ("id", "user_id")]
        ordered = "created_at" in cols
        sql = f'SELECT {",".join(cols)} FROM "{table}"{" ORDER BY created_at DESC" if ordered else ""} LIMIT ?'
    rows = c.execute(sql, (limit,)).fetchall()
    total = c.execute(f'SELECT count(*) FROM "{table}"').fetchone()[0]
    print(f"{table}: showing {len(rows)} of {total} rows{' (newest first, local time)' if ordered else ''}\n")
    print_table(rows, cols)
    if table == "events":
        print("\nOnly metadata is stored for each detection; page text, images and video are never saved (FR-HIST-05, AC-15).")
    if table == "users":
        print("\nEmail addresses are masked; password hashes are never printed.")


def evaluation():
    if not EVAL.exists():
        sys.exit(f"No {EVAL.name} yet. Create it with scripts/evaluate.py --save on a labeled dataset.")
    data = json.loads(EVAL.read_text(encoding="utf-8"))
    print(f"{EVAL}  (measured results shown by the dashboard, FR-DASH-08)\n")
    rows = [(n, m.get("samples"), m.get("accuracy"), m.get("macro_f1"), m.get("harmful_detection_rate"), m.get("false_positive_rate"), m.get("evaluated_at"))
            for n, m in data.items()]
    pct = lambda v: "-" if v is None else f"{v * 100:.1f}%"
    print(f"{'ENGINE':45} {'ROWS':>5} {'ACCURACY':>9} {'MACRO-F1':>9} {'HARMFUL CAUGHT':>15} {'FALSE ALARMS':>13}  DATE")
    for n, s, a, f, h, fp, d in rows:
        print(f"{n[:45]:45} {s or '-':>5} {pct(a):>9} {pct(f):>9} {pct(h):>15} {pct(fp):>13}  {d or '-'}")
    if rows:
        print(f"\nDataset: {next(iter(data.values())).get('dataset', '-')}")


def main():
    p = argparse.ArgumentParser(description="Read-only viewer for the MORVIX database and evaluation results.")
    p.add_argument("what", nargs="?", help="a table name, or 'evaluation'")
    p.add_argument("--limit", type=int, default=15, help="rows to show (default 15)")
    a = p.parse_args()
    if a.what == "evaluation":
        return evaluation()
    with connect() as c:
        overview(c) if not a.what else show(c, a.what, max(1, a.limit))


if __name__ == "__main__":
    main()
