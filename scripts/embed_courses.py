#!/usr/bin/env python3
"""Embed every candidate course for similar courses, For You and the HackerTube join.

Text per course: likely_title + page title + page description (deduplicated), so
pages that gave no description fall back to their title. Model: fastembed
BAAI/bge-small-en-v1.5 with no prefix, L2-normalized, the same model and settings
as my-hackertube's embeddings.npy, so course and video vectors compare directly.

Writes data/course_embeddings.npy (float16 [n, 384], row i = keys[i]) and
data/course_embedding_keys.json. Needs `pip install fastembed` (downloads ~70 MB
of model from Hugging Face on first run).

Usage: python3 scripts/embed_courses.py
"""
import json
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
from course_info import likely_title  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
MODEL = "BAAI/bge-small-en-v1.5"


def course_texts():
    meta = {}
    if (DATA / "courses_meta.jsonl").exists():
        for line in open(DATA / "courses_meta.jsonl"):
            m = json.loads(line)
            meta[m["key"]] = m
    keys, texts = [], []
    for line in open(DATA / "courses_candidates.jsonl"):
        r = json.loads(line)
        pm = meta.get(r["key"], {})
        parts = []
        for t in (likely_title(r), pm.get("title"), pm.get("description")):
            t = " ".join((t or "").split())
            if t and t.lower() not in " ".join(parts).lower():
                parts.append(t)
        keys.append(r["key"])
        texts.append(". ".join(parts))
    return keys, texts


def main():
    from fastembed import TextEmbedding
    keys, texts = course_texts()
    model = TextEmbedding(MODEL)
    emb = np.array(list(model.embed(texts)), dtype=np.float32)
    emb /= np.linalg.norm(emb, axis=1, keepdims=True)
    np.save(DATA / "course_embeddings.npy", emb.astype(np.float16))
    with open(DATA / "course_embedding_keys.json", "w") as f:
        json.dump(keys, f)
    print(f"wrote data/course_embeddings.npy {emb.shape}")


if __name__ == "__main__":
    main()
