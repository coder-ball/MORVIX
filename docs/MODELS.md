# Model inventory (MORVIX v3)

Every engine runs locally from `./models`. With `MORVIX_LOCAL_ONLY=1` the backend sets `HF_HUB_OFFLINE=1` and never contacts a hosted service (FR-ML-06, NI-02). Text engines run in parallel and are combined by `fuse_text`: the maximum per category, plus three self-harm arbitration rules chosen on the dev set. Every result returns the engine that decided (`decided_by`), every engine's scores (`engines`) and the rules that fired (`fusion_notes`).

**Measured text performance (held-out set, 160 rows): accuracy 0.900, macro-F1 0.895, 92 % of harmful rows detected, 8.6 % false positives. For comparison, the v2 lexicon alone scores 0.525 / 0.373.** See [EVALUATION.md](EVALUATION.md) for the datasets, method, errors and limitations.

## Why the models were changed (v2 → v3)

| Problem in v2 | Fix in v3 |
|---|---|
| The transformer engines never ran. `pipeline(..., local_files_only=...)` raised a `TypeError` that was silently swallowed, so only the lexicon was active. | Weights load from `./models` in offline mode. Failures are reported in `/classifiers/status → failed_engines`. |
| `unitary/toxic-bert` covers only *threat*. Nothing modelled self-harm or graphic violence in text. | Added `KoalaAI/Text-Moderation` (violence, graphic violence, self-harm) and `sentinet/suicidality` (self-harm). |
| CLIP with five fixed prompts forced every image into one of four harmful classes or a single benign class. | Prompt ensemble with 11 harmful and 16 benign prompts, plus a dedicated ViT violence classifier. |
| Curly apostrophes were not normalised (a mis-encoded string in the source), so `I’ll kill you` could be missed. | Fixed: real `’` / `‘` handling. |
| The violence checkpoint (see below) silently loaded random weights. | timm → transformers key conversion at load time, with a strict check. |

## Engines

