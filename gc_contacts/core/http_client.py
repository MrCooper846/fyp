"""
HTTP client utilities: fetching, caching, robots.txt handling, retries.
"""

import logging
import json
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse, urlunparse
import hashlib
import time

import httpx
import aiofiles
from bs4 import BeautifulSoup, FeatureNotFound

import gc_contacts.config as config

LOG = logging.getLogger("gc")


def normalize_url(u: str) -> str:
    """Normalize URL by removing fragments."""
    if not u:
        return u
    p = urlparse(u)
    p = p._replace(fragment="")
    return urlunparse(p)


def cache_path(url: str) -> Path:
    """Generate cache file path for a URL."""
    return config.CACHE_DIR / (hashlib.sha1(url.encode()).hexdigest() + ".html")


def negative_cache_path(url: str) -> Path:
    """Generate negative-cache metadata path for a URL."""
    return config.NEGATIVE_CACHE_DIR / (hashlib.sha1(url.encode()).hexdigest() + ".json")


def redirect_cache_path(url: str) -> Path:
    """Generate redirect-cache metadata path for a URL."""
    return config.REDIRECT_CACHE_DIR / (hashlib.sha1(url.encode()).hexdigest() + ".json")


async def read_cache(url: str) -> Optional[str]:
    """Read cached HTML for a URL."""
    p = cache_path(url)
    if p.exists():
        async with aiofiles.open(p, "r", encoding="utf-8") as f:
            return await f.read()
    return None


async def write_cache(url: str, html: str) -> None:
    """Write HTML to cache."""
    async with aiofiles.open(cache_path(url), "w", encoding="utf-8") as f:
        await f.write(html)


async def read_negative_cache(url: str) -> Optional[dict]:
    """Read negative-cache metadata for a URL if it is still fresh."""
    p = negative_cache_path(url)
    if not p.exists():
        return None
    try:
        async with aiofiles.open(p, "r", encoding="utf-8") as f:
            payload = json.loads(await f.read())
    except Exception:
        try:
            p.unlink(missing_ok=True)
        except Exception:
            pass
        return None
    cached_at = float(payload.get("cached_at", 0.0) or 0.0)
    ttl = max(0.0, float(getattr(config, "NEGATIVE_CACHE_TTL", 0.0) or 0.0))
    if ttl <= 0:
        return None
    if cached_at <= 0 or (time.time() - cached_at) >= ttl:
        try:
            p.unlink(missing_ok=True)
        except Exception:
            pass
        return None
    return payload


async def write_negative_cache(url: str, status: int) -> None:
    """Persist a negative-cache entry for an exact dead URL."""
    payload = {"url": url, "status": int(status or 0), "cached_at": time.time()}
    async with aiofiles.open(negative_cache_path(url), "w", encoding="utf-8") as f:
        await f.write(json.dumps(payload))


async def read_redirect_cache(url: str) -> Optional[str]:
    """Read cached redirect target for a URL if it is still fresh."""
    p = redirect_cache_path(url)
    if not p.exists():
        return None
    try:
        async with aiofiles.open(p, "r", encoding="utf-8") as f:
            payload = json.loads(await f.read())
    except Exception:
        try:
            p.unlink(missing_ok=True)
        except Exception:
            pass
        return None
    cached_at = float(payload.get("cached_at", 0.0) or 0.0)
    ttl = max(0.0, float(getattr(config, "REDIRECT_CACHE_TTL", 0.0) or 0.0))
    target = normalize_url(str(payload.get("target_url", "") or "").strip())
    if ttl <= 0 or not target:
        return None
    if cached_at <= 0 or (time.time() - cached_at) >= ttl:
        try:
            p.unlink(missing_ok=True)
        except Exception:
            pass
        return None
    return target


async def write_redirect_cache(source_url: str, target_url: str) -> None:
    """Persist a canonical redirect target for a legacy URL."""
    source = normalize_url(source_url)
    target = normalize_url(target_url)
    if not source or not target or source == target:
        return
    payload = {"source_url": source, "target_url": target, "cached_at": time.time()}
    async with aiofiles.open(redirect_cache_path(source), "w", encoding="utf-8") as f:
        await f.write(json.dumps(payload))


