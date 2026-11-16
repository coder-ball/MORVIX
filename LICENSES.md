# License and attribution inventory

## Project code

No project-wide redistribution license is asserted here. Add the capstone authors' approved license before publishing or sharing the repository.

## Models

- `unitary/toxic-bert`: model repository lists Apache-2.0. Preserve upstream notices and verify model/dataset attribution requirements if distributing weights or derivatives.
- `openai/clip-vit-base-patch32`: OpenAI CLIP repository is MIT licensed. The model card separately warns that deployment needs careful task-specific testing; the license does not establish suitability for child protection.

## Runtime dependencies

FastAPI, Uvicorn, Pydantic, Python Multipart, Pillow, OpenCV, Transformers, PyTorch, Hugging Face Hub, pytest, and HTTPX retain their own licenses. Review installed package metadata and upstream notices before redistribution. This file is an inventory, not legal advice.

References: [toxic-bert model card](https://huggingface.co/unitary/toxic-bert), [OpenAI CLIP source and license](https://github.com/OpenAI/CLIP), [CLIP model card](https://huggingface.co/openai/clip-vit-base-patch32).

## v3 model additions

| Model | License | Source |
|---|---|---|
| KoalaAI/Text-Moderation | OpenRAIL (use restrictions apply; read the model card) | https://huggingface.co/KoalaAI/Text-Moderation |
| sentinet/suicidality | CC0-1.0 | https://huggingface.co/sentinet/suicidality |
| jaranohaal/vit-base-violence-detection | Apache-2.0 | https://huggingface.co/jaranohaal/vit-base-violence-detection |

Weights are downloaded into `./models` by `scripts/download_models.py` and are not redistributed with the source.
| MoritzLaurer/deberta-v3-base-zeroshot-v2.0 | MIT | https://huggingface.co/MoritzLaurer/deberta-v3-base-zeroshot-v2.0 |
