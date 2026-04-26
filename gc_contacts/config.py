"""
Global configuration, constants, and state management for the contact crawler.
"""

import os
import asyncio
import time
from pathlib import Path
from urllib.robotparser import RobotFileParser
from collections import defaultdict
from typing import Optional

import httpx
from openai import AsyncOpenAI
from gc_contacts.localisation import COUNTRY_DISCOVERY_PACKS as LOCALISATION_COUNTRY_DISCOVERY_PACKS

try:
    from dotenv import load_dotenv
except ModuleNotFoundError:  # pragma: no cover - optional local dependency
    def load_dotenv(dotenv_path=None, *_args, **_kwargs):
        """Minimal fallback loader so local runs do not depend on python-dotenv."""
        env_path = Path(dotenv_path) if dotenv_path else Path(".env")
        if not env_path.exists():
            return False
        loaded = False
        for raw_line in env_path.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            if key and key not in os.environ:
                os.environ[key] = value
                loaded = True
        return loaded

# ───────── ENVIRONMENT & SECRETS ─────────
PROJECT_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(dotenv_path=PROJECT_ROOT / ".env")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
if not OPENAI_API_KEY:
    raise RuntimeError("❌ OPENAI_API_KEY not found in .env")

# Optional Postgres dual-write. CSV/debug exports remain enabled even when this
# is configured, so database rollout can be verified safely.
DATABASE_URL = os.getenv("DATABASE_URL", "").strip()
POSTGRES_DUAL_WRITE = os.getenv("POSTGRES_DUAL_WRITE", "0").strip().lower() in {"1", "true", "yes", "on"}

# ───────── API & MODEL CONFIG ─────────
OPENALEX_API = "https://api.openalex.org/institutions"
MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
# Light tasks: default to the main chat-capable model unless explicitly overridden.
MODEL_LIGHT = os.getenv("OPENAI_MODEL_LIGHT", MODEL)
# Heavy extraction also uses Chat Completions, so default to a chat-capable model.
MODEL_HEAVY = os.getenv("OPENAI_MODEL_HEAVY", MODEL)
if "instruct" in MODEL_HEAVY.lower():
    MODEL_HEAVY = MODEL

# ───────── HTTP CONFIG ─────────
DEFAULT_UA = "UniContactsAsync/3.5 (+debug+csv)"
BROWSER_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"

HEADERS = {
    "User-Agent": DEFAULT_UA,
    "Accept-Language": "en;q=0.9,*;q=0.6",
}
TIMEOUT = httpx.Timeout(30.0)

