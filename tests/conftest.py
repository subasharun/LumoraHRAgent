"""Shared test setup.

Tests run fully offline and deterministically:
- TF-IDF embeddings (no model download),
- the rules planner (no LLM key needed) unless a test starts the fake LLM server,
- a pinned "today" so date rules (notice periods, tenure) give stable results,
- a temporary file for mock tickets so the repository data is never modified.
"""

import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

_tmp = tempfile.mkdtemp(prefix="hr-agent-tests-")
os.environ.setdefault("EMBEDDING_BACKEND", "tfidf")
os.environ.setdefault("APP_TODAY", "2026-10-12")
os.environ.setdefault("AGENT_MODE", "rules")
os.environ["MOCK_TICKETS_RUNTIME_PATH"] = os.path.join(_tmp, "tickets.json")
os.environ.pop("LLM_API_KEY", None)
os.environ.pop("GROQ_API_KEY", None)
