"""Build, save, load and search the policy vector index (FAISS).

Files written to `<INDEX_DIR>/<backend>-w<max_words>/`:
- faiss.index   : the vectors (FAISS IndexFlatIP = exact inner-product search;
                  vectors.npy instead when faiss is not installed, see vector_store.py)
- chunks.json   : chunk text + metadata (doc ID, title, section, source file) for citations
- meta.json     : how the index was built (backend, model, chunk sizes, corpus hash)
- tfidf.pkl     : the fitted TF-IDF vocabulary (only for the TF-IDF backend)

Exact (flat) search is used on purpose: the corpus is ~200 chunks, so brute force
takes well under a millisecond and avoids approximate-search recall loss.
"""

from __future__ import annotations

import hashlib
import json
import logging
import math
import re
import time
from dataclasses import dataclass
from pathlib import Path

from ..config import Settings, get_settings
from .chunking import Chunk, chunk_corpus
from .embeddings import EmbeddingBackend, create_backend, load_backend
from .parsers import SUPPORTED_SUFFIXES, load_corpus
from .vector_store import create_store, load_store, save_store

log = logging.getLogger(__name__)

from .query import _GENERIC, STOP_WORDS, SYNONYMS, rewrite_query  # noqa: E402  (re-exported for callers)

_TOKEN = re.compile(r"[a-z0-9]+(?:\([a-z0-9]+\))?")


def _tokens(text: str) -> list[str]:
    return [t for t in _TOKEN.findall(text.lower()) if t not in STOP_WORDS and len(t) > 1]


@dataclass
class SearchHit:
    chunk: Chunk
    score: float  # final (reranked) score, roughly 0..1
    dense_score: float
    lexical_score: float

    def to_dict(self) -> dict:
        c = self.chunk
        return {
            "chunk_id": c.chunk_id,
            "doc_id": c.doc_id,
            "title": c.title,
            "section": c.section,
            "section_number": c.section_number,
            "source_file": c.source_file,
            "citation": f"{c.doc_id} §{c.section_number}" if c.section_number else c.doc_id,
            "snippet": c.text if len(c.text) <= 900 else c.text[:900].rsplit(" ", 1)[0] + " …",
            "score": round(self.score, 4),
        }


