"""Central configuration for the HR agent.

Every setting is read from an environment variable so that secrets (API keys)
never live in the code or the repository. A local `.env` file is loaded if it
exists, which is convenient for development; on Render the same variables are
set in the dashboard instead.

Read this file first if you want to know which knobs exist.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import date
from pathlib import Path

# Repository root = the folder that contains `hr_agent/`, `corpus/`, `mock_data/`, ...
ROOT_DIR = Path(__file__).resolve().parents[1]


def _load_dotenv() -> None:
    """Load KEY=VALUE lines from a local .env file without overriding real env vars.

    We avoid an extra dependency (python-dotenv) because the format we need is tiny.
    """
    env_file = ROOT_DIR / ".env"
    if not env_file.exists():
        return
    for raw in env_file.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


_load_dotenv()


def _env(name: str, default: str) -> str:
    return os.environ.get(name, default)


@dataclass(frozen=True)
class Settings:
    """A snapshot of all settings. Build one with `get_settings()`."""

    # Paths
    corpus_dir: Path
    mock_data_dir: Path
    index_dir: Path
    tickets_runtime_path: Path
    # Retrieval (RAG)
    embedding_backend: str
    embedding_model: str
    embedding_cache_dir: Path
    chunk_max_words: int
    chunk_overlap_words: int
    retrieval_k: int
    rerank_alpha: float
    rerank: str
    # LLM provider
    llm_api_key: str
    llm_base_url: str
    llm_model: str
    llm_temperature: float
    llm_reasoning_effort: str
    llm_timeout_s: float
    agent_mode: str
    max_agent_steps: int
    # MCP
    mcp_transport: str
    mcp_server_url: str
    mcp_call_timeout_s: float
    # Misc
    app_today: str
    seed: int
    disabled_tools: tuple[str, ...]

    def today(self) -> date:
        """The date the agent treats as 'today' (pinned via APP_TODAY for reproducible evals)."""
        return date.fromisoformat(self.app_today) if self.app_today else date.today()

    @property
    def use_llm(self) -> bool:
        """True when the agent should plan with the LLM, False for the offline rule planner."""
        if self.agent_mode == "rules":
            return False
        if self.agent_mode == "llm":
            return True
        return bool(self.llm_api_key)  # "auto": use the LLM only when a key is configured


def get_settings() -> Settings:
    """Read the environment and return a fresh Settings object.

    Built on every call (cheap) so that tests and the evaluation script can change
    environment variables between runs.
    """
    return Settings(
        corpus_dir=ROOT_DIR / "corpus",
        mock_data_dir=ROOT_DIR / "mock_data",
        index_dir=Path(_env("INDEX_DIR", str(ROOT_DIR / "data" / "index"))),
        # New mock tickets go to a runtime file so the committed seed data is never modified.
        tickets_runtime_path=Path(
            _env("MOCK_TICKETS_RUNTIME_PATH", str(ROOT_DIR / "data" / "runtime_tickets.json"))
        ),
        # "fastembed" = local ONNX embedding model (BAAI/bge-small-en-v1.5): free, no API key.
        # "tfidf"     = classic TF-IDF vectors: no model download at all (used in CI, and as fallback).
        embedding_backend=_env("EMBEDDING_BACKEND", "fastembed"),
        embedding_model=_env("EMBEDDING_MODEL", "BAAI/bge-small-en-v1.5"),
        # Model files are cached inside the project so a model downloaded during the Render build
        # is still there at runtime (Render keeps the project folder, not /tmp).
        embedding_cache_dir=Path(_env("EMBEDDING_CACHE_DIR", str(ROOT_DIR / "data" / "models"))),
        chunk_max_words=int(_env("CHUNK_MAX_WORDS", "220")),
        chunk_overlap_words=int(_env("CHUNK_OVERLAP_WORDS", "40")),
        retrieval_k=int(_env("RETRIEVAL_K", "5")),
        # Weight of the dense (embedding) score vs. the keyword-overlap score when reranking.
        rerank_alpha=float(_env("RERANK_ALPHA", "0.75")),
        # "on" (default) blends dense similarity with IDF-weighted keyword overlap; "off" = dense score only;
        # "auto" = rerank only dense (fastembed) indexes. See the retrieval ablation in evaluation/results.
        rerank=_env("RERANK", "on"),
        # Any OpenAI-compatible endpoint works; the default is Groq's free tier.
        # Leave LLM_API_KEY empty to run the deterministic offline planner instead.
        llm_api_key=_env("LLM_API_KEY", _env("GROQ_API_KEY", "")),
        llm_base_url=_env("LLM_BASE_URL", "https://api.groq.com/openai/v1"),
        llm_model=_env("LLM_MODEL", "openai/gpt-oss-120b"),
        # Only sent to reasoning models (gpt-oss): "low" keeps latency and free-tier token use down.
        llm_reasoning_effort=_env("LLM_REASONING_EFFORT", "low"),
        llm_temperature=float(_env("LLM_TEMPERATURE", "0")),
        llm_timeout_s=float(_env("LLM_TIMEOUT_S", "45")),
        # "auto" = LLM when a key is present, otherwise rules. Can be forced to "llm" or "rules".
        agent_mode=_env("AGENT_MODE", "auto"),
        max_agent_steps=int(_env("MAX_AGENT_STEPS", "8")),
        # "stdio": the web app starts the MCP server as a child process (one Render service).
        # "http":  connect to a separately deployed MCP server (Streamable HTTP) at MCP_SERVER_URL.
        mcp_transport=_env("MCP_TRANSPORT", "stdio"),
        mcp_server_url=_env("MCP_SERVER_URL", "http://127.0.0.1:8765/mcp"),
        mcp_call_timeout_s=float(_env("MCP_CALL_TIMEOUT_S", "30")),
        # Pin "today" for reproducible evaluation (e.g. APP_TODAY=2026-10-12). Empty = real date.
        app_today=_env("APP_TODAY", ""),
        seed=int(_env("SEED", "42")),
        # Comma-separated tool names to hide from the agent (used for the tool-availability ablation).
        disabled_tools=tuple(t.strip() for t in _env("DISABLED_TOOLS", "").split(",") if t.strip()),
    )
