"""Embeddings with a deterministic local fallback.

Semantic de-duplication, memory recall and element matching are *core* features,
so they cannot depend on a model being configured. When no provider offers
embeddings, GaleQEA falls back to a hashed character n-gram encoder: it is not
as good as a learned model at paraphrase, but it is deterministic, instant,
offline, and good enough to catch the near-duplicate test cases and repeated
failure signatures that matter most in practice.
"""

from __future__ import annotations

import hashlib
import math
import re
from collections.abc import Sequence

DIM = 384
_TOKEN = re.compile(r"[a-z0-9]+")


def _tokens(text: str) -> list[str]:
    words = _TOKEN.findall(text.lower())
    grams: list[str] = list(words)
    grams += [f"{a}_{b}" for a, b in zip(words, words[1:], strict=False)]
    # Character trigrams keep short, typo-adjacent strings close together.
    joined = " ".join(words)
    grams += [joined[i : i + 3] for i in range(0, max(0, len(joined) - 2))]
    return grams


def local_embed(text: str, dim: int = DIM) -> list[float]:
    """Hashed n-gram encoder with sublinear term weighting and L2 normalisation."""
    vec = [0.0] * dim
    counts: dict[str, int] = {}
    for gram in _tokens(text):
        counts[gram] = counts.get(gram, 0) + 1
    for gram, count in counts.items():
        digest = hashlib.blake2b(gram.encode(), digest_size=8).digest()
        idx = int.from_bytes(digest[:4], "big") % dim
        sign = 1.0 if digest[4] & 1 else -1.0
        vec[idx] += sign * (1.0 + math.log(count))
    norm = math.sqrt(sum(v * v for v in vec)) or 1.0
    return [v / norm for v in vec]


def cosine(a: Sequence[float], b: Sequence[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b, strict=False))
    na = math.sqrt(sum(x * x for x in a)) or 1.0
    nb = math.sqrt(sum(y * y for y in b)) or 1.0
    return dot / (na * nb)


def top_k(
    query: Sequence[float], candidates: list[tuple[str, Sequence[float]]], k: int = 5
) -> list[tuple[str, float]]:
    scored = [(cid, cosine(query, vec)) for cid, vec in candidates if vec]
    scored.sort(key=lambda pair: pair[1], reverse=True)
    return scored[:k]
