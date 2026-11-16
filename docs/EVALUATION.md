# Text classifier evaluation (FR-EVAL-03, FR-DASH-08)

## Datasets

| File | Role | Rows | Composition |
|---|---|---|---|
| `data/eval/text_seed_v1.csv` | **Dev set**, used to choose engines, hypotheses and fusion rules | 200 | 40 Threat, 40 Self-harm, 40 Graphic Violence, 80 None (40 benign + 40 hard negatives) |
| `data/eval/text_test_v1.csv` | **Held-out test set**, written after tuning and never used to tune | 160 | 30 Threat, 30 Self-harm, 30 Graphic Violence, 70 None (35 benign + 35 hard negatives) |

Both sets were written by the project team as short English sentences. Hard negatives are harmless texts that resemble harmful ones: idioms ("this homework is killing me"), crisis-line and prevention text, news, history, fiction, first aid, games and sport. Each row has a `kind` column (direct / online / ideation / self-injury / description / benign / hard_negative).

**Limitations:**
- The sentences are synthetic and short, written by one team, and in English only.
- They are not real browsing data.
- The numbers below describe these rows only. They are not a general accuracy claim, and they do not validate MORVIX as a child-safety or crisis-detection tool.
- Image, video and audio are **not** evaluated yet.

## Method

```powershell
.\.venv\Scripts\python.exe scripts\evaluate.py --csv data\eval\text_test_v1.csv --save --dataset "MORVIX held-out text test set v1 (160 rows)" --report data\eval\text_test_v1_report.json
```

A prediction counts as a detection when its top category is at or above **0.5**, the default flag threshold. The script evaluates the fused ensemble and each engine separately, and saves the results to `data/evaluation.json`; the dashboard shows only those saved numbers. Single-category engines (toxic-bert, suicidality) can only score their own category, so compare them by **per-category recall**, not overall accuracy.

## Results: held-out test set (160 rows)

| Pipeline | Accuracy | Macro-F1 | Harmful detected | False-positive rate |
|---|---|---|---|---|
| **MORVIX v3.1 text ensemble** | **0.900** | **0.895** | **92.2 %** | **8.6 %** (benign 0 %, hard negatives 17 %) |
| Pre-access fast pass (no NLI; v3.6, 2026-11-15) | 0.694 | 0.576 | 50.0 % | 1.4 % |
| Lexicon only (what v2 actually ran) | 0.525 | 0.373 | 16.7 % | 1.4 % |
| DeBERTa-v3 zero-shot NLI alone | 0.825 | 0.794 | 82.2 % | 7.1 % |

Per category (ensemble, test set):

| Category | Precision | Recall | F1 | n |
|---|---|---|---|---|
| Graphic Violence | 0.882 | 1.000 | 0.938 | 30 |
| Self-harm Indicators | 0.931 | 0.900 | 0.915 | 30 |
| Threat Language | 0.885 | 0.767 | 0.821 | 30 |
| None | 0.901 | 0.914 | 0.908 | 70 |

Single-engine recall on its own category (test set):
- suicidality: Self-harm 0.93
- toxic-bert: Threat 0.53
- KoalaAI: Self-harm 0.47, Graphic Violence 0.00
- lexicon: 0.17 for each category

The dev set gives accuracy 0.915 and macro-F1 0.916, close to the held-out result. That suggests the fusion rules were not over-fitted to the dev rows.

## What the dev set changed

| Step (dev set) | Accuracy | Macro-F1 | Notes |
|---|---|---|---|
| v3.0 ensemble (lexicon + KoalaAI + suicidality + toxic-bert, max fusion) | 0.655 | 0.544 | Graphic Violence recall 5 %: KoalaAI rated gore "OK" with p ≈ 0.88. The suicidality model fires on threats and gore. |
| + zero-shot NLI engine, first hypotheses | 0.885 | 0.879 | Graphic Violence recall 100 %, but 20 % of hard negatives flagged |
| + hypotheses chosen by per-hypothesis recall/false-positive rate, plus fusion rules | 0.915 | 0.916 | v3.1 configuration |
| + rule 5 (lexicon needs model support), v3.5 diagnosis | **0.920** | **0.920** | Final configuration. False positives 10 % → 8.75 %, harmful detected unchanged (95 %). Held-out results unchanged (0.900 / 0.895). |

