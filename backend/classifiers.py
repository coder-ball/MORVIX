"""MORVIX local classification layer (SRS API-02, NFR-MNT-02, FR-ML-01..08).

Everything here runs on the local machine. No external moderation API is called
(FR-ML-06, NI-02, BR-07). The backend only talks to this module through
``classify_text`` / ``classify_image`` / ``transcribe_audio`` / ``status`` /
``engine_inventory`` so a model can be swapped without touching the API or the
dashboard.

Text pipeline (ensemble, per-category maximum)
----------------------------------------------
* ``morvix-lexicon-v1``          always available. Unicode normalisation, weighted
  phrase rules, negation and context handling for the three categories.
* ``KoalaAI/Text-Moderation``    DeBERTa moderation classifier (labels V, V2, SH,
  H2 ...). V2 -> Graphic Violence, SH -> Self-harm Indicators, V/H2 -> Threat
  Language (discounted because "violence" is broader than a threat).
* ``sentinet/suicidality``       ELECTRA suicidality classifier -> Self-harm.
* ``unitary/toxic-bert``         toxic-comment classifier; ``threat`` -> Threat.

Image / video pipeline
----------------------
* ``jaranohaal/vit-base-violence-detection``  ViT fine-tuned on real-life
  violence video frames -> Graphic Violence (physical violence).
* ``openai/clip-vit-base-patch32``  zero-shot CLIP with a prompt ensemble that
  includes many benign prompts -> Graphic Violence, Self-harm, Threat (weapon).
* Video = sampled frames through the image pipeline (aggregate_frames).

Audio (optional)
----------------
* ``faster-whisper`` local speech-to-text; transcript goes through the text pipeline.

Each optional engine is used only when its libraries and local weights exist.
Scores are model outputs, not calibrated probabilities; MORVIX makes no accuracy
claim unless scripts/evaluate.py has measured one (FR-DASH-08).
"""
from __future__ import annotations

import hashlib
import heapq
import importlib.util
import io
import math
import os
import re
import threading
import unicodedata
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from pathlib import Path

CATEGORIES = ["Graphic Violence", "Threat Language", "Self-harm Indicators"]
LEXICON_ID = "morvix-lexicon-v1"
MAX_IMAGE_PIXELS = 20_000_000
ROOT = Path(__file__).resolve().parents[1]
LOCAL_ONLY = lambda: os.getenv("MORVIX_LOCAL_ONLY", "1") == "1"  # noqa: E731


def _env_list(name: str, default: str) -> list[str]:
    return [x.strip() for x in os.getenv(name, default).split(",") if x.strip()]


# Text engines: label -> [(category, weight)]
TEXT_ENGINES = {
    "MoritzLaurer/deberta-v3-base-zeroshot-v2.0": {
        "kind": "nli",
        # Plain-language hypotheses scored as entailment probability; category score = max(weight * p).
        # Chosen on the dev set data/eval/text_seed_v1.csv (see docs/EVALUATION.md).
        "hypotheses": {
            "Threat Language": [("The author is threatening or intimidating another person.", 1.0)],
            "Self-harm Indicators": [("The author wants to die or no longer wants to live.", 1.0)],
            "Graphic Violence": [("This text describes a body being mutilated, wounded or killed in graphic detail.", 1.0),
                                 ("This text describes gory, bloody or graphic violence.", 0.7)],
        },
        "description": "DeBERTa-v3 zero-shot NLI classifier; scores one plain-language hypothesis per MORVIX category.",
        "license": "MIT",
    },
    "KoalaAI/Text-Moderation": {
        "labels": {"V2": [("Graphic Violence", 1.0)], "SH": [("Self-harm Indicators", 1.0)],
                   "V": [("Threat Language", 0.85)], "H2": [("Threat Language", 0.7)]},
        "description": "DeBERTa text-moderation classifier (violence, graphic violence, self-harm, hate/threatening).",
        "license": "OpenRAIL",
    },
    "sentinet/suicidality": {
        "labels": {"LABEL_1": [("Self-harm Indicators", 1.0)]},
        "description": "ELECTRA classifier for suicidality / suicidal ideation in short text.",
        "license": "CC0-1.0",
    },
    "unitary/toxic-bert": {
        "labels": {"threat": [("Threat Language", 1.0)]},
        "description": "BERT toxic-comment classifier; only its 'threat' output is used.",
        "license": "Apache-2.0",
    },
}
TEXT_MODELS = _env_list("MORVIX_TEXT_MODELS", ",".join(TEXT_ENGINES))
VIOLENCE_MODEL = os.getenv("MORVIX_VIOLENCE_MODEL", "jaranohaal/vit-base-violence-detection")
IMAGE_MODEL = os.getenv("MORVIX_IMAGE_MODEL", "openai/clip-vit-base-patch32")
AUDIO_MODEL = os.getenv("MORVIX_AUDIO_MODEL", "tiny")
# Applied to model scores when the page is clearly news / fiction / prevention material. Measured on
# a crisis-hotline sentence the suicidality model gives ~0.99; 0.5 keeps it below the default
# flag threshold, while a direct first-person statement on such a page still reaches it via the lexicon.
ML_DAMPEN = 0.5
MIN_IMAGE_STDDEV = 12  # near-uniform images (blank banners, solid fills) carry no content to classify


class ModelUnavailable(Exception):
    """Raised when an optional local model cannot be used."""


class InputRejected(Exception):
    """Raised for input that is valid HTTP but cannot be processed."""


