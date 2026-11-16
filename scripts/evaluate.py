"""Compute classification metrics for a labeled CSV (SRS FR-EVAL-03, FR-DASH-08).

Two input shapes:
  truth,prediction        -> metrics for predictions you already produced (needs --model to save)
  truth,text[,kind]       -> runs MORVIX's local text pipeline on every row and evaluates
                             the ensemble AND each engine separately
A prediction counts as a detection when its confidence >= --threshold (default 0.5, the
default flag/warn threshold). Use "None" (or blank) as the truth label for benign rows.
--save records results in data/evaluation.json; the dashboard shows accuracy ONLY from there.
"""
import argparse, csv, json, sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ENSEMBLE = "morvix-text-ensemble"
FAST = "morvix-text-fast-pass"


def metrics(pairs):
    """pairs: list of (truth, prediction). Returns dict with matrix, per-label and summary metrics."""
    labels = sorted({t for t, _ in pairs} | {p for _, p in pairs}, key=lambda x: (x == "None", x))
    m = {t: {q: 0 for q in labels} for t in labels}
    for t, p in pairs:
        m[t][p] += 1
    per, f1s = {}, []
    for l in labels:
        tp = m[l][l]
        fp = sum(m[t][l] for t in labels if t != l)
        fn = sum(m[l][q] for q in labels if q != l)
        pr = tp / (tp + fp) if tp + fp else 0.0
        rc = tp / (tp + fn) if tp + fn else 0.0
        f1 = 2 * pr * rc / (pr + rc) if pr + rc else 0.0
        support = sum(m[l].values())
        per[l] = {"precision": round(pr, 4), "recall": round(rc, 4), "f1": round(f1, 4), "support": support}
        if support:
            f1s.append(f1)
    harmful = [(t, p) for t, p in pairs if t != "None"]
    benign = [(t, p) for t, p in pairs if t == "None"]
    return {"labels": labels, "matrix": m, "per_label": per, "samples": len(pairs),
            "accuracy": round(sum(t == p for t, p in pairs) / len(pairs), 4),
            "macro_f1": round(sum(f1s) / len(f1s), 4) if f1s else 0.0,
            "harmful_detection_rate": round(sum(p != "None" for _, p in harmful) / len(harmful), 4) if harmful else None,
            "false_positive_rate": round(sum(p != "None" for _, p in benign) / len(benign), 4) if benign else None}


def predict(scores, threshold):
    cat, conf = max(scores.items(), key=lambda kv: kv[1]) if scores else (None, 0)
    return cat if cat and conf >= threshold else "None"