Fusion rules (`fuse_text` in `backend/classifiers.py`):
1. Threat and Graphic Violence take the maximum score across engines.
2. Self-harm specialist votes (suicidality, KoalaAI SH) are halved when the text has no first-person reference.
3. They are halved again when the NLI engine reads the text as a threat or graphic description aimed at someone else.
4. A strong first-person self-harm signal with no other target halves the threat and graphic scores. For example, "I started cutting again" is self-harm, not graphic violence.
5. (v3.5) A lexicon score of 0.5 or more that no model supports (effective model vote < 0.3 after rules 2–3) is halved. Keyword hits alone ("suicide", "self-harm") had flagged a crisis-line page at 0.71 while every model scored it below 0.01; it now scores 0.36. In lexicon-only mode (no models installed) the rule does not apply.

Rejected after measurement:
- int8 dynamic quantisation of the NLI model: about 35 % faster, but the scores collapsed to about 0.5.
- Gating every self-harm vote on NLI agreement: self-harm recall fell to 0.35.

## Remaining errors (held-out)

- **Implied threats with no violent words** (6 of 30), e.g. "i have your address now. see you soon", "Everyone at that party is going to pay".
- **Violent idioms** flagged as threats or gore (5 of 35 hard negatives), e.g. "My brother is going to murder me for eating his fries lol", "Kill two birds with one stone", "We dissected a frog in biology class".
- **Help-seeking text** about self-harm (1), and one self-harm sentence about school read as a threat.

These are known weak spots. The default policy action is **warn**, not block, so a mistake is shown to the child as a dismissible banner and recorded for the parent to review.

## Long pages: buried-sentence test (v3.6, 2026-11-15)

The held-out rows are single sentences (at most 102 characters), so they cannot show how a harmful sentence behaves inside a long page. This test places each of 48 harmful sentences and 24 hard negatives (randomly drawn, seed 7, from both sets) at character 1,700 of a ≈ 3,400-character page built from the `safe.html` and `idiom.html` demo pages. A detection is a page score ≥ 0.5.

| Text pipeline | Buried harmful found | Buried hard negatives flagged | Page without the sentence | Time per page |
|---|---|---|---|---|
| v3.5: 4 chunks × 900 chars, page-level context and fusion | 14 / 48 | 0 / 24 | 0.00 | 8.3 s |
| **v3.6: 300-char windows, each fused on its own text** | **31 / 48** (all in the right category) | **1 / 24** | 0.39 | 9.7 s |
| Rejected: one-sentence windows | 48 / 48 | 24 / 24 | 0.64 (would flag the page) | 15.6 s |
| Same 48 sentences on their own (upper bound) | 43 / 48 | — | — | — |

v3.6 window scoring leaves every held-out result unchanged (each row is one window): ensemble 0.900 / 0.895 / 92.2 % / 8.6 %, re-measured 2026-11-15. One-sentence windows find everything but judge each idiom on the filler page in isolation, so every page was flagged; they were rejected. The 17 v3.6 misses are mostly implied threats and short self-harm statements that are still diluted by the ≈ 200 characters of ordinary text sharing their window.

## Pre-access fast pass (v3.6)

While the extension's "checking" cover is up, the page text gets a fast first pass: the opening three windows (≈ 900 characters) through the lexicon and the three quick classifiers, without the NLI engine. It can block or flag at once; the full check (all engines, whole extract) follows and can still block or flag. A fast-pass flag is shown but not recorded, so history holds only flags the full ensemble confirms. A fast-pass block is recorded.

On the held-out set the fast pass finds half of the harmful rows (row kinds: direct threats 60 %, online threats 40 %, ideation 90 %, self-injury 90 %, **graphic descriptions 0 %**) with 1.4 % false positives. Graphic descriptions are caught only by the NLI engine, i.e. by the full check, a few seconds after the page is shown.

## Sensitivity levels (v3.7, 2026-11-15)

Full ensemble on the held-out set (90 harmful, 70 harmless rows), counted at each level's thresholds. "Caught" = hidden from the child (flagged or blocked).

| Level | Flag / block from | Harmful caught | Harmful blocked | Harmless wrongly hidden | Harmless blocked |
|---|---|---|---|---|---|
| 1 | 0.90 / 0.98 | 74 % | 48 % | 1 % | 1 % |
| 2 | 0.85 / 0.95 | 77 % | 62 % | 1 % | 1 % |
| 3 | 0.75 / 0.92 | 81 % | 71 % | 3 % | 1 % |
| 4 | 0.65 / 0.88 | 90 % | 74 % | 4 % | 1 % |
| 5 | 0.55 / 0.84 | 92 % | 77 % | 7 % | 1 % |
| **6 (default)** | **0.50 / 0.80** | **92 %** | **80 %** | **9 %** | **1 %** |
| 7 | 0.45 / 0.70 | 96 % | 81 % | 10 % | 3 % |
| 8 | 0.40 / 0.60 | 96 % | 90 % | 13 % | 6 % |
| 9 | 0.35 / 0.50 | 97 % | 92 % | 14 % | 9 % |
| 10 | 0.30 / 0.40 | 98 % | 96 % | 14 % | 13 % |

