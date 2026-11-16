"""Smoke tests for the optional local ML engines. Each test is skipped when that engine's
weights are not in ./models. These check wiring and sanity, not accuracy (see scripts/evaluate.py)."""
import io

import pytest

from backend import classifiers as ml


@pytest.fixture(autouse=True)
def ml_enabled(monkeypatch):
    # test_api.py switches the ML engines off for deterministic API tests; switch them back on here
    monkeypatch.setenv("MORVIX_TEXT_ML", "1")
    monkeypatch.setenv("MORVIX_IMAGE_ML", "1")


def need(name, ready):
    if not ready:
        pytest.skip(f"{name} weights not installed (python scripts/download_models.py)")


@pytest.mark.parametrize("engine", list(ml.TEXT_ENGINES))
def test_text_engine_runs_and_maps_labels(engine):
    need(engine, engine in ml.text_engines_ready())
    windows = ["Here is a recipe for banana bread and some gardening tips.", "We planted tomatoes and basil in the school garden."]
    out = ml._engine_text_scores(engine, windows)
    assert len(out) == 2  # one score dict per window
    for s in out:
        assert set(s) == set(ml.CATEGORIES) and all(0 <= v <= 1 for v in s.values())
        assert max(s.values()) < 0.5, s  # benign sentences should not score high
    assert ml._engine_text_scores(engine, windows) == out  # second call is served from the score cache


def test_inference_gate_runs_fast_checks_first():
    """Model work runs one job at a time; a waiting fast check goes before waiting normal work."""
    import threading, time
    order, started = [], threading.Event()
    def hold():
        with ml.inference(ml.PRIORITY_NORMAL):
            started.set(); time.sleep(0.3)
    def job(name, prio):
        with ml.inference(prio):
            order.append(name)
    t0 = threading.Thread(target=hold); t0.start(); started.wait()
    normal = threading.Thread(target=job, args=("normal", ml.PRIORITY_NORMAL)); normal.start(); time.sleep(0.05)
    fast = threading.Thread(target=job, args=("fast", ml.PRIORITY_FAST)); fast.start()
    for t in (t0, normal, fast):
        t.join(5)
    assert order == ["fast", "normal"]
    with ml.inference(ml.PRIORITY_FAST):  # re-entrant for the holder: no deadlock
        with ml.inference(ml.PRIORITY_NORMAL):
            pass


def test_fast_pass_skips_nli_and_reads_opening_text_only():
    need("text engines", ml.text_ml_ready())
    r = ml.classify_text(_page("I am going to find you after school and I will kill you."), fast=True)
    assert r["stage"] == "fast" and ml.NLI_ENGINE not in r["engines"] and r["windows"] <= ml.FAST_WINDOWS
    opening = ml.classify_text("I am going to find you after school and I will kill you. " + _page(""), fast=True)
    assert opening["category"] == "Threat Language" and opening["confidence"] >= 0.5


def test_ensemble_scores_harmful_text_higher_than_benign():
    need("text engines", ml.text_ml_ready())
    benign = ml.classify_text("We baked cookies and walked the dog in the park this afternoon.")
    selfharm = ml.classify_text("I can't do this anymore. I just want to die, nobody would even notice if I was gone.")
    threat = ml.classify_text("Listen to me. I am going to find you after school and I will kill you.")
    assert benign["category"] is None or benign["confidence"] < 0.5
    assert selfharm["category"] == "Self-harm Indicators" and selfharm["confidence"] >= 0.5
    assert threat["category"] == "Threat Language" and threat["confidence"] >= 0.5
    assert len(selfharm["engines"]) > 1


def _png(uniform=False):
    from PIL import Image, ImageDraw
    im = Image.new("RGB", (320, 240), (135, 190, 235))
    if not uniform:
        d = ImageDraw.Draw(im)
        d.rectangle([0, 160, 320, 240], fill=(60, 160, 70))
        d.rectangle([110, 90, 210, 170], fill=(200, 160, 120))
        d.polygon([(100, 90), (160, 40), (220, 90)], fill=(150, 40, 40))
    buf = io.BytesIO()
    im.save(buf, "PNG")
    return buf.getvalue()


def test_image_engines_return_normalised_result():
    need("image engines", ml.image_ready())
    r = ml.classify_image(_png())
    assert set(r["scores"]) == set(ml.CATEGORIES) and r["engines"]
    assert all(0 <= v <= 1.0001 for v in r["scores"].values())


def test_uniform_image_is_not_sent_to_models():
    need("image engines", ml.image_ready())
    r = ml.classify_image(_png(uniform=True))
    assert r["benign"] and r["engines"] == {}


def test_prevention_context_is_dampened():
    need("text engines", ml.text_ml_ready())
    r = ml.classify_text("Suicide prevention hotline: if you are struggling, call 988. Help is available.")
    assert r["context_dampened"] and r["confidence"] < 0.5