# --------------------------------------------------------------------------- lexicon
_P = lambda pattern, weight: (re.compile(pattern, re.I), weight)  # noqa: E731

PATTERNS = {
    "Threat Language": [
        _P(r"\b(i(?:'| a)?m going to|i(?:'| wi)ll|i am gonna|im gonna|gonna|i'm gonna|i will|going to)\s+(?:\w+\s+){0,2}(?:kill|murder|shoot|stab|strangle|destroy|hurt|beat)\s+(?:you|u|him|her|them|your)\b", 1.3),
        _P(r"\b(?:kill|murder|shoot|stab|strangle)\s+(?:you|u)\b", 0.9),
        _P(r"\b(?:you(?:'re| are)|ur)\s+(?:going to be\s+)?(?:dead|gonna die)\b", 1.0),
        _P(r"\byou(?:'| wi)?ll\s+(?:die|regret|pay for)\b", 0.9),
        _P(r"\bi know where you (?:live|sleep|work|go to school)\b", 1.2),
        _P(r"\bwatch your back\b", 0.7),
        _P(r"\bmake you pay\b", 0.7),
        _P(r"\b(?:i(?:'| wi)?ll|going to|gonna)\s+(?:find|hunt)\s+you\b", 1.0),
        _P(r"\b(?:bomb|shooting) threat\b", 0.9),
        _P(r"\b(?:blow up|burn down|shoot up)\s+(?:the|your|this)\b", 1.0),
        _P(r"\bbeat you (?:up|to death)\b", 1.0),
        _P(r"\bcome after you\b", 0.7),
        _P(r"\byou (?:deserve|should) (?:to )?die\b", 1.1),
    ],
    "Self-harm Indicators": [
        _P(r"\b(?:kill|hurt|harm|cut|injure)\s+(?:myself|my ?self)\b", 1.2),
        _P(r"\bi (?:want|wanna|need|plan) to (?:die|disappear forever|end it all)\b", 1.2),
        _P(r"\bend (?:my|this) (?:own )?life\b", 1.2),
        _P(r"\btake my own life\b", 1.3),
        _P(r"\bsuicid(?:e|al)\b", 0.6),
        _P(r"\bself[- ]?harm(?:ing)?\b", 0.7),
        _P(r"\bno reason to (?:live|go on|be alive)\b", 1.0),
        _P(r"\b(?:better|world is better) off (?:dead|without me)\b", 1.1),
        _P(r"\bdon'?t want to (?:be alive|live anymore|exist)\b", 1.1),
        _P(r"\b(?:overdose on|overdosing on)\b", 0.8),
        _P(r"\bcutting (?:myself|my wrists|my arms)\b", 1.2),
        _P(r"\bhow to (?:commit suicide|kill yourself|end your life)\b", 1.4),
    ],
    "Graphic Violence": [
        _P(r"\b(?:decapitat\w*|dismember\w*|disembowel\w*|mutilat\w*|eviscerat\w*|beheading\w*)\b", 1.0),
        _P(r"(?<!al )\bgor(?:e|y)\b(?!-?tex)", 0.6),  # not "Al Gore" or "Gore-Tex"
        _P(r"\bgruesome\b", 0.5),
        _P(r"\bbloodbath\b", 0.9),
        _P(r"\bblood (?:splatter|spatter|everywhere|pooled|gushed)\b", 0.9),
        _P(r"\b(?:brutally|savagely)\s+(?:murdered|killed|beaten|stabbed|attacked)\b", 1.2),
        _P(r"\bstabbed (?:\w+\s+){0,2}to death\b", 1.1),
        _P(r"\bbeaten to death\b", 1.1),
        _P(r"\bmass (?:shooting|murder|killing|stabbing)\b", 0.8),
        _P(r"\bmassacre\b", 0.8),
        _P(r"\b(?:torture|tortured|torturing)\b", 0.6),
        _P(r"\bdismembered (?:body|corpse)\b", 1.2),
        _P(r"\b(?:mangled|charred|severed)\s+(?:body|bodies|corpse|limb|head|remains)\b", 1.1),
        _P(r"\bgraphic (?:violence|footage|content)\b", 0.7),
    ],
}

NEGATION = re.compile(
    r"\b(?:not|never|no|don'?t|do not|didn'?t|won'?t|wouldn'?t|would never|can'?t|cannot|"
    r"how to stop|how to prevent|prevent(?:ing)?|stop(?:ping)?|against)\b[^.!?\n]{0,18}$", re.I)
CONTEXT_DAMPEN = re.compile(
    r"\b(?:prevention|hotline|helpline|lifeline|awareness|support group|counsel(?:l)?ing|"
    r"video game|movie|film|novel|fiction|lyrics|documentary|news report|history lesson)\b", re.I)


def normalise(text: str) -> str:
    """Unicode-normalise, lowercase, unify apostrophes and collapse whitespace."""
    text = unicodedata.normalize("NFKC", text).lower()
    text = text.replace("’", "'").replace("‘", "'").replace("ʼ", "'").replace("`", "'")
    text = re.sub(r"(.)\1{3,}", r"\1\1", text)  # coooool -> cool
    return re.sub(r"\s+", " ", text).strip()


