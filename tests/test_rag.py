"""Ingestion, chunking and retrieval tests (no network needed)."""

from hr_agent.config import get_settings
from hr_agent.rag.chunking import chunk_corpus
from hr_agent.rag.index import build_index, load_or_build_index, rewrite_query
from hr_agent.rag.parsers import load_corpus


def test_corpus_parses_all_formats():
    docs = load_corpus(get_settings().corpus_dir)
    assert len(docs) >= 13
    formats = {d.source_format for d in docs}
    assert {"markdown", "html", "text", "pdf"} <= formats
    ids = [d.doc_id for d in docs]
    assert len(ids) == len(set(ids)), "document IDs must be unique"
    assert all(d.doc_id.startswith("POL-") for d in docs)
    assert sum(d.word_count for d in docs) > 12000  # roughly 30+ pages


def test_chunking_is_heading_aware_and_deterministic():
    docs = load_corpus(get_settings().corpus_dir)
    a, b = chunk_corpus(docs, 220, 40), chunk_corpus(docs, 220, 40)
    assert [c.chunk_id for c in a] == [c.chunk_id for c in b]
    assert all(len(c.text.split()) <= 220 for c in a)
    pto = [c for c in a if c.doc_id == "POL-002"]
    assert any(c.section_number == "5.1" for c in pto), "sub-sections keep their own number for citations"


def test_query_rewrite_expands_everyday_words():
    assert "PTO" in rewrite_query("how much vacation do I have")
    assert "work from abroad" in rewrite_query("can I work from Portugal")
    assert "abroad" not in rewrite_query("what is the capital of France")


def test_search_finds_the_right_policy():
    index = load_or_build_index(get_settings())
    cases = {
        "Can I work from Portugal for six weeks?": "POL-005",
        "How much notice do I need for 3 days of vacation?": "POL-002",
        "Can I get reimbursed for a laptop?": "POL-007",
        "How many days of bereavement leave for a grandparent?": "POL-009",
        "When are we paid each month?": "POL-014",
    }
    for question, doc_id in cases.items():
        top3 = [h.chunk.doc_id for h in index.search(question, k=3)]
        assert doc_id in top3, f"{question!r} -> {top3}"


def test_doc_filter_and_section_lookup():
    index = load_or_build_index(get_settings())
    hits = index.search("approval", k=5, doc_ids=["POL-005"])
    assert hits and all(h.chunk.doc_id == "POL-005" for h in hits)
    section = index.get_section("POL-012", "4")
    assert section and all(c.section_number == "4" for c in section), "'4' must not match '3.4'"


def test_scope_signals():
    index = load_or_build_index(get_settings())
    from hr_agent.agent.guardrails import is_out_of_scope

    q = "What is the capital of France?"
    assert is_out_of_scope(False, index.keyword_coverage(q), index.unmatched_terms(q))
    assert index.keyword_coverage("How many PTO days carry over?") > 0.6
    assert "pet" in index.unmatched_terms("Does Lumora offer pet insurance?")
    assert index.unmatched_terms("bereavement leave for my grandmother") == []


def test_build_index_with_other_chunk_size(tmp_path, monkeypatch):
    monkeypatch.setenv("INDEX_DIR", str(tmp_path))
    index = build_index(get_settings(), "tfidf", max_words=120)
    assert index.meta["chunk_max_words"] == 120
    folder = tmp_path / "tfidf-w120"
    assert (folder / "faiss.index").exists() or (folder / "vectors.npy").exists()


def test_numpy_fallback_matches_faiss(tmp_path, monkeypatch):
    """Without faiss installed the NumPy store must return the same ranking."""
    from hr_agent.rag import vector_store

    monkeypatch.setenv("INDEX_DIR", str(tmp_path / "with"))
    with_default = build_index(get_settings(), "tfidf")
    monkeypatch.setattr(vector_store, "HAVE_FAISS", False)
    monkeypatch.setenv("INDEX_DIR", str(tmp_path / "numpy"))
    numpy_only = build_index(get_settings(), "tfidf")
    assert numpy_only.meta["vector_store"] == "numpy"
    q = "How much notice do I need for 3 days of PTO?"
    assert [h.chunk.chunk_id for h in with_default.search(q)] == [h.chunk.chunk_id for h in numpy_only.search(q)]