Short single sentences only; long pages score lower (buried-sentence test above). The dashboard shows these figures next to the slider.

## Search queries (v3.7)

On a search results page the query is checked on its own with every engine (also during the fast pass). Spot checks, full ensemble: "gore" 0.66, "gore videos" 0.62, "real gore pics" 0.66, "dead body photos" 0.69, "beheading video" 0.90 (Graphic Violence); "how to kill myself" 0.98, "best way to cut myself" 0.95 (Self-harm); "cute puppies", "history of world war 2" 0.00. Known false hit: "minecraft how to kill the ender dragon" 0.64 (Graphic Violence, from the NLI engine). "Al Gore" and "Gore-Tex" were excluded from the lexicon's gore rule (0.31 before; no evaluation row contains either). The quick engines alone scored "gore" 0.31 and "dead body photos" 0.00, which is why the query always gets the full ensemble. In the browser test a "gore" search page was replaced by the MORVIX screen 1.7 s after loading (query check 1.3 s), before any result was shown.

## Speed (Intel Core i5-13420H laptop, CPU only, after warm-up)

Measured 2026-11-15 on a busier machine than the November 11 figures (a short sentence took 1.0 s instead of 0.4–0.6 s), so compare rows within a column rather than with older numbers.

| Input | Time |
|---|---|
| Short sentence, full ensemble | ≈ 1.0 s (0.4–0.6 s on 2026-11-11) |
| Short sentence, fast pass | ≈ 0.3–0.4 s |
| Opening ≈ 800 characters, fast pass (what the cover waits for) | ≈ 0.5 s in the browser test, 1.6 s on the busier machine |
| Full 3,400-character page, 12 windows × 5 engines | ≈ 9.7 s in the buried test; 17 s on the busier machine |
| One page image (CLIP + violence ViT), warm | ≈ 0.4 s |

Browser check on the demo pages (extension script injected, local server): the cover was up for 0.7 s; an added image stayed blurred until its verdict and was then shown; the threat page was blocked by the full check at 6.3 s; the graphic-violence page was flagged by the full check at 6.8 s.

**Under load (v3.7.2):** in a real Chrome session the server handled up to 40 thumbnail checks per tab, full page checks and other tabs' checks all at once, and page-text verdicts came back after 45–47 s. With one-job-at-a-time priority scheduling (docs/ARCHITECTURE.md), the cover's check (fast page pass + search query "beheaded" / "cute puppies" / "suicide") answered in 2.64 s, 2.45 s and 2.17 s while 40 image checks and a full page check were running; the background work itself finished in 23–26 s.

Earlier measurements (v3.5, 4 chunks): full 3,500-character page ≈ 9–10 s while other apps used 25–50 % CPU.

Per engine on a full page: NLI 5.7 s, KoalaAI 1.2 s, suicidality 1.0 s, toxic-bert 0.9 s, lexicon < 0.01 s. An earlier figure of ≈ 2.2 s per page could not be reproduced and has been withdrawn. Running the engines in parallel gives no gain on this CPU because each one already uses all cores. In v3.5 the NLI engine runs all chunk × hypothesis pairs in one batch (identical scores, max difference 2 × 10⁻⁶), which made short texts about 2.3× faster but full pages only about 7 % faster. **A full page therefore does not meet NFR-PERF-02 (≈ 2 s) on this hardware.** Since v3.6 the child-facing wait is the fast pass (well under 2 s); the full check runs after the page is shown. Per-window scores are cached in memory by SHA-256 digest (no page text), so the full check does not re-score windows the fast pass already scored. Options, each needing a new evaluation: a smaller NLI model, fewer NLI hypotheses, or ONNX Runtime.

## Next steps for the team

1. Add real, consented examples (e.g. public forum posts with personal data removed) and Filipino/Taglish text if the users browse in those languages.
2. Have two team members label each row independently and report agreement.
3. Build image and video sets the same way, using a dataset whose license allows the use, and run `evaluate.py` with `truth,prediction` rows.