def lexicon_scores(text: str, contextual: bool | None = None) -> tuple[dict, int, bool]:
    """Per-category rule strength in [0,1), total rule hits, and whether context damping applied.
    ``contextual`` overrides the context check (used when a neighbouring window carries the context)."""
    clean = normalise(text)
    if contextual is None:
        contextual = bool(CONTEXT_DAMPEN.search(clean))
    damp = 0.6 if contextual else 1.0
    scores, total_hits = {}, 0
    for category, rules in PATTERNS.items():
        score = hits = 0
        for rx, weight in rules:
            for m in rx.finditer(clean):
                if NEGATION.search(clean[max(0, m.start() - 22):m.start()]):
                    continue
                score += weight
                hits += 1
        scores[category] = round(1 - math.exp(-1.6 * score * damp), 4) if score else 0.0
        total_hits += hits
    return scores, total_hits, contextual


def _result(scores: dict, model: str, note: str, **extra) -> dict:
    category, confidence = max(scores.items(), key=lambda kv: kv[1]) if scores else (None, 0.0)
    benign = confidence < 0.2
    return {"category": None if benign else category, "confidence": 0.0 if benign else round(float(confidence), 4),
            "benign": benign, "model": model, "scores": {k: round(float(v), 4) for k, v in scores.items()}, "note": note, **extra}


def lexicon_classify(text: str) -> dict:
    scores, hits, _ = lexicon_scores(text)
    return _result(scores, LEXICON_ID, "Rule-based local classifier; scores are rule strengths, not calibrated probabilities.", matches=hits)


# ------------------------------------------------------------- optional model plumbing
def _hub_dir() -> Path:
    if os.getenv("HUGGINGFACE_HUB_CACHE"):
        return Path(os.environ["HUGGINGFACE_HUB_CACHE"])
    if os.getenv("HF_HOME"):
        return Path(os.environ["HF_HOME"]) / "hub"
    return Path.home() / ".cache" / "huggingface" / "hub"


def local_dir(repo: str) -> Path:
    return ROOT / "models" / repo.split("/")[-1]


def resolve(repo: str) -> str:
    """Prefer a weights folder under ./models (works on Windows without symlink rights)."""
    local = local_dir(repo)
    return str(local) if (local / "config.json").exists() else repo


def _cached(repo: str) -> bool:
    if resolve(repo) != repo:
        return True
    return (_hub_dir() / ("models--" + repo.replace("/", "--"))).exists()


def _libs(*names: str) -> bool:
    return all(importlib.util.find_spec(n) is not None for n in names)


def text_ml_enabled() -> bool:
    return os.getenv("MORVIX_TEXT_ML", "1") == "1" and _libs("torch", "transformers")


def text_engines_ready() -> list[str]:
    return [m for m in TEXT_MODELS if m in TEXT_ENGINES and text_ml_enabled() and _cached(m)]


def text_ml_ready() -> bool:
    return bool(text_engines_ready())


def violence_ready() -> bool:
    return os.getenv("MORVIX_IMAGE_ML", "1") == "1" and _libs("PIL", "torch", "transformers") and _cached(VIOLENCE_MODEL)


def clip_ready() -> bool:
    return os.getenv("MORVIX_IMAGE_ML", "1") == "1" and _libs("PIL", "torch", "transformers") and _cached(IMAGE_MODEL)


def image_engines_ready() -> list[str]:
    return [m for m, ok in ((VIOLENCE_MODEL, violence_ready()), (IMAGE_MODEL, clip_ready())) if ok]


def image_ready() -> bool:
    return bool(image_engines_ready())


def video_ready() -> bool:
    return image_ready() and _libs("cv2")


def audio_ready() -> bool:
    return _libs("faster_whisper")


_cache: dict = {}
_lock = threading.RLock()
_engine_locks: dict[str, threading.Lock] = {}
_pool = ThreadPoolExecutor(max_workers=6, thread_name_prefix="morvix-engine")
_failed: dict[str, str] = {}


# ---------------------------------------------------------------- inference scheduling
# Every model call already uses all CPU cores, so requests running side by side only slow each other
# down (measured 2026-11-15 in Chrome: page-text verdicts took 45-47 s while a tab sent ~40 thumbnails,
# so the pre-access cover gave up before its answer arrived). Model work therefore runs one job at a
# time, lowest priority number first, FIFO within a priority:
PRIORITY_FAST = 0   # the pre-access fast pass and search-query checks (what the child is waiting for)
PRIORITY_NORMAL = 1  # images, video frames, the full text pass (in slices), audio, warm-up
FULL_PASS_SLICE = 2  # windows per turn in the full pass, so a fast check waits for one slice at most (4 measured ~4 s)


class _InferenceGate:
    """One model job at a time, by priority. Re-entrant for the thread holding it, so a caller can
    hold one turn across several checks (the fast page pass plus the search query)."""

    def __init__(self):
        self._cv = threading.Condition()
        self._busy = False
        self._queue: list[tuple[int, int]] = []
        self._seq = 0
        self._owner = None

    @contextmanager
    def __call__(self, priority: int):
        if self._owner == threading.get_ident():
            yield  # already holding the turn
            return
        with self._cv:
            self._seq += 1
            ticket = (priority, self._seq)
            heapq.heappush(self._queue, ticket)
            while self._busy or self._queue[0] != ticket:
                self._cv.wait()
            heapq.heappop(self._queue)
            self._busy = True
            self._owner = threading.get_ident()
        try:
            yield
        finally:
            with self._cv:
                self._busy = False
                self._owner = None
                self._cv.notify_all()


inference = _InferenceGate()


