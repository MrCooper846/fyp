"""
Utility functions.
"""

import re
from urllib.parse import urlparse
from pathlib import Path

import gc_contacts.config as config


def tokens_of(text: str) -> int:
    """Rough estimation of token count."""
    return max(1, len(text) // 4)


def url_features(u: str) -> dict:
    """Extract structural features from a URL."""
    p = urlparse(u)
    path_tokens = [t for t in p.path.split("/") if t]
    ext = ""
    if "." in (path_tokens[-1] if path_tokens else ""):
        ext = (path_tokens[-1].split(".")[-1] or "").lower()
    return {
        "depth": p.path.count("/"),
        "path_tokens": len(path_tokens),
        "subdomain": ".".join(p.netloc.split(".")[:-2]) if p.netloc.count(".") >= 2 else "",
        "ext": ext if ext else "",
    }


def safe_slug(s: str) -> str:
    """Convert string to safe filename slug."""
    return re.sub(r"[^A-Za-z0-9._-]+", "_", s)[:80]


def home_domain_of(url: str) -> str:
    """Extract domain from URL."""
    return urlparse(url).netloc


def cost_for_tokens(tokens_in: int, tokens_out: int) -> float:
    """
    Estimate OpenAI cost using per-million-token pricing constants.
    """
    return (tokens_in / 1_000_000) * config.COST_IN + (tokens_out / 1_000_000) * config.COST_OUT
