"""Embedding backends. Both are free and run locally; neither needs an API key.

- `FastEmbedBackend` (default): BAAI/bge-small-en-v1.5 via the `fastembed` library.
  It runs on CPU with ONNX Runtime (no PyTorch), uses ~150 MB of RAM, and produces
  384-dimension vectors. The model (~67 MB) is downloaded once from Hugging Face
  during the Render build and cached.
- `TfidfBackend`: TF-IDF word/bigram vectors from scikit-learn. No download at all,
  fully deterministic, and used in CI and as an automatic fallback if the model
  download fails. Retrieval quality is lower for paraphrased questions.

Every backend returns L2-normalised float32 vectors, so inner product = cosine similarity.
"""

from __future__ import annotations

import logging
import pickle
from pathlib import Path
from typing import Protocol

import numpy as np

log = logging.getLogger(__name__)


def _normalize(vectors: np.ndarray) -> np.ndarray:
    vectors = np.asarray(vectors, dtype="float32")
    norms = np.linalg.norm(vectors, axis=1, keepdims=True)
    norms[norms == 0] = 1.0  # avoid dividing by zero for empty texts
    return vectors / norms


class EmbeddingBackend(Protocol):
    name: str

    def fit(self, texts: list[str]) -> None: ...
    def embed_documents(self, texts: list[str]) -> np.ndarray: ...
    def embed_query(self, text: str) -> np.ndarray: ...
    def save(self, folder: Path) -> None: ...


class FastEmbedBackend:
    name = "fastembed"

    def __init__(self, model_name: str = "BAAI/bge-small-en-v1.5", cache_dir: Path | None = None):
        from fastembed import TextEmbedding  # lazy import: heavy and optional

        self.model_name = model_name
        kwargs = {"cache_dir": str(cache_dir)} if cache_dir else {}
        self._model = TextEmbedding(model_name=model_name, **kwargs)

    def fit(self, texts: list[str]) -> None:
        """Nothing to fit: the model is pre-trained."""

    def embed_documents(self, texts: list[str]) -> np.ndarray:
        # passage_embed adds the passage prefix that bge models expect for documents.
        return _normalize(np.array(list(self._model.passage_embed(texts))))

    def embed_query(self, text: str) -> np.ndarray:
        # query_embed adds the "Represent this sentence for searching..." instruction for queries.
        return _normalize(np.array(list(self._model.query_embed([text]))))[0]

    def save(self, folder: Path) -> None:
        """The model is cached by fastembed itself; only the name is stored in meta.json."""


class TfidfBackend:
    name = "tfidf"

    def __init__(self, vectorizer=None):
        from sklearn.feature_extraction.text import TfidfVectorizer

        self._vectorizer = vectorizer or TfidfVectorizer(
            lowercase=True,
            stop_words="english",
            ngram_range=(1, 2),  # bigrams help with phrases like "home office" or "short notice"
            sublinear_tf=True,
            min_df=1,
        )

    def fit(self, texts: list[str]) -> None:
        self._vectorizer.fit(texts)

    def embed_documents(self, texts: list[str]) -> np.ndarray:
        return _normalize(self._vectorizer.transform(texts).toarray())

    def embed_query(self, text: str) -> np.ndarray:
        return _normalize(self._vectorizer.transform([text]).toarray())[0]

    def save(self, folder: Path) -> None:
        with open(folder / "tfidf.pkl", "wb") as f:
            pickle.dump(self._vectorizer, f)

    @classmethod
    def load(cls, folder: Path) -> "TfidfBackend":
        # The pickle is produced by our own build step (never downloaded), so loading it is safe.
        with open(folder / "tfidf.pkl", "rb") as f:
            return cls(vectorizer=pickle.load(f))


def create_backend(name: str, model_name: str, cache_dir: Path | None = None) -> EmbeddingBackend:
    """Create a backend for building a new index. Falls back to TF-IDF if fastembed fails."""
    if name == "fastembed":
        try:
            return FastEmbedBackend(model_name, cache_dir)
        except Exception as exc:  # e.g. no network during the model download
            log.warning("fastembed unavailable (%s); falling back to TF-IDF embeddings", exc)
            return TfidfBackend()
    if name == "tfidf":
        return TfidfBackend()
    raise ValueError(f"Unknown EMBEDDING_BACKEND: {name}")


def load_backend(name: str, model_name: str, folder: Path, cache_dir: Path | None = None) -> EmbeddingBackend:
    """Re-create the backend that was used to build an existing index."""
    if name == "tfidf":
        return TfidfBackend.load(folder)
    return FastEmbedBackend(model_name, cache_dir)