def _offline():
    """MORVIX_LOCAL_ONLY=1: forbid any Hugging Face network lookup at runtime (NI-02)."""
    if LOCAL_ONLY():
        os.environ["HF_HUB_OFFLINE"] = "1"
        os.environ["TRANSFORMERS_OFFLINE"] = "1"


def _hf_pipeline(key, task, model, **kw):
    with _lock:
        if key not in _cache:
            _offline()
            from transformers import pipeline
            _cache[key] = pipeline(task, model=resolve(model), **kw)
        return _cache[key]


def engine_inventory() -> list[dict]:
    """Model metadata entity (SRS section 5): every engine, its modality and readiness."""
    ready_text = set(text_engines_ready())
    rows = [{"name": LEXICON_ID, "version": "1.1", "modality": "text", "ready": True, "license": "Project code",
             "description": "Rule-based NLP text classifier (normalisation, phrase rules, negation/context handling)."}]
    for name in TEXT_MODELS:
        if name in TEXT_ENGINES:
            meta = TEXT_ENGINES[name]
            rows.append({"name": name, "version": "local", "modality": "text", "ready": name in ready_text and name not in _failed,
                         "license": meta["license"], "description": meta["description"]})
    rows.append({"name": VIOLENCE_MODEL, "version": "local", "modality": "image", "ready": violence_ready() and VIOLENCE_MODEL not in _failed,
                 "license": "Apache-2.0", "description": "ViT image classifier fine-tuned on real-life violence video frames (also used on video frames)."})
    rows.append({"name": IMAGE_MODEL, "version": "local", "modality": "image", "ready": clip_ready() and IMAGE_MODEL not in _failed,
                 "license": "MIT", "description": "CLIP zero-shot prompt ensemble for gore, self-harm and weapon-threat imagery (also used on video frames)."})
    rows.append({"name": "faster-whisper:" + AUDIO_MODEL, "version": "local", "modality": "audio", "ready": audio_ready(),
                 "license": "MIT", "description": "Optional local speech-to-text; transcript is classified by the text pipeline."})
    return rows


def status() -> dict:
    text_engines = [LEXICON_ID] + [m for m in text_engines_ready() if m not in _failed]
    img = [m for m in image_engines_ready() if m not in _failed]
    return {
        "failed_engines": dict(_failed),
        "text": {"ready": True, "engines": text_engines, "model": " + ".join(text_engines), "reason": None},
        "image": {"ready": bool(img), "engines": img, "model": " + ".join(img) or VIOLENCE_MODEL + " + " + IMAGE_MODEL,
                  "reason": None if img else "Install requirements-ml.txt and run scripts/download_models.py to enable local image classification."},
        "video": {"ready": bool(img) and _libs("cv2"), "engines": img, "model": (" + ".join(img) or "image models") + " (sampled frames)",
                  "reason": None if (img and _libs("cv2")) else "Needs a local image model plus OpenCV (requirements-ml.txt)."},
        "audio": {"ready": audio_ready(), "engines": ["faster-whisper:" + AUDIO_MODEL] if audio_ready() else [], "model": "faster-whisper:" + AUDIO_MODEL + " + text pipeline",
                  "reason": None if audio_ready() else "Optional. pip install faster-whisper to enable local audio transcription."},
    }


# --------------------------------------------------------------------------- text
# A harmful sentence inside a long page is judged in a short window of its own, not diluted by the
# surrounding text. Buried-sentence test (docs/EVALUATION.md): 900-char chunks with page-level context
# found far fewer buried sentences than the same sentences on their own. 14 x 300 covers the
# extension's 3,500-character page extract.
WINDOW_CHARS = 300
MAX_WINDOWS = 14


def text_chunks(text: str, size: int = WINDOW_CHARS, limit: int = MAX_WINDOWS, min_chars: int | None = None) -> list[str]:
    """Split page text into sentence-packed windows of at most ``size`` characters.
    A window is closed once it holds ``min_chars`` characters (default: pack up to ``size``)."""
    min_chars = size if min_chars is None else min_chars
    out, cur = [], ""
    for sent in re.split(r"(?<=[.!?\n])\s+", text.strip()):
        sent = sent.strip()
        while len(sent) > size:
            out.append(sent[:size])
            sent = sent[size:]
        if not sent:
            continue
        if cur and (len(cur) >= min_chars or len(cur) + len(sent) + 1 > size):
            out.append(cur)
            cur = sent
        else:
            cur = (cur + " " + sent).strip()
        if len(out) >= limit:
            break
    if cur and len(out) < limit:
        out.append(cur)
    return out[:limit] or [text[:size]]


_window_cache: "OrderedDict[tuple[str, str], dict]" = OrderedDict()  # (engine, SHA-256 of window) -> scores
_window_cache_lock = threading.Lock()
WINDOW_CACHE_SIZE = 4000


def _engine_text_scores(name: str, chunks: list[str]) -> list[dict]:
    """One {category: score} dict per window, in input order. Scores are cached per engine and
    window, so the full pass reuses what the fast pass already computed for the opening windows
    and a page that re-renders the same text is not scored twice. The cache is in memory only and
    keyed by a SHA-256 digest, so it holds no page text (FR-HIST-05 / NFR-PRIV-02)."""
    keys = [(name, hashlib.sha256(c.encode("utf-8")).hexdigest()) for c in chunks]
    out, todo = [None] * len(chunks), []
    with _window_cache_lock:
        for i, k in enumerate(keys):
            hit = _window_cache.get(k)
            if hit is None:
                todo.append(i)
            else:
                _window_cache.move_to_end(k)
                out[i] = dict(hit)
    if todo:
        fresh = _score_windows(name, [chunks[i] for i in todo])
        with _window_cache_lock:
            for i, s in zip(todo, fresh):
                out[i] = s
                _window_cache[keys[i]] = dict(s)
            while len(_window_cache) > WINDOW_CACHE_SIZE:
                _window_cache.popitem(last=False)
    return out


