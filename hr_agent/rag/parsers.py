"""Turn policy files (Markdown, HTML, TXT, PDF) into a common structure.

Each parser returns a `ParsedDocument`: the document ID, its title, and an ordered
list of `Section`s (heading + clean text). Keeping the heading structure is what
lets the chunker split on section boundaries and lets answers cite "POL-002 §5.1".

Supported formats and how the document ID is found:
- Markdown: YAML-style front matter at the top (`doc_id: POL-002`).
- HTML:     <meta name="doc-id" content="POL-003">.
- TXT:      a header line `DOC-ID: POL-010`, sections written as `== 3. Title ==`.
- PDF:      a first line `DOC-ID: POL-014`, numbered headings such as `5. Annual Review`.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from bs4 import BeautifulSoup

SUPPORTED_SUFFIXES = {".md", ".html", ".htm", ".txt", ".pdf"}

# Matches a leading section number such as "3", "3.1" or "10." at the start of a heading.
_SECTION_NUMBER = re.compile(r"^(\d+(?:\.\d+)*)\.?\s+")


@dataclass
class Section:
    heading: str  # e.g. "5.1 Short-Notice Requests"
    text: str  # cleaned body text of this section only (not its sub-sections)
    level: int = 2  # 1 = document title, 2 = section, 3 = sub-section

    @property
    def number(self) -> str | None:
        """The section number ("5.1") if the heading starts with one, else None."""
        match = _SECTION_NUMBER.match(self.heading)
        return match.group(1) if match else None


@dataclass
class ParsedDocument:
    doc_id: str
    title: str
    source_file: str
    source_format: str
    sections: list[Section] = field(default_factory=list)

    @property
    def word_count(self) -> int:
        return sum(len(s.text.split()) for s in self.sections)


def _clean(text: str) -> str:
    """Collapse whitespace and drop Markdown emphasis/blockquote markers."""
    text = text.replace("**", "").replace("`", "")
    text = re.sub(r"^\s*>\s?", "", text, flags=re.MULTILINE)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _markdown_table_to_text(text: str) -> str:
    """Make pipe tables readable as plain text: drop separator rows, use ' | ' between cells."""
    lines = []
    for line in text.splitlines():
        stripped = line.strip()
        if re.fullmatch(r"\|?[\s:\-|]+\|?", stripped) and "-" in stripped:
            continue  # the |---|---| separator row carries no content
        if stripped.startswith("|") and stripped.endswith("|"):
            cells = [c.strip() for c in stripped.strip("|").split("|")]
            line = " | ".join(cells)
        lines.append(line)
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Markdown
# ---------------------------------------------------------------------------
def parse_markdown(path: Path) -> ParsedDocument:
    raw = path.read_text(encoding="utf-8")
    meta: dict[str, str] = {}
    body = raw
    if raw.startswith("---"):
        _, front, body = raw.split("---", 2)
        for line in front.strip().splitlines():
            if ":" in line:
                key, value = line.split(":", 1)
                meta[key.strip()] = value.strip()

    doc = ParsedDocument(
        doc_id=meta.get("doc_id", path.stem),
        title=meta.get("title", path.stem),
        source_file=path.name,
        source_format="markdown",
    )
    heading, level, buffer = "Introduction", 2, []

    def flush() -> None:
        text = _clean(_markdown_table_to_text("\n".join(buffer)))
        if text:
            doc.sections.append(Section(heading=heading, text=text, level=level))

    for line in body.splitlines():
        match = re.match(r"^(#{1,6})\s+(.*)", line)
        if match:
            flush()
            buffer = []
            level, heading = len(match.group(1)), match.group(2).strip()
            if level == 1:  # the H1 is the document title, not a section
                doc.title = doc.title or heading
                heading, level = "Introduction", 2
        else:
            buffer.append(line)
    flush()
    return doc


# ---------------------------------------------------------------------------
# HTML
# ---------------------------------------------------------------------------
def parse_html(path: Path) -> ParsedDocument:
    soup = BeautifulSoup(path.read_text(encoding="utf-8"), "html.parser")
    # Remove page chrome and scripts so only policy content is indexed.
    for tag in soup(["script", "style", "nav", "header", "footer"]):
        tag.decompose()
    for div in soup.select(".banner"):
        div.decompose()

    meta_id = soup.find("meta", attrs={"name": "doc-id"})
    title_tag = soup.find("title")
    doc = ParsedDocument(
        doc_id=meta_id["content"] if meta_id else path.stem,
        title=title_tag.get_text(strip=True) if title_tag else path.stem,
        source_file=path.name,
        source_format="html",
    )

    heading, level, buffer = "Introduction", 2, []

    def flush() -> None:
        text = _clean("\n".join(buffer))
        if text:
            doc.sections.append(Section(heading=heading, text=text, level=level))

    # Walk the content elements in document order; headings start a new section.
    for el in soup.body.find_all(["h1", "h2", "h3", "p", "li", "tr"]) if soup.body else []:
        if el.name in ("h1", "h2", "h3"):
            if el.name == "h1":
                continue  # document title
            flush()
            buffer = []
            heading, level = el.get_text(" ", strip=True), int(el.name[1])
        elif el.name == "tr":
            cells = [c.get_text(" ", strip=True) for c in el.find_all(["td", "th"])]
            buffer.append(" | ".join(cells))
        elif el.name == "li":
            buffer.append("- " + el.get_text(" ", strip=True))
        elif el.find_parent("li") is None:  # <p> not nested inside a list item
            buffer.append(el.get_text(" ", strip=True))
    flush()
    return doc


# ---------------------------------------------------------------------------
# Plain text
# ---------------------------------------------------------------------------
def parse_text(path: Path) -> ParsedDocument:
    lines = path.read_text(encoding="utf-8").splitlines()
    meta: dict[str, str] = {}
    body_start = 0
    for i, line in enumerate(lines):
        match = re.match(r"^([A-Z\-]+):\s*(.*)$", line)
        if match:
            meta[match.group(1)] = match.group(2).strip()
        elif line.strip() == "":
            body_start = i + 1
            break

    doc = ParsedDocument(
        doc_id=meta.get("DOC-ID", path.stem),
        title=meta.get("TITLE", path.stem),
        source_file=path.name,
        source_format="text",
    )
    heading, buffer = "Introduction", []

    def flush() -> None:
        text = _clean("\n".join(buffer))
        if text:
            doc.sections.append(Section(heading=heading, text=text))

    for line in lines[body_start:]:
        match = re.match(r"^==\s*(.+?)\s*==\s*$", line)
        if match:
            flush()
            buffer = []
            heading = match.group(1)
        else:
            buffer.append(line)
    flush()
    return doc


# ---------------------------------------------------------------------------
# PDF
# ---------------------------------------------------------------------------
def parse_pdf(path: Path) -> ParsedDocument:
    from pypdf import PdfReader  # imported lazily: only needed for PDF files

    reader = PdfReader(str(path))
    text = "\n".join(page.extract_text() or "" for page in reader.pages)
    lines = [ln.strip() for ln in text.splitlines()]

    doc_id_match = re.search(r"DOC-ID:\s*(\S+)", text)
    pdf_title = (reader.metadata.title if reader.metadata else None) or path.stem
    doc = ParsedDocument(
        doc_id=doc_id_match.group(1) if doc_id_match else path.stem,
        title=pdf_title,
        source_file=path.name,
        source_format="pdf",
    )
    # A heading in our PDFs is a short line such as "5. Annual Compensation Review".
    heading_re = re.compile(r"^(\d{1,2})\.\s+([A-Z][A-Za-z ,&()\-]{2,60})$")
    heading, buffer = "Introduction", []

    def flush() -> None:
        # PDF extraction breaks lines mid-sentence; re-join them into paragraphs.
        joined = " ".join(b for b in buffer if b)
        text = _clean(joined)
        if text:
            doc.sections.append(Section(heading=heading, text=text))

    for line in lines:
        if line.startswith(("DOC-ID:", "OWNER:")) or line == doc.title:
            continue
        match = heading_re.match(line)
        if match:
            flush()
            buffer = []
            heading = f"{match.group(1)}. {match.group(2)}"
        else:
            buffer.append(line)
    flush()
    return doc


def parse_file(path: Path) -> ParsedDocument:
    """Dispatch to the right parser based on file extension."""
    suffix = path.suffix.lower()
    if suffix == ".md":
        return parse_markdown(path)
    if suffix in (".html", ".htm"):
        return parse_html(path)
    if suffix == ".txt":
        return parse_text(path)
    if suffix == ".pdf":
        return parse_pdf(path)
    raise ValueError(f"Unsupported file type: {path}")


def load_corpus(corpus_dir: Path) -> list[ParsedDocument]:
    """Parse every supported file in the corpus folder, sorted by name for determinism."""
    files = sorted(p for p in corpus_dir.iterdir() if p.suffix.lower() in SUPPORTED_SUFFIXES)
    return [parse_file(p) for p in files]
