"""Heading-aware chunking.

Strategy (and why):
1. Every section of a policy becomes its own chunk. HR policies are written as
   numbered sections that each answer one question ("5. Notice Requirements"),
   so a section is a natural unit of meaning and gives a precise citation.
2. A section longer than `max_words` is split into overlapping windows of
   `max_words` words with `overlap` words shared between neighbours, breaking
   on line boundaries where possible, so a rule is never cut in half without
   context.
3. Each chunk's embedding text is prefixed with "Document title > Section" so
   short chunks (e.g. a table) still carry their topic.

Chunking is fully deterministic: same corpus + same parameters = same chunks.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass

from .parsers import ParsedDocument


@dataclass
class Chunk:
    chunk_id: str  # stable ID, e.g. "POL-002::5.1::0"
    doc_id: str
    title: str
    section: str  # full heading, e.g. "5.1 Short-Notice Requests"
    section_number: str | None  # "5.1"
    source_file: str
    text: str  # the chunk's own text (shown as the citation snippet)

    @property
    def embed_text(self) -> str:
        """What we actually embed: title and section give the chunk its context."""
        return f"{self.title} > {self.section}\n{self.text}"

    def to_dict(self) -> dict:
        return asdict(self)


def _split_long(text: str, max_words: int, overlap: int) -> list[str]:
    """Split text into windows of at most `max_words` words, overlapping by `overlap` words."""
    words = text.split()
    if len(words) <= max_words:
        return [text]
    step = max(1, max_words - overlap)
    pieces = []
    for start in range(0, len(words), step):
        window = words[start : start + max_words]
        pieces.append(" ".join(window))
        if start + max_words >= len(words):
            break
    return pieces


def chunk_document(doc: ParsedDocument, max_words: int = 220, overlap: int = 40) -> list[Chunk]:
    chunks: list[Chunk] = []
    for section in doc.sections:
        # Skip the one-line "this is a fictional company" notice at the top of each file:
        # it carries no policy content and would only add retrieval noise.
        if "fictional company" in section.text and len(section.text.split()) < 40:
            continue
        key = section.number or section.heading[:30]
        for i, piece in enumerate(_split_long(section.text, max_words, overlap)):
            chunks.append(
                Chunk(
                    chunk_id=f"{doc.doc_id}::{key}::{i}",
                    doc_id=doc.doc_id,
                    title=doc.title,
                    section=section.heading,
                    section_number=section.number,
                    source_file=doc.source_file,
                    text=piece,
                )
            )
    return chunks


def chunk_corpus(docs: list[ParsedDocument], max_words: int = 220, overlap: int = 40) -> list[Chunk]:
    out: list[Chunk] = []
    for doc in docs:
        out.extend(chunk_document(doc, max_words, overlap))
    return out