def _score_windows(name: str, chunks: list[str]) -> list[dict]:
    """Run one engine over the windows (no caching)."""
    meta = TEXT_ENGINES[name]
    if meta.get("kind") == "nli":
        # Same computation as the zero-shot pipeline with multi_label=True (entailment vs. contradiction
        # softmax per chunk/hypothesis pair), but every pair goes through the model in ONE batch instead
        # of one forward pass per pair: identical scores (max difference 2e-6 on the dev set), 2-3x faster.
        import torch
        clf = _hf_pipeline("text:" + name, "zero-shot-classification", name)
        model, tok = clf.model, clf.tokenizer
        ent = next(i for label, i in model.config.label2id.items() if label.lower().startswith("entail"))
        con = -1 if ent == 0 else 0
        pairs = [(i, chunk, hyp, cat, w) for i, chunk in enumerate(chunks) for cat, hs in meta["hypotheses"].items() for hyp, w in hs]
        enc = tok([p[1] for p in pairs], [p[2] for p in pairs], truncation="only_first", max_length=512, padding=True, return_tensors="pt")
        with torch.no_grad():
            probs = model(**enc).logits[:, [con, ent]].softmax(-1)[:, 1].tolist()
        scores = [{c: 0.0 for c in CATEGORIES} for _ in chunks]
        for (i, _, _, cat, w), p in zip(pairs, probs):
            scores[i][cat] = max(scores[i][cat], float(p) * w)
        return scores
    clf = _hf_pipeline("text:" + name, "text-classification", name)
    outputs = clf(chunks, truncation=True, max_length=512, top_k=None, batch_size=8)
    scores = [{c: 0.0 for c in CATEGORIES} for _ in chunks]
    for i, per_chunk in enumerate(outputs):
        per_chunk = per_chunk if isinstance(per_chunk, list) else [per_chunk]
        for item in per_chunk:
            for category, weight in meta["labels"].get(item["label"], []):
                scores[i][category] = max(scores[i][category], float(item["score"]) * weight)
    return scores


NLI_ENGINE = "MoritzLaurer/deberta-v3-base-zeroshot-v2.0"
SELF_HARM_SPECIALISTS = ("sentinet/suicidality", "KoalaAI/Text-Moderation")
FIRST_PERSON = re.compile(r"\b(?:i|i'm|im|i've|ive|i'll|ill|me|my|myself|mine)\b", re.I)
OTHER_TARGET = re.compile(r"\b(?:you|your|yours|u|ur|him|her|them|his|their|everyone)\b", re.I)


def fuse_text(engines: dict, text: str) -> tuple[dict, list[str]]:
    """Combine engine scores per category (evaluated in docs/EVALUATION.md).

    * Threat and Graphic Violence: maximum over engines.
    * Self-harm: the specialist models (suicidality, KoalaAI SH) also fire on threats and gore, so
      their vote is halved when the text has no first-person reference, and halved again when the
      NLI engine clearly reads the text as a threat / graphic description directed elsewhere.
    * A strong first-person self-harm signal without another target halves Threat/Graphic scores
      (e.g. "I started cutting again" is self-harm, not graphic violence).
    * Keyword hits alone ("suicide", "self-harm", "gore") also occur in prevention and news text, so
      when ML engines are present a lexicon score that no model supports (effective vote < 0.3,
      after the rules above) is halved.
    Returns the fused scores and short notes describing which rules fired."""
    notes = []
    clean = normalise(text)
    first, other = bool(FIRST_PERSON.search(clean)), bool(OTHER_TARGET.search(clean))
    SH = "Self-harm Indicators"
    # 1. self-harm specialist vote, with its gating rules
    sh_ml = max((engines[n].get(SH, 0.0) for n in SELF_HARM_SPECIALISTS if n in engines), default=0.0)
    if sh_ml:
        if not first:
            sh_ml *= 0.5
            notes.append("self-harm specialists halved: no first-person reference")
        nli = engines.get(NLI_ENGINE)
        if nli and max(nli["Threat Language"], nli["Graphic Violence"]) >= 0.5 and nli[SH] < 0.5 and (other or not first):
            sh_ml *= 0.5
            notes.append("self-harm specialists halved: NLI reads threat/graphic content aimed elsewhere")
    # 2. effective model votes per category (specialists count only through the gated sh_ml)
    model = {c: max((s.get(c, 0.0) for n, s in engines.items() if n != LEXICON_ID and not (c == SH and n in SELF_HARM_SPECIALISTS)), default=0.0)
             for c in CATEGORIES}
    model[SH] = max(model[SH], sh_ml)
    has_models = any(n != LEXICON_ID for n in engines)
    # 3. lexicon vote, halved when no model supports it
    lex = dict(engines.get(LEXICON_ID, {}))
    if has_models:
        for c in CATEGORIES:
            if lex.get(c, 0.0) >= 0.5 and model[c] < 0.3:
                lex[c] *= 0.5
                notes.append(f"lexicon {c} halved: no model supports it")
    fused = {c: max(model[c], lex.get(c, 0.0)) for c in CATEGORIES}
    if first and not other and fused["Self-harm Indicators"] >= 0.8:
        for c in ("Graphic Violence", "Threat Language"):
            fused[c] *= 0.5
        notes.append("first-person self-harm outweighs threat/graphic readings")
    return fused, notes


