"""Vector store: FAISS when installed, otherwise an equivalent NumPy implementation.

Both do *exact* inner-product search over L2-normalised vectors (= cosine similarity),
so results are identical. FAISS is the documented choice; the NumPy fallback exists
because `faiss-cpu` wheels are not available on every platform/Python version (for
example some macOS or brand-new Python releases). With ~200 chunks, a NumPy matrix
product is just as fast.

Files on disk:
- FAISS:  faiss.index
- NumPy:  vectors.npy
`load_store` reads whichever exists, so an index built with one can be read by the other
only if the matching file is present (rebuilding the index fixes any mismatch).
"""

from __future__ import annotations

import logging
from pathlib import Path

import numpy as np

log = logging.getLogger(__name__)

try:  # FAISS is optional at runtime
    import faiss  # type: ignore

    HAVE_FAISS = True
except ImportError:  # pragma: no cover - depends on the platform
    faiss = None
    HAVE_FAISS = False


class NumpyFlatIP:
    """Minimal stand-in for faiss.IndexFlatIP: exact inner-product search with NumPy."""

    def __init__(self, vectors: np.ndarray):
        self.vectors = np.asarray(vectors, dtype="float32")

    def search(self, queries: np.ndarray, k: int):
        scores = queries.astype("float32") @ self.vectors.T  # (n_queries, n_vectors)
        k = min(k, self.vectors.shape[0])
        order = np.argsort(-scores, axis=1)[:, :k]  # highest scores first
        return np.take_along_axis(scores, order, axis=1), order


def create_store(vectors: np.ndarray):
    """Build an exact inner-product index over the given vectors."""
    if HAVE_FAISS:
        index = faiss.IndexFlatIP(vectors.shape[1])
        index.add(np.asarray(vectors, dtype="float32"))
        return index
    log.warning("faiss not installed; using the NumPy vector store (same results)")
    return NumpyFlatIP(vectors)


def save_store(index, folder: Path) -> str:
    """Write the index to `folder`. Returns the store type name recorded in meta.json."""
    if HAVE_FAISS and not isinstance(index, NumpyFlatIP):
        faiss.write_index(index, str(folder / "faiss.index"))
        return "faiss"
    np.save(folder / "vectors.npy", index.vectors)
    return "numpy"


def load_store(folder: Path):
    """Read the index from `folder`, using FAISS when available."""
    if HAVE_FAISS and (folder / "faiss.index").exists():
        return faiss.read_index(str(folder / "faiss.index"))
    if (folder / "vectors.npy").exists():
        return NumpyFlatIP(np.load(folder / "vectors.npy"))
    if (folder / "faiss.index").exists():
        raise FileNotFoundError("Index was built with FAISS but faiss is not installed; rebuild the index.")
    raise FileNotFoundError(f"No vector index in {folder}")