def show(name, r):
    print(f"\n=== {name} ===  samples={r['samples']} accuracy={r['accuracy']:.3f} macro-F1={r['macro_f1']:.3f} "
          f"harmful-detected={r['harmful_detection_rate']} false-positive-rate={r['false_positive_rate']}")
    w = max(len(l) for l in r["labels"]) + 2
    print("truth \\ pred".ljust(w) + "".join(l[:12].rjust(14) for l in r["labels"]))
    for t in r["labels"]:
        print(t.ljust(w) + "".join(str(r["matrix"][t][q]).rjust(14) for q in r["labels"]))
    for l, v in r["per_label"].items():
        print(f"  {l:22} P={v['precision']:.3f} R={v['recall']:.3f} F1={v['f1']:.3f} n={v['support']}")


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--csv", required=True)
    p.add_argument("--save", action="store_true", help="write results to data/evaluation.json for the dashboard")
    p.add_argument("--model", default=None, help="name to save under when the CSV already contains predictions")
    p.add_argument("--dataset", default=None, help="short dataset description stored with the result")
    p.add_argument("--threshold", type=float, default=0.5, help="min confidence counted as a detection (default flag threshold)")
    p.add_argument("--report", default=None, help="optional path for a full JSON report (matrices, per-kind, errors)")
    p.add_argument("--fast", action="store_true", help="evaluate the pre-access fast pass (no NLI engine); saved as " + "morvix-text-fast-pass")
    a = p.parse_args()
    with open(a.csv, newline="", encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))
    if not rows or "truth" not in rows[0] or not ({"prediction"} <= set(rows[0]) or {"text"} <= set(rows[0])):
        raise SystemExit("CSV needs a truth column plus either prediction or text, and at least one data row")
    for r in rows:
        r["truth"] = (r["truth"] or "").strip() or "None"

    results, errors = {}, []
    if "prediction" in rows[0]:
        if a.save and not a.model:
            raise SystemExit("--save needs --model NAME when the CSV already contains predictions")
        results[a.model or "supplied predictions"] = metrics([(r["truth"], (r["prediction"] or "").strip() or "None") for r in rows])
    else:
        sys.path.insert(0, str(ROOT))
        from backend import classifiers as ml
        print("Engines:", ml.status()["text"]["model"], "(fast pass: NLI skipped)" if a.fast else "")
        ens_name = FAST if a.fast else ENSEMBLE
        per_engine = defaultdict(list)
        by_kind = defaultdict(lambda: defaultdict(list))
        for i, r in enumerate(rows, 1):
            out = ml.classify_text(r["text"], fast=a.fast)
            ens = predict(out["scores"], a.threshold)
            per_engine[ens_name].append((r["truth"], ens))
            by_kind[r.get("kind") or "all"][ens_name].append((r["truth"], ens))
            if not a.fast:  # single engines are unchanged by the fast pass; never overwrite their saved results from it
                for name, scores in out.get("engines", {}).items():
                    per_engine[name].append((r["truth"], predict(scores, a.threshold)))
            if ens != r["truth"]:
                errors.append({"truth": r["truth"], "predicted": ens, "confidence": out["confidence"], "kind": r.get("kind"),
                               "decided_by": out.get("decided_by"), "text": r["text"]})
            if i % 50 == 0:
                print(f"  {i}/{len(rows)} classified")
        results = {name: metrics(pairs) for name, pairs in per_engine.items()}
        print("\nEnsemble by row kind:")
        for kind, d in by_kind.items():
            k = metrics(d[ens_name])
            print(f"  {kind:14} n={k['samples']:3} accuracy={k['accuracy']:.3f} false-positive-rate={k['false_positive_rate']}")
    for name, r in results.items():
        show(name, r)
    if errors:
        print(f"\nEnsemble errors ({len(errors)}):")
        for e in errors:
            print(f"  truth={e['truth']:21} pred={e['predicted']:21} conf={e['confidence']:.2f} {e['text'][:80]}")
    print("\nMetrics describe only the supplied rows; they are not a general accuracy claim.")

    stamp = datetime.now(timezone.utc).date().isoformat()
    dataset = a.dataset or Path(a.csv).name
    if a.report:
        Path(a.report).write_text(json.dumps({"dataset": dataset, "threshold": a.threshold, "evaluated_at": stamp,
                                               "results": results, "ensemble_errors": errors}, indent=2), encoding="utf-8")
        print("Full report written to", a.report)
    if a.save:
        import os
        out = Path(os.getenv("MORVIX_EVALUATION_FILE", str(ROOT / "data" / "evaluation.json")))
        data = json.loads(out.read_text(encoding="utf-8")) if out.exists() else {}
        for name, r in results.items():
            data[name] = {"dataset": dataset, "samples": r["samples"], "accuracy": r["accuracy"], "macro_f1": r["macro_f1"],
                          "harmful_detection_rate": r["harmful_detection_rate"], "false_positive_rate": r["false_positive_rate"],
                          "threshold": a.threshold, "evaluated_at": stamp}
        out.write_text(json.dumps(data, indent=2), encoding="utf-8")
        print(f"Saved measured results for {', '.join(results)} to {out}")


if __name__ == "__main__":
    main()