FAST_WINDOWS = 3  # fast first pass: the opening ~900 characters, i.e. what the child sees first


def classify_text(text: str, fast: bool = False, priority: int | None = None) -> dict:
    """Classify text window by window (see WINDOW_CHARS).

    ``fast=True`` is the pre-access first pass used while the page is still covered: it reads only
    the first FAST_WINDOWS windows and skips the slow NLI engine. The full pass (fast=False) runs
    afterwards and can still block or flag the page.

    Each window is scored by every engine and fused on its OWN text, so first-person / target cues
    come from the sentence itself rather than from anywhere on the page. Context damping (news,
    fiction, prevention material) applies when the window or a neighbouring window carries the
    context words. The page result is the strongest window. Text of one window (a sentence or a
    short paragraph) is classified exactly as before."""
    windows = text_chunks(text)
    if fast:
        windows = windows[:FAST_WINDOWS]
    n = len(windows)
    ctx = [bool(CONTEXT_DAMPEN.search(normalise(w))) for w in windows]
    near_ctx = [any(ctx[max(0, i - 1):i + 2]) for i in range(n)]
    per_window = []  # one {engine: scores} dict per window
    hits = 0
    for w, c in zip(windows, near_ctx):
        lex, h, _ = lexicon_scores(w, contextual=c)
        per_window.append({LEXICON_ID: lex})
        hits += h
    names = [n_ for n_ in text_engines_ready() if n_ not in _failed and not (fast and n_ == NLI_ENGINE)]
    if priority is None:
        priority = PRIORITY_FAST if fast else PRIORITY_NORMAL

    def run(name, sub):
        with _engine_locks.setdefault(name, threading.Lock()):  # one request per model at a time
            return _engine_text_scores(name, sub)

    # The fast pass is one turn of the inference gate; the full pass takes FULL_PASS_SLICE windows per
    # turn so that waiting fast checks get in between. Within a turn the engines run in parallel
    # (measured ~20 % faster than sequential on an 8-thread CPU).
    step = n if (fast or priority == PRIORITY_FAST) else FULL_PASS_SLICE
    produced = set()
    for start in range(0, n, step) if names else ():
        idx = list(range(start, min(start + step, n)))
        sub = [windows[i] for i in idx]
        results = {}
        with inference(priority):
            futures = {name: _pool.submit(run, name, sub) for name in names if name not in _failed}
            for name, f in futures.items():
                try:
                    results[name] = f.result()
                except Exception as e:  # optional engine; the remaining engines still decide
                    _failed[name] = str(e)[:200]
        for name, scores in results.items():
            produced.add(name)
            for i, s in zip(idx, scores):
                if near_ctx[i]:
                    s = {k: v * ML_DAMPEN for k, v in s.items()}
                per_window[i][name] = {k: round(v, 4) for k, v in s.items()}
    used = [LEXICON_ID] + [nm for nm in names if nm in produced]
    fused_w = [fuse_text(e, w) for e, w in zip(per_window, windows)]
    fused = {c: max(f[c] for f, _ in fused_w) for c in CATEGORIES}
    best_i = max(range(n), key=lambda i: max(fused_w[i][0].values()))
    # per-engine page verdict: each category's maximum over windows (reported for transparency / evaluation)
    engines = {name: {c: max(pw[name][c] for pw in per_window if name in pw) for c in CATEGORIES} for name in used}
    result = _result(fused, " + ".join(used),
                     "Ensemble of local engines (rule-based fusion, see docs/EVALUATION.md). Scores are model outputs, not calibrated probabilities.",
                     matches=hits, engines=engines, context_dampened=near_ctx[best_i], fusion_notes=fused_w[best_i][1],
                     windows=n, evidence_window=best_i, stage="fast" if fast else "full")
    if result["category"]:
        # the engine(s) whose raw score in the deciding window is closest to the fused score decided the result
        cat, win = result["category"], per_window[best_i]
        best = min(abs(s.get(cat, 0) - fused[cat]) for s in win.values())
        result["decided_by"] = [nm for nm, s in win.items() if abs(s.get(cat, 0) - fused[cat]) - best < 1e-4]
    return result


# --------------------------------------------------------------------------- image
CLIP_PROMPTS = {
    "Graphic Violence": ["a photo of a bloody injured person", "a photo of gore with blood and wounds", "a photo of a dead body",
                         "a photo of people violently fighting", "a photo of someone being beaten"],
    "Self-harm Indicators": ["a photo of self-harm cuts on an arm", "a photo of a person cutting their wrist", "a photo of a person harming themselves"],
    "Threat Language": ["a photo of a person pointing a gun at the camera", "a photo of someone threatening another person with a knife",
                        "a photo of an armed attacker"],
    None: ["a photo of everyday life", "a photo of people smiling", "a photo of a landscape", "a screenshot of a website or app",
           "a photo of food", "a photo of an animal", "a cartoon or illustration", "a photo of a product", "a photo of a document with text",
           "a photo of people playing sports", "a photo of a classroom", "a photo of a city street", "a selfie", "a video game screenshot",
           "a photo of a red tomato sauce", "a movie poster"],
}