class PolicyIndex:
    """A loaded index plus the search logic (query rewrite → dense search → rerank)."""

    def __init__(self, folder: Path, backend: EmbeddingBackend, index, chunks: list[Chunk], meta: dict):
        self.folder = folder
        self.backend = backend
        self.index = index
        self.chunks = chunks
        self.meta = meta
        # Pre-compute token sets and IDF weights for the keyword (lexical) part of reranking.
        self._chunk_tokens = [set(_tokens(c.embed_text)) for c in chunks]
        doc_freq: dict[str, int] = {}
        for toks in self._chunk_tokens:
            for t in toks:
                doc_freq[t] = doc_freq.get(t, 0) + 1
        n = len(chunks)
        self._idf = {t: math.log((n + 1) / (df + 0.5)) for t, df in doc_freq.items()}
        self._vocab = sorted(self._idf)

    # ------------------------------------------------------------------ search
    def search(
        self,
        query: str,
        k: int = 5,
        doc_ids: list[str] | None = None,
        rewrite: bool = True,
        rerank: bool = True,
        alpha: float = 0.75,
        max_per_doc: int = 3,
    ) -> list[SearchHit]:
        """Return the top-k chunks for a query.

        Args:
            doc_ids: optional filter, e.g. ["POL-002"] to search only the PTO policy.
            rewrite: expand everyday words into policy vocabulary (see QUERY_EXPANSIONS).
            rerank: blend dense similarity with IDF-weighted keyword overlap.
            alpha: weight of the dense score in the blend (1.0 = dense only).
            max_per_doc: cap chunks per document so multi-policy questions get variety.
        """
        q = rewrite_query(query) if rewrite else query
        qvec = self.backend.embed_query(q).reshape(1, -1).astype("float32")
        # Over-fetch candidates so filtering and reranking still leave k results.
        n_candidates = min(len(self.chunks), max(k * 6, 30))
        scores, ids = self.index.search(qvec, n_candidates)

        q_tokens = set(_tokens(q))
        q_weight = sum(self._idf.get(t, 0.0) for t in q_tokens) or 1.0

        hits: list[SearchHit] = []
        for dense, idx in zip(scores[0], ids[0]):
            if idx < 0:
                continue
            chunk = self.chunks[idx]
            if doc_ids and chunk.doc_id not in doc_ids:
                continue
            overlap = q_tokens & self._chunk_tokens[idx]
            lexical = sum(self._idf.get(t, 0.0) for t in overlap) / q_weight
            final = alpha * float(dense) + (1 - alpha) * lexical if rerank else float(dense)
            hits.append(SearchHit(chunk, final, float(dense), lexical))

        hits.sort(key=lambda h: h.score, reverse=True)
        result, per_doc = [], {}
        for hit in hits:
            if per_doc.get(hit.chunk.doc_id, 0) >= max_per_doc:
                continue
            per_doc[hit.chunk.doc_id] = per_doc.get(hit.chunk.doc_id, 0) + 1
            result.append(hit)
            if len(result) == k:
                break
        return result

    def _to_vocab(self, token: str) -> str | None:
        """Map a query word to a word the library uses, or None if the library never uses it.

        Tries, in order: the word itself, a synonym ("grandmother" → "grandparent"), and a shared
        stem of at least 4 letters ("night" → "nightly", "reimbursed" → "reimbursement").
        """
        token = SYNONYMS.get(token, token)
        if token in self._idf:
            return token
        stem = token[: max(4, len(token) - 2)]
        if len(token) >= 4:
            for v in self._vocab:
                if v.startswith(stem) or (len(v) >= 4 and token.startswith(v)):
                    return v
        return None

    def keyword_coverage(self, query: str) -> float:
        """How well the library's vocabulary covers the question (0..1), before any query rewriting.

        Used by the out-of-scope guardrail: "What is the capital of France?" shares almost
        no meaningful words with the HR policies, so its coverage is close to 0.
        For each chunk we compute the IDF-weighted share of query words it contains and
        return the best chunk's value. Words the library never uses get a high weight, so
        they pull coverage down.
        """
        mapped = {t: self._to_vocab(t) for t in set(_tokens(query))}
        if not mapped:
            return 0.0
        max_idf = math.log(len(self.chunks) + 1) + 1
        weights = {t: (self._idf[v] if v else max_idf) for t, v in mapped.items()}
        total = sum(weights.values())
        best = 0.0
        for toks in self._chunk_tokens:
            covered = sum(w for t, w in weights.items() if mapped[t] and mapped[t] in toks)
            best = max(best, covered / total)
        return best

    def unmatched_terms(self, query: str) -> list[str]:
        """Meaningful question words that never appear in the policy library (e.g. "pet" in "pet insurance").

        Together with low keyword coverage this is a strong hint that the library does not cover the
        question, which the agent uses to avoid answering beyond the evidence.
        """
        out = set()
        for t in _tokens(query):
            if t in _GENERIC or t.isdigit() or len(t) < 3:
                continue
            if self._to_vocab(t) is None:
                out.add(t)
        return sorted(out)

    def get_section(self, doc_id: str, section: str) -> list[Chunk]:
        """All chunks of one section. `section` may be a number ("5.1") or part of a heading."""
        wanted = section.strip().lstrip("§").strip().rstrip(".").lower()
        in_doc = [c for c in self.chunks if c.doc_id == doc_id]
        if re.fullmatch(r"\d+(\.\d+)*", wanted):
            # A section number must match exactly ("4" must not match "3.4").
            exact = [c for c in in_doc if (c.section_number or "") == wanted]
            # A parent section with no text of its own (e.g. "3. Employment Categories") is
            # represented by its sub-sections (3.1, 3.2, ...).
            return exact or [c for c in in_doc if (c.section_number or "").startswith(wanted + ".")]
        return [c for c in in_doc if wanted in c.section.lower()]

    def list_documents(self) -> list[dict]:
        docs: dict[str, dict] = {}
        for c in self.chunks:
            d = docs.setdefault(c.doc_id, {"doc_id": c.doc_id, "title": c.title, "source_file": c.source_file, "sections": []})
            if c.section not in d["sections"]:
                d["sections"].append(c.section)
        return list(docs.values())

    # ------------------------------------------------------------------ persistence
    @classmethod
    def load(cls, folder: Path) -> "PolicyIndex":
        meta = json.loads((folder / "meta.json").read_text())
        chunks = [Chunk(**c) for c in json.loads((folder / "chunks.json").read_text())]
        backend = load_backend(meta["backend"], meta.get("model", ""), folder, get_settings().embedding_cache_dir)
        index = load_store(folder)
        return cls(folder, backend, index, chunks, meta)


