"""Command-line entry point to (re)build the policy index.

Usage:
    python -m hr_agent.rag.build_index                      # uses EMBEDDING_BACKEND (default fastembed)
    python -m hr_agent.rag.build_index --backend tfidf      # no model download
    python -m hr_agent.rag.build_index --max-words 150      # different chunk size (for ablations)

Render runs this during the build step so the first request does not pay the indexing cost.
"""

from __future__ import annotations

import argparse
import json
import logging

from ..config import get_settings
from .index import build_index


def main() -> None:
    parser = argparse.ArgumentParser(description="Build the HR policy vector index.")
    parser.add_argument("--backend", choices=["fastembed", "tfidf"], default=None)
    parser.add_argument("--max-words", type=int, default=None)
    parser.add_argument("--overlap", type=int, default=None)
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    index = build_index(get_settings(), args.backend, args.max_words, args.overlap)
    print(json.dumps({"folder": str(index.folder), **index.meta}, indent=2))


if __name__ == "__main__":
    main()
