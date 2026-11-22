"""Runtime settings, read from environment variables / .env."""
from __future__ import annotations

import os
from datetime import date
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
load_dotenv(ROOT / ".env")

# httpx / the Anthropic SDK reject the `socks://` scheme that some VPN clients put
# into ALL_PROXY. HTTPS_PROXY (if any) keeps working, so drop only the unusable var
# for this process.
for _k in ("ALL_PROXY", "all_proxy"):
    if os.environ.get(_k, "").startswith("socks://"):
        os.environ.pop(_k)

DATA_DIR = ROOT / "data"
EXAMPLES_DIR = DATA_DIR / "examples"
DOSSIER_DIR = DATA_DIR / "dossiers"
UPLOAD_DIR = DATA_DIR / "uploads"
CACHE_DIR = DATA_DIR / "cache"
SJR_DB = Path(os.getenv("SJR_DB", str(DATA_DIR / "sjr.sqlite")))
MODELS_DIR = Path(os.getenv("MODELS_DIR", str(DATA_DIR / "models")))
# multilingual sentence-embedding model for matching candidates to ITMO units (runs locally, ONNX)
EMBED_MODEL = os.getenv("EMBED_MODEL", "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2")
for _d in (DOSSIER_DIR, UPLOAD_DIR, CACHE_DIR, MODELS_DIR):
    _d.mkdir(parents=True, exist_ok=True)

# LLM (optional): DeepSeek / any OpenAI-compatible endpoint, or Anthropic Claude — see app/llm.py
LLM_PROVIDER = os.getenv("LLM_PROVIDER", "").strip().lower()  # empty = auto by available key
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "").strip()
CLAUDE_MODEL = os.getenv("CLAUDE_MODEL", "claude-opus-5-5")
LLM_API_KEY = (os.getenv("LLM_API_KEY") or os.getenv("DEEPSEEK_API_KEY") or "").strip()
LLM_BASE_URL = os.getenv("LLM_BASE_URL", "https://api.deepseek.com").strip()
LLM_MODEL = os.getenv("LLM_MODEL", "deepseek-flash").strip()
LLM_TIMEOUT = float(os.getenv("LLM_TIMEOUT", "240"))
SCOPUS_API_KEY = os.getenv("SCOPUS_API_KEY", "").strip()
SCOPUS_INST_TOKEN = os.getenv("SCOPUS_INST_TOKEN", "").strip()
OPENALEX_API_KEY = os.getenv("OPENALEX_API_KEY", "").strip()
CONTACT_EMAIL = os.getenv("CONTACT_EMAIL", "").strip()  # optional, sent to Crossref/OpenAlex as courtesy

# How many years of publications go into the dossier ("за последние четыре года").
# Default window: current year and the four full years before it (e.g. 2022–2026).
PUB_WINDOW_YEARS = int(os.getenv("PUB_WINDOW_YEARS", "4"))
CACHE_TTL_DAYS = int(os.getenv("CACHE_TTL_DAYS", "14"))
HTTP_TIMEOUT = float(os.getenv("HTTP_TIMEOUT", "30"))

# Optional HTTP Basic auth for the whole site (set both to enable; recommended on a public server)
APP_USERNAME = os.getenv("APP_USERNAME", "").strip()
APP_PASSWORD = os.getenv("APP_PASSWORD", "").strip()


def default_from_year() -> int:
    return date.today().year - PUB_WINDOW_YEARS


def llm_enabled() -> bool:
    from app import llm

    return llm.enabled()