# ───────── CRAWL LIMITS ─────────
PROBE_LIMIT = 24  # max candidate URLs to probe per university
DISCOVERY_ROOT_LINK_LIMIT = 80
DISCOVERY_FIRST_HOP_FETCH_LIMIT = 6
DISCOVERY_CHILD_LINK_LIMIT = 25
DISCOVERY_FINAL_CANDIDATE_LIMIT = 60
CONCURRENCY = 12  # concurrent university processing
GPT_CONCURRENCY = 4  # concurrent GPT requests
LIGHT_GPT_CONCURRENCY = int(os.getenv("LIGHT_GPT_CONCURRENCY", "8"))
HEAVY_GPT_CONCURRENCY = int(os.getenv("HEAVY_GPT_CONCURRENCY", "2"))
PAGINATION_CAP = 5  # pages per directory
DISCOVERY_REAL_LINK_TIMEOUT = float(os.getenv("DISCOVERY_REAL_LINK_TIMEOUT", "45"))
DISCOVERY_SITEMAP_TIMEOUT = float(os.getenv("DISCOVERY_SITEMAP_TIMEOUT", "20"))
DISCOVERY_CMS_TIMEOUT = float(os.getenv("DISCOVERY_CMS_TIMEOUT", "20"))
DISCOVERY_CACHE_TTL = float(os.getenv("DISCOVERY_CACHE_TTL", "1800"))
DISCOVERY_COLLECTOR_COOLDOWN = float(os.getenv("DISCOVERY_COLLECTOR_COOLDOWN", "1800"))
DISCOVERY_WEB_RESCUE_ENABLED = os.getenv("DISCOVERY_WEB_RESCUE_ENABLED", "1").strip().lower() not in {"0", "false", "no"}
DISCOVERY_WEB_RESCUE_RESULT_LIMIT = int(os.getenv("DISCOVERY_WEB_RESCUE_RESULT_LIMIT", "4"))
NEGATIVE_CACHE_TTL = float(os.getenv("NEGATIVE_CACHE_TTL", str(60 * 60 * 24 * 30)))
NEGATIVE_CACHEABLE_STATUSES = {404, 410, 451}
REDIRECT_CACHE_TTL = float(os.getenv("REDIRECT_CACHE_TTL", str(60 * 60 * 24 * 30)))
DEAD_FAMILY_CACHE_TTL = float(os.getenv("DEAD_FAMILY_CACHE_TTL", str(60 * 60 * 24 * 30)))
BENCHMARK_METHOD_TIMEOUT = float(os.getenv("BENCHMARK_METHOD_TIMEOUT", "180"))
RENDER_FALLBACK_ENABLED = os.getenv("RENDER_FALLBACK_ENABLED", "1").strip().lower() not in {"0", "false", "no"}
RENDER_FALLBACK_TIMEOUT_MS = int(os.getenv("RENDER_FALLBACK_TIMEOUT_MS", "8000"))
RENDER_FALLBACK_WAIT_MS = int(os.getenv("RENDER_FALLBACK_WAIT_MS", "800"))

# ───────── CMS SEARCH ─────────
CMS_SEARCH_TERMS = [
    "international", "admissions", "recruitment", "contact", "directory",
    "people", "global", "engagement", "partnerships", "cooperation",
    "internationalisation", "internationalization", "erasmus", "study abroad",
    "global opportunities"
]

# Keep localisation packs modular: config re-exports them for backward compatibility.
COUNTRY_DISCOVERY_PACKS = LOCALISATION_COUNTRY_DISCOVERY_PACKS

# ───────── TOKEN BUCKET (rate limiting) ─────────
TOK_BUCKET = 200_000
LIGHT_TOK_BUCKET = int(os.getenv("LIGHT_TOK_BUCKET", "120000"))
HEAVY_TOK_BUCKET = int(os.getenv("HEAVY_TOK_BUCKET", "80000"))
TOK_REFRESH = 60.0
COST_IN, COST_OUT = 0.60, 2.40

# ───────── OPENAI RETRY CONFIG ─────────
OAI_MAX_RETRIES = 4
OAI_BACKOFF_BASE = 1.0  # seconds
OAI_BACKOFF_MAX = 10.0  # seconds

# ───────── UNIVERSITY RETRY CONFIG ─────────
UNI_MAX_RETRIES = 3
UNI_BACKOFF_BASE = 2.0  # seconds
UNI_BACKOFF_MAX = 20.0  # seconds

# ───────── CACHE ─────────
CACHE_DIR = PROJECT_ROOT / ".cache"
CACHE_DIR.mkdir(exist_ok=True)
NEGATIVE_CACHE_DIR = CACHE_DIR / "negative"
NEGATIVE_CACHE_DIR.mkdir(exist_ok=True)
REDIRECT_CACHE_DIR = CACHE_DIR / "redirects"
REDIRECT_CACHE_DIR.mkdir(exist_ok=True)
DEAD_FAMILY_CACHE_DIR = CACHE_DIR / "dead_families"
DEAD_FAMILY_CACHE_DIR.mkdir(exist_ok=True)

# ───────── TOKEN BUCKET STATE ─────────
bucket_lock = asyncio.Lock()
bucket_used = 0
bucket_reset = time.monotonic()
light_bucket_lock = asyncio.Lock()
light_bucket_used = 0
light_bucket_reset = time.monotonic()
heavy_bucket_lock = asyncio.Lock()
heavy_bucket_used = 0
heavy_bucket_reset = time.monotonic()