| Modality | Engine | Labels used → MORVIX category | Size | License | Notes |
|---|---|---|---|---|---|
| Text | `morvix-lexicon-v1` (project code) | phrase rules → all three | – | project | Negation ("I would never…") and context damping (news, fiction, prevention ×0.6). Always on. |
| Text | [`MoritzLaurer/deberta-v3-base-zeroshot-v2.0`](https://huggingface.co/MoritzLaurer/deberta-v3-base-zeroshot-v2.0) | NLI entailment of: "The author is threatening or intimidating another person." → Threat; "The author wants to die or no longer wants to live." → Self-harm; "…a body being mutilated, wounded or killed in graphic detail." (and "…gory, bloody or graphic violence." ×0.7) → Graphic Violence | 380 MB | MIT | Added in v3.1 after the dev-set evaluation. The strongest single engine (held-out accuracy 0.825); it covers the graphic-violence and implied-threat gaps of the other models. |
| Text | [`KoalaAI/Text-Moderation`](https://huggingface.co/KoalaAI/Text-Moderation) | `V2` → Graphic Violence, `SH` → Self-harm, `V` ×0.85 / `H2` ×0.7 → Threat | 557 MB | OpenRAIL | DeBERTa, trained on an OpenAI-moderation-style taxonomy. |
| Text | [`sentinet/suicidality`](https://huggingface.co/sentinet/suicidality) | `LABEL_1` (suicidal) → Self-harm | 438 MB | CC0-1.0 | ELECTRA. Its model card reports 0.939 validation accuracy on its own Reddit/Twitter data, which is **not** a MORVIX measurement. |
| Text | [`unitary/toxic-bert`](https://huggingface.co/unitary/toxic-bert) | `threat` → Threat Language | 438 MB | Apache-2.0 | Jigsaw toxic-comment model. Profanity can raise scores. |
| Image / video | [`jaranohaal/vit-base-violence-detection`](https://huggingface.co/jaranohaal/vit-base-violence-detection) | `LABEL_1` (violence) → Graphic Violence | 343 MB | Apache-2.0 | ViT-B/16 fine-tuned on *Real Life Violence Situations* video frames. The published `model.safetensors` uses timm key names; MORVIX converts them (`_timm_to_hf_vit`). The repo has no label names; the ImageFolder order (NonViolence = 0, Violence = 1) is assumed. Override with `MORVIX_VIOLENCE_LABEL=LABEL_0` if your evaluation shows the direction is reversed. |
| Image / video | [`openai/clip-vit-base-patch32`](https://huggingface.co/openai/clip-vit-base-patch32) | prompt ensemble → all three | 605 MB | MIT | Zero-shot. Softmax over all prompts, summed per category. |
| Audio | `faster-whisper` `tiny` | transcript → text ensemble | 75 MB | MIT | Optional and off by default. Only the transcript length is returned; the transcript itself is never stored. |

## Pre-processing and guards

- **Text:** NFKC normalisation and apostrophe unification. The page prefix (3,500 chars from the extension) is split into ≤ 14 sentence-packed windows of ≤ 300 chars. Every window is scored and fused on its own text, and the page takes the strongest window, so a harmful sentence buried in a long page is judged on its own instead of being diluted (see the buried-sentence test in EVALUATION.md).
- **Context damping:** on pages that read as news, fiction or prevention material, model scores are multiplied by **0.5** and lexicon scores by 0.6. Measured: a crisis-hotline sentence fell from 0.99 to below the 0.5 flag threshold.
- **Images:** decoding errors return 413 and are never treated as safe. Images over 20 MP are rejected. Near-uniform images (mean RGB std-dev < 12) skip the models, because a flat colour scored 0.73 "violence" in testing. Images with no photographic detail skip the ViT violence model (`image_has_structure`): colour gradients scored 0.45–0.83 and random noise 0.66 "violence", while 18 real photos scored 0.05–0.23. CLIP still checks those images. Gradients and blank images have a mean local pixel change below 0.4, while real photos measured 0.7–7.1; random noise has more than 45 % strong edges and a change above 14. Steep full-range gradients (e.g. black to white) reach a change of about 0.5 and slipped through: one was flagged Graphic Violence at 0.89. Since 2026-11-11 the guard also requires fine detail (mean difference from a 2-px Gaussian blur) of at least 1.2: smooth linear, radial and cosine gradients measured 0.00–0.98 after JPEG, 64 real photos and screenshots 1.54–32. Flat-colour drawings with little texture can also fall below 1.2 and are then checked by CLIP only. The extension re-encodes GIF/AVIF/SVG and large images as ≤ 1024 px JPEG.
- **Video:** the extension sends a frame every 5 s while playing (batches of 3). Uploads are sampled every 3 s (≤ 60 frames, ≤ 3 min). The result is the highest non-benign frame above the flag threshold.

## Measured on this machine (CPU, after warm-up)

| Operation | Time |
|---|---|
| Text, 3,500-char page through 5 engines | ~10 s on a busy laptop CPU (short sentence ~0.5–1 s); runs after the page is shown; see EVALUATION.md |
| Text fast pass (opening ~900 chars, no NLI) | ~0.5 s warm; this is what the pre-access cover waits for |
| One page image (CLIP + violence ViT), warm | ~0.4 s; the image stays blurred until then |
| Audio clip (5 s speech) through faster-whisper + text engines | ~3–6 s |
| Image through ViT + CLIP | ~1.3 s |
| 12 s video upload (4 frames) | ~1.2 s |
| Cold model load without warm-up | ~10–12 s (hence the start-up warm-up, `MORVIX_WARMUP=1`) |

Sanity checks (`tests/test_models.py`) confirm that each engine loads and maps labels, that benign text scores low, and that clear self-harm and threat sentences score ≥ 0.5.

## Measuring accuracy (FR-DASH-08)

Measured text results are in [EVALUATION.md](EVALUATION.md). Image, video and audio are not evaluated yet. To add a dataset, label a held-out CSV (`truth,text`, where `truth` is a category name or `None`) and run:

```powershell
.\.venv\Scripts\python.exe scripts\evaluate.py --csv data\labeled.csv --save --dataset "team test set v1"
```

The dashboard (Dashboard → Model performance; Test classifiers → Installed engines) shows accuracy and macro-F1 only after such a run. Report class support, and evaluate each modality separately.

## Swapping models

Set `MORVIX_TEXT_MODELS` (comma list), `MORVIX_VIOLENCE_MODEL` or `MORVIX_IMAGE_MODEL` in `.env`. A text engine needs an entry in `TEXT_ENGINES` (in `backend/classifiers.py`) mapping its labels to MORVIX categories. Nothing in the API or dashboard changes (NFR-MNT-02).
