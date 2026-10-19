"""
Turns text into vectors for semantic search - matching on meaning, not just
shared words. Chunking splits a document into overlapping word windows
first, since embedding an entire long document as a single vector loses too
much granularity to point back at a specific relevant passage.
"""
import re
from functools import lru_cache

import numpy as np

_CHUNK_WORDS = 150
_CHUNK_OVERLAP_WORDS = 30
_MODEL_NAME = "all-MiniLM-L6-v2"  # small, fast on CPU, no GPU or API key needed
_WORD_RE = re.compile(r"\S+")


def count_chunks(text: str) -> int:
    """
    How many chunks chunk_text(text) would make, without making them. Counting
    words as a stream keeps memory flat: chunk_text keeps a match object for
    every word, which for a few million words is hundreds of megabytes - far too
    much to spend just on finding out a document is too long to index.
    """
    words = sum(1 for _ in _WORD_RE.finditer(text))
    if words <= _CHUNK_WORDS:
        return 1 if words else 0
    step = _CHUNK_WORDS - _CHUNK_OVERLAP_WORDS
    return 1 + -(-(words - _CHUNK_WORDS) // step)  # ceil division


def chunk_text(text: str) -> list[tuple[str, int, int]]:
    """
    Returns (chunk_text, char_start, char_end) triples - overlapping windows
    of whole "words" (whitespace-separated tokens), sliced directly out of
    the original text by character offset rather than rejoined from split
    words. That's what lets a citation point at an exact character range
    later, instead of just an approximate snippet.
    """
    tokens = list(_WORD_RE.finditer(text))
    if not tokens:
        return []

    chunks = []
    step = _CHUNK_WORDS - _CHUNK_OVERLAP_WORDS
    start = 0
    while start < len(tokens):
        window = tokens[start : start + _CHUNK_WORDS]
        char_start, char_end = window[0].start(), window[-1].end()
        chunks.append((text[char_start:char_end], char_start, char_end))
        if start + _CHUNK_WORDS >= len(tokens):
            break
        start += step
    return chunks


@lru_cache(maxsize=1)
def _get_model():
    # Imported lazily, not at module load - this pulls in sentence-transformers
    # and torch, genuinely heavy dependencies that nothing outside semantic
    # search should pay the import cost for.
    from sentence_transformers import SentenceTransformer

    return SentenceTransformer(_MODEL_NAME)


def embed_texts(texts: list[str]) -> list[list[float]]:
    if not texts:
        return []
    return _get_model().encode(texts, convert_to_numpy=True).tolist()


def cosine_similarity(a: list[float], b: list[float]) -> float:
    a_arr, b_arr = np.array(a), np.array(b)
    denom = np.linalg.norm(a_arr) * np.linalg.norm(b_arr)
    if denom == 0:
        return 0.0
    return float(np.dot(a_arr, b_arr) / denom)