def clear_negative_cache(url: str) -> None:
    """Remove any stale negative-cache entry once a URL succeeds."""
    try:
        negative_cache_path(url).unlink(missing_ok=True)
    except Exception:
        pass


async def apply_redirect_cache(url: str, hops: int = 3) -> str:
    """Resolve a known redirect chain without hitting the network."""
    resolved = normalize_url(url)
    seen: set[str] = set()
    for _ in range(max(0, hops)):
        if not resolved or resolved in seen:
            break
        seen.add(resolved)
        target = await read_redirect_cache(resolved)
        if not target or target == resolved:
            break
        resolved = target
    return resolved


def bs_text(html: str) -> str:
    """Extract plain text from HTML using BeautifulSoup."""
    try:
        soup = BeautifulSoup(html, "lxml")
    except FeatureNotFound:
        soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()
    return soup.get_text("\n", strip=True)


async def allowed(url: str) -> bool:
    """Check if URL is allowed by robots.txt."""
    if config.IGNORE_ROBOTS:
        return True
    try:
        parsed = urlparse(url)
        host = parsed.scheme + "://" + parsed.netloc
        rp = config._ROBOTS.get(host)
        if not rp:
            robots_url = host.rstrip("/") + "/robots.txt"
            host_sem = config.HOST_SEMS[parsed.netloc]
            async with host_sem:
                try:
                    r = await config.HTTP.get(robots_url, timeout=config.TIMEOUT, follow_redirects=True)
                    txt = r.text if r.status_code == 200 else ""
                except Exception:
                    txt = ""
            from urllib.robotparser import RobotFileParser
            rp = RobotFileParser()
            rp.set_url(robots_url)
            rp.parse((txt or "").splitlines())
            config._ROBOTS[host] = rp
        return rp.can_fetch(config.HEADERS.get("User-Agent", "*"), url)
    except Exception:
        return True


async def get_with_retry(url: str, tries: int = 3) -> Optional[httpx.Response]:
    """GET request with retry logic and per-host rate limiting."""
    host = urlparse(url).netloc
    async with config.HOST_SEMS[host]:
        for i in range(tries):
            try:
                r = await config.HTTP.get(url, timeout=config.TIMEOUT, follow_redirects=True)
                r.raise_for_status()
                return r
            except httpx.HTTPStatusError as e:
                status = int(e.response.status_code or 0)
                LOG.debug("GET fail %s try %d: HTTP %s", url, i + 1, status)
                if status in getattr(config, "NEGATIVE_CACHEABLE_STATUSES", set()):
                    await write_negative_cache(url, status)
                if status not in {408, 425, 429, 500, 502, 503, 504}:
                    break
            except Exception as e:
                LOG.debug("GET fail %s try %d: %s", url, i + 1, e)
                import asyncio
                await asyncio.sleep(0.5 * (2 ** i))
                continue
            import asyncio
            await asyncio.sleep(0.5 * (2 ** i))
        LOG.debug("GET exhausted %s", url)
        return None


async def fetch_page(url: str, expect_html: bool = True) -> Optional[str]:
    """
    Fetch a page with caching, robots.txt checking, and retries.
    """
    original_url = normalize_url(url)
    url = await apply_redirect_cache(original_url)
    if not await allowed(url):
        LOG.debug("robots disallow: %s", url)
        return None
    negative_cached = await read_negative_cache(url)
    if negative_cached is not None:
        LOG.debug("negative cache hit %s (HTTP %s)", url, negative_cached.get("status"))
        return None
    cached = await read_cache(url)
    if cached is not None:
        return cached
    r = await get_with_retry(url)
    if not r:
        return None
    final_url = normalize_url(str(getattr(r, "url", "") or "")) or url
    if final_url != original_url:
        await write_redirect_cache(original_url, final_url)
    if expect_html:
        ctype = (r.headers.get("Content-Type") or "").lower()
        if "text/html" not in ctype and "application/xhtml" not in ctype and "xml" not in ctype:
            LOG.debug("skip non-HTML %s (ctype=%s)", url, ctype)
            return None
    text = r.text
    clear_negative_cache(original_url)
    clear_negative_cache(final_url)
    await write_cache(final_url, text)
    if original_url != final_url:
        await write_cache(original_url, text)
    return text