# ───────── OPENAI CLIENT ─────────
OAI = AsyncOpenAI(api_key=OPENAI_API_KEY)
GPT_SEM = asyncio.Semaphore(GPT_CONCURRENCY)
LIGHT_GPT_SEM = asyncio.Semaphore(LIGHT_GPT_CONCURRENCY)
HEAVY_GPT_SEM = asyncio.Semaphore(HEAVY_GPT_CONCURRENCY)

# ───────── HTTP CLIENT (lazily initialized) ─────────
HTTP: Optional[httpx.AsyncClient] = None
HOST_SEMS = defaultdict(lambda: asyncio.Semaphore(3))  # per-host throttle
_ROBOTS: dict[str, RobotFileParser] = {}

# ───────── DEBUG STATE ─────────
DEBUG_ENABLED = False
DEBUG_DIR = Path("debug_logs")
TRAIN_CSV_PATH: Optional[Path] = None
IGNORE_ROBOTS = False

# ───────── KEYWORD LISTS FOR CANDIDATE DISCOVERY ─────────
TOKENS = [
    "staff", "directory", "people", "leadership", "administration",
    "contacts", "recruitment", "admissions", "rector", "chancellor",
    "governance", "executive", "faculty", "personnel",
    "international", "global", "partnerships", "relations", "engagement",
    "cooperation", "internationalisation", "internationalization", "mobility",
    "exchange", "erasmus", "abroad", "opportunities",
]

PREFIXES = ["", "about", "about-us", "administration", "contact", "hr", "about/leadership"]

SLUGS = [""] + [
    v
    for p in PREFIXES
    for t in TOKENS
    for v in {
        f"/{p}/{t}" if p else f"/{t}",
        f"/{p}/{t}/" if p else f"/{t}/",
        f"/{p}/{t}/index.html" if p else f"/{t}/index.html",
        f"/{p}/{t}.html" if p else f"/{t}.html",
    }
]

# ─────────────── US-SPECIFIC URL PATTERNS (.edu universities) ───────────────
# These patterns are common for US institutions but less common in UK
US_SLUGS = [
    "/about/leadership",
    "/about/administration",
    "/administration/officers",
    "/administration/leadership",
    "/leadership",
    "/leadership/team",
    "/leadership/officers",
    "/president",
    "/provost",
    "/executives",
    "/leadership-team",
    "/senior-leadership",
    "/senior-management",
    "/about/senior-leadership",
    "/board-trustees",
    "/board/trustees",
    "/executive-team",
    "/about/executive-team",
]

SUBDOMS = ["international", "global", "admissions", "apply", "about", "contact"]

# ───────── REGEX PATTERNS ─────────
ALLOWED_ROLE_WORDS = r"(international|global|admissions?|recruit(ment|er)?|partnerships?|relations?|engagement|enrol?ment|outreach|external|mobility|exchange|worldwide|external affairs|institutional advancement|rector|chancellor|president|provost|vice[- ]?chancellor|prorector|vice[- ]?president)"
SENIORITY = r"(head|director|chief|deputy|associate|assistant|manager|lead|officer|coordinator|vice|pro)"
INTL_HINTS = r"(国际|招生|留学生|国際|入試|国际|国际处|relaciones internacionales|admisiones|rekrutacja|échanges|relations internationales|internacional|mobilidade|auslands|internationale)"

# ───────── DEBUG CSV HEADERS ─────────
TRAIN_HEADERS = [
    "university","country","homepage","candidate_url","source_type","source_strategy","source_stage","page_family","parent_url","anchor_text","heuristic_score",
    "raw_contacts","kept_contacts","page_length","mailto_count",
    "depth","path_tokens","subdomain","ext",
    "cms_wordpress","cms_drupal","hreflang_en_hop",
    "Label","ReasonCode","Notes"
]