def _clip():
    with _lock:
        if "clip" not in _cache:
            _offline()
            import torch
            from transformers import CLIPModel, CLIPProcessor
            path = resolve(IMAGE_MODEL)
            model = CLIPModel.from_pretrained(path).eval()
            proc = CLIPProcessor.from_pretrained(path)
            prompts, owners = [], []
            for cat, ps in CLIP_PROMPTS.items():
                prompts += ps
                owners += [cat] * len(ps)
            with torch.no_grad():
                t = model.get_text_features(**proc(text=prompts, return_tensors="pt", padding=True))
                t = t / t.norm(dim=-1, keepdim=True)
            _cache["clip"] = (model, proc, t, owners)
        return _cache["clip"]


def clip_scores(img) -> dict:
    import torch
    model, proc, text_emb, owners = _clip()
    with torch.no_grad():
        i = model.get_image_features(**proc(images=img, return_tensors="pt"))
        i = i / i.norm(dim=-1, keepdim=True)
        probs = (model.logit_scale.exp() * i @ text_emb.T).softmax(dim=-1)[0].tolist()
    scores = {c: 0.0 for c in CATEGORIES}
    for p, owner in zip(probs, owners):
        if owner:
            scores[owner] += p
    return scores


def _violent_label(id2label: dict) -> set[str]:
    forced = os.getenv("MORVIX_VIOLENCE_LABEL")
    if forced:
        return {forced}
    names = {str(v) for v in id2label.values()}
    named = {n for n in names if "violen" in n.lower() and not re.search(r"non|no[ _-]?viol", n.lower())}
    return named or {"LABEL_1"}  # ImageFolder order: NonViolence=0, Violence=1


def _timm_to_hf_vit(sd: dict, layers: int, hidden: int) -> dict:
    """The published violence checkpoint stores timm ViT keys (blocks.N.attn.qkv ...). Hugging Face
    ViTForImageClassification would silently ignore them and start from random weights, so map
    them to the transformers layout (same mapping as transformers' convert_vit_timm script)."""
    out = {"vit.embeddings.cls_token": sd["cls_token"], "vit.embeddings.position_embeddings": sd["pos_embed"],
           "vit.embeddings.patch_embeddings.projection.weight": sd["patch_embed.proj.weight"],
           "vit.embeddings.patch_embeddings.projection.bias": sd["patch_embed.proj.bias"],
           "vit.layernorm.weight": sd["norm.weight"], "vit.layernorm.bias": sd["norm.bias"],
           "classifier.weight": sd["head.weight"], "classifier.bias": sd["head.bias"]}
    for i in range(layers):
        b, h = f"blocks.{i}.", f"vit.encoder.layer.{i}."
        pairs = {"norm1": "layernorm_before", "norm2": "layernorm_after", "attn.proj": "attention.output.dense",
                 "mlp.fc1": "intermediate.dense", "mlp.fc2": "output.dense"}
        for src, dst in pairs.items():
            out[h + dst + ".weight"] = sd[b + src + ".weight"]
            out[h + dst + ".bias"] = sd[b + src + ".bias"]
        for j, name in enumerate(("query", "key", "value")):
            out[h + f"attention.attention.{name}.weight"] = sd[b + "attn.qkv.weight"][j * hidden:(j + 1) * hidden]
            out[h + f"attention.attention.{name}.bias"] = sd[b + "attn.qkv.bias"][j * hidden:(j + 1) * hidden]
    return out


def _violence():
    with _lock:
        if "violence" not in _cache:
            _offline()
            import torch
            from transformers import ViTConfig, ViTForImageClassification, ViTImageProcessor
            path = resolve(VIOLENCE_MODEL)
            proc = ViTImageProcessor.from_pretrained(path)
            weights = Path(path) / "model.safetensors"
            sd = None
            if weights.exists():
                from safetensors.torch import load_file
                sd = load_file(str(weights))
            if sd is not None and "blocks.0.attn.qkv.weight" in sd:
                cfg = ViTConfig.from_pretrained(path)
                cfg.layer_norm_eps = 1e-6  # timm ViT uses eps=1e-6
                model = ViTForImageClassification(cfg)
                missing, unexpected = model.load_state_dict(_timm_to_hf_vit(sd, cfg.num_hidden_layers, cfg.hidden_size), strict=False)
                if [k for k in missing if "pooler" not in k] or unexpected:
                    raise ModelUnavailable("Violence model weights do not match the ViT layout")
            else:
                model = ViTForImageClassification.from_pretrained(path)
            model.eval()
            _cache["violence"] = (model, proc, torch)
        return _cache["violence"]


def violence_scores(img) -> dict:
    model, proc, torch = _violence()
    with torch.no_grad():
        probs = model(**proc(images=img, return_tensors="pt")).logits.softmax(-1)[0].tolist()
    violent = _violent_label(model.config.id2label)
    score = sum(p for i, p in enumerate(probs) if model.config.id2label.get(i, f"LABEL_{i}") in violent)
    return {"Graphic Violence": score, "Threat Language": 0.0, "Self-harm Indicators": 0.0}


def classify_image(data: bytes, priority: int = PRIORITY_NORMAL) -> dict:
    with inference(priority):
        return _classify_image(data)


