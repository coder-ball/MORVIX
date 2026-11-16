"""Download MORVIX's local model weights into ./models (no symlinks, so it works on Windows
without admin rights). Run once while online; at runtime MORVIX loads them with
local_files_only and never calls a hosted inference API.

    python scripts/download_models.py            # all engines (~1.9 GB)
    python scripts/download_models.py --only KoalaAI/Text-Moderation
    python scripts/download_models.py --skip-audio
"""
import argparse
from pathlib import Path

from huggingface_hub import snapshot_download

ROOT = Path(__file__).resolve().parents[1]
TOKENIZER = ["config.json", "tokenizer*.json", "special_tokens_map.json", "vocab.*", "merges.txt", "preprocessor_config.json"]
MODELS = {
    # repo: (files to fetch, approx size)
    "MoritzLaurer/deberta-v3-base-zeroshot-v2.0": (TOKENIZER + ["added_tokens.json", "spm.model", "model.safetensors"], "380 MB"),
    "KoalaAI/Text-Moderation": (TOKENIZER + ["model.safetensors"], "557 MB"),
    "sentinet/suicidality": (TOKENIZER + ["model.safetensors"], "438 MB"),
    "unitary/toxic-bert": (TOKENIZER + ["pytorch_model.bin"], "438 MB"),
    "jaranohaal/vit-base-violence-detection": (TOKENIZER + ["model.safetensors"], "343 MB"),
    "openai/clip-vit-base-patch32": (TOKENIZER + ["pytorch_model.bin"], "605 MB"),
}


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--only", action="append", help="repo id to download (repeatable)")
    p.add_argument("--skip-audio", action="store_true")
    a = p.parse_args()
    for repo, (patterns, size) in MODELS.items():
        if a.only and repo not in a.only:
            continue
        target = ROOT / "models" / repo.split("/")[-1]
        print(f"Downloading {repo} (~{size}) -> {target}")
        snapshot_download(repo_id=repo, local_dir=str(target), allow_patterns=patterns)
    if not a.skip_audio and not a.only:
        print("Downloading faster-whisper tiny (optional audio, ~75 MB) ...")
        snapshot_download(repo_id="Systran/faster-whisper-tiny")
    print("Done. Restart the backend; MORVIX loads these locally and calls no hosted inference API.")


if __name__ == "__main__":
    main()