def test_gradient_and_noise_images_skip_the_violence_vit():
    """Regression (v3.5 diagnosis): the ViT scored 0.45-0.83 'violence' on plain gradients and noise."""
    need("image engines", ml.image_ready())
    import numpy as np
    from PIL import Image
    x = np.linspace(0, 1, 640)[None, :, None]
    grad = Image.fromarray((np.array((255, 0, 0)) * (1 - x) + np.array((0, 0, 255)) * x).repeat(400, 0).astype(np.uint8))
    noise = Image.fromarray(np.random.default_rng(0).integers(0, 255, (400, 640, 3), dtype=np.uint8))
    for im in (grad, noise):
        assert not ml.image_has_structure(im)
        buf = io.BytesIO(); im.save(buf, "PNG")
        r = ml.classify_image(buf.getvalue())
        assert ml.VIOLENCE_MODEL not in r["engines"] and r["confidence"] < 0.5, r
    photo = Image.open(io.BytesIO(_png())).convert("RGB").resize((640, 480))
    rng = np.random.default_rng(1)  # add mild sensor-like texture so it resembles a photo
    photo = Image.fromarray(np.clip(np.asarray(photo, np.int16) + rng.integers(-12, 12, (480, 640, 3)), 0, 255).astype(np.uint8))
    assert ml.image_has_structure(photo)


@pytest.mark.parametrize("size", [(320, 240), (1024, 200), (200, 800), (64, 64)])
def test_steep_full_range_gradients_skip_the_violence_vit(size):
    """Regression (2026-11-11): a black-to-white ramp passed MIN_LOCAL_CHANGE and the ViT flagged it 0.89."""
    import numpy as np
    from PIL import Image
    w, h = size
    ramp = np.linspace(0, 255, w, dtype=np.float32)
    yy, xx = np.mgrid[0:h, 0:w]
    r = np.hypot(xx - w / 2, yy - h / 2)
    for a in (np.tile(ramp, (h, 1)), np.tile(np.linspace(0, 255, h, dtype=np.float32)[:, None], (1, w)), 255 * r / r.max()):
        buf = io.BytesIO(); Image.fromarray(a.astype(np.uint8)).convert("RGB").save(buf, "JPEG")
        assert not ml.image_has_structure(Image.open(io.BytesIO(buf.getvalue())).convert("RGB"))
    if ml.image_ready():
        buf = io.BytesIO(); Image.fromarray(np.tile(ramp, (h, 1)).astype(np.uint8)).convert("RGB").save(buf, "JPEG")
        res = ml.classify_image(buf.getvalue())
        assert ml.VIOLENCE_MODEL not in res["engines"] and res["confidence"] < 0.5, res


def _page(sentence):
    """~3,000 characters of ordinary text with ``sentence`` in the middle."""
    filler = ("Our class spent the morning at the science museum. We looked at fossils, planets and a model of the heart. "
              "After lunch we wrote short reports about our favourite exhibit and shared them with the group. ") * 7
    mid = len(filler) // 2
    return filler[:mid] + " " + sentence + " " + filler[mid:]


def test_harmful_sentence_buried_in_long_page_is_found():
    """Regression (2026-11-15): one harmful sentence in a long page was diluted by the surrounding text."""
    need("text engines", ml.text_ml_ready())
    for sentence, category in (("I am going to find you after school and I will kill you.", "Threat Language"),
                               ("I just want to die, nobody would even notice if I was gone.", "Self-harm Indicators")):
        r = ml.classify_text(_page(sentence))
        assert r["windows"] > 1 and r["category"] == category and r["confidence"] >= 0.5, r
    assert ml.classify_text(_page("We laughed so hard we nearly died when the volcano model erupted."))["confidence"] < 0.5


def test_prevention_context_in_neighbouring_window_still_dampens():
    """The context word may sit in the next window; the window holding the keywords must still be dampened."""
    need("text engines", ml.text_ml_ready())
    text = _page("If you or someone you know is having thoughts of suicide or self-harm, you are not alone. "
                 "Please reach out to a trusted adult or call the crisis hotline, trained counselors are available all day.")
    assert ml.classify_text(text)["confidence"] < 0.5


def test_crisis_line_text_is_not_flagged():
    """Regression (v3.5 diagnosis): keyword hits alone flagged prevention text at 0.71."""
    need("text engines", ml.text_ml_ready())
    r = ml.classify_text("If you or someone you know is having thoughts of suicide or self-harm, you are not alone. "
                         "Reach out to a trusted adult, a school counselor, or a crisis hotline. Trained counselors are available 24 hours a day.")
    assert r["confidence"] < 0.5, r