def corpus_hash(corpus_dir: Path) -> str:
    """Fingerprint of the corpus files, used to detect a stale index."""
    h = hashlib.sha256()
    for p in sorted(corpus_dir.iterdir()):
        if p.suffix.lower() in SUPPORTED_SUFFIXES:
            h.update(p.name.encode())
            h.update(p.read_bytes())
    return h.hexdigest()[:16]


def index_folder(settings: Settings, backend: str | None = None, max_words: int | None = None) -> Path:
    backend = backend or settings.embedding_backend
    max_words = max_words or settings.chunk_max_words
    return settings.index_dir / f"{backend}-w{max_words}"


def build_index(
    settings: Settings | None = None,
    backend_name: str | None = None,
    max_words: int | None = None,
    overlap: int | None = None,
) -> PolicyIndex:
    """Parse → chunk → embed → store. Returns the loaded index."""
    settings = settings or get_settings()
    backend_name = backend_name or settings.embedding_backend
    max_words = max_words or settings.chunk_max_words
    overlap = settings.chunk_overlap_words if overlap is None else overlap

    started = time.perf_counter()
    docs = load_corpus(settings.corpus_dir)
    chunks = chunk_corpus(docs, max_words=max_words, overlap=overlap)
    backend = create_backend(backend_name, settings.embedding_model, settings.embedding_cache_dir)
    texts = [c.embed_text for c in chunks]
    backend.fit(texts)
    vectors = backend.embed_documents(texts)

    index = create_store(vectors)  # FAISS IndexFlatIP, or the NumPy equivalent if faiss is missing

    # If fastembed failed and we fell back to TF-IDF, name the folder after the real backend.
    folder = index_folder(settings, backend.name, max_words)
    folder.mkdir(parents=True, exist_ok=True)
    store_type = save_store(index, folder)
    (folder / "chunks.json").write_text(json.dumps([c.to_dict() for c in chunks], indent=1))
    backend.save(folder)
    meta = {
        "backend": backend.name,
        "model": settings.embedding_model if backend.name == "fastembed" else "tfidf-1-2gram",
        "dimensions": int(vectors.shape[1]),
        "vector_store": store_type,
        "chunk_max_words": max_words,
        "chunk_overlap_words": overlap,
        "documents": len(docs),
        "chunks": len(chunks),
        "corpus_words": sum(d.word_count for d in docs),
        "formats": sorted({d.source_format for d in docs}),
        "corpus_hash": corpus_hash(settings.corpus_dir),
        "build_seconds": round(time.perf_counter() - started, 2),
    }
    (folder / "meta.json").write_text(json.dumps(meta, indent=2))
    log.info("Built %s index: %d docs, %d chunks in %.1fs", backend.name, len(docs), len(chunks), meta["build_seconds"])
    return PolicyIndex(folder, backend, index, chunks, meta)


def load_or_build_index(settings: Settings | None = None) -> PolicyIndex:
    """Load the configured index; build it if missing or if the corpus changed.

    If the configured backend is fastembed but only a TF-IDF index exists (the model
    download failed at build time), the TF-IDF index is used so the app still starts.
    """
    settings = settings or get_settings()
    candidates = [index_folder(settings)]
    if settings.embedding_backend != "tfidf":
        candidates.append(index_folder(settings, "tfidf"))
    current_hash = corpus_hash(settings.corpus_dir)
    for folder in candidates:
        if (folder / "meta.json").exists():
            meta = json.loads((folder / "meta.json").read_text())
            if meta.get("corpus_hash") == current_hash:
                try:
                    return PolicyIndex.load(folder)
                except Exception as exc:  # corrupt files or model not loadable
                    log.warning("Could not load index at %s: %s", folder, exc)
    return build_index(settings)