def _classify_image(data: bytes) -> dict:
    engines_ready = [m for m in image_engines_ready() if m not in _failed]
    if not engines_ready:
        raise ModelUnavailable("Local image model is not installed (see docs/MODELS.md).")
    try:
        from PIL import Image
        img = Image.open(io.BytesIO(data))
        if img.width * img.height > MAX_IMAGE_PIXELS:
            raise InputRejected("Image dimensions exceed the processing limit")
        img = img.convert("RGB")
    except InputRejected:
        raise
    except Exception as e:
        raise InputRejected("Image could not be decoded") from e
    from PIL import ImageStat
    if sum(ImageStat.Stat(img).stddev) / 3 < MIN_IMAGE_STDDEV:
        return _result({c: 0.0 for c in CATEGORIES}, "pixel-variance guard", "Image is nearly uniform; nothing to classify.", engines={})
    fused = {c: 0.0 for c in CATEGORIES}
    engines, used = {}, []
    photographic = image_has_structure(img)
    for name, fn in ((VIOLENCE_MODEL, violence_scores), (IMAGE_MODEL, clip_scores)):
        if name not in engines_ready:
            continue
        if name == VIOLENCE_MODEL and not photographic:
            continue  # out of the ViT's training domain (real video frames): it scores 0.45-0.83 "violence" on plain gradients/noise
        try:
            s = fn(img)
        except Exception as e:
            _failed[name] = str(e)[:200]
            continue
        engines[name] = {k: round(v, 4) for k, v in s.items()}
        used.append(name)
        for k, v in s.items():
            fused[k] = max(fused[k], v)
    if not used:
        raise ModelUnavailable("Image could not be processed by the local models")
    note = "Local image engines (per-category maximum); not a validated safety classifier."
    if not photographic and VIOLENCE_MODEL in engines_ready:
        note += " Violence ViT skipped: the image has no photographic detail (gradient, blank or noise)."
    return _result(fused, " + ".join(used), note, engines=engines)


# The ViT violence model was trained on real video frames. On content-free images it is unreliable
# (measured: colour gradients 0.45-0.83, random noise 0.66, a near-blank wallpaper 0.48 "violence"),
# while real photos and screenshots measured 0.7-7.1 mean local change and strong-edge share <= 0.17.
MIN_LOCAL_CHANGE = 0.4                       # gentle gradients/blank images measured 0.0-0.1
NOISE_EDGE_SHARE, NOISE_LOCAL_CHANGE = 0.45, 14.0   # random noise measured 0.57 / 17.8
# Steep full-range gradients reach ~0.5 local change and slipped past MIN_LOCAL_CHANGE (a black-to-white
# ramp scored 0.89 "violence"). Fine detail = mean |pixel - blurred pixel|: smooth linear, radial and
# cosine gradients measured 0.00-0.98 (after JPEG), 64 real photos and screenshots 1.54-32.
MIN_FINE_DETAIL = 1.2


def image_has_structure(img) -> bool:
    import numpy as np
    from PIL import ImageFilter
    small = img.convert("L").resize((256, 256))
    g = np.asarray(small, dtype=np.float32)
    gx, gy = np.abs(np.diff(g, axis=1))[:-1, :], np.abs(np.diff(g, axis=0))[:, :-1]
    change = float(gx.mean() + gy.mean()) / 2
    edges = float(((gx > 20) | (gy > 20)).mean())
    detail = float(np.abs(g - np.asarray(small.filter(ImageFilter.GaussianBlur(2)), dtype=np.float32)).mean())
    return (change >= MIN_LOCAL_CHANGE and detail >= MIN_FINE_DETAIL
            and not (edges > NOISE_EDGE_SHARE and change > NOISE_LOCAL_CHANGE))


def aggregate_frames(results: list[dict], threshold: float) -> dict:
    """Video strategy: highest-scoring non-benign sampled frame above threshold, else highest frame."""
    hits = [x for x in results if not x.get("benign") and x["confidence"] >= threshold]
    best = dict(max(hits or results, key=lambda x: x["confidence"]))
    best["flagged_frames"] = len(hits)
    return best


def warmup() -> None:
    """Load the installed engines once in the background so the first real request meets
    NFR-PERF-02 instead of paying the model-loading cost (~10 s on CPU)."""
    def run():
        try:
            classify_text("warm up the local text engines.")
            if image_ready():
                from PIL import Image, ImageDraw
                im = Image.new("RGB", (224, 224), (120, 160, 200))
                ImageDraw.Draw(im).rectangle([40, 40, 180, 180], fill=(200, 80, 60))
                buf = io.BytesIO()
                im.save(buf, "PNG")
                classify_image(buf.getvalue())
        except Exception:
            pass  # readiness and failures are reported through status()
    threading.Thread(target=run, name="morvix-warmup", daemon=True).start()


# --------------------------------------------------------------------------- audio
def transcribe_audio(path: str) -> str:
    if not audio_ready():
        raise ModelUnavailable("Optional audio model (faster-whisper) is not installed.")
    try:
        from faster_whisper import WhisperModel
        with _lock:
            if "audio" not in _cache:
                _cache["audio"] = WhisperModel(AUDIO_MODEL, device="cpu", compute_type="int8", local_files_only=LOCAL_ONLY())
        with inference(PRIORITY_NORMAL):
            segments, _ = _cache["audio"].transcribe(path)
            return " ".join(s.text for s in segments)[:12000]
    except Exception as e:
        raise ModelUnavailable("Local audio transcription failed or weights are not cached") from e
