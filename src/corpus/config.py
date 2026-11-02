"""Where the archive lives, and settings read from the environment.

Every path the pipeline uses is derived from ROOT, found once at import:

1. CORPUS_ROOT, if set;
2. the checkout this package was installed from in editable mode (the
   directory two levels above this file, if it holds manifest.json, or
   pyproject.toml and scripts/: a fresh clone has no manifest yet);
3. the nearest directory at or above the current one that holds manifest.json.

Settings:
  OPEN_PALEO_DATA   the open-paleo data repository (default: ../data next to ROOT)
  CORPUS_CONTACT    contact address sent in HTTP User-Agents (default: UNPAYWALL_EMAIL)
  PDF_BACKEND       pdfium (default) or pymupdf; read by corpus.pdf.text

`.env` at ROOT holds the API keys. `load_env()` loads it once; nothing else in
the package reads it.
"""
from __future__ import annotations

import os
from pathlib import Path


def _find_root() -> Path:
    env = os.environ.get("CORPUS_ROOT")
    if env:
        return Path(env).expanduser().resolve()
    here = Path(__file__).resolve().parents[2]
    if (here / "manifest.json").exists() or \
            ((here / "pyproject.toml").exists() and (here / "scripts").is_dir()):
        return here
    cwd = Path.cwd().resolve()
    for d in (cwd, *cwd.parents):
        if (d / "manifest.json").exists():
            return d
    return cwd


ROOT = _find_root()

MANIFEST = ROOT / "manifest.json"
PDF_DIR = ROOT / "pdfs"
MD_DIR = ROOT / "markdown"
SUPP_PDF_DIR = ROOT / "supplementary"
SUPP_MD_DIR = MD_DIR / "supplementary"
SOURCE_IMAGES_DIR = ROOT / "source_images"
PDF_IMAGES_DIR = ROOT / "pdf_images"
INDEX_DIR = ROOT / "index"
DB = INDEX_DIR / "papers.db"
ENV_FILE = ROOT / ".env"

RUNTIME = ROOT / ".runtime"
LOG_DIR = RUNTIME / "logs"
CACHE_ROOT = RUNTIME / "cache"
TMP_DIR = RUNTIME / "tmp"
VISION_CACHE = CACHE_ROOT / "vision"


def cache_dir(name: str) -> Path:
    """.runtime/cache/{name}/"""
    return CACHE_ROOT / name


def log_path(name: str) -> Path:
    """.runtime/logs/{name}"""
    return LOG_DIR / name


def data_repo() -> Path:
    """The open-paleo data repository: OPEN_PALEO_DATA, else ../data beside ROOT."""
    env = os.environ.get("OPEN_PALEO_DATA")
    return Path(env).expanduser() if env else ROOT.parent / "data"


_env_loaded = False


def load_env() -> None:
    """Load ROOT/.env into os.environ once (existing variables win)."""
    global _env_loaded
    if _env_loaded:
        return
    try:
        from dotenv import load_dotenv
    except ImportError:
        _env_loaded = True
        return
    load_dotenv(ENV_FILE)
    _env_loaded = True


def require_env(name: str) -> str:
    """The value of a required setting from the environment or .env, or exit
    with a message naming it (the value itself is never printed)."""
    load_env()
    value = os.environ.get(name)
    if not value:
        raise SystemExit(f"{name} is not set: add it to {ENV_FILE} or the environment.")
    return value


def user_agent(product: str = "open-paleo-corpus/0.1") -> str:
    """HTTP User-Agent naming a contact address, as polite crawling asks.
    CORPUS_CONTACT, else UNPAYWALL_EMAIL; no address if neither is set."""
    load_env()
    contact = os.environ.get("CORPUS_CONTACT") or os.environ.get("UNPAYWALL_EMAIL")
    return f"{product} (+{contact})" if contact else product
